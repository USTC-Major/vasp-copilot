"""CAT-01/02 bounded local contracts; synthetic geometry, no DFT or AI."""
import hashlib
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace
import zipfile

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pymatgen.io.vasp.inputs import Poscar

from backend.input_validation import validate_poscar
from backend.toolbox.api import router, error_handler
from backend.toolbox.contracts import ToolboxError
from backend.toolbox.catalysis import science
from backend.toolbox.catalysis.schemas import CreateDraft, PatchDraft, BuildSurfaces, SetConstraints
from backend.toolbox.catalysis.service import CatalysisService
from backend.app.schemas.structure import build_structure_summary, to_structure_context, validated_structure_context
from backend.app.services.file_store import FileStore


def slab_text():
    # Oblique right-handed cell, slab crossing the normal-period boundary;
    # nuclei at z=9, 0, 1 must form a 2 Å span with 8 Å periodic empty gap.
    return ('mixed boundary fixture\n1\n2 0 0\n.5 2 0\n1 2 10\nPt C Pt O\n1 1 1 1\n'
            'Selective dynamics\nCartesian\n0 0 9 F F F\n.5 .5 0 T T F\n'
            '1 1 1 T T T\n1.5 1.5 0 T F T\n')


def create(svc, *, role='bulk', text=None):
    source = {'kind': 'poscar' if text else 'example', 'role': role}
    if text:
        source.update(content=text, name='研究 结构.POSCAR')
    return svc.create(CreateDraft(name='测试草稿', source=source))


def build(svc, draft, **params):
    return svc.build(draft['draft_id'], BuildSurfaces(revision=draft['revision'], **params))


def test_pt111_science_identity_constraints_export_restart(tmp_path):
    svc = CatalysisService(tmp_path)
    draft = create(svc)
    source = draft['input_snapshot']
    draft = build(svc, draft)
    option = draft['surfaces'][0]
    surface = option['surface']
    layers = surface['layers']
    # Independent fcc formula, using projected layer positions, not |c|.
    np.testing.assert_allclose(np.diff([l['projection_angstrom'] for l in layers]), 3.92 / np.sqrt(3), atol=1e-10)
    assert surface['normal_period_angstrom'] - surface['actual_nuclei_span_angstrom'] == pytest.approx(surface['periodic_vacuum_gap_angstrom'])
    assert surface['periodic_vacuum_gap_angstrom'] >= 15
    atoms = option['snapshot']['atoms']
    ids = [a['atom_id'] for a in atoms]
    assert len(set(ids)) == len(ids)
    assert set(ids).isdisjoint(a['atom_id'] for a in source['atoms'])
    source_by_id = {a['atom_id']: a for a in source['atoms']}
    for a in atoms:
        assert a['element'] == source_by_id[a['provenance']['source_atom_id']]['element']
        assert a['provenance']['mapping'].startswith('replicated_source_site')
    released, frozen = layers[0]['atom_ids'][0], layers[-1]['atom_ids'][0]
    before = option['snapshot']
    draft = svc.constraints(draft['draft_id'], SetConstraints(revision=draft['revision'], bottom_fixed_layers=2,
                            atom_overrides={released: 'free', frozen: 'fixed'}))
    selected = svc.selected(draft)
    current = selected['snapshot']
    assert [a['atom_id'] for a in current['atoms']] == ids
    assert [a['fractional'] for a in current['atoms']] == [a['fractional'] for a in before['atoms']]
    assert before['snapshot_id'] != current['snapshot_id']
    assert all(a['selective_dynamics'] == ([True] * 3 if a['atom_id'] == released else [False] * 3)
               for a in current['atoms'] if a['atom_id'] in layers[0]['atom_ids'])
    geometry = svc.geometry(draft['draft_id'], draft['revision'])
    assert geometry['revision'] == draft['revision']
    assert {a['atom_id'] for a in geometry['geometry']['sites']} == set(ids)
    with zipfile.ZipFile(io.BytesIO(svc.export(draft['draft_id'], draft['revision']))) as archive:
        poscar = archive.read('POSCAR')
        metadata = json.loads(archive.read('metadata.json'))
        assert metadata['input_snapshot'] == source
        manifest = json.loads(archive.read('manifest.json'))
        assert metadata['poscar_sha256'] == hashlib.sha256(poscar).hexdigest()
        assert manifest['files']['metadata.json']['sha256'] == hashlib.sha256(archive.read('metadata.json')).hexdigest()
        parsed = validate_poscar(poscar.decode(), include_coordinates=True)
        flags = {a['atom_id']: a['selective_dynamics'] for a in current['atoms']}
        for row, actual in zip(metadata['poscar_row_mapping'], parsed.selective_flags):
            assert list(actual) == flags[row['atom_id']]
    assert CatalysisService(tmp_path).get(draft['draft_id']) == draft
    assert CatalysisService(tmp_path).list()[0]['revision'] == draft['revision']


