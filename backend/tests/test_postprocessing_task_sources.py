"""Offline task -> remote helper boundary -> persistent PP cache contracts."""
from __future__ import annotations

import base64
import copy
import gzip
import hashlib
import json
import os
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.toolbox.api import create_toolbox_app
from backend.toolbox.computation import digest
from backend.toolbox.config import ExecutionConfig
from backend.toolbox.contracts import ToolboxError
from backend.toolbox.postprocessing.store import AnalysisStore, NAMES, atomic
from backend.toolbox.scheduler_profile import target_binding
from backend.toolbox.ssh.remote_files import RemoteFileError

BASE = '/api/v1/toolbox/postprocessing'
FIXTURES = Path(__file__).parent / 'fixtures' / 'postprocessing'


def seed_submitted_task(service, cfg):
    """Reusable isolated identity fixture; never talks to a scheduler."""
    project = service.store.create_project('PP-02 fixture')
    task = service.store.create_task(project['id'], title='Public XML fixture', hpc_workspace='/fixture/calc')
    identity = {'project_id': project['id'], 'task_id': task['id'], 'job_key': 'static', 'attempt_id': 'attempt-one'}
    target = target_binding(cfg)
    draft = {'job_key': 'static', 'attempt_id': 'attempt-one', 'dir': '/fixture/calc/static'}
    binding = {'operation': 'submit', **identity, 'draft': draft, 'remote_root': '/fixture/calc',
               'endpoint_digest': digest({'scheduler_target': target, 'host_key_evidence': 'unknown'})}
    action = {'action_id': 'a' * 32, 'kind': 'submit', 'state': 'executed', 'binding': binding, 'binding_hash': digest(binding)}
    job = {'key': 'static', 'attempt_id': 'attempt-one', 'status': 'completed', 'submission_state': 'submitted',
           'slurm_id': 103, 'scheduler_target': target, 'submission_action_id': action['action_id'],
           'precheck': {'snapshot': {'scheduler_target': target}}, 'draft': draft}
    flow = {'plan': {'jobs': [job]}, 'consent': {'actions': {action['action_id']: action}}}
    service.store.update_task(identity['project_id'], identity['task_id'], flow=flow)
    return identity, flow


class FakePPRemote:
    """Explicit fake transport, reusable by isolated UI acceptance."""
    def __init__(self, target, payloads=None):
        self.scheduler_target = {**target, 'identity_file': target['identity_file'] or None,
                                 'known_hosts_path': target['known_hosts_path'] or None}
        self.payloads = payloads or {}
        self.calls = []
        self.revision = 1
        self.block = None
        self.entered = threading.Event()
        self.on_stream = None
        self.failure = None

    def preview_pp(self, directory, *, expected=None, should_cancel=None):
        self.calls.append(('preview', directory))
        if self.failure:
            raise self.failure
        info = {'device': 1, 'inode': 10, 'type': 'directory', 'size': 4096,
                'mtime_ns': 1, 'ctime_ns': 1, 'mode': 448}
        root = {'requested_path': directory, 'canonical_path': directory, 'resolution_chain': [],
                'identity': info, 'ancestors': []}
        files = []
        for index, name in enumerate(sorted(NAMES)):
            if name in self.payloads:
                files.append({'name': name, 'available': True, 'size_bytes': len(self.payloads[name]),
                              'metadata': {**info, 'type': 'file', 'inode': index + 20,
                                           'size': len(self.payloads[name]), 'mtime_ns': self.revision,
                                           'ctime_ns': self.revision}})
            else:
                files.append({'name': name, 'available': False, 'size_bytes': None, 'reason': 'SOURCE_NOT_FOUND'})
        return {'root': root, 'files': files,
                'endpoint': {'host_key': {'verification': 'known_hosts'}, 'endpoint_digest': 'fixture-host'}}

    def stream_pp(self, preview, name, target, *, should_cancel, progress, deadline):
        self.calls.append(('stream', name))
        self.entered.set()
        while self.block is not None and not self.block.is_set():
            if should_cancel():
                raise RemoteFileError('ACTION_ABORTED', 'cancelled fixture')
            time.sleep(.01)
        if self.on_stream:
            self.on_stream(name)
        if self.failure:
            raise self.failure
        body = self.payloads[name]
        expected = next(f for f in preview['files'] if f['name'] == name)
        if expected['metadata']['mtime_ns'] != self.revision:
            raise RemoteFileError('SOURCE_CHANGED', 'fixture changed')
        for offset in range(0, len(body), 32768):
            if should_cancel():
                raise RemoteFileError('ACTION_ABORTED', 'cancelled fixture')
            target.write(body[offset:offset + 32768])
            progress(min(offset + 32768, len(body)))
        return len(body), hashlib.sha256(body).hexdigest()


