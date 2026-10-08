"""PC-1 uses synthetic metadata fixtures only, never a user's potential library."""
from __future__ import annotations

import copy
import gzip
import hashlib
import json
import os
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from backend.toolbox.contracts import ToolboxError
from backend.toolbox.potcar import PotcarLibraryService
from backend.toolbox.potcar.filesystem import checked_path, enumerate_candidates
from backend.toolbox.potcar.limits import Limits
from backend.toolbox.potcar.metadata import decode
from backend.toolbox.storage import atomic_json


def synthetic(variant='Si', *, element=None, family='PAW_PBE', lexch='PE', extra=''):
    """Deliberately not a runnable scientific potential."""
    element = element or variant.split('_')[0]
    return (f'TITEL = {family} {variant} 05Jan2001\nVRHFIN = {element}: s2p2\n'
            f'ZVAL = 4.0; ENMAX = 250.0; LEXCH = {lexch}\n{extra}\nEnd of Dataset\n').encode()


def unix_compress_literals(raw):
    # Below 256 literals the stream never requires a width transition. Other
    # dictionary/clear cases are covered by the vendored decoder test module.
    assert len(raw) < 256
    packed = bytearray(b'\x1f\x9d\x90')
    pending = bits = 0
    for byte in raw:
        pending |= byte << bits
        bits += 9
        while bits >= 8:
            packed.append(pending & 255)
            pending >>= 8
            bits -= 8
    if bits:
        packed.append(pending & 255)
    return bytes(packed)


def put(root, name='Si/POTCAR', raw=None):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(synthetic() if raw is None else raw)
    return path


def register(svc, root, name='合成来源', **overrides):
    payload = {'display_name': name, 'root_path': str(root), 'source_ack': {'confirmed': True},
               'expected_registry_revision': svc.list_libraries()['revision'], **overrides}
    return svc.register(payload)['library']


