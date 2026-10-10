"""PP22-MC independent cards and atomic shared edits; synthetic sources only."""
import copy
import json

import pytest

from backend.tests.test_postprocessing_energy import BASE, BASIS, energy_api, manual
from backend.tests.test_energy_review_flow import adsorption_review
from backend.toolbox.postprocessing.energy.store import EnergyStore


def success(response, status=200):
    assert response.status_code == status, response.text
    return response.json()['collection']


def create(client, kind='adsorption'):
    return success(client.post(BASE + '/collections', json={
        'title': '多卡合成', 'analysis_kind': kind, 'workflow': 'cards'}), 201)


def path(doc, suffix=''):
    return f"{BASE}/collections/{doc['id']}" + suffix


def config(ident, clean, ref, targets):
    return {'id': ident, 'name': ident, 'kind': 'adsorption', 'energy_basis': BASIS,
            'clean_sample_id': clean, 'adsorbate_sample_id': ref, 'reference_units': 1,
            'targets': [{'sample_id': sid, 'adsorbate_count': 1} for sid in targets]}


def add(client, doc, card):
    return success(client.post(path(doc, '/cards'), json={'expected_revision': doc['revision'], 'card': card}), 201)


def action(client, doc, card_id, verb, **extra):
    return client.post(path(doc, f'/cards/{card_id}/{verb}'), json={'expected_revision': doc['revision'], **extra})


def card(doc, ident):
    return next(row for row in doc['groups'] if row['id'] == ident)


def ready(client, doc, ident):
    doc = success(action(client, doc, ident, 'lock', accepted_warnings=True))
    return success(action(client, doc, ident, 'calculate'))


def adsorption_cards(client):
    doc = create(client)
    for name, comp, energy in [('cleanA', {'Pt': 4}, -100), ('COA', {'C': 1, 'O': 1}, -10),
                              ('targetA', {'Pt': 4, 'C': 1, 'O': 1}, -112),
                              ('targetA2', {'Pt': 4, 'C': 1, 'O': 1}, -111),
                              ('cleanB', {'Pt': 4}, -90), ('COB', {'C': 1, 'O': 1}, -9),
                              ('targetB', {'Pt': 4, 'C': 1, 'O': 1}, -101)]:
        doc = manual(client, doc, name, comp, energy)
    ids = [sample['id'] for sample in doc['samples']]
    doc = add(client, doc, config('A', ids[0], ids[1], ids[2:4]))
    doc = add(client, doc, config('B', ids[4], ids[5], [ids[6]]))
    doc = add(client, doc, config('C', ids[0], ids[1], [ids[2]]))
    return doc


def editable_config(row):
    from backend.toolbox.postprocessing.energy.cards import CONFIG
    return {key: copy.deepcopy(row.get(key)) for key in CONFIG}