@pytest.fixture
def task_api(tmp_path):
    cfg = ExecutionConfig(data_dir=tmp_path, ssh_host='fixture.invalid', ssh_username='tester')
    remote = FakePPRemote(target_binding(cfg), {'DOSCAR': b'not parsed in boundary tests', 'INCAR': b'ISPIN=1\n'})
    app = create_toolbox_app(root=tmp_path, settings_loader=lambda: cfg, monitor_enabled=False, file_factory=lambda: remote)
    with TestClient(app) as client:
        identity, flow = seed_submitted_task(app.state.toolbox, cfg)
        yield client, app, cfg, remote, identity, flow


def preview(client, identity, kind='dos'):
    response = client.post(BASE + '/task-sources/preview', json={**identity, 'kind': kind})
    assert response.status_code == 200, response.text
    return response.json()['preview']


def download(client, receipt, names=None):
    response = client.post(BASE + '/task-sources/import', json={'preview_id': receipt['id'], 'files': names or receipt['suggested_files']})
    assert response.status_code == 202, response.text
    return response.json()['dataset']


def wait_for(client, ident, status='downloading', seconds=15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        response = client.get(BASE + '/datasets/' + ident)
        assert response.status_code == 200
        doc = response.json()['dataset']
        if doc['status'] != status:
            return doc
        time.sleep(.02)
    raise AssertionError('fixture did not finish: ' + json.dumps(doc))


def test_preview_metadata_explicit_download_and_permission_boundary(task_api):
    client, app, _, remote, identity, _ = task_api
    receipt = preview(client, identity)
    assert receipt['source']['remote_directory'] == '/fixture/calc/static'
    assert receipt['suggested_files'] == ['DOSCAR', 'INCAR']
    assert len(receipt['files']) == 7
    assert next(f for f in receipt['files'] if f['name'] == 'DOSCAR')['size_bytes'] == len(remote.payloads['DOSCAR'])
    assert 'metadata' not in receipt['files'][0]
    assert remote.calls == [('preview', '/fixture/calc/static')]
    assert app.state.postprocessing.list() == []
    assert client.post(BASE + '/task-sources/preview', json={**identity, 'kind': 'band', 'directory': '/other'}).status_code == 422
    for names in (['POTCAR'], ['WAVECAR'], ['../DOSCAR'], ['DOSCAR'], ['DOSCAR', 'DOSCAR']):
        assert client.post(BASE + '/task-sources/import', json={'preview_id': receipt['id'], 'files': names}).status_code == 400
    assert client.get(f"/api/v1/toolbox/projects/{identity['project_id']}/tasks/{identity['task_id']}/results/DOSCAR",
                      params={'job_key': 'static', 'attempt_id': 'attempt-one'}).status_code == 400


@pytest.mark.parametrize('change', ['attempt', 'endpoint', 'binding', 'running'])
def test_preview_exact_submitted_terminal_attempt_before_ssh(task_api, change):
    client, app, cfg, remote, identity, flow = task_api
    if change == 'attempt':
        identity = {**identity, 'attempt_id': 'old-attempt'}
    elif change == 'endpoint':
        cfg.ssh_host = 'changed.invalid'
    elif change == 'binding':
        flow['consent']['actions']['a' * 32]['binding_hash'] = 'broken'
    else:
        flow['plan']['jobs'][0]['status'] = 'running'
    app.state.toolbox.store.update_task(identity['project_id'], identity['task_id'], flow=flow)
    response = client.post(BASE + '/task-sources/preview', json={**identity, 'kind': 'dos'})
    assert response.status_code in {404, 409}
    assert remote.calls == []


@pytest.mark.parametrize('kind, fixture', [('dos', 'vasprun.Al.xml.gz'), ('band', 'vasprun_Si_bands.xml.gz')])
def test_public_fixture_task_and_local_same_parser_cache_offline_export(task_api, kind, fixture):
    client, app, cfg, remote, identity, _ = task_api
    remote.payloads = {'vasprun.xml': gzip.decompress((FIXTURES / fixture).read_bytes())}
    if kind == 'band':
        remote.payloads['KPOINTS'] = (FIXTURES / 'KPOINTS_Si_bands').read_bytes()
    receipt = preview(client, identity, kind)
    doc = download(client, receipt)
    doc = wait_for(client, doc['id'])
    assert doc['status'] == 'draft' and doc['download']['status'] == 'cached', doc
    assert doc['download']['completed_bytes'] == sum(map(len, remote.payloads.values()))
    assert all(f['remote_path'].startswith('/fixture/calc/static/') for f in doc['files'])
    assert doc['source']['cached_at'] and doc['source']['snapshot_sha256']
    before_source = copy.deepcopy(doc['source'])
    assert client.put(BASE + '/datasets/' + doc['id'] + '/files/INCAR', content=b'no mutation').status_code == 409
    # Reuse and parsing work after both connection and authoritative current endpoint change.
    calls = list(remote.calls)
    cfg.ssh_host = 'offline.invalid'
    remote.failure = RemoteFileError('REMOTE_CAPABILITY_UNAVAILABLE', 'offline fixture')
    assert client.post(BASE + '/task-sources/import', json={'reuse_dataset_id': doc['id']}).status_code == 202
    assert client.post(BASE + '/datasets/' + doc['id'] + '/analyses').status_code == 202
    parsed_task = wait_for(client, doc['id'], 'processing', 45)
    assert parsed_task['status'] == 'ready', parsed_task
    local = client.post(BASE + '/datasets', json={'kind': kind}).json()['dataset']
    for name, payload in remote.payloads.items():
        assert client.put(BASE + '/datasets/' + local['id'] + '/files/' + name, content=payload).status_code == 200
    assert client.post(BASE + '/datasets/' + local['id'] + '/analyses').status_code == 202
    assert wait_for(client, local['id'], 'processing', 45)['status'] == 'ready'
    task_curves = client.get(BASE + '/datasets/' + doc['id'] + '/curves').json()
    local_curves = client.get(BASE + '/datasets/' + local['id'] + '/curves').json()
    for key in ('curves', 'parser', 'view', 'reference_ev', 'warnings', 'ticks', 'units'):
        assert task_curves[key] == local_curves[key]
    assert task_curves['source'] == before_source
    exported = client.get(BASE + '/datasets/' + doc['id'] + '/export?format=csv').text
    assert identity['task_id'] in exported and 'attempt-one' in exported and '/fixture/calc/static/' in exported
    assert remote.calls == calls
    restored = AnalysisStore(app.state.toolbox.root).read(doc['id'])
    assert restored['files'] == parsed_task['files'] and restored['source'] == before_source
    (app.state.postprocessing.directory(doc['id']) / 'vasprun.xml').write_bytes(b'corrupt cached bytes')
    broken = client.post(BASE + '/task-sources/import', json={'reuse_dataset_id': doc['id']})
    assert broken.status_code == 409 and broken.json()['error']['code'] == 'PP_SOURCE_CHANGED'


def test_cancel_retry_and_restart_persist_without_partial_batch(task_api):
    client, app, _, remote, identity, _ = task_api
    remote.block = threading.Event()
    receipt = preview(client, identity)
    doc = download(client, receipt)
    assert remote.entered.wait(2)
    cancelled = client.post(BASE + '/datasets/' + doc['id'] + '/cancel').json()['dataset']
    assert cancelled['status'] == 'cancelled' and cancelled['download']['status'] == 'cancelled'
    thread = app.state.postprocessing.task_sources.active.get(doc['id'])
    if thread:
        thread[1].join(2)
    assert not app.state.postprocessing.task_sources.active
    directory = app.state.postprocessing.directory(doc['id'])
    assert not list(directory.glob('*.download')) and not (directory / 'DOSCAR').exists()
    remote.block = None
    retry = client.post(BASE + '/datasets/' + doc['id'] + '/retry-download')
    assert retry.status_code == 202
    assert wait_for(client, doc['id'])['download']['status'] == 'cached'
    interrupted = download(client, receipt)
    manager = app.state.postprocessing.task_sources
    current = wait_for(client, interrupted['id'])
    current.update(status='downloading', files=[])
    current['download']['status'] = 'downloading'
    app.state.postprocessing.save(current)
    (app.state.postprocessing.directory(current['id']) / 'DOSCAR.download').write_bytes(b'partial')
    manager.close()
    restored = AnalysisStore(app.state.toolbox.root)
    recovered = restored.read(current['id'])
    assert recovered['status'] == 'failed' and recovered['download']['error']['code'] == 'PP_DOWNLOAD_INTERRUPTED'
    assert not (restored.directory(current['id']) / 'DOSCAR').exists()
    assert not list(restored.directory(current['id']).glob('*.download'))


def test_changed_remote_same_size_and_attempt_during_download_discard_batch(task_api):
    client, app, _, remote, identity, flow = task_api
    receipt = preview(client, identity)
    remote.revision += 1
    doc = wait_for(client, download(client, receipt)['id'])
    assert doc['status'] == 'failed' and doc['error']['code'] == 'PP_SOURCE_CHANGED'
    assert doc['files'] == []
    assert client.post(BASE + '/datasets/' + doc['id'] + '/analyses').status_code == 400
    receipt = preview(client, identity)
    def change(name):
        flow['plan']['jobs'][0]['attempt_id'] = 'attempt-two'
        app.state.toolbox.store.update_task(identity['project_id'], identity['task_id'], flow=flow)
    remote.on_stream = change
    second = wait_for(client, download(client, receipt)['id'])
    assert second['status'] == 'failed' and second['files'] == []
    assert second['error']['code'] in {'RESULT_ATTEMPT_NOT_FOUND', 'RESULT_ATTEMPT_CHANGED'}
    assert not list(app.state.postprocessing.directory(second['id']).glob('*.download'))


def test_refresh_new_snapshot_preserves_old_and_expiry_requires_preview(task_api):
    client, app, _, remote, identity, _ = task_api
    first = wait_for(client, download(client, preview(client, identity))['id'])
    remote.payloads['DOSCAR'] = b'refreshed remote output'
    remote.revision += 1
    second = wait_for(client, download(client, preview(client, identity))['id'])
    assert second['id'] != first['id'] and second['files'][0]['sha256'] != first['files'][0]['sha256']
    assert app.state.postprocessing.read(first['id']) == first
    receipt = preview(client, identity)
    path = app.state.postprocessing.task_sources.previews / (receipt['id'] + '.json')
    expired = json.loads(path.read_text(encoding='utf-8'))
    expired['public']['expires_at'] = '2000-01-01T00:00:00+00:00'
    atomic(path, expired)
    response = client.post(BASE + '/task-sources/import', json={'preview_id': receipt['id'], 'files': receipt['suggested_files']})
    assert response.status_code == 409 and response.json()['error']['code'] == 'PP_PREVIEW_EXPIRED'


@pytest.mark.parametrize('unknown', [False, True])
def test_import_checks_measured_local_space_before_creating_snapshot(task_api, monkeypatch, unknown):
    from backend.toolbox.postprocessing import task_sources
    client, app, _, remote, identity, _ = task_api
    receipt = preview(client, identity)
    def capacity(path):
        if unknown:
            raise OSError('fixture capacity unavailable')
        return SimpleNamespace(free=0)
    monkeypatch.setattr(task_sources.shutil, 'disk_usage', capacity)
    response = client.post(BASE + '/task-sources/import', json={'preview_id': receipt['id'], 'files': receipt['suggested_files']})
    assert response.status_code == (503 if unknown else 413)
    assert response.json()['error']['code'] == ('PP_STORAGE_UNAVAILABLE' if unknown else 'PP_DISK_FULL')
    assert not app.state.postprocessing.list()
    assert remote.calls == [('preview', '/fixture/calc/static')]


def test_batch_observation_ignores_unrelated_parent_timestamp_change(task_api):
    client, _, _, remote, identity, _ = task_api
    original = remote.preview_pp
    count = 0
    def observe(directory, **kwargs):
        nonlocal count
        count += 1
        result = original(directory, **kwargs)
        result['root']['ancestors'] = [{'path': '/fixture', 'device': 1, 'inode': 2, 'type': 'directory',
                                       'mtime_ns': count, 'ctime_ns': count, 'size': 4096, 'mode': 448}]
        return result
    remote.preview_pp = observe
    doc = wait_for(client, download(client, preview(client, identity))['id'])
    assert doc['status'] == 'draft' and doc['download']['status'] == 'cached', doc


def test_same_named_files_from_distinct_tasks_are_separate_caches(task_api):
    client, app, cfg, remote, identity, _ = task_api
    first = wait_for(client, download(client, preview(client, identity))['id'])
    second_identity, _ = seed_submitted_task(app.state.toolbox, cfg)
    remote.payloads['DOSCAR'] = b'second task result'
    remote.revision += 1
    second = wait_for(client, download(client, preview(client, second_identity))['id'])
    assert first['id'] != second['id'] and first['source']['task_id'] != second['source']['task_id']
    assert first['files'][0]['sha256'] != second['files'][0]['sha256']
    assert app.state.postprocessing.read(first['id']) == first
    remote.failure = RemoteFileError('REMOTE_CAPABILITY_UNAVAILABLE', 'offline')
    for cached in (first, second):
        response = client.post(BASE + '/task-sources/import', json={'reuse_dataset_id': cached['id']})
        assert response.status_code == 202 and response.json()['dataset'] == cached


def test_pp_wire_bounded_frames_checksum_cancel_and_separate_allowlist(monkeypatch, tmp_path):
    from backend.toolbox.ssh import remote_files
    from backend.toolbox.ssh import file_helper
    endpoint = {'host_key': {'verification': 'known_hosts'}, 'endpoint_digest': 'same'}
    class Manager:
        def file_endpoint(self, target):
            return copy.deepcopy(endpoint)
    class Wire:
        frames = []
        closed = 0
        def __init__(self, manager, **kwargs):
            assert kwargs['expected_endpoint'] == endpoint
        def call(self, request, **kwargs):
            assert kwargs['timeout'] <= 5
            if kwargs.get('should_cancel') and kwargs['should_cancel']():
                raise RemoteFileError('ACTION_ABORTED', 'cancelled')
            return self.frames.pop(0)
        def check_endpoint(self):
            pass
        def close(self):
            Wire.closed += 1
    monkeypatch.setattr(remote_files, '_Wire', Wire)
    files = remote_files.RemoteFiles(Manager(), scheduler_target={})
    body = b'short frame output'
    receipt = {'endpoint': endpoint, 'root': {}, 'files': [{'name': 'DOSCAR', 'available': True,
                 'size_bytes': len(body), 'metadata': {'size': len(body)}}]}
    Wire.frames = [{'state': 'ready', 'size': len(body)}, {'state': 'chunk', 'index': 0, 'data': base64.b64encode(body).decode()},
                   {'state': 'complete', 'length': len(body), 'sha256': hashlib.sha256(body).hexdigest()}]
    progress = []
    with (tmp_path / 'staging').open('wb') as target:
        assert files.stream_pp(receipt, 'DOSCAR', target, should_cancel=lambda: False, progress=progress.append,
                               deadline=time.monotonic() + 10) == (len(body), hashlib.sha256(body).hexdigest())
    assert progress == [len(body)] and Wire.closed == 1
    with (tmp_path / 'staging').open('wb') as target, pytest.raises(RemoteFileError, match='cancelled'):
        files.stream_pp(receipt, 'DOSCAR', target, should_cancel=lambda: True, progress=lambda n: None, deadline=time.monotonic() + 10)
    assert Wire.closed == 1  # pre-cancel never opens a new helper channel
    with pytest.raises(RemoteFileError):
        files.read_result('/fixture', 'DOSCAR')
    assert file_helper.RESULT_NAMES == {'OUTCAR', 'OSZICAR', 'CONTCAR'}
    assert not file_helper.PP_NAMES & {'POTCAR', 'WAVECAR', 'CHGCAR', 'PROCAR'}


@pytest.mark.parametrize('failure', ['checksum', 'index', 'base64', 'excess_bytes', 'deadline'])
def test_pp_wire_rejects_corrupt_out_of_order_excess_frames_and_deadline(monkeypatch, tmp_path, failure):
    from backend.toolbox.ssh import remote_files
    endpoint = {'host_key': {'verification': 'known_hosts'}, 'endpoint_digest': 'same'}
    class Manager:
        def file_endpoint(self, target):
            return copy.deepcopy(endpoint)
    class Wire:
        closed = False
        def __init__(self, manager, **kwargs):
            pass
        def call(self, request, **kwargs):
            return frames.pop(0)
        def check_endpoint(self):
            pass
        def close(self):
            Wire.closed = True
    monkeypatch.setattr(remote_files, '_Wire', Wire)
    files = remote_files.RemoteFiles(Manager(), scheduler_target={})
    body = b'bounded fixture'
    frame = {'state': 'chunk', 'index': 0, 'data': base64.b64encode(body).decode()}
    complete = {'state': 'complete', 'length': len(body), 'sha256': hashlib.sha256(body).hexdigest()}
    if failure == 'checksum':
        complete['sha256'] = 'wrong'
    elif failure == 'index':
        frame['index'] = 1
    elif failure == 'base64':
        frame['data'] = '#not-base64'
    elif failure == 'excess_bytes':
        frame['data'] = base64.b64encode(b'x' * 32769).decode()
    frames = [{'state': 'ready', 'size': len(body)}, frame, complete]
    receipt = {'endpoint': endpoint, 'root': {}, 'files': [{'name': 'DOSCAR', 'available': True,
               'size_bytes': len(body), 'metadata': {'size': len(body)}}]}
    with (tmp_path / 'bad.download').open('wb') as target, pytest.raises(RemoteFileError) as caught:
        files.stream_pp(receipt, 'DOSCAR', target, should_cancel=lambda: False, progress=lambda n: None,
                        deadline=time.monotonic() + (-1 if failure == 'deadline' else 10))
    assert caught.value.code == ('PP_DOWNLOAD_TIMEOUT' if failure == 'deadline' else
                                 'SOURCE_CHANGED' if failure == 'checksum' else 'PROTOCOL_ERROR')
    assert Wire.closed is (failure != 'deadline')


@pytest.mark.skipif(os.name != 'posix', reason='O_NOFOLLOW/dir_fd helper requires POSIX')
def test_posix_pp_preview_reader_links_size_and_same_size_source_change(tmp_path):
    from backend.toolbox.ssh import file_helper as helper
    target = tmp_path / 'DOSCAR'
    target.write_bytes(b'first')
    root = helper.root_evidence(str(tmp_path))
    receipt = helper.pp_preview(root)
    expected = next(f['metadata'] for f in receipt['files'] if f['name'] == 'DOSCAR')
    reader = helper.PPReader(root, 'DOSCAR', expected)
    assert base64.b64decode(reader.next()['data']) == b'first'
    target.write_bytes(b'other')
    with pytest.raises(helper.FileError):
        reader.next()
    reader.close()
    with pytest.raises(helper.FileError):
        helper.PPReader(helper.root_evidence(str(tmp_path)), 'DOSCAR', expected)
    target.unlink()
    target.symlink_to(tmp_path / 'INCAR')
    receipt = helper.pp_preview(helper.root_evidence(str(tmp_path)))
    link = next(f for f in receipt['files'] if f['name'] == 'DOSCAR')
    assert not link['available'] and link['reason'] == 'PATH_SYMLINK_ESCAPE'
    target.unlink()
    with target.open('wb') as stream:
        stream.truncate(helper.PP_LIMIT + 1)
    receipt = helper.pp_preview(helper.root_evidence(str(tmp_path)))
    large = next(f for f in receipt['files'] if f['name'] == 'DOSCAR')
    assert not large['available'] and large['reason'] == 'PP_TOO_LARGE'
