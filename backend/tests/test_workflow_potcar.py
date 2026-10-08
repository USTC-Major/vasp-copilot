"""Final-POSCAR workflow binding and immutable byte transport; synthetic only."""
import io
import json
from types import SimpleNamespace
import zipfile

import pytest
from fastapi.testclient import TestClient

from backend.tests.test_potcar_assembly import setup, poscar, confirmation, preview
from backend.tests.test_potcar_library import put, scan, synthetic
from backend.app.schemas.generation import WorkflowGenerateRequest, StructureContext, PotcarConfig
from backend.app.schemas.recipe import TaskType
from backend.app.services.workflow_service import WorkflowService
from backend.app.core.errors import ConflictError
from backend.toolbox.potcar.metadata import digest


def request(species=('Si',), counts=None, **kwargs):
    counts = counts or [1] * len(species)
    return WorkflowGenerateRequest(workflow_id='wf_bound',
        structure=StructureContext(formula='synthetic', elements=list(species), counts=counts,
                                   poscar_text=poscar(species, counts)),
        requested_tasks=[TaskType.STATIC], potcar=PotcarConfig(mode='include'), **kwargs)


def bound(service, svc, lid, req):
    planned = service.plan(req)
    value = service.potcar_preview(req.workflow_id, {'revision': planned['revision'], 'library_id': lid,
                                    'index_revision': svc.datasets(lid)['index_revision']}, svc)
    artifact = svc.generate(confirmation(value, 'workflow-' + str(planned['revision'])))['artifact']
    generated_req = req.model_copy(update={'potcar': PotcarConfig(mode='include', artifact_id=artifact['artifact_id'])})
    return value, artifact, generated_req


def test_final_rendered_poscar_and_exact_bytes_no_filestore(setup):
    svc, lid, _ = setup
    captured = []
    store = SimpleNamespace(register_file=lambda *args: captured.append(args))
    service = WorkflowService(file_store=store, potcar_prepared=True)
    req = request(sample_name='最终样品')
    value, artifact, req = bound(service, svc, lid, req)
    assert value['structure_sha256'] == digest(service._pipeline.final_poscar(req).encode())
    assert value['structure_sha256'] != digest(req.structure.poscar_text.encode())
    result = service.generate(req, potcar_service=svc)
    archive = zipfile.ZipFile(io.BytesIO(service.get_artifact(req.workflow_id).zip_bytes))
    assert archive.read('02_static/POTCAR') == svc.download(artifact['artifact_id'])
    assert archive.read('02_static/POSCAR').decode().startswith('最终样品\n')
    assert not any(row[1].upper() == 'POTCAR' for row in captured)
    stack = list(result['file_tree']['children'])
    while stack:
        node = stack.pop()
        stack.extend(node.get('children', []))
        if node['name'] == 'POTCAR':
            assert node['preview_available'] is False
    assert result['potcar']['status'] == 'generated'
    assert 'POTCAR_NOT_PREPARED' not in result['steps'][0]['blocked_by']
    assert json.loads(archive.read('workflow_plan.json'))['potcar'] == result['potcar']
    assert json.loads(archive.read('workflow_manifest.json'))['potcar'] == result['potcar']
    assert result['manifest']['potcar'] == result['potcar']
    report = archive.read('INPUT_CHECK_REPORT.md').decode()
    assert artifact['sha256'] in report and '待补文件：POTCAR' not in report
    assert 'POTCAR_REQUIRED.md' not in archive.namelist()
    assert 'PRIVATE_SYNTHETIC_BODY' not in json.dumps(result)


def test_static_band_same_snapshot_and_other_gates_unchanged(setup):
    svc, lid, _ = setup
    service = WorkflowService()
    req = request().model_copy(update={'requested_tasks': [TaskType.STATIC, TaskType.BAND], 'enable_band_workflow': True})
    _, artifact, req = bound(service, svc, lid, req)
    result = service.generate(req, potcar_service=svc)
    archive = zipfile.ZipFile(io.BytesIO(service.get_artifact(req.workflow_id).zip_bytes))
    assert archive.read('02_static/POTCAR') == archive.read('04_band/POTCAR') == svc.download(artifact['artifact_id'])
    assert all('POTCAR_NOT_PREPARED' not in s['blocked_by'] for s in result['steps'])
    band = next(s for s in result['steps'] if s['task'] == 'band')
    assert 'UPSTREAM_OUTPUT_MISSING' in band['blocked_by']
    assert 'UPSTREAM_DIAGNOSIS_NOT_PASSED' in band['blocked_by'] and not band['runnable']


