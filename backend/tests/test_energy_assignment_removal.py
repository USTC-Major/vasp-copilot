"""RT04/05 candidate inference and atomic membership removal on synthetic data."""
import copy

import pytest

from backend.tests.test_postprocessing_energy import BASE, BASIS, configure, energy_api, manual
from backend.tests.test_energy_review_flow import action, adsorption_review, create_typed, error, sample_rows, success
from backend.toolbox.contracts import ToolboxError


def imported_batch(client, app, monkeypatch, values, kind='adsorption'):
    from backend.toolbox.postprocessing.energy import parser
    doc = create_typed(client, kind)
    svc = app.state.energy
    parsed = {}
    for index, (comp, energy) in enumerate(values):
        parsed[str(index)] = svc.manual_sample({'name': 'OUTCAR', 'composition': comp,
            'energy_fields': {BASIS: energy}, 'energy_basis': BASIS, 'unit': 'eV'})['parsed']
    monkeypatch.setattr(parser, 'parse_energy_outcar', lambda text: copy.deepcopy(parsed[text]))
    for index in range(len(values)):
        response = client.post(f"{BASE}/collections/{doc['id']}/outcar", params={'expected_revision': doc['revision']}, content=str(index).encode())
        assert response.status_code == 201, response.text
        doc = response.json()['collection']
        assert all(row['role'] is None for row in doc['samples'])
    return doc


def inferred(client, doc):
    return success(action(client, doc, 'autofill'))


def review(client, doc):
    rows = sample_rows(doc)
    for row in rows:
        if row['included']:
            row.update(confirmed=True, accepted_warnings=True)
    groups = copy.deepcopy(doc['groups'])
    for group in groups:
        group['basis_confirmed'] = True
    return configure(client, doc, groups, rows)


def removal(client, doc, ids=None, clear_all=False, preview=False):
    return client.post(f"{BASE}/collections/{doc['id']}/samples/" + ('removal-preview' if preview else 'remove'),
                       json={'expected_revision': doc['revision'], 'sample_ids': ids or [], 'clear_all': clear_all})


def test_same_outcar_full_batch_adsorption_unique_composition_candidates_and_lock(energy_api, monkeypatch):
    client, app = energy_api
    doc = imported_batch(client, app, monkeypatch, [({'Pt': 4}, -100), ({'C': 1, 'O': 1}, -10),
                         ({'Pt': 4, 'C': 1, 'O': 1}, -112), ({'Pt': 4, 'C': 1, 'O': 1}, -111)])
    assert all(s['name'] == 'OUTCAR' and s['source']['relative_path'] is None for s in doc['samples'])
    doc = inferred(client, doc)
    assert doc['assignment_report'] == {'state': 'ready', 'issues': []}
    assert [s['role'] for s in doc['samples']] == ['clean_slab', 'adsorbate', 'adsorbed', 'adsorbed']
    assert all(s['included'] and s['role_origin'] == 'auto' and s['included_origin'] == 'auto' for s in doc['samples'])
    assert all(not s['confirmed'] and not s['accepted_warnings'] for s in doc['samples'])
    assert doc['groups'][0]['reference_units'] == 1
    assert [t['adsorbate_count'] for t in doc['groups'][0]['targets']] == [1, 1]
    doc = review(client, doc)
    doc = success(action(client, doc, 'lock'))
    assert [row['delta_ev'] for row in success(action(client, doc, 'calculate'))['result']['groups'][0]['rows']] == [-2, -1]


def test_default_empty_group_persist_then_real_outcar_uploads_autofill(energy_api):
    from backend.tests.test_energy_parser import FOOTER, block, header
    client, _ = energy_api
    doc = create_typed(client)
    group = {'id': 'analysis', 'name': '吸附能', 'kind': 'adsorption', 'energy_basis': BASIS,
             'clean_sample_id': '', 'adsorbate_sample_id': '', 'reference_units': 1,
             'element_references': {}, 'targets': [], 'reference_origins': {}}
    doc = configure(client, doc, [group], [])  # UI persists its default group before importing the batch.
    assert doc['groups'][0]['clean_sample_id'] is None and doc['groups'][0]['adsorbate_sample_id'] is None
    assert doc['groups'][0]['reference_origins'] == {}
    for comp, value in [({'Pt': 4}, -100), ({'C': 1, 'O': 1}, -10),
                        ({'Pt': 4, 'C': 1, 'O': 1}, -112), ({'Pt': 4, 'C': 1, 'O': 1}, -111)]:
        species = ''.join(f' TITEL = PAW_PBE {element} 04Jan2005\n VRHFIN ={element}: synthetic\n' for element in comp)
        species += ' ions per type = ' + ' '.join(str(count) for count in comp.values()) + f'\n NIONS = {sum(comp.values())}\n'
        data = (header(species=species) + block(toten=str(value), without=str(value), e0=str(value)) + FOOTER).encode()
        response = client.post(f"{BASE}/collections/{doc['id']}/outcar", params={'expected_revision': doc['revision']}, content=data)
        assert response.status_code == 201, response.text
        doc = response.json()['collection']
        sample = doc['samples'][-1]
        assert sample['parsed']['composition'] == comp and sample['parsed']['metadata']['support']['automatic_comparison']
        assert sample['name'] == 'OUTCAR' and sample['source']['relative_path'] is None
        assert all(row['role'] is None for row in doc['samples'])
    doc = inferred(client, doc)
    assert doc['assignment_report'] == {'state': 'ready', 'issues': []}
    assert [sample['role'] for sample in doc['samples']] == ['clean_slab', 'adsorbate', 'adsorbed', 'adsorbed']
    assert doc['groups'][0]['reference_origins']['clean_sample_id'] == 'auto'
    assert doc['groups'][0]['reference_origins']['adsorbate_sample_id'] == 'auto'
    assert len(doc['groups'][0]['targets']) == 2
    assert all(sample['included'] and not sample['confirmed'] and not sample['accepted_warnings'] for sample in doc['samples'])


