"""PC-2 risk checks use tiny synthetic data, never licensed potential bodies."""
import copy
import gzip
import json
import threading
from dataclasses import replace

import pytest

from backend.tests.test_potcar_library import (assert_error, put, register, scan, synthetic, unix_compress_literals)
from backend.toolbox.potcar import PotcarLibraryService
from backend.toolbox.potcar.metadata import digest


def poscar(species=('Si',), counts=None, *, legacy=False, mode='Direct'):
    counts = counts or [1] * len(species)
    return '\n'.join(['Synthetic structure', '1', '1 0 0', '0 1 0', '0 0 1',
                      *([] if legacy else [' '.join(species)]), ' '.join(map(str, counts)), mode,
                      *(['0 0 0'] * sum(counts)), ''])


@pytest.fixture
def setup(tmp_path):
    root = tmp_path / '中文源库 (合成)&[原始]'
    put(root, raw=synthetic(extra='PRIVATE_SYNTHETIC_BODY'))
    svc = PotcarLibraryService(tmp_path / 'state')
    lid = register(svc, root)['library_id']
    assert scan(svc, lid)['status'] == 'succeeded'
    try:
        yield svc, lid, root
    finally:
        svc.close()


def preview(svc, lid, text=None, **kw):
    return svc.preview({'library_id': lid, 'index_revision': svc.datasets(lid)['index_revision'],
                        'poscar_text': text or poscar(), **kw})


def confirmation(value, key='synthetic-key'):
    return {'preview_id': value['preview_id'], 'selection_digest': value['selection_digest'],
            'confirmed_order_and_variants': True, 'idempotency_key': key}