def test_imported_slab_periodic_gap_mixed_flags_release_and_row_mapping(tmp_path):
    svc = CatalysisService(tmp_path)
    draft = create(svc, role='slab', text=slab_text())
    original = draft['input_snapshot']
    surface = svc.selected(draft)['surface']
    assert surface['normal'] == [0, 0, 1]
    assert surface['normal_period_angstrom'] == pytest.approx(10)
    assert surface['actual_nuclei_span_angstrom'] == pytest.approx(2)
    assert surface['periodic_vacuum_gap_angstrom'] == pytest.approx(8)
    assert len(surface['layers']) == 3
    assert [len(l['atom_ids']) for l in surface['layers']] == [1, 2, 1]
    assert original['coordinate_mode'] == 'cartesian'
    ids = [a['atom_id'] for a in original['atoms']]
    draft = svc.constraints(draft['draft_id'], SetConstraints(revision=1, bottom_fixed_layers=0))
    assert [a['selective_dynamics'] for a in svc.selected(draft)['snapshot']['atoms']] == [a['selective_dynamics'] for a in original['atoms']]
    text, rows = science.poscar_export(svc.selected(draft)['snapshot'])
    assert [r['snapshot_index'] for r in rows] == [0, 2, 1, 3]
    assert validate_poscar(text).elements == ('Pt', 'C', 'O')
    with zipfile.ZipFile(io.BytesIO(svc.export(draft['draft_id'], draft['revision']))) as archive:
        metadata = json.loads(archive.read('metadata.json'))
        info = validate_poscar(archive.read('POSCAR').decode(), include_coordinates=True)
        original_flags = {a['atom_id']: a['selective_dynamics'] for a in original['atoms']}
        for row, flags in zip(metadata['poscar_row_mapping'], info.selective_flags):
            assert list(flags) == original_flags[row['atom_id']]
    draft = svc.constraints(draft['draft_id'], SetConstraints(revision=draft['revision'], bottom_fixed_layers=0, reset_existing=True))
    assert [a['atom_id'] for a in svc.selected(draft)['snapshot']['atoms']] == ids
    assert all(a['selective_dynamics'] == [True] * 3 for a in svc.selected(draft)['snapshot']['atoms'])
    exported, _ = science.poscar_export(svc.selected(draft)['snapshot'])
    poscar = Poscar.from_str(exported)
    assert poscar.selective_dynamics is None or np.array(poscar.selective_dynamics).all()
    # The reset policy persists through a subsequent edit. An original FFF or
    # mixed source row must not silently reappear when fixing a different atom.
    draft = svc.constraints(draft['draft_id'], SetConstraints(revision=draft['revision'], bottom_fixed_layers=0,
                            reset_existing=True, atom_overrides={ids[2]: 'fixed'}))
    assert [a['selective_dynamics'] for a in svc.selected(draft)['snapshot']['atoms']] == [[True] * 3, [True] * 3, [False] * 3, [True] * 3]
    assert svc.selected(draft)['surface']['reset_existing'] is True


def test_draft_pending_parameters_conflicts_invalidate_surfaces(tmp_path):
    svc = CatalysisService(tmp_path)
    draft = build(svc, create(svc))
    old_surface = draft['active_surface_id']
    old_revision = draft['revision']
    saved = svc.patch(draft['draft_id'], PatchDraft(revision=old_revision, parameters={'miller_index': [1, 0, 0], 'min_slab_size': 12}))
    assert saved['parameters']['min_slab_size'] == 12
    assert saved['surfaces'] == [] and saved['active_surface_id'] is None
    assert CatalysisService(tmp_path).get(draft['draft_id']) == saved
    for operation in (lambda: svc.geometry(draft['draft_id'], old_revision, old_surface),
                      lambda: svc.export(draft['draft_id'], old_revision, old_surface),
                      lambda: svc.patch(draft['draft_id'], PatchDraft(revision=old_revision, name='旧保存'))):
        with pytest.raises(ToolboxError) as caught:
            operation()
        assert caught.value.code == 'CAT_REVISION_CONFLICT'
        assert caught.value.payload()['details']['current_revision'] == saved['revision']
    with pytest.raises(ToolboxError) as caught:
        svc.export(draft['draft_id'], saved['revision'], old_surface)
    assert caught.value.code == 'CAT_SURFACE_NOT_FOUND'