def test_formation_multiple_targets_unique_element_references_and_duplicate_ambiguity(energy_api):
    client, _ = energy_api
    doc = create_typed(client, 'formation')
    for comp, value in [({'Al': 2, 'O': 1}, -20), ({'Al': 1, 'O': 1}, -15), ({'Al': 2}, -8), ({'O': 2}, -10)]:
        doc = manual(client, doc, 'OUTCAR', comp, value)
    doc = inferred(client, doc)
    assert doc['assignment_report']['state'] == 'ready'
    assert [s['role'] for s in doc['samples']] == ['material', 'material', 'element_reference', 'element_reference']
    assert len(doc['groups'][0]['targets']) == 2
    doc = review(client, doc)
    original = copy.deepcopy(doc)
    doc = manual(client, doc, 'OUTCAR', {'Al': 1}, -4)
    doc = inferred(client, doc)
    assert doc['assignment_report']['state'] == 'ambiguous'
    assert 'Al' not in doc['groups'][0]['element_references']
    assert all(not s['confirmed'] for s in doc['samples'][:3])
    assert doc['samples'][3]['confirmed'] == original['samples'][3]['confirmed']
    assert all(s['accepted_warnings'] for s in doc['samples'][:4])


def test_new_duplicate_clean_retracts_auto_candidates_without_energy_ranking(energy_api):
    client, _ = energy_api
    doc = create_typed(client)
    for comp, value in [({'Pt': 4}, -100), ({'C': 1, 'O': 1}, -10), ({'Pt': 4, 'C': 1, 'O': 1}, -112)]:
        doc = manual(client, doc, 'OUTCAR', comp, value)
    doc = inferred(client, doc)
    assert doc['assignment_report']['state'] == 'ready'
    doc = manual(client, doc, 'OUTCAR', {'Pt': 4}, -1000)
    assert doc['assignment_report'] is None
    doc = inferred(client, doc)
    assert doc['assignment_report']['state'] == 'ambiguous'
    assert doc['groups'][0]['clean_sample_id'] is None and doc['groups'][0]['adsorbate_sample_id'] is None
    assert all(s['role'] is None for s in doc['samples'])
    assert any(issue['code'] == 'AUTO_ASSIGNMENT_CONFLICT' for issue in doc['assignment_report']['issues'])


def test_manual_same_value_role_reference_clear_and_exclusion_are_protected(energy_api):
    client, _ = energy_api
    doc = create_typed(client)
    for comp, value in [({'Pt': 4}, -100), ({'C': 1, 'O': 1}, -10), ({'Pt': 4, 'C': 1, 'O': 1}, -112)]:
        doc = manual(client, doc, 'OUTCAR', comp, value)
    doc = inferred(client, doc)
    rows = sample_rows(doc)
    for row in rows:
        row.update(role_origin='manual', included_origin='manual')
    group = doc['groups'][0]
    group['reference_origins'].update(clean_sample_id='manual', adsorbate_sample_id='manual')
    doc = configure(client, doc, [group], rows)
    doc = manual(client, doc, 'OUTCAR', {'Pt': 4}, -999)
    rows = sample_rows(doc)
    rows[-1].update(included=False, included_origin='manual')
    doc = configure(client, doc, doc['groups'], rows)
    doc = inferred(client, doc)
    assert doc['groups'][0]['clean_sample_id'] == doc['samples'][0]['id']
    assert all(s['role_origin'] == 'manual' for s in doc['samples'][:3])
    assert not doc['samples'][-1]['included'] and doc['samples'][-1]['included_origin'] == 'manual'
    group = doc['groups'][0]
    group['clean_sample_id'] = None
    doc = configure(client, doc, [group], sample_rows(doc))
    doc = inferred(client, doc)
    assert doc['groups'][0]['clean_sample_id'] is None
    assert doc['groups'][0]['reference_origins']['clean_sample_id'] == 'manual'
    assert doc['groups'][0]['targets'][0]['sample_id'] == doc['samples'][2]['id']
    assert all(s['role_origin'] == 'manual' and s['included'] for s in doc['samples'][:3])