def test_repeated_zero_species_dftu_order_unchanged(setup):
    from backend.app.schemas.generation import DftuSettings, DftuEntry
    svc, lid, root = setup
    put(root, 'Ni/POTCAR', synthetic('Ni'))
    put(root, 'O/POTCAR', synthetic('O'))
    scan(svc, lid)
    req = request(('Ni', 'O', 'Ni'), [1, 0, 1], dftu=DftuSettings(enabled=True,
        entries=[DftuEntry(element='Ni', l=2, u_ev=5, j_ev=0, confirmed_by_user=True)]))
    service = WorkflowService()
    _, artifact, req = bound(service, svc, lid, req)
    out = service.generate(req, potcar_service=svc)
    archive = zipfile.ZipFile(io.BytesIO(service.get_artifact(req.workflow_id).zip_bytes))
    incar = archive.read('02_static/INCAR').decode()
    assert 'LDAUL = 2 -1 2' in incar and 'LDAUU = 5 0 5' in incar
    assert [r['element'] for r in artifact['rows']] == ['Ni', 'O', 'Ni']
    assert [r['atom_count'] for r in artifact['rows']] == [1, 0, 1]
    assert out['potcar']['status'] == 'generated'


def test_independent_artifact_or_revision_reuse_rejected(setup):
    svc, lid, _ = setup
    service = WorkflowService()
    req = request()
    independent = svc.generate(confirmation(preview(svc, lid)))['artifact']
    service.plan(req)
    bad = req.model_copy(update={'potcar': PotcarConfig(mode='include', artifact_id=independent['artifact_id'])})
    with pytest.raises(ConflictError, match='不匹配'):
        service.generate(bad, potcar_service=svc)
    _, _, req = bound(service, svc, lid, req)
    service.plan(req)
    with pytest.raises(ConflictError) as exc:
        service.generate(req, potcar_service=svc)
    assert exc.value.code == 'POTCAR_BINDING_MISMATCH'


def test_include_failure_observable_and_no_old_success_zip(setup, monkeypatch):
    from backend.app.core.errors import NotFoundError
    svc, lid, _ = setup
    service = WorkflowService()
    _, artifact, req = bound(service, svc, lid, request())
    service.generate(req, potcar_service=svc)
    original = svc.download
    def fail_download(*args):
        assert service.get_workflow(req.workflow_id)['potcar']['status'] == 'generating'
        raise OSError('PRIVATE_BODY_SHOULD_NOT_LEAK')
    monkeypatch.setattr(svc, 'download', fail_download)
    from backend.toolbox.contracts import ToolboxError
    with pytest.raises(ToolboxError) as error:
        service.generate(req, potcar_service=svc)
    assert error.value.code == 'POTCAR_STORE_INVALID'
    current = service.get_workflow(req.workflow_id)
    assert current['potcar']['status'] == 'failed'
    assert 'PRIVATE_BODY' not in json.dumps(current)
    with pytest.raises(NotFoundError):
        service.get_artifact(req.workflow_id)
    monkeypatch.setattr(svc, 'download', original)
    assert svc.download(artifact['artifact_id'])
    assert service.generate(req, potcar_service=svc)['potcar']['status'] == 'generated'


def test_include_without_artifact_omits_only_when_explicitly_requested(setup):
    svc, _, _ = setup
    service = WorkflowService(potcar_prepared=True)
    req = request()
    service.plan(req)
    with pytest.raises(ConflictError) as exc:
        service.generate(req, potcar_service=svc)
    assert exc.value.code == 'POTCAR_CONFIRMATION_REQUIRED'
    omit = req.model_copy(update={'potcar': PotcarConfig()})
    result = service.generate(omit)
    assert result['potcar']['status'] == 'omitted'
    assert 'POTCAR_NOT_PREPARED' in result['steps'][0]['blocked_by']
    archive = zipfile.ZipFile(io.BytesIO(service.get_artifact(req.workflow_id).zip_bytes))
    assert not any(name.endswith('/POTCAR') for name in archive.namelist())


