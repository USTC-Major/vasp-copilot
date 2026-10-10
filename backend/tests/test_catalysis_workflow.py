"""CAT-05/06 real local pipeline round trips; no VASP, remote API or real POTCAR."""
import copy
import hashlib
import io
import json
import zipfile
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pymatgen.io.vasp.inputs import Poscar

from backend.input_validation import validate_poscar, InputValidationError
from backend.app.core.errors import ConflictError
from backend.app.generators.kpoints import KpointsGenerator
from backend.app.generators.poscar import PoscarGenerator
from backend.app.recipes.errors import BeAError, KpointsGenerationFailed
from backend.app.schemas.generation import WorkflowGenerateRequest, ParameterPatch, PotcarConfig
from backend.app.schemas.recipe import TaskType
from backend.app.schemas.structure import to_structure_context
from backend.app.services.file_store import FileStore
from backend.app.services.surface_inputs import validate_surface_context, surface_grid
from backend.app.services.workflow_service import WorkflowService
from backend.toolbox.catalysis.schemas import BindWorkflow, BuildSurfaces, SetConstraints, PatchDraft
from backend.toolbox.catalysis.service import CatalysisService
from backend.toolbox.contracts import ToolboxError
from backend.tests.test_catalysis_adsorption import pt_surface, prepare, generate
from backend.tests.test_catalysis_surface import create, slab_text


def bind(svc, draft, store, candidate=False):
    selected = {'candidate_id': draft['adsorption']['candidates'][0]['candidate_id']} if candidate else {'surface_id': draft['active_surface_id']}
    return svc.bind_workflow(draft['draft_id'], BindWorkflow(revision=draft['revision'], **selected), file_store=store)


def workflow(summary, **kwargs):
    return WorkflowGenerateRequest(workflow_id='wf_cat_local', structure=to_structure_context(summary),
        requested_tasks=kwargs.pop('requested_tasks', [TaskType.RELAX, TaskType.STATIC]), **kwargs)


def test_pt111_co_identity_flags_persistence_real_generation_and_manifest(tmp_path):
    root = tmp_path / '中文 状态 (CAT)'
    svc, store = CatalysisService(root), FileStore(root / 'files')
    draft = pt_surface(svc)
    draft = svc.constraints(draft['draft_id'], SetConstraints(revision=draft['revision'], bottom_fixed_layers=2))
    clean_summary = bind(svc, draft, store)
    draft = generate(svc, prepare(svc, draft))
    summary = bind(svc, draft, store, True)
    binding = summary.catalysis_binding
    assert binding.parent_clean_snapshot_sha256 == clean_summary.catalysis_binding.snapshot_sha256
    assert binding.parent_clean_snapshot_id == clean_summary.catalysis_binding.snapshot_id
    assert len(binding.snapshot['atoms']) == len(binding.parent_clean_snapshot['atoms']) + 2
    assert summary.structure_id != clean_summary.structure_id
    assert svc.get(draft['draft_id']) == draft  # handoff never mutates CAT draft
    restored = FileStore(root / 'files').get_structure(summary.structure_id).summary
    assert restored == summary
    service = WorkflowService(file_store=store)
    req = workflow(restored, requested_tasks=[TaskType.RELAX, TaskType.STATIC, TaskType.DOS])
    preview = service.plan(req)
    assert preview['surface_policy']['c_parallel_to_normal'] is True
    assert preview['catalysis_binding']['revision'] == draft['revision']
    assert 'CAT_SURFACE_POLICY' in {c['key'] for c in preview['confirmations']}
    assert preview['steps'][0]['parameters']['ISIF'] == 2
    assert preview['steps'][1]['parameters']['ISIF'] == 2  # existing static recipe unchanged
    result = service.generate(service.replay_request(req.workflow_id))
    archive = zipfile.ZipFile(io.BytesIO(service.get_artifact(req.workflow_id).zip_bytes))
    metadata = json.loads(archive.read('catalysis_metadata.json'))
    for folder in ('01_relax', '02_static', '03_dos'):
        raw = archive.read(folder + '/POSCAR')
        assert hashlib.sha256(raw).hexdigest() == metadata['final_poscar_sha256'] == binding.poscar_sha256
        parsed = validate_poscar(raw, include_coordinates=True)
        for row, actual in zip(binding.poscar_row_mapping, parsed.selective_flags):
            assert list(actual) == binding.snapshot['atoms'][row.snapshot_index]['selective_dynamics']
        mesh = archive.read(folder + '/KPOINTS').decode().splitlines()
        assert mesh[2] == 'Gamma' and mesh[3].split()[2] == '1'
    plan = json.loads(archive.read('workflow_plan.json'))
    manifest = json.loads(archive.read('workflow_manifest.json'))
    assert plan['structure']['catalysis_binding'] == manifest['catalysis_binding'] == metadata['binding']
    assert result['manifest']['catalysis_binding'] == result['catalysis_binding']
    assert result['surface_policy'] == service.get_workflow(req.workflow_id)['surface_policy']
    assert all('POTCAR_NOT_PREPARED' in s['blocked_by'] for s in result['steps'])
    composition = preview['recipe_compositions'][0]
    source = next(p for p in composition['provenance'] if p['parameter'] == 'ISIF')
    assert source['source_type'] == 'surface_policy' and source['overrode']['value'] == 3
    assert source['overrode']['provenance']['source_id'].startswith('task.relax.standard')
    # Independent workflow remains bound to the old immutable model after edits.
    svc.constraints(draft['draft_id'], SetConstraints(revision=draft['revision'], bottom_fixed_layers=0, reset_existing=True))
    assert service.generate(service.replay_request(req.workflow_id))['catalysis_binding'] == result['catalysis_binding']
    with pytest.raises(ToolboxError) as error:
        bind(svc, svc.get(draft['draft_id']), store, True)
    assert error.value.code == 'CAT_CANDIDATE_STALE'