def test_constraints_belong_to_each_termination_and_original_fff_replicates(tmp_path):
    svc = CatalysisService(tmp_path)
    text = science.ideal_pt()
    info = validate_poscar(text, include_coordinates=True)
    structure = science.from_snapshot(science.import_poscar(text, 512))
    structure.add_site_property('selective_dynamics', [[False] * 3] + [[True] * 3] * 3)
    text = Poscar(structure).get_str()
    draft = build(svc, create(svc, text=text))
    assert all(a['selective_dynamics'] == ([False] * 3 if a['provenance']['source_index'] == 0 else [True] * 3)
               for s in draft['surfaces'] for a in s['snapshot']['atoms'])
    assert len(draft['surfaces']) >= 2
    first, second = draft['surfaces'][:2]
    second_before = second['snapshot']
    draft = svc.constraints(draft['draft_id'], SetConstraints(revision=draft['revision'], bottom_fixed_layers=1))
    draft = svc.patch(draft['draft_id'], PatchDraft(revision=draft['revision'], active_surface_id=second['surface_id']))
    assert svc.selected(draft)['snapshot'] == second_before
    assert next(s for s in draft['surfaces'] if s['surface_id'] == first['surface_id'])['surface']['bottom_fixed_layers'] == 1


def test_head_publish_failure_keeps_old_revision_and_retry_works(tmp_path, monkeypatch):
    svc = CatalysisService(tmp_path)
    draft = create(svc)
    replace = Path.replace
    failed = False
    def fault(path, target):
        nonlocal failed
        if Path(target).name == 'head.json' and not failed:
            failed = True
            raise OSError('controlled head publication failure')
        return replace(path, target)
    monkeypatch.setattr(Path, 'replace', fault)
    with pytest.raises(ToolboxError) as caught:
        svc.patch(draft['draft_id'], PatchDraft(revision=1, name='未发布'))
    assert caught.value.code == 'CAT_DRAFT_WRITE_FAILED'
    assert svc.get(draft['draft_id']) == draft
    saved = svc.patch(draft['draft_id'], PatchDraft(revision=1, name='重试成功'))
    assert saved['revision'] == 2 and CatalysisService(tmp_path).get(draft['draft_id']) == saved


def test_budget_refuses_before_generator_and_mixed_bulk_rejected(tmp_path, monkeypatch):
    svc = CatalysisService(tmp_path)
    draft = create(svc)
    called = False
    original = science.SlabGenerator
    def unexpected(*args, **kwargs):
        nonlocal called
        called = True
        return original(*args, **kwargs)
    monkeypatch.setattr(science, 'SlabGenerator', unexpected)
    with pytest.raises(ToolboxError) as caught:
        build(svc, draft, min_slab_size=100, in_plane_supercell=[8, 8])
    assert caught.value.code == 'CAT_SURFACE_BUDGET_LIMIT' and not called
    assert svc.get(draft['draft_id']) == draft
    mixed = create(svc, text=slab_text())
    with pytest.raises(ToolboxError) as caught:
        build(svc, mixed)
    assert caught.value.code == 'CAT_MIXED_FLAGS_TRANSFORM_UNSUPPORTED' and not called


def test_termination_budget_refuses_before_get_slab(tmp_path, monkeypatch):
    svc = CatalysisService(tmp_path)
    draft = create(svc)
    monkeypatch.setattr(science.SlabGenerator, 'gen_possible_terminations', lambda self, **kwargs: [i / 17 for i in range(17)])
    monkeypatch.setattr(science.SlabGenerator, 'get_slab', lambda *args, **kwargs: pytest.fail('must refuse before slab allocation'))
    with pytest.raises(ToolboxError) as caught:
        build(svc, draft)
    assert caught.value.code == 'CAT_TERMINATION_LIMIT'


@pytest.mark.parametrize('text,code', [
    (slab_text().replace('1 2 10', '1 2 -10'), 'CAT_LEFT_HANDED_CELL_UNSUPPORTED'),
    (slab_text().replace('1.5 1.5 0', 'NaN 1.5 0'), 'INPUT_NUMBER_INVALID'),
    ('atom limit\n1\n2 0 0\n0 2 0\n0 0 10\nPt\n2049\nDirect\n' + '0 0 0\n' * 2049, 'CAT_INPUT_ATOM_LIMIT'),
], ids=['left_handed', 'nonfinite', 'atom_limit'])
def test_invalid_structures_create_no_draft(tmp_path, text, code):
    svc = CatalysisService(tmp_path)
    with pytest.raises(ToolboxError) as caught:
        create(svc, role='slab', text=text)
    # Shared validator names its own strict coordinate failures.
    assert caught.value.code == code
    assert svc.list() == []


