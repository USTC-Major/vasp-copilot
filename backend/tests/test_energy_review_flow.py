"""PP22 retest review state and compatibility; isolated synthetic inputs only."""
import copy

import pytest

from backend.tests.test_postprocessing_energy import BASE, BASIS, configure, energy_api, manual
from backend.toolbox.contracts import ToolboxError
from backend.toolbox.postprocessing.energy.calculation import (
    confirmation, input_fingerprint, legacy_confirmation,
)
from backend.toolbox.postprocessing.energy.store import EnergyStore


def create_typed(client, kind='adsorption'):
    response = client.post(BASE + '/collections', json={'title': '复测合成样本', 'analysis_kind': kind})
    assert response.status_code == 201, response.text
    return response.json()['collection']


def sample_rows(doc):
    return [{'sample_id': s['id'], **{key: copy.deepcopy(s.get(key)) for key in
            ('name', 'role', 'included', 'confirmed', 'accepted_warnings', 'override')}} for s in doc['samples']]


def adsorption_review(client):
    doc = create_typed(client)
    for name, comp, energy in [('表面', {'Pt': 4}, -100), ('CO', {'C': 1, 'O': 1}, -10),
                              ('目标A', {'Pt': 4, 'C': 1, 'O': 1}, -112),
                              ('未纳入', {'Pt': 4, 'C': 1, 'O': 1}, -111)]:
        doc = manual(client, doc, name, comp, energy)
    ids = [s['id'] for s in doc['samples']]
    group = {'id': 'ads', 'name': '吸附', 'kind': 'adsorption', 'energy_basis': BASIS,
             'basis_confirmed': True, 'clean_sample_id': ids[0], 'adsorbate_sample_id': ids[1],
             'reference_units': 1, 'targets': [{'sample_id': ids[2], 'adsorbate_count': 1}]}
    rows = sample_rows(doc)
    for i, row in enumerate(rows):
        row.update(role=['clean_slab', 'adsorbate', 'adsorbed', 'adsorbed'][i], included=i != 3,
                   confirmed=True, accepted_warnings=True)
    return configure(client, doc, [group], rows)


def action(client, doc, verb):
    return client.post(f"{BASE}/collections/{doc['id']}/{verb}", json={'expected_revision': doc['revision']})


def success(response):
    assert response.status_code == 200, response.text
    return response.json()['collection']


def error(response, code):
    assert response.status_code == 409, response.text
    assert response.json()['error']['code'] == code


def test_typed_lock_persistence_all_mutation_bypasses_unlock_and_revision(energy_api):
    client, app = energy_api
    doc = adsorption_review(client)
    error(action(client, doc, 'calculate'), 'ENERGY_LOCK_REQUIRED')
    locked = success(action(client, doc, 'lock'))
    assert locked['locked'] and locked['lock_fingerprint'] == input_fingerprint(locked)
    assert EnergyStore(app.state.toolbox.root).read(doc['id']) == locked
    error(action(client, doc, 'unlock'), 'ENERGY_REVISION_CONFLICT')
    revision = locked['revision']
    collection_url = f"{BASE}/collections/{doc['id']}"
    attempts = [
        client.put(collection_url + '/configuration', json={'expected_revision': revision, 'samples': [], 'groups': []}),
        client.post(collection_url + '/manual', json={'expected_revision': revision, 'name': '新样本',
            'composition': {'Pt': 4}, 'energy_fields': {BASIS: -100}, 'energy_basis': BASIS, 'unit': 'eV'}),
        client.post(collection_url + '/csv', params={'expected_revision': revision}, content=b'invalid csv'),
        client.post(collection_url + '/outcar', params={'expected_revision': revision}, content=b'OUTCAR'),
        client.post(collection_url + '/task-sources/import', json={'expected_revision': revision, 'preview_id': 'bad'}),
        client.post(collection_url + '/reuse', json={'expected_revision': revision,
            'source_collection_id': 'bad', 'sample_id': 'bad'}),
    ]
    for response in attempts:
        error(response, 'ENERGY_LOCKED')
    assert client.get(collection_url).json()['collection'] == locked
    calculated = success(action(client, locked, 'calculate'))
    assert calculated['result']['groups'][0]['rows'][0]['delta_ev'] == -2
    assert client.get(collection_url + '/export').status_code == 200
    unlocked = success(action(client, calculated, 'unlock'))
    assert not unlocked['locked'] and unlocked['lock_fingerprint'] is None
    assert unlocked['samples'] == calculated['samples']
    assert unlocked['groups'] == calculated['groups']
    error(client.get(collection_url + '/export'), 'ENERGY_LOCK_REQUIRED')