def test_raw_gz_z_order_repeats_zero_and_exact_bytes(setup):
    svc, lid, root = setup
    fe, o = synthetic('Fe').replace(b'\n', b'\r\n'), synthetic('O')
    put(root, 'Fe/POTCAR.gz', gzip.compress(fe))
    put(root, 'O/POTCAR.Z', unix_compress_literals(o))
    scan(svc, lid)
    original = {str(p): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    value = preview(svc, lid, poscar(('Fe', 'Si', 'O', 'Si'), [1, 1, 0, 1]))
    assert [(r['element'], r['atom_count']) for r in value['rows']] == [('Fe', 1), ('Si', 1), ('O', 0), ('Si', 1)]
    assert not value['blockers']
    assert all(r['reason']['code'] == 'UNIQUE_COMPATIBLE' for r in value['rows'])
    assert 'PRIVATE_SYNTHETIC_BODY' not in json.dumps(value)
    assert str(root) not in json.dumps(value)
    out = svc.generate(confirmation(value))['artifact']
    expected = fe + synthetic(extra='PRIVATE_SYNTHETIC_BODY') + o + synthetic(extra='PRIVATE_SYNTHETIC_BODY')
    assert svc.download(out['artifact_id']) == expected
    assert out['sha256'] == digest(expected)
    assert {str(p): p.read_bytes() for p in root.rglob('*') if p.is_file()} == original
    assert not any('relative_path' in r for r in out['rows'])
    assert svc.generate(confirmation(value))['artifact'] == out


def test_multiple_candidates_require_selection_including_ambiguous(setup):
    svc, lid, root = setup
    put(root, 'Si_sv/POTCAR', synthetic('Si_sv'))
    put(root, 'Si/POTCAR.gz', gzip.compress(synthetic(extra='different')))
    scan(svc, lid)
    value = preview(svc, lid)
    assert value['rows'][0]['dataset_id'] is None
    assert len(value['rows'][0]['candidates']) == 3
    assert value['blockers'][0]['code'] == 'POTCAR_SELECTION_REQUIRED'
    assert_error('SELECTION_BLOCKED', lambda: svc.generate(confirmation(value)))
    candidate = next(r for r in value['rows'][0]['candidates'] if r['status'] == 'ambiguous')
    chosen = preview(svc, lid, dataset_ids=[candidate['dataset_id']])
    assert chosen['rows'][0]['reason']['code'] == 'USER_SELECTED'
    assert svc.generate(confirmation(chosen))['artifact']['rows'][0]['dataset_id'] == candidate['dataset_id']


def test_legacy_explicit_mapping_and_mode(setup):
    svc, lid, _ = setup
    text = poscar(('Si', 'Si'), [0, 1], legacy=True)
    assert_error('LEGACY_SPECIES_REQUIRED', lambda: preview(svc, lid, text))
    for mapping in (['Si'], ['Si', 'Unknown'], 'Si Si', [None, 'Si']):
        assert_error('INVALID_REQUEST', lambda: preview(svc, lid, text, legacy_species=mapping))
    value = preview(svc, lid, text, legacy_species=['Si', 'Si'])
    assert len(value['rows']) == 2 and value['rows'][0]['atom_count'] == 0
    assert svc.generate(confirmation(value))['artifact']['size_bytes'] > 0
    assert_error('INVALID_REQUEST', lambda: preview(svc, lid, legacy_species=['Si']))
    assert_error('POSCAR_MODE_UNSUPPORTED', lambda: preview(svc, lid, poscar(mode='typo')))


def test_invalid_multi_unsupported_missing_no_bypass(setup):
    svc, lid, root = setup
    put(root, 'O/POTCAR', synthetic('O') * 2)
    put(root, 'Fe/POTCAR', synthetic('Fe', family='PAW_GGA'))
    put(root, 'H/POTCAR.gz', b'\x1f\x8bcorrupted')
    scan(svc, lid)
    for element in ('O', 'Fe', 'H', 'Xe'):
        value = preview(svc, lid, poscar((element,)))
        assert value['rows'][0]['reason']['code'] == 'NO_COMPATIBLE_DATASET'
        assert not value['rows'][0]['candidates']
        assert_error('SELECTION_BLOCKED', lambda: svc.generate(confirmation(value)))
    bad = next(row for row in svc.datasets(lid)['datasets'] if row['element'] == 'O')
    assert_error('DATASET_SELECTION_INVALID', lambda: preview(svc, lid, poscar(('O',)), dataset_ids=[bad['dataset_id']]))


def test_source_mutation_and_revision_do_not_publish(setup):
    svc, lid, root = setup
    value = preview(svc, lid)
    put(root, raw=synthetic(extra='changed'))
    assert_error('SOURCE_CHANGED', lambda: svc.generate(confirmation(value)))
    assert list(svc.assembly.root.iterdir()) == []
    scan(svc, lid)
    assert_error('INDEX_REVISION_CONFLICT', lambda: svc.generate(confirmation(value)))


def test_source_replacement_deletion_and_active_scan(setup, tmp_path):
    svc, lid, root = setup
    value = preview(svc, lid)
    new = tmp_path / 'other'
    put(new, raw=synthetic())
    rev = svc.detail(lid)['library']['revision']
    svc.relink(lid, {'root_path': str(new), 'expected_revision': rev, 'source_ack': {'confirmed': True}}, replace=True)
    assert_error('SOURCE_REPLACED', lambda: svc.generate(confirmation(value)))
    scan(svc, lid)
    value = preview(svc, lid)
    # A queued scan blocks generating from the old complete index as well.
    with svc._lock:
        svc._data['scans']['f'*32] = {'scan_id': 'f'*32, 'library_id': lid, 'status': 'running'}
        assert_error('SCAN_ACTIVE', lambda: svc.generate(confirmation(value)))
        del svc._data['scans']['f'*32]
    svc.delete(lid, svc.detail(lid)['library']['revision'])
    assert_error('LIBRARY_NOT_FOUND', lambda: svc.generate(confirmation(value)))
    assert (root / 'Si/POTCAR').exists()


def test_persistent_immutable_artifact_and_exact_key_conflicts(setup):
    svc, lid, root = setup
    value = preview(svc, lid)
    req = confirmation(value, 'valid key')
    out = svc.generate(req)['artifact']
    assert svc.generate(req)['artifact'] == out
    for invalid_key in ('control\x1fkey', 'k' * 129):
        assert_error('INVALID_REQUEST', lambda invalid_key=invalid_key: svc.generate(confirmation(value, invalid_key)))
    expected = svc.download(out['artifact_id'])
    another = preview(svc, lid)
    assert_error('IDEMPOTENCY_CONFLICT', lambda: svc.generate(confirmation(another, 'valid key')))
    svc.delete(lid, svc.detail(lid)['library']['revision'])
    put(root, raw=synthetic(extra='later source'))
    svc.close()
    restarted = PotcarLibraryService(svc.root.parent)
    try:
        assert restarted.artifact(out['artifact_id'])['artifact'] == out
        assert restarted.download(out['artifact_id']) == expected
        assert restarted.generate(req)['artifact'] == out
        assert_error('PREVIEW_NOT_FOUND', lambda: restarted.generate(confirmation(another, 'new-key')))
    finally:
        restarted.close()


def test_confirmation_digest_expiry_and_limits(setup, monkeypatch):
    svc, lid, root = setup
    value = preview(svc, lid)
    req = confirmation(value)
    assert_error('CONFIRMATION_REQUIRED', lambda: svc.generate({**req, 'confirmed_order_and_variants': 1}))
    assert_error('SELECTION_DIGEST_MISMATCH', lambda: svc.generate({**req, 'selection_digest': 'f'*64}))
    svc.assembly.previews[value['preview_id']]['expiry'] = 0
    assert_error('PREVIEW_EXPIRED', lambda: svc.generate(req))
    value = preview(svc, lid)
    svc.assembly.limits = replace(svc.limits, max_assembly=10)
    assert_error('ASSEMBLY_LIMIT', lambda: svc.generate(confirmation(value)))
    assert list(svc.assembly.root.iterdir()) == []
    svc.assembly.limits = svc.limits
    out = svc.generate(confirmation(value))['artifact']
    monkeypatch.setattr('backend.toolbox.potcar.assembly.time.time', lambda: 10**12)
    assert_error('ARTIFACT_EXPIRED', lambda: svc.download(out['artifact_id']))


def test_fail_before_atomic_publication_removes_only_own_staging(setup, monkeypatch):
    svc, lid, root = setup
    unrelated = svc.assembly.root / 'unrelated'
    unrelated.mkdir()
    (unrelated / 'keep').write_text('preserve')
    value = preview(svc, lid)
    def failure(*args):
        raise OSError('synthetic publication failure')
    monkeypatch.setattr('backend.toolbox.potcar.assembly.os.rename', failure)
    with pytest.raises(OSError):
        svc.generate(confirmation(value))
    assert list(svc.assembly.root.iterdir()) == [unrelated]
    assert (root / 'Si/POTCAR').read_bytes() == synthetic(extra='PRIVATE_SYNTHETIC_BODY')


def test_concurrent_idempotency_and_immutable_byte_integrity(setup):
    from concurrent.futures import ThreadPoolExecutor
    svc, lid, _ = setup
    req = confirmation(preview(svc, lid))
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: svc.generate(req), range(4)))
    assert all(result == results[0] for result in results)
    out = results[0]['artifact']
    (svc.assembly.root / out['artifact_id'] / 'POTCAR').write_bytes(synthetic(extra='tampered'))
    assert_error('ARTIFACT_INVALID', lambda: svc.download(out['artifact_id']))
    assert_error('ARTIFACT_INVALID', lambda: svc.generate(req))