def test_mixed_species_cartesian_flags_normalize_and_handoff(tmp_path):
    svc, store = CatalysisService(tmp_path), FileStore(tmp_path / 'files')
    text = slab_text().replace('1 2 10', '0 0 10')
    draft = create(svc, role='slab', text=text)
    summary = bind(svc, draft, store)
    assert summary.elements == ['Pt', 'C', 'O'] and summary.counts == [2, 1, 1]
    rows = summary.catalysis_binding.poscar_row_mapping
    assert [row.snapshot_index for row in rows] == [0, 2, 1, 3]
    normalized = PoscarGenerator().generate(to_structure_context(summary), normalize=True)
    before, after = Poscar.from_str(summary.poscar_text), Poscar.from_str(normalized)
    np.testing.assert_allclose(before.structure.cart_coords, after.structure.cart_coords, atol=1e-10)
    assert np.array_equal(before.selective_dynamics, after.selective_dynamics)
    assert validate_poscar(normalized).coordinate_mode == 'direct'
    # Malformed flags fail rather than normalize away constraints.
    with pytest.raises(BeAError):
        PoscarGenerator._normalize(text.replace('T T F', 'T T X'))


def test_skew_pt123_explicit_mesh_and_tetra_band_failures(tmp_path):
    svc, store = CatalysisService(tmp_path), FileStore(tmp_path / 'files')
    draft = create(svc)
    draft = svc.build(draft['draft_id'], BuildSurfaces(revision=draft['revision'], miller_index=(1, 2, 3)))
    summary = bind(svc, draft, store)
    assert summary.surface_policy.kpoint_mode == 'explicit_gamma_2d'
    service = WorkflowService()
    req = workflow(summary)
    preview = service.plan(req)
    service.generate(req)
    archive = zipfile.ZipFile(io.BytesIO(service.get_artifact(req.workflow_id).zip_bytes))
    cell = np.array(summary.lattice.matrix)
    reciprocal = np.linalg.inv(cell).T
    normal = np.array(summary.surface_policy.normal)
    for folder in ('01_relax', '02_static'):
        raw = archive.read(folder + '/KPOINTS').decode().splitlines()
        assert raw[2] == 'Reciprocal'
        points = np.array([[float(v) for v in line.split()] for line in raw[3:]])
        assert int(raw[1]) == len(points) and (points[:, 3] == 1).all()
        assert ((points[:, :3] == [0, 0, 0]).all(axis=1)).sum() == 1
        cartesian = points[:, :3] @ reciprocal
        np.testing.assert_allclose(cartesian @ normal, 0, atol=1e-12)
        np.testing.assert_allclose(cartesian @ cell[0], points[:, 0], atol=1e-12)
        np.testing.assert_allclose(cartesian @ cell[1], points[:, 1], atol=1e-12)
        assert np.abs(points[:, 2]).max() > .01  # not mechanical NxNy1
    assert np.array_equal(Poscar.from_str(archive.read('01_relax/POSCAR').decode()).structure.lattice.matrix, cell)
    for tasks, code in [([TaskType.DOS], 'CAT_SURFACE_TETRAHEDRON_UNSUPPORTED'),
                        ([TaskType.BAND], 'CAT_SURFACE_BAND_UNSUPPORTED')]:
        with pytest.raises(BeAError) as error:
            service.plan(workflow(summary, requested_tasks=tasks, enable_band_workflow=True))
        assert error.value.code == code
    dos_patch = ParameterPatch(patch_id='manual-dos-smearing', step_id='03_dos', parameter='ISMEAR',
                              operation='replace', value=0, confirmed_by_user=True,
                              reason='Synthetic verification of explicit user smearing, not a recommendation')
    assert service.plan(workflow(summary, requested_tasks=[TaskType.DOS], patches=[dos_patch]))['steps'][-1]['parameters']['ISMEAR'] == 0
    # Grid dimensions depend on a/b and atoms, not vacuum-only c length.
    stretched = cell.copy(); stretched[2] *= 2
    assert surface_grid(cell, normal, 1000, summary.atom_count) == surface_grid(stretched, normal, 1000, summary.atom_count)