def wait_scan(svc, scan_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        value = svc.scan(scan_id)['scan']
        if value['status'] not in {'queued', 'running'}:
            # Wait for worker bookkeeping too; a cancelled worker still owns its
            # slot until it acknowledges cancellation and releases its bytes.
            thread = svc._threads.get(scan_id)
            if thread:
                thread.join(2)
            return value
        time.sleep(.005)
    raise AssertionError('synthetic scan failed to terminate')


def scan(svc, lid):
    library = svc.detail(lid)['library']
    value = svc.start_scan(lid, {'expected_revision': library['revision']})
    return wait_scan(svc, value['scan']['scan_id'])


@pytest.fixture
def library_service(tmp_path):
    svc = PotcarLibraryService(tmp_path / 'app')
    try:
        yield svc
    finally:
        svc.close()


def assert_error(code, action):
    with pytest.raises(ToolboxError) as error:
        action()
    assert error.value.code == 'POTCAR_' + code
    return error.value


def test_unicode_formats_metadata_read_only_persistence(tmp_path, library_service):
    svc = library_service
    root = tmp_path / '中文赝势库 空格 (测试)&[01] 😺'
    raw = synthetic(extra='SYNTHETIC_BODY_MUST_NEVER_ESCAPE')
    paths = [put(root, raw=raw), put(root, 'Si/POTCAR.gz', gzip.compress(raw)),
             put(root, 'Si/POTCAR.Z', unix_compress_literals(raw))]
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    library = register(svc, root, version_note='用户备注，不是发布版本')
    assert scan(svc, library['library_id'])['status'] == 'succeeded'
    detail = svc.detail(library['library_id'])['library']
    assert detail['summary'] == {'total': 3, 'ready': 3, 'unsupported': 0, 'invalid': 0, 'ambiguous': 0}
    rows = svc.datasets(library['library_id'])['datasets']
    assert {row['compression'] for row in rows} == {'raw', 'gzip', 'Z'}
    assert len({row['decoded_sha256'] for row in rows}) == 1
    assert sum(row['duplicate_of'] is not None for row in rows) == 2
    for row in rows:
        assert (row['element'], row['variant'], row['family'], row['lexch'], row['zval'], row['enmax_ev']) == ('Si', 'Si', 'PAW_PBE', 'PE', 4.0, 250.0)
        assert row['dataset_date'] == '05Jan2001'
    assert before == {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    for state in svc.root.rglob('*.json'):
        assert 'SYNTHETIC_BODY_MUST_NEVER_ESCAPE' not in state.read_text(encoding='utf-8')
    svc.close()
    restored = PotcarLibraryService(tmp_path / 'app')
    try:
        assert restored.detail(library['library_id'])['library']['source_ack'] == library['source_ack']
        assert restored.datasets(library['library_id']) == svc.datasets(library['library_id'])
    finally:
        restored.close()


def test_source_ack_exact_and_strict_patch(tmp_path, library_service):
    root = tmp_path / 'source'
    put(root)
    svc = library_service
    for ack in (None, False, {'confirmed': False}, {'confirmed': 'true'}, {'confirmed': True, 'junk': True}):
        assert_error('SOURCE_ACK_REQUIRED', lambda: register(svc, root, source_ack=ack))
    library = register(svc, root)
    assert_error('INVALID_REQUEST', lambda: svc.patch(library['library_id'], {'expected_revision': 1, 'root_path': str(root)}))
    updated = svc.patch(library['library_id'], {'expected_revision': 1, 'display_name': '修改', 'version_note': None})['library']
    assert updated['source_ack'] == library['source_ack']
    assert updated['revision'] == 2
    assert_error('LIBRARY_EXISTS', lambda: register(svc, root))
    assert_error('INVALID_REQUEST', lambda: svc.patch(library['library_id'], {'expected_revision': True, 'display_name': 'x'}))


def test_parent_discovery_requires_concrete_collection(tmp_path, library_service):
    svc = library_service
    parent = tmp_path / 'parent'
    for name in ('paw_pbe', 'paw_gga', 'paw', 'pot', 'pot_GGA'):
        put(parent / name)
    discovered = svc.discover({'root_path': str(parent)})
    assert discovered['requires_selection'] is True
    assert len(discovered['collections']) == 5
    assert_error('COLLECTION_SELECTION_REQUIRED', lambda: register(svc, parent))
    root = parent / 'paw_pbe'
    put(root, 'Fe/FePOTCAR', synthetic('Fe'))
    put(root, 'In/POTCAR/POTCAR', synthetic('In'))
    put(root, 'H1.25/POTCAR', synthetic('H1.25', element='H'))
    put(root, 'H.5/POTCAR', synthetic('H.5', element='H'))
    put(root, 'H.75/POTCAR', synthetic('H.75', element='H'))
    put(root, 'Fe_pv_GW/POTCAR', synthetic('Fe_pv_GW', element='Fe'))
    found = svc.discover({'root_path': str(root)})
    assert found['requires_selection'] is False
    assert found['collections'][0]['candidate_count'] == 7
    register(svc, root)


def test_mixed_parent_does_not_offer_unusable_root(tmp_path, library_service):
    root = tmp_path / 'mixed'
    put(root)
    put(root / 'other-collection')
    discovered = library_service.discover({'root_path': str(root)})
    assert discovered['requires_selection']
    assert all(item['root_path'] != str(root) for item in discovered['collections'])
    assert_error('COLLECTION_SELECTION_REQUIRED', lambda: register(library_service, root))


def test_anomalies_special_variants_conflict_unknowns(tmp_path, library_service):
    root = tmp_path / 'fixtures'
    put(root, 'H1.25/POTCAR', synthetic('H1.25', element='H'))
    put(root, 'H1.5/POTCAR', synthetic('H1.5', element='H'))
    put(root, 'H.5/POTCAR', synthetic('H.5', element='H'))
    put(root, 'H.75/POTCAR', synthetic('H.75', element='H'))
    put(root, 'Xe/POTCAR', synthetic('Xe', element='X'))
    put(root, 'Fe/POTCAR', synthetic('Fe', family='PAW_RPBE', lexch='RP'))
    put(root, 'Si/POTCAR', b'TITEL = PAW_PBE Si\nVRHFIN = Si: s2p2\nEnd of Dataset\n')
    put(root, 'Ge/POTCAR', synthetic('Ge', extra='TITEL = PAW_PBE Ge 05Jan2001'))
    put(root, 'C/POTCAR', synthetic('C', lexch='CA'))
    put(root, 'Al/POTCAR', synthetic('Al').replace(b'ZVAL = 4.0', b'ZVAL = NaN'))
    lid = register(library_service, root)['library_id']
    assert scan(library_service, lid)['status'] == 'succeeded'
    rows = {row['variant']: row for row in library_service.datasets(lid)['datasets']}
    assert rows['H1.25']['status'] == rows['H1.5']['status'] == 'unsupported'
    assert rows['H.5']['status'] == rows['H.75']['status'] == 'unsupported'
    assert rows['Xe']['status'] == 'invalid'
    assert 'POTCAR_SPECIES_CONFLICT' in {item['code'] for item in rows['Xe']['issues']}
    assert rows['Fe']['status'] == 'unsupported'
    assert rows['Si']['status'] == 'ready'
    assert rows['Si']['zval'] is rows['Si']['enmax_ev'] is rows['Si']['lexch'] is rows['Si']['dataset_date'] is None
    assert rows['Ge']['status'] == rows['C']['status'] == rows['Al']['status'] == 'invalid'


def test_distinct_content_same_variant_ambiguous(tmp_path, library_service):
    root = tmp_path / 'source'
    put(root)
    put(root, 'Si/POTCAR-copy', synthetic(extra='alternate synthetic body'))
    lid = register(library_service, root)['library_id']
    scan(library_service, lid)
    rows = library_service.datasets(lid)['datasets']
    assert len(rows) == 2
    assert all(row['status'] == 'ambiguous' for row in rows)
    assert all(row['duplicate_of'] is None for row in rows)


@pytest.mark.parametrize('zval, enmax', [
    ('4.000 mass and valenz', '245.345'),
    ('4.000 mass and valence', '245.345 eV'),
    ('4.000D+00  mass  and  valenz', '2.45345E+02 eV'),
])
def test_standard_vasp_numeric_header_labels(tmp_path, library_service, zval, enmax):
    root = tmp_path / 'source'
    raw = (f'TITEL = PAW_PBE Si 05Jan2001\nVRHFIN = Si: s2p2\n'
           f'LEXCH = PE\nPOMASS = 28.085; ZVAL = {zval}\n'
           f'ENMAX = {enmax}; ENMIN = 183.000 eV\nEnd of Dataset\n').encode()
    put(root, raw=raw)
    lid = register(library_service, root)['library_id']
    assert scan(library_service, lid)['status'] == 'succeeded'
    row = library_service.datasets(lid)['datasets'][0]
    assert row['status'] == 'ready'
    assert row['zval'] == 4.0
    assert row['enmax_ev'] == 245.345


@pytest.mark.parametrize('invalid', ['4.000 garbage', '4.000 mass and unknown', 'NaN', '4.000E999'])
def test_unrecognized_numeric_suffixes_remain_invalid(tmp_path, library_service, invalid):
    root = tmp_path / 'source'
    put(root, raw=synthetic().replace(b'ZVAL = 4.0', ('ZVAL = ' + invalid).encode()))
    lid = register(library_service, root)['library_id']
    assert scan(library_service, lid)['status'] == 'succeeded'
    row = library_service.datasets(lid)['datasets'][0]
    assert row['status'] == 'invalid'
    assert row['zval'] is None


def test_invalid_files_visible_and_multiblock_invalid(tmp_path, library_service):
    root = tmp_path / 'source'
    put(root)
    put(root, 'C/POTCAR.gz', b'\x1f\x8bcorrupt')
    put(root, 'Fe/POTCAR.Z', b'\x1f\x9d\x70broken')
    put(root, 'Ge/POTCAR.gz', synthetic('Ge'))
    put(root, 'Al/POTCAR', synthetic('Al') + synthetic('Al'))
    lid = register(library_service, root)['library_id']
    result = scan(library_service, lid)
    assert result['status'] == 'succeeded'
    assert result['failed_count'] == 4
    codes = {issue['code'] for row in library_service.datasets(lid)['datasets'] for issue in row['issues']}
    assert {'POTCAR_DECODE_INVALID', 'POTCAR_FORMAT_CONFLICT', 'POTCAR_MULTIPLE_DATASETS'} <= codes


@pytest.mark.parametrize('format', ['gzip', 'Z'])
def test_decode_output_and_input_caps(format):
    raw = synthetic()
    encoded = gzip.compress(raw) if format == 'gzip' else unix_compress_literals(raw)
    name = 'POTCAR.gz' if format == 'gzip' else 'POTCAR.Z'
    assert_error('INPUT_LIMIT', lambda: decode(encoded, name, Limits(max_input=len(encoded) - 1)))
    assert_error('OUTPUT_LIMIT', lambda: decode(encoded, name, Limits(max_output=len(raw) - 1)))
    assert_error('DECODE_TIMEOUT', lambda: decode(encoded, name, Limits(), timeout=0))


def test_concurrent_registry_and_library_conflicts(tmp_path, library_service):
    svc = library_service
    roots = [tmp_path / 'one', tmp_path / 'two']
    for root in roots:
        put(root)
    gate = threading.Barrier(2)

    def create(root):
        gate.wait()
        try:
            return register(svc, root, expected_registry_revision=0)
        except ToolboxError as exc:
            return exc.code

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(create, roots))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert 'POTCAR_REVISION_CONFLICT' in results
    library = next(result for result in results if isinstance(result, dict))
    gate = threading.Barrier(2)

    def update(name):
        gate.wait()
        try:
            return svc.patch(library['library_id'], {'display_name': name, 'expected_revision': 1})
        except ToolboxError as exc:
            return exc.code

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(update, ['a', 'b']))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert 'POTCAR_REVISION_CONFLICT' in results