def test_changed_structure_parameters_or_rules_invalidates_binding(setup, monkeypatch):
    from backend.app.schemas.generation import ParameterPatch
    from backend.toolbox.potcar import recommendations
    svc, lid, _ = setup
    service = WorkflowService()
    _, _, req = bound(service, svc, lid, request())
    for changed in (req.model_copy(update={'sample_name': 'changed'}),
                    req.model_copy(update={'patches': [ParameterPatch(patch_id='encut', parameter='ENCUT',
                        operation='replace', value=600, confirmed_by_user=True)]})):
        with pytest.raises(ConflictError) as exc:
            service.generate(changed, potcar_service=svc)
        assert exc.value.code == 'POTCAR_BINDING_MISMATCH'
    monkeypatch.setattr(recommendations, 'RULE_VERSION', 'paw-pbe-selection-r2')
    with pytest.raises(ConflictError) as exc:
        service.generate(req, potcar_service=svc)
    assert exc.value.code == 'POTCAR_BINDING_MISMATCH'


@pytest.fixture
def api(setup, monkeypatch):
    from app.main import app
    from app.api.v1 import workflows
    svc, lid, root = setup
    service = WorkflowService()
    monkeypatch.setattr(workflows, 'workflow_service', service)
    monkeypatch.setattr(app.state, 'toolbox', SimpleNamespace(potcar=svc), raising=False)
    return TestClient(app), svc, lid, root, service


def test_http_plan_replay_patches_context_revision_and_generate(api):
    client, svc, lid, root, service = api
    req = request()
    planned = client.post('/api/v1/workflows/plan', json={'workflow_id': req.workflow_id,
                           'workflow': req.model_dump(mode='json')})
    assert planned.status_code == 200, planned.text
    revision = planned.json()['data']['revision']
    patch = {'patch_id': 'spin', 'parameter': 'ISPIN', 'operation': 'add', 'value': 2,
             'confirmed_by_user': True}
    replay = client.post('/api/v1/workflows/plan', json={'workflow_id': req.workflow_id, 'patches': [patch],
                                                          'potcar': {'mode': 'include'}})
    assert replay.status_code == 200, replay.text
    assert replay.json()['data']['revision'] == revision + 1
    payload = {'revision': revision, 'library_id': lid, 'index_revision': svc.datasets(lid)['index_revision']}
    stale = client.post(f'/api/v1/workflows/{req.workflow_id}/potcar/preview', json=payload)
    assert stale.status_code == 409 and stale.json()['error']['code'] == 'WORKFLOW_REVISION_CONFLICT'
    payload['revision'] += 1
    value = client.post(f'/api/v1/workflows/{req.workflow_id}/potcar/preview', json=payload)
    assert value.status_code == 200, value.text
    value = value.json()['data']
    assert value['context']['spin_polarized'] is True and value['context_source'] == 'workflow'
    artifact = client.post('/api/v1/toolbox/potcar/artifacts', json=confirmation(value)).json()['artifact']
    generated = client.post('/api/v1/workflows/generate', json={'workflow_id': req.workflow_id,
        'patches': [patch], 'potcar': {'mode': 'include', 'artifact_id': artifact['artifact_id']}})
    assert generated.status_code == 200, generated.text
    assert generated.json()['data']['revision'] == revision + 1
    assert client.get(generated.json()['data']['download_url']).status_code == 200
    # Explicit empty patches must clear the previously persisted patch list.
    cleared = client.post('/api/v1/workflows/plan', json={'workflow_id': req.workflow_id, 'patches': []})
    assert cleared.status_code == 200
    assert service.replay_request(req.workflow_id).patches == []
    value = client.post(f'/api/v1/workflows/{req.workflow_id}/potcar/preview', json={**payload,
        'revision': cleared.json()['data']['revision']}).json()['data']
    assert value['context']['functional'] == 'PBE'
    assert value['context']['spin_polarized'] is None


