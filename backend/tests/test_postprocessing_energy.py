"""Synthetic accounting and isolated API/storage/identity contracts; no VASP/SSH."""
import copy
import csv
import hashlib
import io
import json
import base64
import time

import pytest
from fastapi.testclient import TestClient

from backend.toolbox.api import create_toolbox_app
from backend.toolbox.config import ExecutionConfig
from backend.toolbox.scheduler_profile import target_binding
from backend.toolbox.ssh.remote_files import RemoteFileError
from backend.tests.test_postprocessing_task_sources import FakePPRemote, seed_submitted_task

BASE = '/api/v1/toolbox/postprocessing/energy'
BASIS = 'sigma_to_zero_ev'


@pytest.fixture
def energy_api(tmp_path):
    app = create_toolbox_app(root=tmp_path, monitor_enabled=False)
    with TestClient(app) as client:
        yield client, app


def create(client):
    response = client.post(BASE + '/collections', json={'title': '人工核算样例'})
    assert response.status_code == 201, response.text
    return response.json()['collection']


def manual(client, doc, name, comp, energy, basis=BASIS):
    response = client.post(f"{BASE}/collections/{doc['id']}/manual", json={
        'expected_revision': doc['revision'], 'name': name, 'composition': comp,
        'energy_fields': {basis: energy}, 'energy_basis': basis, 'unit': 'eV', 'reference_note': '合成计量值'})
    assert response.status_code == 201, response.text
    return response.json()['collection']


def adsorption(client):
    doc = create(client)
    for name, comp, energy in [('清洁表面', {'Pt': 4}, -100), ('CO分子', {'C': 1, 'O': 1}, -10),
                                ('构型 A', {'Pt': 4, 'C': 1, 'O': 1}, -112),
                                ('构型 B', {'Pt': 4, 'C': 1, 'O': 1}, -111)]:
        doc = manual(client, doc, name, comp, energy)
    ids = [s['id'] for s in doc['samples']]
    group = {'id': 'ads', 'name': 'CO 吸附', 'kind': 'adsorption', 'energy_basis': BASIS, 'basis_confirmed': True,
             'clean_sample_id': ids[0], 'adsorbate_sample_id': ids[1], 'reference_units': 1,
             'targets': [{'sample_id': ids[2], 'adsorbate_count': 1}, {'sample_id': ids[3], 'adsorbate_count': 1}]}
    roles = ['clean_slab', 'adsorbate', 'adsorbed', 'adsorbed']
    rows = [{'sample_id': sid, 'role': role, 'included': True, 'confirmed': True, 'accepted_warnings': True}
            for sid, role in zip(ids, roles)]
    return doc, group, rows


def configure(client, doc, groups, rows):
    response = client.put(f"{BASE}/collections/{doc['id']}/configuration", json={
        'expected_revision': doc['revision'], 'samples': rows, 'groups': groups})
    assert response.status_code == 200, response.text
    return response.json()['collection']


def calculate(client, doc):
    return client.post(f"{BASE}/collections/{doc['id']}/calculate", json={'expected_revision': doc['revision']})


def test_adsorption_shared_references_save_reopen_export_and_csv_formula_guard(energy_api):
    client, app = energy_api
    doc, group, rows = adsorption(client)
    rows[2]['name'] = '=HYPERLINK("https://fixture.invalid")'
    doc = configure(client, doc, [group], rows)
    response = calculate(client, doc)
    assert response.status_code == 200, response.text
    doc = response.json()['collection']
    result_rows = doc['result']['groups'][0]['rows']
    assert [r['delta_ev'] for r in result_rows] == [-2, -1]
    assert [r['normalized_ev'] for r in result_rows] == [-2, -1]
    assert all(len(r['terms']) == 3 and r['warnings'] for r in result_rows)
    assert '运行未完成或结束状态未知' in '\n'.join(doc['result']['warnings'])
    reopened = client.get(f"{BASE}/collections/{doc['id']}").json()['collection']
    assert reopened == doc
    # Instantiate a fresh store, proving persistent recovery independent of app state.
    from backend.toolbox.postprocessing.energy.store import EnergyStore
    assert EnergyStore(app.state.toolbox.root).read(doc['id']) == doc
    exported = client.get(f"{BASE}/collections/{doc['id']}/export").json()['collection']
    assert exported['samples'][0]['parsed']['status']['completion'] == 'unknown'
    assert exported['result'] == doc['result']
    csv_response = client.get(f"{BASE}/collections/{doc['id']}/export?format=csv")
    records = list(csv.DictReader(io.StringIO(csv_response.content.decode('utf-8-sig'))))
    assert any(r['name'].startswith("'=HYPERLINK") for r in records)
    assert [float(r['delta_ev']) for r in records if r['record_type'] == 'result'] == [-2, -1]
    assert client.get('/api/v1/toolbox/postprocessing/datasets').json()['datasets'] == []