def test_card_independence_multiple_targets_copy_delete_rename_unlock_and_global_guards(energy_api):
    client, app = energy_api
    doc = adsorption_cards(client)
    assert all(sample['role'] is None and not sample['included'] and not sample['confirmed'] for sample in doc['samples'])
    doc = ready(client, doc, 'A')
    assert [row['delta_ev'] for row in card(doc, 'A')['result']['groups'][0]['rows']] == [-2, -1]
    original = copy.deepcopy(card(doc, 'A'))
    doc = manual(client, doc, '追加无关来源', {'He': 1}, -1)
    assert card(doc, 'A') == original
    b = editable_config(card(doc, 'B'))
    b['reference_units'] = 2
    doc = success(client.put(path(doc, '/cards/B'), json={'expected_revision': doc['revision'], 'card': b}))
    assert card(doc, 'A') == original and card(doc, 'B')['status'] == 'stale'
    a = editable_config(card(doc, 'A'))
    note_change = {**a, 'reference_note': '改变参考单元定义'}
    denied = client.put(path(doc, '/cards/A'), json={'expected_revision': doc['revision'], 'card': note_change})
    assert denied.status_code == 409 and denied.json()['error']['code'] == 'ENERGY_CARD_LOCKED'
    a['name'] = '名' * 120
    doc = success(client.put(path(doc, '/cards/A'), json={'expected_revision': doc['revision'], 'card': a}))
    assert card(doc, 'A')['locked'] and card(doc, 'A')['confirmation_fingerprint'] == original['confirmation_fingerprint']
    doc = success(action(client, doc, 'A', 'unlock'))
    assert card(doc, 'A')['confirmed'] and card(doc, 'A')['result'] and not card(doc, 'A')['locked']
    assert client.get(path(doc, '/cards/A/export')).status_code == 200
    patch = [{'sample_id': doc['samples'][2]['id'], 'name': '构型重命名'}]
    doc = success(client.put(path(doc, '/samples/configuration'), json={'expected_revision': doc['revision'], 'samples': patch, 'title': '新标题'}))
    assert card(doc, 'A')['confirmed'] and card(doc, 'A')['result']['groups'][0]['rows'][0]['name'] == '构型重命名'
    assert doc['title'] == '新标题'
    for verb in ('lock', 'unlock', 'calculate', 'autofill'):
        response = client.post(path(doc, '/' + verb), json={'expected_revision': doc['revision']})
        assert response.status_code == 409 and response.json()['error']['code'] == 'ENERGY_CARD_WORKFLOW_REQUIRED'
    response = client.put(path(doc, '/configuration'), json={'expected_revision': doc['revision'], 'samples': []})
    assert response.status_code == 409
    assert client.get(path(doc)).json()['collection'] == doc
    copied = success(action(client, doc, 'A', 'copy'), 201)
    duplicate = copied['groups'][-1]
    assert duplicate['id'] != 'A' and not duplicate['confirmed'] and not duplicate['locked'] and duplicate['result'] is None
    assert len(duplicate['name']) == 120 and duplicate['name'] == '名' * 117 + ' 副本'
    before_samples = copy.deepcopy(copied['samples'])
    doc = success(client.request('DELETE', path(copied, '/cards/' + duplicate['id']), json={'expected_revision': copied['revision']}))
    assert doc['samples'] == before_samples and len(doc['groups']) == 3
    assert EnergyStore(app.state.toolbox.root).read(doc['id']) == doc


def test_shared_change_requires_bound_preview_ack_and_only_invalidates_dependents(energy_api):
    client, app = energy_api
    doc = adsorption_cards(client)
    for ident in ('A', 'B', 'C'):
        doc = ready(client, doc, ident)
    original = copy.deepcopy(doc)
    sample_id = doc['samples'][0]['id']
    patches = [{'sample_id': sample_id, 'override': {'energy_fields': {BASIS: -101}, 'energy_basis': BASIS, 'unit': 'eV', 'note': '合成复核'}}]
    body = {'expected_revision': doc['revision'], 'samples': patches}
    impact_response = client.post(path(doc, '/samples/change-preview'), json=body)
    assert impact_response.status_code == 200, impact_response.text
    impact = impact_response.json()['impact']
    assert [(row['card_id'], row['locked']) for row in impact['affected_cards']] == [('A', True), ('C', True)]
    assert impact['requires_acknowledgement'] and app.state.energy.read(doc['id']) == original
    denied = client.put(path(doc, '/samples/configuration'), json=body)
    assert denied.status_code == 409 and denied.json()['error']['code'] == 'ENERGY_IMPACT_ACK_REQUIRED'
    altered = copy.deepcopy(body)
    altered['samples'][0]['override']['note'] = '提交改变了预览内容'
    denied = client.put(path(doc, '/samples/configuration'), json={**altered, 'preview_id': impact['preview_id'], 'acknowledge_locked_cards': True})
    assert denied.json()['error']['code'] == 'ENERGY_IMPACT_PREVIEW_INVALID'
    denied = client.put(path(doc, '/samples/configuration'), json={**body, 'expected_revision': doc['revision'] - 1,
        'preview_id': impact['preview_id'], 'acknowledge_locked_cards': True})
    assert denied.json()['error']['code'] == 'ENERGY_REVISION_CONFLICT'
    assert app.state.energy.read(doc['id']) == original
    doc = success(client.put(path(doc, '/samples/configuration'), json={**body, 'preview_id': impact['preview_id'], 'acknowledge_locked_cards': True}))
    assert card(doc, 'B') == card(original, 'B')
    for ident in ('A', 'C'):
        assert card(doc, ident)['status'] == 'stale' and not card(doc, ident)['locked'] and card(doc, ident)['result'] is None
        assert client.get(path(doc, f'/cards/{ident}/export')).status_code == 409
    assert client.get(path(doc, '/cards/B/export?format=csv')).status_code == 200
    doc = ready(client, doc, 'A')
    assert card(doc, 'A')['result']['groups'][0]['rows'][0]['delta_ev'] == -1