def test_http_preview_refuses_client_structure_context_and_service_unavailable(api, monkeypatch):
    client, svc, lid, _, _ = api
    req = request()
    planned = client.post('/api/v1/workflows/plan', json={'workflow_id': req.workflow_id,
                           'workflow': req.model_dump(mode='json')}).json()['data']
    payload = {'revision': planned['revision'], 'library_id': lid, 'index_revision': svc.datasets(lid)['index_revision']}
    for extra in ({'poscar_text': 'PRIVATE_BODY'}, {'context': {'purpose': 'regular'}}, {'step_ids': ['fake']}):
        out = client.post(f'/api/v1/workflows/{req.workflow_id}/potcar/preview', json={**payload, **extra})
        assert out.status_code == 400
        assert 'PRIVATE_BODY' not in out.text
    from app.main import app
    monkeypatch.setattr(app.state, 'toolbox', SimpleNamespace(potcar=None))
    out = client.post(f'/api/v1/workflows/{req.workflow_id}/potcar/preview', json=payload)
    assert out.status_code == 409


def test_composed_context_considers_all_steps_and_unknown_conditions():
    req = request()
    context = WorkflowService._potcar_context(req, {'steps': [
        {'parameters': {'ISPIN': 1}}, {'parameters': {'LHFCALC': True, 'HFSCREEN': .2, 'PSTRESS': 100}}]})
    assert context['functional'] == 'HSE06' and context['high_pressure'] is True
    assert context['purpose'] == 'special'
    assert context['short_bonds'] is None and context['magnetic_energy'] is None


def test_old_workflow_preview_confirmation_invalid_after_replan_but_snapshot_download_survives(setup):
    from backend.tests.test_potcar_library import assert_error
    svc, lid, _ = setup
    service = WorkflowService()
    req = request()
    _, artifact, _ = bound(service, svc, lid, req)
    raw = svc.download(artifact['artifact_id'])
    record = service._plans[req.workflow_id]
    value = service.potcar_preview(req.workflow_id, {'revision': record.revision, 'library_id': lid,
                                    'index_revision': svc.datasets(lid)['index_revision']}, svc)
    service.plan(req)
    assert_error('WORKFLOW_BINDING_STALE', lambda: svc.generate(confirmation(value, 'stale-confirm')))
    assert svc.download(artifact['artifact_id']) == raw


def test_workflow_confirmation_without_configured_validator_fails_closed(setup):
    from backend.tests.test_potcar_library import assert_error
    svc, lid, _ = setup
    service = WorkflowService()
    req = request()
    plan = service.plan(req)
    value = service.potcar_preview(req.workflow_id, {'revision': plan['revision'], 'library_id': lid,
                                    'index_revision': svc.datasets(lid)['index_revision']}, svc)
    svc.assembly.workflow_binding_validator = None
    assert_error('WORKFLOW_BINDING_STALE', lambda: svc.generate(confirmation(value, 'no-validator')))


def test_old_full_include_cannot_replace_changed_current_stored_plan(setup):
    svc, lid, _ = setup
    service = WorkflowService()
    _, artifact, old = bound(service, svc, lid, request(sample_name='old'))
    changed = old.model_copy(update={'sample_name': 'new', 'potcar': PotcarConfig()})
    service.generate(changed)
    assert service.replay_request(old.workflow_id).sample_name == 'new'
    with pytest.raises(ConflictError) as error:
        service.generate(old, potcar_service=svc)
    assert error.value.code == 'POTCAR_BINDING_MISMATCH'
    assert svc.download(artifact['artifact_id'])


def test_replan_during_file_registration_cannot_publish_old_zip_or_remove_new_success(setup):
    svc, lid, _ = setup
    service = WorkflowService()
    _, _, req = bound(service, svc, lid, request(sample_name='old'))
    triggered = []
    def register(*args):
        if triggered:
            return
        triggered.append(True)
        changed = req.model_copy(update={'sample_name': 'new', 'potcar': PotcarConfig()})
        service.plan(changed)
        service.generate(changed)
    service._file_store = SimpleNamespace(register_file=register)
    with pytest.raises(ConflictError) as error:
        service.generate(req, potcar_service=svc)
    assert error.value.code == 'WORKFLOW_REVISION_CONFLICT'
    latest = service.get_workflow(req.workflow_id)
    assert latest['revision'] == 2 and latest['potcar']['status'] == 'omitted'
    archive = zipfile.ZipFile(io.BytesIO(service.get_artifact(req.workflow_id).zip_bytes))
    assert archive.read('02_static/POSCAR').startswith(b'new\n')