def test_cif_ordered_and_disorder_boundaries(tmp_path):
    cif = '''data_pt
_cell_length_a 3.92
_cell_length_b 3.92
_cell_length_c 3.92
_cell_angle_alpha 90
_cell_angle_beta 90
_cell_angle_gamma 90
_symmetry_space_group_name_H-M 'P 1'
loop_
_space_group_symop_operation_xyz
'x,y,z'
loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
_atom_site_occupancy
Pt1 Pt 0 0 0 1
'''
    svc = CatalysisService(tmp_path)
    draft = svc.create(CreateDraft(source={'kind': 'cif', 'role': 'bulk', 'content': cif, 'name': 'sample.cif'}))
    assert draft['source']['standardized'] is False
    assert draft['input_snapshot']['atoms'][0]['fractional'] == [0, 0, 0]
    with pytest.raises(ToolboxError):
        svc.create(CreateDraft(source={'kind': 'cif', 'role': 'bulk', 'content': cif.replace('Pt1 Pt 0 0 0 1', 'Pt1 Pt 0 0 0 0.5')}))
    with pytest.raises(ToolboxError) as caught:
        science.cif_poscar(cif.replace("'x,y,z'", '\n'.join(["'x,y,z'"] * 513)), 512)
    assert caught.value.code == 'CAT_CIF_EXPANSION_LIMIT'


def test_toolbox_api_source_id_revision_and_request_bounds(tmp_path, monkeypatch):
    app = FastAPI()
    app.include_router(router, prefix='/api/v1')
    app.add_exception_handler(ToolboxError, error_handler)
    app.state.toolbox = SimpleNamespace(root=tmp_path)
    client = TestClient(app)
    text = slab_text()
    info = validate_poscar(text)
    summary = build_structure_summary(poscar_text=text, elements=list(info.elements), counts=list(info.counts), source_file='fixture.POSCAR')
    store = FileStore(tmp_path / 'app-data' / 'files')
    record = store.store_structure('source', summary)
    monkeypatch.setitem(sys.modules, 'app.api.v1.deps', SimpleNamespace(file_store=store))
    response = client.post('/api/v1/toolbox/catalysis/drafts', json={'source': {'kind': 'structure_id', 'role': 'slab', 'structure_id': record.structure_id}})
    assert response.status_code == 200, response.text
    draft = response.json()['draft']
    received_snapshot = draft['input_snapshot']
    assert science.sha(science.canonical({k: v for k, v in received_snapshot.items() if k != 'sha256'})) == received_snapshot['sha256']
    path = '/api/v1/toolbox/catalysis/drafts/' + draft['draft_id']
    assert client.get(path).json()['draft'] == draft
    assert client.get('/api/v1/toolbox/catalysis/drafts').json()['drafts'][0]['draft_id'] == draft['draft_id']
    assert client.get(path + '/geometry', params={'revision': 1}).json()['geometry']['coordinate_mode'] == 'cartesian'
    changed = client.post(path + '/constraints', json={'revision': 1, 'bottom_fixed_layers': 0, 'reset_existing': True})
    assert changed.status_code == 200, changed.text
    for suffix in ('/geometry', '/export'):
        old = client.get(path + suffix, params={'revision': 1})
        assert old.status_code == 409 and old.json()['error']['code'] == 'CAT_REVISION_CONFLICT'
    assert client.get(path + '/export', params={'revision': 2}).headers['content-type'] == 'application/zip'
    for values in ({'miller_index': [0, 0, 0]}, {'miller_index': [True, 1, 1]}, {'min_vacuum_size': 'NaN'}, {'in_plane_supercell': [9, 1]}, {'unknown': True}):
        invalid = client.post(path + '/surfaces', json={'revision': 2, **values})
        assert invalid.status_code == 422


def test_context_refreshes_coordinate_mode_and_selective_metadata():
    text = slab_text()
    info = validate_poscar(text)
    summary = build_structure_summary(poscar_text=text, elements=list(info.elements), counts=list(info.counts))
    context = to_structure_context(summary)
    assert context.coordinate_mode == 'cartesian' and context.selective_dynamics is True
    untrusted = context.model_copy(update={'coordinate_mode': 'direct', 'selective_dynamics': False})
    validated = validated_structure_context(untrusted)
    assert validated.coordinate_mode == 'cartesian' and validated.selective_dynamics is True