def test_formation_reference_per_atom_oxygen_molecule_and_hard_stoichiometry(energy_api):
    client, _ = energy_api
    doc = create(client)
    for name, comp, energy in [('Al2O', {'Al': 2, 'O': 1}, -20), ('Al胞', {'Al': 2}, -8), ('O2', {'O': 2}, -10)]:
        doc = manual(client, doc, name, comp, energy)
    ids = [s['id'] for s in doc['samples']]
    group = {'id': 'formation', 'name': '元素参考形成能', 'kind': 'formation', 'energy_basis': BASIS,
             'basis_confirmed': True, 'element_references': {'Al': ids[1], 'O': ids[2]}, 'targets': [{'sample_id': ids[0]}]}
    rows = [{'sample_id': sid, 'role': 'material' if i == 0 else 'element_reference',
             'included': True, 'confirmed': True, 'accepted_warnings': True} for i, sid in enumerate(ids)]
    doc = configure(client, doc, [group], rows)
    response = calculate(client, doc)
    assert response.status_code == 200, response.text
    result = response.json()['collection']['result']['groups'][0]['rows'][0]
    assert result['delta_ev'] == -7
    assert result['normalized_ev'] == pytest.approx(-7 / 3)
    assert result['terms'][2]['mu_ev_per_atom'] == -5
    # A reference incorrectly bound to a different element stays a hard error.
    group['element_references']['O'] = ids[1]
    doc = configure(client, response.json()['collection'], [group], rows)
    assert calculate(client, doc).json()['error']['code'] == 'ENERGY_STOICHIOMETRY_CONFLICT'


def test_group_change_invalidates_confirmations_results_and_revision(energy_api):
    client, app = energy_api
    doc, group, rows = adsorption(client)
    doc = configure(client, doc, [group], rows)
    calculated = calculate(client, doc).json()['collection']
    old_result = copy.deepcopy(calculated['result'])
    group['energy_basis'] = 'without_entropy_ev'
    doc = configure(client, calculated, [group], [])
    assert doc['result'] is None
    assert all(not s['confirmed'] and not s['accepted_warnings'] for s in doc['samples'])
    assert calculate(client, doc).status_code == 409
    assert calculate(client, calculated).json()['error']['code'] == 'ENERGY_REVISION_CONFLICT'
    # Copying an old result cannot bypass input-fingerprint validity.
    doc['result'] = old_result
    app.state.energy.save(doc)
    assert client.get(f"{BASE}/collections/{doc['id']}/export").json()['error']['code'] == 'ENERGY_RESULT_STALE'


def test_warning_acceptance_does_not_bypass_missing_energy_or_composition(energy_api):
    client, _ = energy_api
    doc, group, rows = adsorption(client)
    rows[0]['accepted_warnings'] = False
    doc = configure(client, doc, [group], rows)
    assert calculate(client, doc).json()['error']['code'] == 'ENERGY_WARNING_ACCEPTANCE_REQUIRED'
    rows[0]['accepted_warnings'] = True
    rows[2]['override'] = {'unit': 'eV', 'note': '错误计量', 'composition': {'Pt': 4, 'C': 2, 'O': 1}}
    doc = configure(client, doc, [group], rows)
    assert calculate(client, doc).json()['error']['code'] == 'ENERGY_STOICHIOMETRY_CONFLICT'
    rows[2]['override'] = None
    group['energy_basis'] = 'without_entropy_ev'
    doc = configure(client, doc, [group], rows)
    assert calculate(client, doc).json()['error']['code'] == 'ENERGY_FIELD_REQUIRED'