def test_upload_commit_rechecks_lock_and_revision_after_reservation(energy_api, tmp_path):
    client, app = energy_api
    doc = adsorption_review(client)
    svc = app.state.energy
    path, _ = svc.reserve(doc['id'], doc['revision'])
    path.write_bytes(b'vasp.6.3.2\nsynthetic source\n')
    locked = success(action(client, doc, 'lock'))
    try:
        with pytest.raises(ToolboxError) as caught:
            svc.import_path(doc['id'], doc['revision'], path, 'OUTCAR')
        assert caught.value.code == 'ENERGY_REVISION_CONFLICT'
        with pytest.raises(ToolboxError) as caught:
            svc.import_path(doc['id'], locked['revision'], path, 'OUTCAR')
        assert caught.value.code == 'ENERGY_LOCKED'
        assert svc.read(doc['id']) == locked
    finally:
        svc.release(path)


def test_new_target_preserves_existing_reviews_and_risks(energy_api):
    client, _ = energy_api
    doc = adsorption_review(client)
    before = copy.deepcopy(doc)
    doc = manual(client, doc, '新增B', {'Pt': 4, 'C': 1, 'O': 1}, -111)
    assert doc['samples'][:-1] == before['samples']
    group = doc['groups'][0]
    group['targets'].append({'sample_id': doc['samples'][-1]['id'], 'adsorbate_count': 1})
    rows = sample_rows(doc)
    rows[-1].update(role='adsorbed', included=True, confirmed=False)
    doc = configure(client, doc, [group], rows)
    assert all(s['confirmed'] and s['accepted_warnings'] for s in doc['samples'][:-1])
    assert doc['groups'][0]['basis_confirmed']
    assert not doc['samples'][-1]['confirmed'] and not doc['samples'][-1]['accepted_warnings']
    rows = sample_rows(doc)
    rows[-1].update(confirmed=True, accepted_warnings=True)
    doc = configure(client, doc, doc['groups'], rows)
    doc = success(action(client, doc, 'lock'))
    result = success(action(client, doc, 'calculate'))['result']['groups'][0]['rows']
    assert [row['delta_ev'] for row in result] == [-2, -1]


def test_reference_change_invalidates_dependents_but_preserves_risks_and_unrelated(energy_api):
    client, _ = energy_api
    doc = adsorption_review(client)
    rows = sample_rows(doc)
    rows[0]['override'] = {'energy_fields': {BASIS: -101}, 'energy_basis': BASIS, 'unit': 'eV', 'note': '人工复核'}
    doc = configure(client, doc, doc['groups'], rows)
    assert [s['confirmed'] for s in doc['samples']] == [False, True, False, True]
    assert all(s['accepted_warnings'] for s in doc['samples'])
    assert not doc['groups'][0]['basis_confirmed']
    error(action(client, doc, 'lock'), 'ENERGY_BASIS_CONFIRMATION_REQUIRED')
    rows = sample_rows(doc)
    for row in rows:
        row['confirmed'] = True
    group = doc['groups'][0]
    group['basis_confirmed'] = True
    doc = configure(client, doc, [group], rows)
    # Name and explanation changes preserve scientific and risk checks.
    rows = sample_rows(doc)
    rows[0]['name'] = '重新命名'
    rows[0]['override']['note'] = '补充说明'
    doc = configure(client, doc, doc['groups'], rows)
    assert all(s['confirmed'] and s['accepted_warnings'] for s in doc['samples'])
    doc = success(action(client, doc, 'lock'))
    assert success(action(client, doc, 'calculate'))['result']['groups'][0]['rows'][0]['delta_ev'] == -1