def test_removal_impact_atomicity_and_preserves_other_card_sources(energy_api):
    client, app = energy_api
    doc = ready(client, adsorption_cards(client), 'A')
    doc = ready(client, doc, 'B')
    before = copy.deepcopy(doc)
    body = {'expected_revision': doc['revision'], 'sample_ids': [doc['samples'][0]['id']], 'workflow': 'cards'}
    preview = client.post(path(doc, '/samples/removal-preview'), json=body).json()['removal']
    assert preview['impact']['requires_acknowledgement']
    denied = client.post(path(doc, '/samples/remove'), json=body)
    assert denied.status_code == 409 and app.state.energy.read(doc['id']) == before
    response = client.post(path(doc, '/samples/remove'), json={**body, 'preview_id': preview['impact']['preview_id'], 'acknowledge_locked_cards': True})
    doc = success(response)
    assert card(doc, 'A')['clean_sample_id'] is None and card(doc, 'A')['status'] == 'stale'
    assert card(doc, 'B') == card(before, 'B') and len(doc['samples']) == len(before['samples']) - 1
    assert card(doc, 'C')['clean_sample_id'] is None and len(doc['groups']) == 3


def test_card_risk_acceptance_structured_validation_and_hard_errors(energy_api):
    client, app = energy_api
    doc = adsorption_cards(client)
    failed = action(client, doc, 'A', 'lock')
    assert failed.status_code == 409 and failed.json()['error']['field_errors'][0]['card_id'] == 'A'
    assert failed.json()['error']['field_errors'][0]['field'] == 'accepted_warnings'
    config_a = editable_config(card(doc, 'A'))
    for reference_key in ('clean_sample_id', 'adsorbate_sample_id'):
        response = client.put(path(doc, '/cards/A'), json={'expected_revision': doc['revision'],
            'card': {**config_a, reference_key: 'missing'}})
        assert response.status_code == 400 and response.json()['error']['field_errors'][0]['field'] == reference_key
    sample_id = doc['samples'][0]['id']
    response = client.put(path(doc, '/samples/configuration'), json={'expected_revision': doc['revision'], 'samples': [
        {'sample_id': sample_id, 'override': {'energy_fields': {BASIS: -100}, 'unit': 'eV', 'note': '缺失口径'}}]})
    assert response.status_code == 400
    error = response.json()['error']['field_errors'][0]
    assert error['sample_id'] == sample_id and error['field'] == 'samples.' + sample_id + '.override.energy_fields'
    sid = config_a['targets'][0]['sample_id']
    config_a['targets'][0]['adsorbate_count'] = 0
    response = client.put(path(doc, '/cards/A'), json={'expected_revision': doc['revision'], 'card': config_a})
    error = response.json()['error']['field_errors'][0]
    assert response.status_code == 422 and error['card_id'] == 'A' and error['sample_id'] == sid
    assert error['field'] == 'targets.' + sid + '.adsorbate_count'
    config_a['targets'][0]['adsorbate_count'] = 2
    doc = success(client.put(path(doc, '/cards/A'), json={'expected_revision': doc['revision'], 'card': config_a}))
    failed = action(client, doc, 'A', 'lock', accepted_warnings=True)
    error = failed.json()['error']['field_errors'][0]
    assert failed.status_code == 400 and error['sample_id'] == sid and error['field'].endswith('.adsorbate_count')
    config_a['targets'][0]['adsorbate_count'] = 1
    doc = success(client.put(path(doc, '/cards/A'), json={'expected_revision': doc['revision'], 'card': config_a}))
    doc['samples'][0]['parsed']['metadata']['support'] = {'automatic_comparison': False, 'reasons': ['UNSUPPORTED_SYNTHETIC_METHOD']}
    app.state.energy.save(doc)
    failed = action(client, doc, 'A', 'lock', accepted_warnings=True)
    assert failed.status_code == 400 and failed.json()['error']['code'] == 'ENERGY_SOURCE_UNSUPPORTED'
    assert not app.state.energy.read(doc['id'])['groups'][0]['locked']