def test_older_same_revision_attempt_cannot_erase_newer_success(setup, monkeypatch):
    svc, lid, _ = setup
    service = WorkflowService()
    _, _, req = bound(service, svc, lid, request())
    original = service._pipeline.generate
    triggered = []
    def generate(*args, **kwargs):
        if not triggered:
            triggered.append(True)
            service.generate(req, potcar_service=svc)
        return original(*args, **kwargs)
    monkeypatch.setattr(service._pipeline, 'generate', generate)
    with pytest.raises(ConflictError):
        service.generate(req, potcar_service=svc)
    assert service.get_workflow(req.workflow_id)['potcar']['status'] == 'generated'
    assert service.get_artifact(req.workflow_id).zip_bytes


def test_http_get_observes_generating_and_failure_without_old_zip(api, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    client, svc, lid, _, service = api
    _, _, req = bound(service, svc, lid, request())
    service.generate(req, potcar_service=svc)
    entered, release = threading.Event(), threading.Event()
    original = svc.download
    def blocked_download(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)
    monkeypatch.setattr(svc, 'download', blocked_download)
    payload = {'workflow_id': req.workflow_id, 'potcar': req.potcar.model_dump()}
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(client.post, '/api/v1/workflows/generate', json=payload)
        try:
            assert entered.wait(5)
            current = client.get(f'/api/v1/workflows/{req.workflow_id}').json()['data']
            assert current['potcar']['status'] == 'generating'
            assert client.get(f'/api/v1/workflows/{req.workflow_id}/download').status_code == 404
        finally:
            release.set()
        assert pending.result(timeout=5).status_code == 200
    def failed_download(*args):
        raise OSError('PRIVATE_BODY')
    monkeypatch.setattr(svc, 'download', failed_download)
    response = client.post('/api/v1/workflows/generate', json=payload)
    assert response.status_code == 503 and 'PRIVATE_BODY' not in response.text
    current = client.get(f'/api/v1/workflows/{req.workflow_id}').json()['data']
    assert current['potcar']['status'] == 'failed'
    assert current['potcar']['error']['code'] == response.json()['error']['code']
    assert client.get(f'/api/v1/workflows/{req.workflow_id}/download').status_code == 404


def test_workflow_artifact_publication_guard_missing_or_changed_plan_fails_closed(setup):
    from contextlib import contextmanager
    from backend.tests.test_potcar_library import assert_error
    svc, lid, _ = setup
    service = WorkflowService()
    req = request()
    plan = service.plan(req)
    value = service.potcar_preview(req.workflow_id, {'revision': plan['revision'], 'library_id': lid,
                                    'index_revision': svc.datasets(lid)['index_revision']}, svc)
    svc.assembly.workflow_publication_guard = None
    assert_error('WORKFLOW_BINDING_STALE', lambda: svc.generate(confirmation(value, 'guard-missing')))
    @contextmanager
    def changed_before_publication(binding):
        service.plan(req)
        with service._potcar_publication_guard(binding) as valid:
            yield valid
    svc.assembly.workflow_publication_guard = changed_before_publication
    assert_error('WORKFLOW_BINDING_STALE', lambda: svc.generate(confirmation(value, 'guard-changed')))
    assert list(svc.assembly.root.iterdir()) == []


def test_workflow_artifact_rename_serializes_concurrent_plan_publication(setup, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from backend.toolbox.potcar import assembly
    svc, lid, _ = setup
    service = WorkflowService()
    req = request()
    plan = service.plan(req)
    value = service.potcar_preview(req.workflow_id, {'revision': plan['revision'], 'library_id': lid,
                                    'index_revision': svc.datasets(lid)['index_revision']}, svc)
    original = assembly.os.rename
    started, blocked, futures = threading.Event(), [], []
    def replan():
        acquired = service._guard.acquire(blocking=False)
        blocked.append(not acquired)
        if acquired:
            service._guard.release()
        started.set()
        return service.plan(req)
    with ThreadPoolExecutor(max_workers=1) as pool:
        def rename(source, target):
            futures.append(pool.submit(replan))
            assert started.wait(5)
            assert blocked == [True]
            assert service._plans[req.workflow_id].revision == 1
            return original(source, target)
        monkeypatch.setattr(assembly.os, 'rename', rename)
        artifact = svc.generate(confirmation(value, 'guard-atomic'))['artifact']
        assert futures[0].result(timeout=5)['revision'] == 2
    assert artifact['workflow_binding']['revision'] == 1
    assert svc.download(artifact['artifact_id'])