def test_csv_manual_import_atomic_finite_unit_and_positive_integer_composition(energy_api):
    client, _ = energy_api
    doc = create(client)
    content = 'name,relative_path,composition,energy_basis,energy_ev,unit,reference_note\n' + \
              '中文样本,中文 目录/OUTCAR,"{""Al"":2}",sigma_to_zero_ev,-8,eV,人工\n'
    response = client.post(f"{BASE}/collections/{doc['id']}/csv?expected_revision={doc['revision']}", content=content.encode())
    assert response.status_code == 201, response.text
    doc = response.json()['collection']
    assert doc['samples'][0]['source']['relative_path'] == '中文 目录/OUTCAR'
    assert doc['samples'][0]['source']['kind'] == 'csv'
    before = copy.deepcopy(doc)
    bad = content + 'bad,,"{""O"":2}",sigma_to_zero_ev,NaN,eV,\n'
    response = client.post(f"{BASE}/collections/{doc['id']}/csv?expected_revision={doc['revision']}", content=bad.encode())
    assert response.status_code == 400
    assert client.get(f"{BASE}/collections/{doc['id']}").json()['collection'] == before
    for comp in ({'Al': 1.5}, {'Al': True}, {'Al': 0}):
        response = client.post(f"{BASE}/collections/{doc['id']}/manual", json={
            'expected_revision': doc['revision'], 'name': 'bad', 'composition': comp,
            'energy_fields': {BASIS: -1}, 'energy_basis': BASIS, 'unit': 'eV'})
        assert response.status_code in {400, 422}


def test_local_same_name_relative_metadata_cache_tamper_and_bad_path(energy_api):
    client, app = energy_api
    doc = create(client)
    payload = b'vasp.6.3.2\nsource fixture lacks scientific fields\n'
    for label in ['中文 表面/位点 A/OUTCAR', '中文 表面/位点 B/OUTCAR']:
        response = client.post(f"{BASE}/collections/{doc['id']}/outcar", params={
            'expected_revision': doc['revision'], 'name': 'OUTCAR', 'relative_path': label}, content=payload)
        assert response.status_code == 201, response.text
        doc = response.json()['collection']
    assert len({s['id'] for s in doc['samples']}) == 2
    assert len({s['source']['sha256'] for s in doc['samples']}) == 1
    assert all(s['role'] is None and not s['confirmed'] for s in doc['samples'])
    for label in ['../OUTCAR', '/绝对/OUTCAR', 'C:/科研/OUTCAR', 'a\\OUTCAR']:
        response = client.post(f"{BASE}/collections/{doc['id']}/outcar", params={
            'expected_revision': doc['revision'], 'relative_path': label}, content=payload)
        assert response.status_code == 400
    sample = doc['samples'][0]
    (app.state.energy.directory(doc['id']) / (sample['id'] + '.bin')).write_bytes(b'changed')
    assert calculate(client, doc).json()['error']['code'] == 'ENERGY_SOURCE_CHANGED'