@pytest.mark.parametrize('grid', [[0, 2, 1], [2, 2, 2], [True, 2, 1], [2.5, 2, 1], [257, 256, 1]])
def test_explicit_mesh_limits(grid):
    with pytest.raises(KpointsGenerationFailed):
        KpointsGenerator().surface_explicit([[2, 0, 0], [0, 2, 0], [1, 2, 10]], [0, 0, 1], grid)


def test_binding_revision_mapping_hash_and_explicit_cell_patch_conflict(tmp_path):
    svc, store = CatalysisService(tmp_path), FileStore(tmp_path / 'files')
    draft = pt_surface(svc)
    summary = bind(svc, draft, store)
    with pytest.raises(ToolboxError) as error:
        svc.bind_workflow(draft['draft_id'], BindWorkflow(revision=1, surface_id=draft['active_surface_id']), file_store=store)
    assert error.value.code == 'CAT_REVISION_CONFLICT'
    context = to_structure_context(summary)
    bad = context.model_copy(deep=True)
    bad.catalysis_binding.poscar_row_mapping[0].atom_id = 'wrong-identity'
    with pytest.raises(InputValidationError):
        validate_surface_context(bad)
    bad = context.model_copy(update={'poscar_text': context.poscar_text + '\n'})
    with pytest.raises(InputValidationError):
        validate_surface_context(bad)
    patch = ParameterPatch(patch_id='invalid-cell', parameter='ISIF', operation='replace', value=3, confirmed_by_user=True)
    with pytest.raises(BeAError) as error:
        WorkflowService().plan(workflow(summary, patches=[patch]))
    assert error.value.code == 'CAT_SURFACE_CELL_PATCH_CONFLICT'
    patch.value = 2
    planned = WorkflowService().plan(workflow(summary, patches=[patch]))
    assert planned['recipe_compositions'][0]['patches'][0]['value'] == 2
    patch.expected_revision = 999
    with pytest.raises(BeAError) as error:
        WorkflowService().plan(workflow(summary, patches=[patch]))
    assert error.value.code == 'COMPOSITION_REVISION_CONFLICT'


def test_normalize_refuses_constraint_loss(monkeypatch):
    from backend.app.schemas.structure import build_structure_summary
    text = slab_text()
    info = validate_poscar(text)
    context = to_structure_context(build_structure_summary(poscar_text=text, elements=list(info.elements), counts=list(info.counts)))
    actual = Poscar.get_str
    def lost_flags(self, *args, **kwargs):
        original = actual(self, *args, **kwargs)
        return original.replace('Selective dynamics\n', '').replace(' F F F', '').replace(' T T F', '').replace(' T F T', '').replace(' T T T', '')
    monkeypatch.setattr(Poscar, 'get_str', lost_flags)
    with pytest.raises(BeAError) as error:
        PoscarGenerator().generate(context, normalize=True)
    assert error.value.code == 'POSCAR_NORMALIZATION_CHANGED'