def test_target_count_change_only_invalidates_that_target(energy_api):
    client, _ = energy_api
    doc = adsorption_review(client)
    group = doc['groups'][0]
    group['targets'][0]['adsorbate_count'] = 2
    doc = configure(client, doc, [group], sample_rows(doc))
    assert [s['confirmed'] for s in doc['samples']] == [True, True, False, True]
    assert doc['groups'][0]['basis_confirmed']
    assert all(s['accepted_warnings'] for s in doc['samples'])
    rows = sample_rows(doc)
    rows[2]['confirmed'] = True
    doc = configure(client, doc, doc['groups'], rows)
    response = action(client, doc, 'lock')
    assert response.status_code == 400 and response.json()['error']['code'] == 'ENERGY_STOICHIOMETRY_CONFLICT'
    assert not client.get(f"{BASE}/collections/{doc['id']}").json()['collection']['locked']


def test_source_risk_change_cannot_be_blessed_by_unchanged_save(energy_api):
    client, app = energy_api
    doc = adsorption_review(client)
    doc['samples'][0]['parsed']['warnings'].append('新的来源风险')
    app.state.energy.save(doc)
    doc = configure(client, doc, doc['groups'], [])
    assert not doc['samples'][0]['confirmed'] and not doc['samples'][2]['confirmed']
    assert not doc['samples'][0]['accepted_warnings']
    assert all(s['accepted_warnings'] for s in doc['samples'][1:])


def test_lock_rejects_unbound_risk_and_hard_science_and_tampered_lock_result(energy_api):
    client, app = energy_api
    doc = adsorption_review(client)
    rows = sample_rows(doc)
    rows[3]['included'] = True
    doc = configure(client, doc, doc['groups'], rows)
    error(action(client, doc, 'lock'), 'ENERGY_UNBOUND_SAMPLE')
    rows = sample_rows(doc)
    rows[3]['included'] = False
    rows[0]['accepted_warnings'] = False
    doc = configure(client, doc, doc['groups'], rows)
    error(action(client, doc, 'lock'), 'ENERGY_WARNING_ACCEPTANCE_REQUIRED')
    rows = sample_rows(doc)
    rows[0]['accepted_warnings'] = True
    doc = configure(client, doc, doc['groups'], rows)
    locked = success(action(client, doc, 'lock'))
    calculated = success(action(client, locked, 'calculate'))
    calculated['samples'][2]['override'] = {'energy_fields': {BASIS: -999}, 'energy_basis': BASIS, 'unit': 'eV', 'note': 'tamper'}
    app.state.energy.save(calculated)
    error(action(client, calculated, 'calculate'), 'ENERGY_LOCK_STALE')
    error(client.get(f"{BASE}/collections/{doc['id']}/export"), 'ENERGY_LOCK_STALE')
    # Even a freshly reviewed lock cannot turn an old result into a current export.
    stale_result = copy.deepcopy(calculated['result'])
    doc = success(action(client, calculated, 'unlock'))
    doc = configure(client, doc, doc['groups'], sample_rows(doc))
    rows = sample_rows(doc)
    for row in rows:
        row['confirmed'] = True
    doc = configure(client, doc, doc['groups'], rows)
    doc = success(action(client, doc, 'lock'))
    doc['result'] = stale_result
    app.state.energy.save(doc)
    error(client.get(f"{BASE}/collections/{doc['id']}/export"), 'ENERGY_RESULT_STALE')