def test_invalid_json_shapes_remain_structured_422_instead_of_server_errors(energy_api):
    client, _ = energy_api
    doc = create(client)
    for body in ([], None, 1, 'invalid', {'expected_revision': doc['revision'], 'card': None},
                 {'expected_revision': doc['revision'], 'card': {'id': 'bad', 'name': 'bad',
                   'kind': 'adsorption', 'energy_basis': BASIS, 'targets': [None, 'bad']}}):
        response = client.post(path(doc, '/cards'), content=json.dumps(body), headers={'Content-Type': 'application/json'})
        assert response.status_code == 422, response.text
        assert response.json()['error']['field_errors']
    response = client.put(path(doc, '/samples/configuration'), json={'expected_revision': doc['revision'], 'samples': [None, 'invalid']})
    assert response.status_code == 422 and len(response.json()['error']['field_errors']) == 2
    assert client.get(path(doc)).json()['collection'] == doc


def test_empty_card_draft_create_save_defers_required_refs_but_rejects_unknown_ids_and_duplicates(energy_api):
    client, _ = energy_api
    doc = create(client)
    cfg = {'id': 'empty', 'name': '空白卡', 'kind': 'adsorption', 'energy_basis': BASIS,
           'clean_sample_id': '', 'adsorbate_sample_id': '', 'reference_units': 1, 'targets': []}
    doc = add(client, doc, cfg)
    assert card(doc, 'empty')['clean_sample_id'] is None and card(doc, 'empty')['adsorbate_sample_id'] is None
    cfg['clean_sample_id'] = None
    doc = success(client.put(path(doc, '/cards/empty'), json={'expected_revision': doc['revision'], 'card': cfg}))
    failed = action(client, doc, 'empty', 'lock', accepted_warnings=True)
    assert failed.status_code == 400 and failed.json()['error']['field_errors'][0]['field'] == 'targets'
    assert client.get(path(doc)).json()['collection'] == doc
    doc = manual(client, doc, '尚缺参考的目标', {'Pt': 4, 'C': 1, 'O': 1}, -112)
    sid = doc['samples'][0]['id']
    cfg['targets'] = [{'sample_id': sid, 'adsorbate_count': 1}]
    doc = success(client.put(path(doc, '/cards/empty'), json={'expected_revision': doc['revision'], 'card': cfg}))
    failed = action(client, doc, 'empty', 'lock', accepted_warnings=True)
    assert failed.status_code == 400 and failed.json()['error']['code'] == 'ENERGY_REFERENCE_REQUIRED'
    assert failed.json()['error']['field_errors'][0]['card_id'] == 'empty'
    assert failed.json()['error']['field_errors'][0]['field'] == 'clean_sample_id'
    assert action(client, doc, 'empty', 'calculate').json()['error']['code'] == 'ENERGY_CARD_LOCK_REQUIRED'
    for invalid in ({**cfg, 'clean_sample_id': 'unknown_nonempty'},
                    {**cfg, 'targets': cfg['targets'] * 2}):
        failed = client.put(path(doc, '/cards/empty'), json={'expected_revision': doc['revision'], 'card': invalid})
        assert failed.status_code == 400 and client.get(path(doc)).json()['collection'] == doc
    formation = create(client, 'formation')
    formation = add(client, formation, {'id': 'empty_element', 'name': '空元素参考', 'kind': 'formation',
                                       'energy_basis': BASIS, 'element_references': {'Al': ''}})
    assert formation['groups'][0]['element_references'] == {}