def test_scan_conflict_cancel_preserves_index_and_retry(tmp_path, library_service, monkeypatch):
    svc = library_service
    root = tmp_path / 'source'
    put(root)
    lid = register(svc, root)['library_id']
    scan(svc, lid)
    before = svc.datasets(lid)
    entered, release = threading.Event(), threading.Event()
    original = svc._scan_rows

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return original(*args, **kwargs)

    monkeypatch.setattr(svc, '_scan_rows', blocked)
    started = svc.start_scan(lid, {'expected_revision': svc.detail(lid)['library']['revision']})
    assert entered.wait(2)
    rev = svc.detail(lid)['library']['revision']
    assert_error('SCAN_ACTIVE', lambda: svc.start_scan(lid, {'expected_revision': rev}))
    assert_error('SCAN_ACTIVE', lambda: svc.patch(lid, {'expected_revision': rev, 'display_name': 'x'}))
    assert svc.cancel(started['scan']['scan_id'])['scan']['status'] == 'cancelled'
    assert svc.datasets(lid) == before
    release.set()
    wait_scan(svc, started['scan']['scan_id'])
    monkeypatch.setattr(svc, '_scan_rows', original)
    assert scan(svc, lid)['status'] == 'succeeded'
    assert svc.datasets(lid)['index_revision'] == before['index_revision'] + 1