def test_molecular_unit_not_reduced_and_manual_m_n_preserved(energy_api):
    client, _ = energy_api
    doc = create_typed(client)
    for comp, value in [({'Pt': 4}, -100), ({'O': 2}, -10), ({'Pt': 4, 'O': 1}, -112)]:
        doc = manual(client, doc, 'OUTCAR', comp, value)
    doc = inferred(client, doc)
    assert doc['assignment_report']['state'] == 'pending'
    assert doc['groups'][0]['reference_units'] == 1 and not doc['groups'][0]['targets']
    group = doc['groups'][0]
    group.update(clean_sample_id=doc['samples'][0]['id'], adsorbate_sample_id=doc['samples'][1]['id'], reference_units=2,
                 targets=[{'sample_id': doc['samples'][2]['id'], 'adsorbate_count': 1}])
    rows = sample_rows(doc)
    for row, role in zip(rows, ['clean_slab', 'adsorbate', 'adsorbed']):
        row.update(role=role, included=True)
    doc = configure(client, doc, [group], rows)
    doc = inferred(client, doc)
    assert doc['groups'][0]['reference_units'] == 2 and doc['groups'][0]['targets'][0]['adsorbate_count'] == 1
    group = doc['groups'][0]
    group['targets'][0]['adsorbate_count'] = 2
    doc = configure(client, doc, [group], sample_rows(doc))
    doc = inferred(client, doc)
    assert doc['groups'][0]['targets'][0]['adsorbate_count'] == 2
    assert any(issue['code'] == 'MANUAL_COUNT_CONFLICT' for issue in doc['assignment_report']['issues'])


def test_hard_source_missing_composition_and_conflicting_hints_remain_pending(energy_api):
    client, app = energy_api
    doc = create_typed(client)
    for name, comp in [('clean molecule', {'Pt': 4}), ('gas', {'C': 1, 'O': 1}), ('target', {'Pt': 4, 'C': 1, 'O': 1})]:
        doc = manual(client, doc, name, comp, -10)
    doc['samples'][1]['parsed']['metadata']['parameters'] = {'LSORBIT': True}
    doc['samples'][2]['parsed']['composition'] = {}
    app.state.energy.save(doc)
    doc = inferred(client, doc)
    codes = {issue['code'] for issue in doc['assignment_report']['issues']}
    assert {'ROLE_EVIDENCE_CONFLICT', 'SOURCE_HARD_ERROR', 'COMPOSITION_REQUIRED'} <= codes
    assert all(s['role'] is None and not s['accepted_warnings'] for s in doc['samples'])


def test_target_removal_keeps_reference_and_unrelated_confirmation(energy_api):
    client, _ = energy_api
    doc = adsorption_review(client)
    removed = doc['samples'][2]['id']
    preview = removal(client, doc, [removed], preview=True)
    assert preview.status_code == 200 and preview.json()['removal']['removed_count'] == 1
    assert preview.json()['removal']['cleared_reference_keys'] == []
    response = removal(client, doc, [removed])
    assert response.status_code == 200, response.text
    current = response.json()['collection']
    assert current['groups'][0]['targets'] == [] and current['groups'][0]['basis_confirmed']
    assert all(s['confirmed'] and s['accepted_warnings'] for s in current['samples'])
    assert current['result'] is None


def test_reference_removal_clears_binding_and_blocks_future_auto_replacement(energy_api):
    client, _ = energy_api
    doc = create_typed(client)
    for comp, value in [({'Pt': 4}, -100), ({'C': 1, 'O': 1}, -10), ({'Pt': 4, 'C': 1, 'O': 1}, -112)]:
        doc = manual(client, doc, 'OUTCAR', comp, value)
    doc = review(client, inferred(client, doc))
    reference, target = doc['samples'][0]['id'], doc['samples'][2]['id']
    preview = removal(client, doc, [reference], preview=True).json()['removal']
    assert preview['affected_target_ids'] == [target] and preview['cleared_reference_keys'] == ['analysis:clean_sample_id']
    response = removal(client, doc, [reference])
    assert response.status_code == 200, response.text
    doc = response.json()['collection']
    assert doc['groups'][0]['clean_sample_id'] is None and not doc['groups'][0]['basis_confirmed']
    assert not next(s for s in doc['samples'] if s['id'] == target)['confirmed']
    assert all(s['accepted_warnings'] for s in doc['samples'])
    doc = manual(client, doc, 'OUTCAR', {'Pt': 4}, -100)
    doc = inferred(client, doc)
    assert doc['groups'][0]['clean_sample_id'] is None
    assert doc['groups'][0]['reference_origins']['clean_sample_id'] == 'manual'