def test_card_autofill_preserves_manual_partial_and_empty_target_selection(energy_api):
    client, _ = energy_api
    doc = create(client)
    for name, comp, energy in [('clean slab', {'Pt': 4}, -100), ('CO molecule', {'C': 1, 'O': 1}, -10),
                              ('adsorbed A', {'Pt': 4, 'C': 1, 'O': 1}, -112),
                              ('adsorbed B', {'Pt': 4, 'C': 1, 'O': 1}, -111)]:
        doc = manual(client, doc, name, comp, energy)
    doc = add(client, doc, {'id': 'auto', 'name': '初始建议', 'kind': 'adsorption', 'energy_basis': BASIS})
    doc = success(action(client, doc, 'auto', 'autofill'))
    assert len(card(doc, 'auto')['targets']) == 2
    cfg = editable_config(card(doc, 'auto'))
    cfg['targets'] = cfg['targets'][:1]
    doc = success(client.put(path(doc, '/cards/auto'), json={'expected_revision': doc['revision'], 'card': cfg}))
    doc = success(action(client, doc, 'auto', 'autofill'))
    assert card(doc, 'auto')['targets'] == cfg['targets']
    cfg = editable_config(card(doc, 'auto'))
    cfg['targets'] = []
    doc = success(client.put(path(doc, '/cards/auto'), json={'expected_revision': doc['revision'], 'card': cfg}))
    doc = success(action(client, doc, 'auto', 'autofill'))
    assert card(doc, 'auto')['targets'] == [] and card(doc, 'auto')['reference_origins']['targets'] == 'manual'


def test_legacy_projection_migration_retains_only_verified_evidence_and_backup(energy_api):
    client, app = energy_api
    doc = adsorption_review(client)
    doc = success(client.post(path(doc, '/lock'), json={'expected_revision': doc['revision']}))
    doc = success(client.post(path(doc, '/calculate'), json={'expected_revision': doc['revision']}))
    svc = app.state.energy
    metadata = svc.directory(doc['id']) / 'metadata.json'
    original = metadata.read_bytes()
    projected = svc.read(doc['id'])
    assert projected['card_projection'][0]['locked'] and projected['card_projection'][0]['result']
    assert metadata.read_bytes() == original and not metadata.with_name('metadata.pp.energy.v1.backup.json').exists()
    doc = success(action(client, projected, 'ads', 'unlock'))
    assert doc['schema_version'] == 'pp.energy.v2' and doc['workflow'] == 'cards'
    assert doc['groups'][0]['confirmed'] and doc['groups'][0]['result'] and not doc['groups'][0]['locked']
    assert metadata.with_name('metadata.pp.energy.v1.backup.json').read_bytes() == original
    assert client.get(path(doc, '/cards/ads/export')).status_code == 200
    # A stale old proof cannot be converted into fresh card confirmation.
    old = json.loads(original)
    old['samples'][0]['confirmation_fingerprint'] = 'invalid'
    old['id'] = svc.create('失效旧记录', 'adsorption')['id']
    stale_path = svc.directory(old['id']) / 'metadata.json'
    svc.write_json(stale_path, old)
    stale = svc.read(old['id'])
    assert not stale['card_projection'][0]['confirmed'] and stale['card_projection'][0]['status'] == 'stale'
    assert stale['card_projection'][0]['result'] is None


def test_legacy_multicard_and_mixed_read_only_explicit_typed_copy(energy_api):
    client, app = energy_api
    doc = adsorption_review(client)
    raw = json.loads((app.state.energy.directory(doc['id']) / 'metadata.json').read_text(encoding='utf-8'))
    raw['groups'].append({**copy.deepcopy(raw['groups'][0]), 'id': 'second', 'name': '同类型旧卡'})
    app.state.energy.write_json(app.state.energy.directory(doc['id']) / 'metadata.json', raw)
    old = app.state.energy.read(doc['id'])
    assert len(old['card_projection']) == 2 and not old['card_migration']['read_only']
    migrated = success(action(client, old, 'ads', 'unlock'))
    assert [row['id'] for row in migrated['groups']] == ['ads', 'second']
    raw['groups'].append({'id': 'form', 'name': '旧形成', 'kind': 'formation', 'energy_basis': BASIS,
                          'basis_confirmed': False, 'element_references': {}, 'targets': []})
    app.state.energy.write_json(app.state.energy.directory(doc['id']) / 'metadata.json', raw)
    original = (app.state.energy.directory(doc['id']) / 'metadata.json').read_bytes()
    mixed = app.state.energy.read(doc['id'])
    assert mixed['card_migration']['read_only'] and len(mixed['groups']) == 3
    denied = action(client, mixed, 'ads', 'unlock')
    assert denied.status_code == 409 and denied.json()['error']['code'] == 'ENERGY_LEGACY_READ_ONLY'
    copied = success(client.post(path(mixed, '/copy'), json={'expected_revision': mixed['revision'],
        'analysis_kind': 'adsorption', 'workflow': 'cards'}), 201)
    assert copied['id'] != mixed['id'] and len(copied['groups']) == 2 and len(copied['samples']) == 3
    assert all(not row['confirmed'] and not row['locked'] and row['result'] is None for row in copied['groups'])
    assert (app.state.energy.directory(doc['id']) / 'metadata.json').read_bytes() == original