@pytest.mark.parametrize('mutation', ['body', 'title', 'path', 'created_at', 'expires_at'])
def test_untrusted_artifact_manifest_cannot_return_body_or_paths(setup, mutation):
    svc, lid, _ = setup
    out = svc.generate(confirmation(preview(svc, lid)))['artifact']
    path = svc.assembly.root / out['artifact_id'] / 'manifest.json'
    stored = json.loads(path.read_text('utf-8'))
    if mutation == 'body':
        stored['artifact']['body'] = 'DO_NOT_RETURN_BODY'
    elif mutation == 'title':
        stored['artifact']['rows'][0]['title'] = 'DO_NOT_RETURN_BODY'
    elif mutation == 'path':
        stored['artifact']['rows'][0]['absolute_path'] = 'DO_NOT_RETURN_BODY'
    else:
        stored['artifact'][mutation] = {'body': 'DO_NOT_RETURN_BODY'}
    path.write_text(json.dumps(stored), encoding='utf-8')
    assert_error('ARTIFACT_INVALID', lambda: svc.artifact(out['artifact_id']))


def test_untrusted_preview_metadata_not_returned(setup):
    svc, lid, _ = setup
    path = svc._index_path(lid)
    original = json.loads(path.read_text('utf-8'))
    poisoned = copy.deepcopy(original)
    poisoned['datasets'][0]['title'] = 'PRIVATE_SYNTHETIC_BODY'
    path.write_text(json.dumps(poisoned), encoding='utf-8')
    with pytest.raises(ValueError):
        preview(svc, lid)
    poisoned = copy.deepcopy(original)
    poisoned['datasets'][0]['issues'] = [{'code': 'POTCAR_DUPLICATE_CONTENT', 'message': 'PRIVATE_SYNTHETIC_BODY'}]
    path.write_text(json.dumps(poisoned), encoding='utf-8')
    value = preview(svc, lid)
    assert 'PRIVATE_SYNTHETIC_BODY' not in json.dumps(value)