def test_removal_lock_revision_validation_atomicity_and_clear_preserves_owned_sources(energy_api, monkeypatch):
    client, app = energy_api
    doc = imported_batch(client, app, monkeypatch, [({'Pt': 4}, -100), ({'C': 1, 'O': 1}, -10), ({'Pt': 4, 'C': 1, 'O': 1}, -112)])
    doc = review(client, inferred(client, doc))
    svc = app.state.energy
    files = {path.name: path.read_bytes() for path in svc.directory(doc['id']).glob('*.bin')}
    locked = success(action(client, doc, 'lock'))
    error(removal(client, locked, [locked['samples'][0]['id']]), 'ENERGY_LOCKED')
    error(action(client, locked, 'autofill'), 'ENERGY_LOCKED')
    error(removal(client, doc, [doc['samples'][0]['id']], preview=True), 'ENERGY_REVISION_CONFLICT')
    doc = success(action(client, locked, 'unlock'))
    before = copy.deepcopy(doc)
    sid = doc['samples'][0]['id']
    for ids in [[sid, sid], ['missing']]:
        assert removal(client, doc, ids).status_code == 400
        assert svc.read(doc['id']) == before
    original_write = svc.write_json
    def broken_write(path, value):
        raise ToolboxError('ENERGY_DISK_FULL', 'synthetic atomic save failure', status=413)
    monkeypatch.setattr(svc, 'write_json', broken_write)
    assert removal(client, doc, [sid]).status_code == 413
    assert svc.read(doc['id']) == before
    monkeypatch.setattr(svc, 'write_json', original_write)
    response = removal(client, doc, clear_all=True)
    assert response.status_code == 200, response.text
    cleared = response.json()['collection']
    assert cleared['samples'] == [] and cleared['groups'][0]['targets'] == []
    assert cleared['groups'][0]['clean_sample_id'] is None and cleared['groups'][0]['adsorbate_sample_id'] is None
    assert {path.name: path.read_bytes() for path in svc.directory(doc['id']).glob('*.bin')} == files
    for comp, value in [({'Pt': 4}, -100), ({'C': 1, 'O': 1}, -10), ({'Pt': 4, 'C': 1, 'O': 1}, -112)]:
        cleared = manual(client, cleared, 'OUTCAR', comp, value)
    cleared = inferred(client, cleared)
    assert cleared['assignment_report']['state'] == 'ready'


def test_copy_pre_provenance_record_preserves_manual_references_after_new_candidate(energy_api):
    client, app = energy_api
    doc = create_typed(client, 'formation')
    for comp, value in [({'Al': 1, 'O': 1}, -15), ({'Al': 2}, -8), ({'O': 2}, -10)]:
        doc = manual(client, doc, 'OUTCAR', comp, value)
    rows = sample_rows(doc)
    for i, row in enumerate(rows):
        row.update(role='material' if i == 0 else 'element_reference', included=True)
    ids = [sample['id'] for sample in doc['samples']]
    group = {'id': 'legacy', 'name': '原人工参考', 'kind': 'formation', 'energy_basis': BASIS,
             'element_references': {'Al': ids[1], 'O': ids[2]}, 'targets': [{'sample_id': ids[0]}]}
    doc = configure(client, doc, [group], rows)
    # Simulate a first-batch persisted record, before provenance fields existed.
    doc['groups'][0].pop('reference_origins')
    path = app.state.energy.directory(doc['id']) / 'metadata.json'
    app.state.energy.write_json(path, doc)
    original = path.read_bytes()
    projected = app.state.energy.read(doc['id'])
    assert projected['groups'][0]['reference_origins']['element:Al'] == 'manual'
    assert path.read_bytes() == original
    response = client.post(f"{BASE}/collections/{doc['id']}/copy", json={
        'expected_revision': doc['revision'], 'analysis_kind': 'formation', 'group_id': 'legacy'})
    assert response.status_code == 201, response.text
    copied = response.json()['collection']
    refs = copy.deepcopy(copied['groups'][0]['element_references'])
    copied = manual(client, copied, 'OUTCAR', {'Al': 1}, -999)
    copied = inferred(client, copied)
    assert copied['groups'][0]['element_references'] == refs
    assert copied['groups'][0]['reference_origins']['element:Al'] == 'manual'
    assert path.read_bytes() == original
    # An explicitly empty map is current auto-capable state, not a legacy signal.
    doc['groups'][0]['reference_origins'] = {}
    app.state.energy.write_json(path, doc)
    assert app.state.energy.read(doc['id'])['groups'][0]['reference_origins'] == {}