def test_pbe_u_common_element_compatibility_and_hard_method_conflicts(energy_api):
    client, app = energy_api
    doc = create(client)
    for name, comp, energy in [('FeO', {'Fe': 1, 'O': 1}, -20), ('Fe参考', {'Fe': 1}, -8), ('O2参考', {'O': 2}, -10)]:
        doc = manual(client, doc, name, comp, energy)
    for sample in doc['samples']:
        method = 'PBE+U' if 'Fe' in sample['parsed']['composition'] else 'PBE'
        sample['parsed']['metadata'].update(method=method, parameters={'LDAUTYPE': 2},
            u_by_element={e: {'l': 2 if e == 'Fe' else -1, 'u': 4.0 if e == 'Fe' else 0.0, 'j': 0.0}
                          for e in sample['parsed']['composition']} if method == 'PBE+U' else {})
    app.state.energy.save(doc)
    ids = [s['id'] for s in doc['samples']]
    group = {'id': 'u', 'name': 'Fe+U/O2', 'kind': 'formation', 'energy_basis': BASIS, 'basis_confirmed': True,
             'element_references': {'Fe': ids[1], 'O': ids[2]}, 'targets': [{'sample_id': ids[0]}]}
    rows = [{'sample_id': sid, 'role': 'material' if i == 0 else 'element_reference', 'included': True,
             'confirmed': True, 'accepted_warnings': True} for i, sid in enumerate(ids)]
    doc = configure(client, doc, [group], rows)
    response = calculate(client, doc)
    assert response.status_code == 200, response.text
    doc = response.json()['collection']
    doc['samples'][1]['parsed']['metadata']['u_by_element']['Fe']['u'] = 3
    app.state.energy.save(doc)
    doc = configure(client, doc, [group], rows)
    assert calculate(client, doc).json()['error']['code'] == 'ENERGY_U_CONFLICT'
    doc['samples'][1]['parsed']['metadata']['u_by_element']['Fe']['u'] = 4
    doc['samples'][2]['parsed']['metadata']['method'] = 'SCAN'
    app.state.energy.save(doc)
    doc = configure(client, doc, [group], rows)
    assert calculate(client, doc).json()['error']['code'] == 'ENERGY_METHOD_CONFLICT'


class FakeEnergyRemote(FakePPRemote):
    def preview_energy(self, directory, *, expected=None, should_cancel=None):
        observation = self.preview_pp(directory, expected=expected, should_cancel=should_cancel)
        info = observation['root']['identity']
        body = self.payloads['OUTCAR']
        observation['files'] = [{'name': 'OUTCAR', 'available': True, 'size_bytes': len(body),
                                 'metadata': {**info, 'type': 'file', 'inode': 200, 'size': len(body),
                                              'mtime_ns': self.revision, 'ctime_ns': self.revision}}]
        return observation

    stream_energy = FakePPRemote.stream_pp


@pytest.fixture
def energy_task_api(tmp_path):
    cfg = ExecutionConfig(data_dir=tmp_path, ssh_host='fixture.invalid', ssh_username='fixture')
    remote = FakeEnergyRemote(target_binding(cfg), {'OUTCAR': b'vasp.6.3.2\nfull synthetic source\n'})
    app = create_toolbox_app(root=tmp_path, settings_loader=lambda: cfg, monitor_enabled=False, file_factory=lambda: remote)
    with TestClient(app) as client:
        identity, flow = seed_submitted_task(app.state.toolbox, cfg)
        flow['plan']['jobs'][0]['status'] = 'running'
        app.state.toolbox.store.update_task(identity['project_id'], identity['task_id'], flow=flow)
        yield client, app, remote, identity, flow


def test_running_task_full_stable_snapshot_offline_reuse_and_old_terminal_policy(energy_task_api):
    client, _, remote, identity, _ = energy_task_api
    response = client.post(BASE + '/task-sources/preview', json=identity)
    assert response.status_code == 200, response.text
    preview = response.json()['preview']
    assert [f['name'] for f in preview['files']] == ['OUTCAR']
    doc = create(client)
    response = client.post(f"{BASE}/collections/{doc['id']}/task-sources/import", json={
        'expected_revision': doc['revision'], 'preview_id': preview['id']})
    assert response.status_code == 201, response.text
    doc = response.json()['collection']
    sample = doc['samples'][0]
    assert sample['source']['sha256'] == hashlib.sha256(remote.payloads['OUTCAR']).hexdigest()
    assert sample['source']['attempt_id'] == identity['attempt_id']
    before_calls = list(remote.calls)
    remote.failure = RemoteFileError('REMOTE_IO_ERROR', 'offline')
    target = create(client)
    response = client.post(f"{BASE}/collections/{target['id']}/reuse", json={
        'expected_revision': target['revision'], 'source_collection_id': doc['id'], 'sample_id': sample['id']})
    assert response.status_code == 201, response.text
    assert response.json()['collection']['samples'][0]['id'] != sample['id']
    assert remote.calls == before_calls
    response = client.post('/api/v1/toolbox/postprocessing/task-sources/preview', json={**identity, 'kind': 'dos'})
    assert response.status_code == 409
    assert remote.calls == before_calls