def test_preview_capacity_and_candidate_bounds(setup):
    svc, lid, _ = setup
    svc.assembly.limits = replace(svc.limits, max_previews=1)
    preview(svc, lid)
    assert_error('PREVIEW_BUSY', lambda: preview(svc, lid))
    svc.assembly.previews.clear()
    svc.assembly.limits = replace(svc.limits, max_preview_candidates=1)
    assert_error('ASSEMBLY_LIMIT', lambda: preview(svc, lid, poscar(('Si', 'Si'))))


def test_unterminated_boundary_does_not_get_silently_repaired(setup):
    svc, lid, root = setup
    # A final End marker without newline is valid individually. The next block
    # would join its marker line, so full assembly must reject, never append LF.
    put(root, raw=synthetic().rstrip(b'\n'))
    scan(svc, lid)
    value = preview(svc, lid, poscar(('Si', 'Si')))
    with pytest.raises(Exception) as error:
        svc.generate(confirmation(value))
    assert error.value.code == 'POTCAR_METADATA_UNVERIFIED'
    assert list(svc.assembly.root.iterdir()) == []


def test_byte_limit_exact_boundary_and_one_additional_block(setup):
    svc, lid, _ = setup
    assert svc.limits.max_assembly == 32 * 1024 * 1024
    size = len(synthetic(extra='PRIVATE_SYNTHETIC_BODY'))
    svc.assembly.limits = replace(svc.limits, max_assembly=size * 2)
    two = preview(svc, lid, poscar(('Si', 'Si')))
    out = svc.generate(confirmation(two))['artifact']
    assert out['size_bytes'] == size * 2
    assert len(svc.download(out['artifact_id'])) == size * 2
    three = preview(svc, lid, poscar(('Si', 'Si', 'Si')))
    assert_error('ASSEMBLY_LIMIT', lambda: svc.generate(confirmation(three, 'other-key')))


def test_generation_validates_and_uses_the_same_read_bytes(setup, monkeypatch):
    svc, lid, root = setup
    value = preview(svc, lid)
    expected = (root / 'Si/POTCAR').read_bytes()
    from backend.toolbox.potcar import assembly
    original = assembly.metadata
    calls = []
    def replace_after_validation(raw, row):
        calls.append(raw)
        original(raw, row)
        put(root, raw=synthetic(extra='REPLACED_AFTER_VALIDATION'))
    monkeypatch.setattr(assembly, 'metadata', replace_after_validation)
    out = svc.generate(confirmation(value))['artifact']
    assert calls == [expected]
    assert svc.download(out['artifact_id']) == expected
    assert (root / 'Si/POTCAR').read_bytes() != expected


def test_source_link_replacement_refused_before_read(setup, tmp_path):
    import os
    svc, lid, root = setup
    value = preview(svc, lid)
    outside = tmp_path / 'outside-source'
    outside.write_bytes(synthetic(extra='OUTSIDE_NOT_READ'))
    path = root / 'Si/POTCAR'
    original = path.read_bytes()
    path.unlink()
    try:
        os.symlink(outside, path)
    except (OSError, NotImplementedError):
        path.write_bytes(original)
        pytest.skip('OS symlink privilege unavailable')
    assert_error('LINK_FORBIDDEN', lambda: svc.generate(confirmation(value)))
    assert list(svc.assembly.root.iterdir()) == []