@pytest.mark.parametrize('kind', ['candidate', 'depth', 'entries', 'deadline'])
def test_incomplete_scan_preserves_completed_index(tmp_path, library_service, kind, monkeypatch):
    svc = library_service
    root = tmp_path / 'source'
    put(root)
    lid = register(svc, root)['library_id']
    scan(svc, lid)
    before = svc.datasets(lid)
    put(root, 'In/POTCAR/POTCAR', synthetic('In'))
    svc.limits = {'candidate': replace(svc.limits, max_candidates=1),
                  'depth': replace(svc.limits, max_depth=1),
                  'entries': replace(svc.limits, max_entries=1),
                  'deadline': replace(svc.limits, scan_timeout=1e-12)}[kind]
    if kind == 'deadline':
        # Inject an already expired start clock; tiny real-time deltas can be
        # rounded differently by Windows timers during a fast synthetic scan.
        from types import SimpleNamespace
        from backend.toolbox.potcar import service as module
        monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda: time.monotonic() - 1))
    result = scan(svc, lid)
    assert result['status'] == 'failed'
    assert result['error']['code'] == 'POTCAR_SCAN_LIMIT'
    assert svc.datasets(lid) == before


def test_source_change_during_scan_preserves_index(tmp_path, library_service, monkeypatch):
    svc = library_service
    root = tmp_path / 'source'
    path = put(root)
    lid = register(svc, root)['library_id']
    scan(svc, lid)
    before = svc.datasets(lid)
    from backend.toolbox.potcar import service as module
    original = module.metadata

    def change(raw, row):
        original(raw, row)
        path.write_bytes(synthetic(extra='changed'))

    monkeypatch.setattr(module, 'metadata', change)
    result = scan(svc, lid)
    assert result['error']['code'] == 'POTCAR_SOURCE_CHANGED'
    assert svc.datasets(lid) == before