@pytest.mark.parametrize('change', ['attempt', 'file', 'binding', 'cancel'])
def test_task_snapshot_change_never_publishes_partial_source(energy_task_api, change):
    client, app, remote, identity, flow = energy_task_api
    preview = client.post(BASE + '/task-sources/preview', json=identity).json()['preview']
    doc = create(client)
    if change == 'attempt':
        flow['plan']['jobs'][0]['attempt_id'] = 'new-attempt'
        app.state.toolbox.store.update_task(identity['project_id'], identity['task_id'], flow=flow)
    elif change == 'binding':
        flow['consent']['actions']['a' * 32]['binding_hash'] = 'bad'
        app.state.toolbox.store.update_task(identity['project_id'], identity['task_id'], flow=flow)
    elif change == 'cancel':
        remote.on_stream = lambda _: app.state.energy.close()
    else:
        remote.on_stream = lambda _: setattr(remote, 'revision', remote.revision + 1)
    response = client.post(f"{BASE}/collections/{doc['id']}/task-sources/import", json={
        'expected_revision': doc['revision'], 'preview_id': preview['id']})
    assert response.status_code in {400, 404, 409}, response.text
    assert client.get(f"{BASE}/collections/{doc['id']}").json()['collection']['samples'] == []
    assert not list(app.state.energy.directory(doc['id']).glob('*.bin'))
    assert not list(app.state.energy.root.glob('*.upload'))


def test_draft_empty_group_saved_but_not_computable(energy_api):
    client, _ = energy_api
    doc = create(client)
    group = {'id': 'draft', 'name': '待核对', 'kind': 'formation', 'energy_basis': BASIS,
             'basis_confirmed': False, 'targets': [], 'element_references': {}}
    doc = configure(client, doc, [group], [])
    assert doc['groups'][0]['targets'] == []
    assert calculate(client, doc).json()['error']['code'] == 'ENERGY_TARGET_REQUIRED'


def test_independent_energy_cache_and_collection_quota_before_import(energy_api, monkeypatch):
    from backend.toolbox.postprocessing.energy import store as store_module
    client, app = energy_api
    doc = create(client)
    svc = app.state.energy
    # Existing DOS/band cache is outside this independent budget.
    old = svc.root.parent / 'postprocessing'
    old.mkdir()
    (old / 'existing-dos.bin').write_bytes(b'x' * 10000)
    monkeypatch.setattr(store_module, 'MAX_STORE', 5000)
    response = client.post(f"{BASE}/collections/{doc['id']}/manual", json={
        'expected_revision': doc['revision'], 'name': 'Al', 'composition': {'Al': 1},
        'energy_fields': {BASIS: -1}, 'energy_basis': BASIS, 'unit': 'eV'})
    assert response.status_code == 201, response.text
    doc = response.json()['collection']
    monkeypatch.setattr(store_module, 'MAX_SAMPLES', 1)
    response = client.post(f"{BASE}/collections/{doc['id']}/manual", json={
        'expected_revision': doc['revision'], 'name': 'extra', 'composition': {'Al': 1},
        'energy_fields': {BASIS: -1}, 'energy_basis': BASIS, 'unit': 'eV'})
    assert response.status_code == 413
    monkeypatch.setattr(store_module, 'MAX_SAMPLES', 100)
    monkeypatch.setattr(store_module, 'MAX_STORE', 1)
    response = client.post(f"{BASE}/collections/{doc['id']}/manual", json={
        'expected_revision': doc['revision'], 'name': 'extra', 'composition': {'Al': 1},
        'energy_fields': {BASIS: -1}, 'energy_basis': BASIS, 'unit': 'eV'})
    assert response.json()['error']['code'] == 'ENERGY_QUOTA'