def test_real_app_handoff_structure_plan_replay_export_and_direct_metadata_strip(tmp_path, monkeypatch):
    from app.api.v1 import deps, workflows
    from app.main import app
    from backend.toolbox.catalysis.api import router as cat_router
    from app.api.v1 import structure as structure_api
    store = FileStore(tmp_path / 'files')
    monkeypatch.setattr(deps, 'file_store', store)
    monkeypatch.setattr(workflows, 'file_store', store)
    monkeypatch.setattr(structure_api, 'file_store', store)
    monkeypatch.setattr(workflows, 'workflow_service', WorkflowService(file_store=store))
    application = FastAPI()
    application.exception_handlers = app.exception_handlers.copy()
    application.include_router(cat_router, prefix='/api/v1/toolbox')
    application.include_router(workflows.router, prefix='/api/v1')
    application.include_router(structure_api.router, prefix='/api/v1')
    application.state.toolbox = SimpleNamespace(root=tmp_path / 'toolbox', potcar=None)
    base = '/api/v1/toolbox/catalysis/drafts'
    with TestClient(application) as client:
        draft = client.post(base, json={'source': {'kind': 'example', 'role': 'bulk'}}).json()['draft']
        draft = client.post(base + '/' + draft['draft_id'] + '/surfaces', json={'revision': draft['revision']}).json()['draft']
        response = client.post(base + '/' + draft['draft_id'] + '/workflow-binding',
                               json={'revision': draft['revision'], 'surface_id': draft['active_surface_id']})
        assert response.status_code == 200, response.text
        bound = response.json()
        assert bound['summary']['structure_id'] == bound['structure_id']
        assert bound['summary']['source_format'] == 'poscar' and bound['summary']['lattice']['matrix']
        assert client.get('/api/v1/structure/' + bound['structure_id'] + '/geometry').status_code == 200
        response = client.post('/api/v1/workflows/plan', json={'structure_id': bound['structure_id'], 'goals': ['relax', 'static']})
        assert response.status_code == 200, response.text
        planned = response.json()['data']
        response = client.post('/api/v1/workflows/generate', json={'workflow_id': planned['workflow_id']})
        assert response.status_code == 200, response.text
        download = client.get('/api/v1/workflows/' + planned['workflow_id'] + '/download')
        assert download.status_code == 200
        archive = zipfile.ZipFile(io.BytesIO(download.content))
        assert json.loads(archive.read('workflow_manifest.json'))['catalysis_binding'] == bound['binding']
        direct = to_structure_context(store.get_structure(bound['structure_id']).summary).model_dump(mode='json')
        response = client.post('/api/v1/workflows/plan', json={'workflow': {'structure': direct, 'requested_tasks': ['relax']}})
        assert response.status_code == 200, response.text
        assert 'catalysis_binding' not in response.json()['data']
        assert response.json()['data']['steps'][0]['parameters']['ISIF'] == 3
        for body in ({'revision': draft['revision']}, {'revision': draft['revision'], 'surface_id': draft['active_surface_id'], 'candidate_id': 'bad'}):
            assert client.post(base + '/' + draft['draft_id'] + '/workflow-binding', json=body).status_code == 422


def test_new_binding_invalidates_existing_synthetic_potcar_confirmation(tmp_path, setup):
    from backend.tests.test_workflow_potcar import bound
    from backend.tests.test_potcar_library import put, synthetic, scan
    potcar, library_id, library_root = setup
    for element in ('Pt', 'C', 'O'):
        put(library_root, element + '/POTCAR', synthetic(element))
    scan(potcar, library_id)
    svc, store = CatalysisService(tmp_path / 'cat'), FileStore(tmp_path / 'files')
    draft = generate(svc, prepare(svc, pt_surface(svc)))
    first = bind(svc, draft, store, True)
    service = WorkflowService()
    req = workflow(first, potcar=PotcarConfig(mode='include'))
    _, artifact, req = bound(service, potcar, library_id, req)
    assert service.generate(req, potcar_service=potcar)['potcar']['status'] == 'generated'
    second = bind(svc, draft, store, True)
    assert first.poscar_text == second.poscar_text
    assert first.catalysis_binding.binding_id != second.catalysis_binding.binding_id
    changed = workflow(second, potcar=PotcarConfig(mode='include', artifact_id=artifact['artifact_id']))
    with pytest.raises(ConflictError) as error:
        service.generate(changed, potcar_service=potcar)
    assert error.value.code == 'CAT_WORKFLOW_BINDING_MISMATCH'
    omitted = changed.model_copy(update={'potcar': PotcarConfig(mode='omit')})
    with pytest.raises(ConflictError) as error:
        service.generate(omitted)
    assert error.value.code == 'CAT_WORKFLOW_BINDING_MISMATCH'
    service.plan(changed)
    with pytest.raises(ConflictError) as error:
        service.generate(changed, potcar_service=potcar)
    assert error.value.code == 'POTCAR_BINDING_MISMATCH'


from backend.tests.test_potcar_assembly import setup  # synthetic-only fixture