def test_relink_same_source_and_replace_requires_ack(tmp_path, library_service):
    svc = library_service
    root = tmp_path / 'source'
    put(root)
    library = register(svc, root)
    lid = library['library_id']
    scan(svc, lid)
    library = svc.detail(lid)['library']
    moved = tmp_path / '移动后的中文目录'
    shutil.move(str(root), str(moved))
    assert svc.detail(lid)['library']['reachable'] is False
    relinked = svc.relink(lid, {'root_path': str(moved), 'expected_revision': library['revision']})['library']
    assert relinked['source_ack'] == library['source_ack']
    assert relinked['source_fingerprint'] == library['source_fingerprint']
    other = tmp_path / 'other'
    put(other, raw=synthetic(extra='different source'))
    assert_error('SOURCE_MISMATCH', lambda: svc.relink(lid, {'root_path': str(other), 'expected_revision': relinked['revision']}))
    assert svc.detail(lid)['library']['root_path'] == str(moved)
    assert_error('SOURCE_ACK_REQUIRED', lambda: svc.relink(lid, {'root_path': str(other), 'expected_revision': relinked['revision'], 'source_ack': {'confirmed': False}}, replace=True))
    replaced = svc.relink(lid, {'root_path': str(other), 'expected_revision': relinked['revision'], 'source_ack': {'confirmed': True}}, replace=True)['library']
    assert replaced['index_revision'] is None
    assert replaced['source_fingerprint'] is None
    assert replaced['root_path'] == str(other)
    assert moved.joinpath('Si/POTCAR').is_file()
    assert scan(svc, lid)['status'] == 'succeeded'


def test_relink_without_baseline_fails(tmp_path, library_service):
    root = tmp_path / 'source'
    put(root)
    library = register(library_service, root)
    assert_error('SOURCE_UNVERIFIED', lambda: library_service.relink(library['library_id'], {'root_path': str(root), 'expected_revision': 1}))


def test_revision_pinned_pagination_and_filter(tmp_path, library_service):
    svc = library_service
    root = tmp_path / 'source'
    put(root)
    put(root, 'Fe/POTCAR', synthetic('Fe'))
    put(root, 'C/POTCAR', synthetic('C'))
    lid = register(svc, root)['library_id']
    scan(svc, lid)
    first = svc.datasets(lid, limit=1)
    second = svc.datasets(lid, limit=1, cursor=first['next_cursor'])
    assert first['index_revision'] == second['index_revision']
    assert first['datasets'][0]['dataset_id'] != second['datasets'][0]['dataset_id']
    assert svc.datasets(lid, element='Si', status='ready')['total'] == 1
    assert_error('INDEX_REVISION_CONFLICT', lambda: svc.datasets(lid, element='Si', cursor=first['next_cursor']))
    scan(svc, lid)
    assert_error('INDEX_REVISION_CONFLICT', lambda: svc.datasets(lid, cursor=first['next_cursor']))
    assert_error('INVALID_CURSOR', lambda: svc.datasets(lid, cursor='!invalid!'))


def test_delete_registration_and_default_never_delete_source(tmp_path, library_service):
    svc = library_service
    root = tmp_path / 'source'
    path = put(root)
    before = path.read_bytes()
    lid = register(svc, root)['library_id']
    scan(svc, lid)
    svc.set_default({'library_id': lid, 'expected_registry_revision': svc.list_libraries()['revision']})
    assert svc.detail(lid)['library']['is_default']
    revision = svc.detail(lid)['library']['revision']
    assert svc.delete(lid, revision)['deleted']
    assert svc.list_libraries()['default_library_id'] is None
    assert path.read_bytes() == before
    assert not svc._index_path(lid).exists()
    assert_error('LIBRARY_NOT_FOUND', lambda: svc.detail(lid))