def test_first_card_write_failure_keeps_legacy_original_and_recoverable_backup(energy_api, monkeypatch):
    from backend.toolbox.contracts import ToolboxError
    client, app = energy_api
    doc = adsorption_review(client)
    metadata = app.state.energy.directory(doc['id']) / 'metadata.json'
    original = metadata.read_bytes()
    def fail_write(*args):
        raise ToolboxError('ENERGY_DISK_FULL', '合成磁盘不足', 413)
    monkeypatch.setattr(app.state.energy, 'write_json', fail_write)
    denied = action(client, doc, 'ads', 'unlock')
    assert denied.status_code == 413 and metadata.read_bytes() == original
    assert metadata.with_name('metadata.pp.energy.v1.backup.json').read_bytes() == original
    restored = app.state.energy.read(doc['id'])
    assert restored['schema_version'] == 'pp.energy.v1' and restored['revision'] == doc['revision']


def test_explicit_typed_copy_preserves_unbound_legacy_pool_without_guessing_cards(energy_api):
    client, app = energy_api
    doc = success(client.post(BASE + '/collections', json={'title': '旧未定类型样本池'}), 201)
    doc = manual(client, doc, '未绑定样本', {'C': 1, 'O': 1}, -10)
    metadata = app.state.energy.directory(doc['id']) / 'metadata.json'
    original = metadata.read_bytes()
    assert doc['card_migration']['reason'] == 'type_required' and not doc['groups']
    copied = success(client.post(path(doc, '/copy'), json={'expected_revision': doc['revision'],
        'analysis_kind': 'adsorption', 'workflow': 'cards'}), 201)
    assert copied['schema_version'] == 'pp.energy.v2' and copied['analysis_kind'] == 'adsorption'
    assert len(copied['samples']) == 1 and not copied['groups'] and copied['result'] is None
    assert copied['samples'][0]['parsed'] == doc['samples'][0]['parsed']
    assert copied['samples'][0]['id'] != doc['samples'][0]['id'] and not copied['samples'][0]['confirmed']
    assert metadata.read_bytes() == original


def test_formation_cards_share_references_and_unready_sibling_does_not_block_formula(energy_api):
    client, _ = energy_api
    doc = create(client, 'formation')
    for name, comp, energy in [('Al2O', {'Al': 2, 'O': 1}, -20), ('Al胞', {'Al': 2}, -8), ('O2', {'O': 2}, -10)]:
        doc = manual(client, doc, name, comp, energy)
    ids = [sample['id'] for sample in doc['samples']]
    cfg = {'id': 'formationA', 'name': '形成A', 'kind': 'formation', 'energy_basis': BASIS,
           'element_references': {'Al': ids[1], 'O': ids[2]}, 'targets': [{'sample_id': ids[0]}]}
    doc = add(client, doc, cfg)
    doc = add(client, doc, {**cfg, 'id': 'formationB', 'element_references': {}})
    before = copy.deepcopy(doc)
    doc = ready(client, doc, 'formationA')
    row = card(doc, 'formationA')['result']['groups'][0]['rows'][0]
    assert row['delta_ev'] == -7 and row['normalized_ev'] == pytest.approx(-7 / 3)
    assert card(doc, 'formationB') == card(before, 'formationB')
    conflict = {'id': 'bad', 'name': '错类型', 'kind': 'adsorption', 'energy_basis': BASIS}
    response = client.post(path(doc, '/cards'), json={'expected_revision': doc['revision'], 'card': conflict})
    assert response.status_code == 400 and response.json()['error']['field_errors'][0]['field'] == 'kind'
    assert client.get(path(doc)).json()['collection'] == doc
    bad_refs = editable_config(card(doc, 'formationB'))
    bad_refs['element_references'] = {'Al': 'missing'}
    response = client.put(path(doc, '/cards/formationB'), json={'expected_revision': doc['revision'], 'card': bad_refs})
    assert response.status_code == 400 and response.json()['error']['field_errors'][0]['field'] == 'element_references.Al'