def test_legacy_projection_validates_old_hash_without_writing_and_explicit_group_copy(energy_api):
    client, app = energy_api
    doc = adsorption_review(client)
    # Construct exactly the original persisted schema with its original hash.
    for key in ('analysis_kind', 'legacy_mode', 'locked', 'lock_fingerprint'):
        doc.pop(key)
    for sample in doc['samples']:
        sample.pop('warning_acceptance_fingerprint')
        sample['confirmation_fingerprint'] = legacy_confirmation(sample, doc['groups'])
    path = app.state.energy.directory(doc['id']) / 'metadata.json'
    app.state.energy.write_json(path, doc)
    original = path.read_bytes()
    projected = client.get(f"{BASE}/collections/{doc['id']}").json()['collection']
    assert projected['legacy_mode'] and projected['analysis_kind'] == 'adsorption'
    assert all(s['confirmation_fingerprint'] == confirmation(s, projected['groups'], projected['samples'])
               for s in projected['samples'])
    assert all(s['warning_acceptance_fingerprint'] for s in projected['samples'])
    assert path.read_bytes() == original
    assert action(client, projected, 'calculate').status_code == 200
    # A stale legacy hash remains stale and does not receive fresh risk proof.
    doc['samples'][0]['confirmation_fingerprint'] = 'invalid'
    app.state.energy.write_json(path, doc)
    stale = app.state.energy.read(doc['id'])
    assert stale['samples'][0]['confirmation_fingerprint'] == 'invalid'
    assert stale['samples'][0]['warning_acceptance_fingerprint'] is None
    error(action(client, stale, 'lock'), 'ENERGY_CONFIRMATION_STALE')
    # Mixed old records stay readable. Multi-group copying requires explicit choice.
    doc['groups'].append({**copy.deepcopy(doc['groups'][0]), 'id': 'ads_second', 'name': '另一个参考条件'})
    doc['groups'].append({'id': 'formation', 'name': '旧形成能', 'kind': 'formation', 'energy_basis': BASIS,
                          'basis_confirmed': False, 'element_references': {}, 'targets': []})
    app.state.energy.write_json(path, doc)
    original = path.read_bytes()
    mixed = client.get(f"{BASE}/collections/{doc['id']}").json()['collection']
    assert mixed['analysis_kind'] is None and len(mixed['groups']) == 3 and mixed['legacy_mode']
    endpoint = f"{BASE}/collections/{doc['id']}/copy"
    error(client.post(endpoint, json={'expected_revision': doc['revision'], 'analysis_kind': 'adsorption'}),
          'ENERGY_COPY_GROUP_REQUIRED')
    response = client.post(endpoint, json={'expected_revision': doc['revision'], 'analysis_kind': 'adsorption', 'group_id': 'ads'})
    assert response.status_code == 201, response.text
    copied = response.json()['collection']
    assert copied['id'] != doc['id'] and copied['analysis_kind'] == 'adsorption' and not copied['legacy_mode']
    assert len(copied['groups']) == 1 and len(copied['samples']) == 3
    assert not copied['locked'] and copied['result'] is None
    assert all(not s['confirmed'] and not s['accepted_warnings'] for s in copied['samples'])
    assert path.read_bytes() == original


def test_typed_kind_constraints_and_empty_lock_do_not_publish_partial_state(energy_api):
    client, _ = energy_api
    doc = create_typed(client, 'formation')
    before = copy.deepcopy(doc)
    response = action(client, doc, 'lock')
    assert response.json()['error']['code'] == 'ENERGY_GROUP_REQUIRED'
    assert client.get(f"{BASE}/collections/{doc['id']}").json()['collection'] == before
    group = {'id': 'bad', 'name': '类型错误', 'kind': 'adsorption', 'energy_basis': BASIS}
    response = client.put(f"{BASE}/collections/{doc['id']}/configuration", json={
        'expected_revision': doc['revision'], 'groups': [group], 'samples': []})
    assert response.json()['error']['code'] == 'ENERGY_ANALYSIS_KIND_CONFLICT'
    assert client.get(f"{BASE}/collections/{doc['id']}").json()['collection'] == before


def test_typed_formation_locks_shared_element_references_without_changing_formula(energy_api):
    client, _ = energy_api
    doc = create_typed(client, 'formation')
    for name, comp, energy in [('Al2O', {'Al': 2, 'O': 1}, -20), ('Al胞', {'Al': 2}, -8), ('O2', {'O': 2}, -10)]:
        doc = manual(client, doc, name, comp, energy)
    rows = sample_rows(doc)
    for i, row in enumerate(rows):
        row.update(role='material' if i == 0 else 'element_reference', included=True, confirmed=True, accepted_warnings=True)
    ids = [s['id'] for s in doc['samples']]
    group = {'id': 'formation', 'name': '元素参考形成能', 'kind': 'formation', 'energy_basis': BASIS,
             'basis_confirmed': True, 'element_references': {'Al': ids[1], 'O': ids[2]}, 'targets': [{'sample_id': ids[0]}]}
    doc = configure(client, doc, [group], rows)
    doc = success(action(client, doc, 'lock'))
    result = success(action(client, doc, 'calculate'))['result']['groups'][0]['rows'][0]
    assert result['delta_ev'] == -7 and result['normalized_ev'] == pytest.approx(-7 / 3)