def test_delete_during_scan_prevents_resurrection(tmp_path, library_service, monkeypatch):
    svc = library_service
    root = tmp_path / 'source'
    path = put(root)
    lid = register(svc, root)['library_id']
    entered, release = threading.Event(), threading.Event()
    original = svc._scan_rows

    def block(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return original(*args, **kwargs)

    monkeypatch.setattr(svc, '_scan_rows', block)
    started = svc.start_scan(lid, {'expected_revision': 1})
    assert entered.wait(2)
    svc.delete(lid, svc.detail(lid)['library']['revision'])
    release.set()
    assert wait_scan(svc, started['scan']['scan_id'])['status'] == 'cancelled'
    assert svc.list_libraries()['libraries'] == []
    assert not svc._index_path(lid).exists()
    assert path.is_file()


def test_restart_marks_interrupted_and_restores_uncommitted_index(tmp_path, library_service):
    svc = library_service
    root = tmp_path / 'source'
    put(root)
    lid = register(svc, root)['library_id']
    scan(svc, lid)
    old_index = json.loads(svc._index_path(lid).read_text(encoding='utf-8'))
    sid = 'a' * 32
    data = copy.deepcopy(svc._data)
    data['scans'][sid] = {'scan_id': sid, 'library_id': lid, 'status': 'running'}
    atomic_json(svc._registry_path, data)
    atomic_json(svc.root / 'pending' / (lid + '.json'), {'scan_id': sid, 'previous': old_index})
    atomic_json(svc._index_path(lid), {**old_index, 'index_revision': 999})
    restored = PotcarLibraryService(tmp_path / 'app')
    try:
        assert restored.scan(sid)['scan']['error']['code'] == 'POTCAR_SCAN_INTERRUPTED'
        assert restored.datasets(lid)['index_revision'] == old_index['index_revision']
        assert restored._threads == {}
    finally:
        restored.close()


def test_publication_registry_failure_restores_previous_index(tmp_path, library_service, monkeypatch):
    svc = library_service
    root = tmp_path / 'source'
    put(root)
    lid = register(svc, root)['library_id']
    scan(svc, lid)
    before = svc.datasets(lid)
    original = svc._save

    def fail_success(value):
        if list(value['scans'].values())[-1]['status'] == 'succeeded' and len(value['scans']) > 1:
            raise OSError('synthetic disk failure')
        return original(value)

    monkeypatch.setattr(svc, '_save', fail_success)
    result = scan(svc, lid)
    assert result['status'] == 'failed'
    assert result['error']['code'] == 'POTCAR_SCAN_FAILED'
    assert svc.datasets(lid) == before


def test_invalid_paths_and_link_ancestors(tmp_path, library_service):
    svc = library_service
    assert_error('INVALID_PATH', lambda: svc.discover({'root_path': 'relative'}))
    assert_error('PATH_UNREACHABLE', lambda: svc.discover({'root_path': str(tmp_path / 'offline')}))
    target = tmp_path / 'target'
    put(target)
    link = tmp_path / 'link'
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        if os.name != 'nt':
            raise
        # Windows junction creation needs no developer-mode symlink privilege.
        import subprocess
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)], capture_output=True)
        if result.returncode:
            pytest.skip('host cannot create synthetic symlink or junction')
    try:
        assert_error('LINK_FORBIDDEN', lambda: checked_path(str(link / 'Si'), directory=True))
        assert_error('LINK_FORBIDDEN', lambda: svc.discover({'root_path': str(link)}))
        assert_error('LINK_FORBIDDEN', lambda: enumerate_candidates(tmp_path, Limits(), time.monotonic() + 2))
    finally:
        if link.is_symlink():
            link.unlink()
        else:
            link.rmdir()