def test_known_source_and_metadata_hard_errors_cannot_be_warning_accepted(energy_api):
    client, app = energy_api
    doc, group, rows = adsorption(client)
    sample = doc['samples'][2]
    sample['parsed']['issues'] = [{'code': 'RUN_UNSUPPORTED', 'severity': 'error', 'message': '非自洽', 'recoverable_by_manual': False}]
    sample['parsed']['errors'] = ['非自洽']
    sample['parsed']['metadata']['support'] = {'automatic_comparison': False, 'reasons': ['RUN_UNSUPPORTED']}
    app.state.energy.save(doc)
    rows[2]['override'] = {'composition': {'Pt': 4, 'C': 1, 'O': 1},
                           'energy_fields': {BASIS: -112}, 'energy_basis': BASIS, 'unit': 'eV', 'note': '不能消除不支持运行'}
    doc = configure(client, doc, [group], rows)
    response = calculate(client, doc)
    assert response.json()['error']['code'] == 'ENERGY_SOURCE_UNSUPPORTED'
    assert '非自洽' in response.json()['error']['message']


def test_same_parser_for_local_task_full_source_and_manual_composition_repair(energy_task_api):
    from backend.tests.test_energy_parser import header, block, FOOTER
    client, _, remote, identity, _ = energy_task_api
    payload = (header() + block(e0='-10') + FOOTER).encode()
    remote.payloads['OUTCAR'] = payload
    doc = create(client)
    doc = client.post(f"{BASE}/collections/{doc['id']}/outcar", params={'expected_revision': doc['revision']},
                      content=payload).json()['collection']
    preview = client.post(BASE + '/task-sources/preview', json=identity).json()['preview']
    response = client.post(f"{BASE}/collections/{doc['id']}/task-sources/import", json={
        'expected_revision': doc['revision'], 'preview_id': preview['id']})
    assert response.status_code == 201, response.text
    doc = response.json()['collection']
    assert doc['samples'][0]['parsed'] == doc['samples'][1]['parsed']
    assert doc['samples'][0]['source']['sha256'] == doc['samples'][1]['source']['sha256']
    ids = [s['id'] for s in doc['samples']]
    group = {'id': 'local_task', 'name': '同源接口核对', 'kind': 'formation', 'energy_basis': BASIS, 'basis_confirmed': True,
             'element_references': {'Si': ids[0]}, 'targets': [{'sample_id': ids[1]}]}
    rows = [{'sample_id': sid, 'role': 'material' if i == 1 else 'element_reference', 'included': True,
             'confirmed': True, 'accepted_warnings': True} for i, sid in enumerate(ids)]
    doc = configure(client, doc, [group], rows)
    response = calculate(client, doc)
    assert response.status_code == 200, response.text
    doc = response.json()['collection']
    assert doc['result']['groups'][0]['rows'][0]['delta_ev'] == 0
    # Composition-only repair preserves parsed unknown and is explicit human provenance.
    missing_counts = (header().replace(' ions per type = 2\n', '') + block(e0='-10') + FOOTER).encode()
    response = client.post(f"{BASE}/collections/{doc['id']}/outcar", params={'expected_revision': doc['revision']}, content=missing_counts)
    assert response.status_code == 201, response.text
    doc = response.json()['collection']
    missing = doc['samples'][-1]
    assert missing['parsed']['composition'] == {}
    assert missing['parsed']['metadata']['potcar_datasets'] == {'Si': 'PAW_PBE Si 05Jan2001'}
    assert missing['parsed']['metadata']['support']['reasons'] == ['COMPOSITION_MISSING']
    group['targets'] = [{'sample_id': missing['id']}]
    rows = [rows[0], {'sample_id': missing['id'], 'role': 'material', 'included': True, 'confirmed': True,
                     'accepted_warnings': True, 'override': {'composition': {'Si': 2}, 'unit': 'eV', 'note': 'NIONS与原文件元素记录人工核对'}}]
    doc = configure(client, doc, [group], rows)
    response = calculate(client, doc)
    assert response.status_code == 200, response.text
    computed = response.json()['collection']['result']['groups'][0]['rows'][0]
    assert computed['effective']['composition_origin'] == 'manual_override'