def test_cancel_after_publication_keeps_success(tmp_path, library_service):
    root = tmp_path / 'source'
    put(root)
    lid = register(library_service, root)['library_id']
    result = scan(library_service, lid)
    before = library_service.datasets(lid)
    assert library_service.cancel(result['scan_id'])['scan']['status'] == 'succeeded'
    assert library_service.datasets(lid) == before


def test_oversized_metadata_is_blocked_and_never_persisted(tmp_path, library_service):
    root = tmp_path / 'source'
    raw = synthetic('Si_' + 'A' * 1024 * 1024, element='Si')
    put(root, raw=raw)
    lid = register(library_service, root)['library_id']
    assert scan(library_service, lid)['status'] == 'succeeded'
    row = library_service.datasets(lid)['datasets'][0]
    assert row['status'] == 'invalid'
    assert row['variant'] is row['title'] is None
    assert 'POTCAR_METADATA_LIMIT' in {issue['code'] for issue in row['issues']}
    assert library_service._index_path(lid).stat().st_size < 8192


@pytest.mark.parametrize('operation', ['cancel', 'delete'])
def test_failed_cancel_or_delete_persistence_does_not_strand_worker(tmp_path, library_service, monkeypatch, operation):
    svc = library_service
    root = tmp_path / 'source'
    put(root)
    lid = register(svc, root)['library_id']
    entered, release = threading.Event(), threading.Event()
    original_rows, original_save = svc._scan_rows, svc._save

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return original_rows(*args, **kwargs)

    monkeypatch.setattr(svc, '_scan_rows', blocked)
    started = svc.start_scan(lid, {'expected_revision': 1})
    sid = started['scan']['scan_id']
    assert entered.wait(2)
    monkeypatch.setattr(svc, '_save', lambda value: (_ for _ in ()).throw(OSError('synthetic write failure')))
    try:
        with pytest.raises(OSError):
            if operation == 'cancel':
                svc.cancel(sid)
            else:
                svc.delete(lid, svc.detail(lid)['library']['revision'])
        assert not svc._events[sid].is_set()
        assert svc.scan(sid)['scan']['status'] == 'running'
    finally:
        monkeypatch.setattr(svc, '_save', original_save)
        release.set()
    assert wait_scan(svc, sid)['status'] == 'succeeded'
    assert svc.cancel(sid)['scan']['status'] == 'succeeded'


def test_publication_wins_concurrent_cancellation_atomically(tmp_path, library_service, monkeypatch):
    svc = library_service
    root = tmp_path / 'source'
    put(root)
    lid = register(svc, root)['library_id']
    entered, release, cancelling = threading.Event(), threading.Event(), threading.Event()
    original = svc._publish

    def blocked(*args):
        entered.set()
        assert release.wait(3)
        return original(*args)

    monkeypatch.setattr(svc, '_publish', blocked)
    started = svc.start_scan(lid, {'expected_revision': 1})
    sid = started['scan']['scan_id']
    assert entered.wait(2)

    def cancel():
        cancelling.set()
        return svc.cancel(sid)

    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(cancel)
        assert cancelling.wait(1)
        release.set()
        assert future.result(3)['scan']['status'] == 'succeeded'
    assert wait_scan(svc, sid)['status'] == 'succeeded'
    assert svc.datasets(lid)['index_revision'] == 1


def test_parallel_scan_resource_bound(tmp_path, library_service, monkeypatch):
    svc = library_service
    roots = [tmp_path / ('source' + str(i)) for i in range(3)]
    for root in roots:
        put(root)
    libraries = [register(svc, root) for root in roots]
    release = threading.Event()
    original = svc._scan_rows

    def blocked(*args, **kwargs):
        assert release.wait(3)
        return original(*args, **kwargs)

    monkeypatch.setattr(svc, '_scan_rows', blocked)
    ids = []
    try:
        for library in libraries[:2]:
            ids.append(svc.start_scan(library['library_id'], {'expected_revision': 1})['scan']['scan_id'])
        assert_error('SCAN_BUSY', lambda: svc.start_scan(libraries[2]['library_id'], {'expected_revision': 1}))
    finally:
        release.set()
        for sid in ids:
            wait_scan(svc, sid)