def test_near_quota_staging_is_counted_once_and_reservations_fit_actual_budget(energy_api, monkeypatch):
    from backend.toolbox.postprocessing.energy import store as store_module
    from backend.toolbox.contracts import ToolboxError
    from backend.tests.test_energy_parser import header, block, FOOTER
    client, app = energy_api
    doc = create(client)
    svc = app.state.energy
    base_bytes = svc.committed_bytes()
    monkeypatch.setattr(store_module, 'MAX_STORE', base_bytes + 15000)
    path, budget = svc.reserve(doc['id'], doc['revision'])
    assert budget == 15000
    with pytest.raises(ToolboxError) as caught:
        svc.reserve(doc['id'], doc['revision'])
    assert caught.value.code == 'ENERGY_QUOTA'
    payload = (header() + block() + FOOTER).encode()
    path.write_bytes(payload + b' ' * (10000 - len(payload)))
    assert svc.committed_bytes() == base_bytes
    imported = svc.import_path(doc['id'], doc['revision'], path, 'OUTCAR')
    assert len(imported['samples']) == 1
    assert svc.committed_bytes() <= store_module.MAX_STORE
    assert not svc.uploads
    # Atomic JSON growth (manual/config/preview) also respects the independent total.
    monkeypatch.setattr(store_module, 'MAX_STORE', svc.committed_bytes())
    changed = copy.deepcopy(imported)
    changed['title'] = '新标题' * 100
    with pytest.raises(ToolboxError) as caught:
        svc.save(changed)
    assert caught.value.code == 'ENERGY_QUOTA'
    assert svc.read(doc['id']) == imported


@pytest.mark.parametrize('failure', [None, 'checksum', 'index'])
def test_energy_wire_separate_allowlist_and_verified_full_frames(monkeypatch, tmp_path, failure):
    from backend.toolbox.ssh import remote_files, file_helper
    endpoint = {'host_key': {'verification': 'known_hosts'}, 'endpoint_digest': 'same'}
    class Manager:
        def file_endpoint(self, target):
            return copy.deepcopy(endpoint)
    body = b'complete bounded OUTCAR fixture'
    requests = []
    frames = [{'state': 'ready', 'size': len(body)},
              {'state': 'chunk', 'index': 1 if failure == 'index' else 0, 'data': base64.b64encode(body).decode()},
              {'state': 'complete', 'length': len(body), 'sha256': 'bad' if failure == 'checksum' else hashlib.sha256(body).hexdigest()}]
    class Wire:
        closed = False
        def __init__(self, manager, **kwargs):
            assert kwargs['expected_endpoint'] == endpoint
        def call(self, request, **kwargs):
            requests.append(request)
            assert kwargs['abort_op'] == 'energy_abort'
            return frames.pop(0)
        def check_endpoint(self):
            pass
        def close(self):
            Wire.closed = True
    monkeypatch.setattr(remote_files, '_Wire', Wire)
    files = remote_files.RemoteFiles(Manager(), scheduler_target={})
    receipt = {'endpoint': endpoint, 'root': {}, 'files': [{'name': 'OUTCAR', 'available': True,
               'size_bytes': len(body), 'metadata': {'size': len(body)}}]}
    with (tmp_path / 'stage').open('wb') as target:
        if failure:
            with pytest.raises(RemoteFileError):
                files.stream_energy(receipt, 'OUTCAR', target, should_cancel=lambda: False,
                                    progress=lambda value: None, deadline=time.monotonic() + 10)
        else:
            assert files.stream_energy(receipt, 'OUTCAR', target, should_cancel=lambda: False,
                                       progress=lambda value: None, deadline=time.monotonic() + 10) == (len(body), hashlib.sha256(body).hexdigest())
    assert requests[0]['op'] == 'energy_begin'
    assert requests[1]['op'] == 'energy_next'
    assert Wire.closed
    for name in ['DOSCAR', 'POTCAR', '../OUTCAR']:
        with pytest.raises(RemoteFileError):
            files.stream_energy(receipt, name, io.BytesIO(), should_cancel=lambda: False,
                                progress=lambda value: None, deadline=time.monotonic() + 10)
    assert file_helper.ENERGY_NAMES == {'OUTCAR'}
    assert 'OUTCAR' not in file_helper.PP_NAMES
