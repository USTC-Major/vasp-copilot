"""Independent energy tasks: metadata-only rename and bounded owned-cache deletion."""
import copy
import json
import os
import subprocess
import uuid

import pytest

from backend.tests.test_energy_cards import (
    action, adsorption_cards, card, create, editable_config, path, ready, success,
)
from backend.tests.test_energy_review_flow import adsorption_review
from backend.tests.test_postprocessing_energy import BASE, energy_api, manual
from backend.toolbox.postprocessing.energy.calculation import input_fingerprint, legacy_confirmation
from backend.toolbox.postprocessing.energy.store import EnergyStore


def metadata(app, doc):
    return app.state.energy.directory(doc['id']) / 'metadata.json'


def unchanged_science(before, after):
    ignored = {'title', 'revision', 'updated_at'}
    assert {key: value for key, value in after.items() if key not in ignored} == {
        key: value for key, value in before.items() if key not in ignored}


def test_task_rename_retains_independent_locked_cards_results_and_list_counts(energy_api):
    client, app = energy_api
    doc = ready(client, adsorption_cards(client), 'A')
    doc = ready(client, doc, 'B')
    other = manual(client, create(client, 'formation'), '另一任务材料', {'Al': 1}, -3)
    other_metadata = metadata(app, other).read_bytes()
    before = copy.deepcopy(doc)
    raw_before = json.loads(metadata(app, doc).read_text(encoding='utf-8'))
    doc = success(client.put(path(doc, '/title'), json={
        'expected_revision': doc['revision'], 'title': '  独立吸附任务  '}))
    assert doc['title'] == '独立吸附任务' and doc['revision'] == before['revision'] + 1
    assert doc['updated_at'] >= before['updated_at']
    unchanged_science(before, doc)
    unchanged_science(raw_before, json.loads(metadata(app, doc).read_text(encoding='utf-8')))
    assert card(doc, 'A')['locked'] and card(doc, 'B')['locked']
    for card_id in ('A', 'B'):
        exported = client.get(path(doc, f'/cards/{card_id}/export'))
        assert exported.status_code == 200 and exported.json()['collection']['title'] == doc['title']
    assert EnergyStore(app.state.toolbox.root).read(doc['id']) == doc
    denied = client.put(path(doc, '/title'), json={'expected_revision': before['revision'], 'title': '过期改名'})
    assert denied.status_code == 409 and denied.json()['error']['code'] == 'ENERGY_REVISION_CONFLICT'
    assert client.get(path(doc)).json()['collection'] == doc
    listed = {row['id']: row for row in client.get(BASE + '/collections').json()['collections']}
    assert set(listed) == {doc['id'], other['id']}
    assert listed[doc['id']]['sample_count'] == 7 and listed[doc['id']]['card_count'] == 3
    assert listed[other['id']]['sample_count'] == 1 and listed[other['id']]['card_count'] == 0
    assert listed[other['id']]['analysis_kind'] == 'formation'
    assert metadata(app, other).read_bytes() == other_metadata


@pytest.mark.parametrize('title', ['', '   ', '名' * 121, '含\n换行', '含\x7f控制符', 123])
def test_task_rename_invalid_title_is_field_located_and_atomic(energy_api, title):
    client, app = energy_api
    doc = create(client)
    original = metadata(app, doc).read_bytes()
    denied = client.put(path(doc, '/title'), json={'expected_revision': doc['revision'], 'title': title})
    assert denied.status_code == 422
    assert denied.json()['error']['field_errors'][0]['field'] == 'title'
    assert metadata(app, doc).read_bytes() == original


def test_legacy_rename_changes_only_persisted_metadata_and_preserves_old_result(energy_api):
    client, app = energy_api
    doc = adsorption_review(client)
    doc = success(client.post(path(doc, '/lock'), json={'expected_revision': doc['revision']}))
    doc = success(client.post(path(doc, '/calculate'), json={'expected_revision': doc['revision']}))
    raw = json.loads(metadata(app, doc).read_text(encoding='utf-8'))
    # Reproduce a pre-review record that only receives compatibility fields on read.
    for key in ('legacy_mode', 'analysis_kind', 'locked', 'lock_fingerprint', 'assignment_report'):
        raw.pop(key, None)
    for group in raw['groups']:
        group.pop('reference_origins', None)
    for sample in raw['samples']:
        sample['confirmation_fingerprint'] = legacy_confirmation(sample, raw['groups'])
        for key in ('warning_acceptance_fingerprint', 'role_origin', 'included_origin', 'assignment_reasons'):
            sample.pop(key, None)
    raw['result']['input_fingerprint'] = input_fingerprint(raw)
    app.state.energy.write_json(metadata(app, doc), raw)
    doc = client.get(path(doc)).json()['collection']
    old_result = copy.deepcopy(doc['result'])
    assert doc['card_projection'][0]['result']
    doc = success(client.put(path(doc, '/title'), json={
        'expected_revision': doc['revision'], 'title': '旧任务新名字'}))
    saved = json.loads(metadata(app, doc).read_text(encoding='utf-8'))
    unchanged_science(raw, saved)
    assert doc['schema_version'] == 'pp.energy.v1' and 'workflow' not in saved
    assert 'legacy_mode' not in saved and 'card_projection' not in saved
    assert not metadata(app, doc).with_name('metadata.pp.energy.v1.backup.json').exists()
    assert doc['result'] == old_result and doc['card_projection'][0]['result']
    assert client.get(path(doc, '/export')).status_code == 200
    listed = next(row for row in client.get(BASE + '/collections').json()['collections'] if row['id'] == doc['id'])
    assert listed['sample_count'] == 4 and listed['card_count'] == len(doc['card_projection']) == 1


def upload(client, doc, original):
    return success(client.post(path(doc, '/outcar'), params={
        'expected_revision': doc['revision'], 'relative_path': '科研原件/OUTCAR'},
        content=original.read_bytes()), 201)


def test_delete_rechecks_revision_and_removes_only_owned_task_cache(energy_api, tmp_path):
    client, app = energy_api
    original = tmp_path / 'scientific-original' / 'OUTCAR'
    original.parent.mkdir()
    original.write_bytes(b'vasp.6.3.2\nsynthetic source without scientific fields\n')
    doc = adsorption_review(client)
    doc = success(action(client, doc, 'ads', 'unlock'))
    doc = upload(client, doc, original)
    directory = app.state.energy.directory(doc['id'])
    assert (directory / 'metadata.pp.energy.v1.backup.json').is_file()
    cached = directory / (doc['samples'][-1]['id'] + '.bin')
    assert cached.read_bytes() == original.read_bytes()
    other = upload(client, create(client, 'formation'), original)
    other_dir = app.state.energy.directory(other['id'])
    other_files = {entry.name: entry.read_bytes() for entry in other_dir.iterdir()}
    source_bytes = original.read_bytes()
    stale_revision = doc['revision']
    doc = manual(client, doc, '删除确认后新增', {'He': 1}, -1)
    stale_delete = client.delete(path(doc), params={'expected_revision': stale_revision})
    assert stale_delete.status_code == 409 and stale_delete.json()['error']['code'] == 'ENERGY_REVISION_CONFLICT'
    assert client.get(path(doc)).json()['collection'] == doc and cached.exists()
    # Crash leftovers and migration backups are also software-owned task files.
    (directory / 'metadata.tmp').write_text('{}', encoding='utf-8')
    (directory / ('metadata.pp.energy.v1.backup.json.' + uuid.uuid4().hex + '.tmp')).write_bytes(b'backup')
    orphan = directory / ('es_' + uuid.uuid4().hex + '.bin')
    os.link(original, orphan)
    own_preview = app.state.energy.cards.impact(doc, [], {'remove': []})['preview_id']
    other_preview = app.state.energy.cards.impact(other, [], {'remove': []})['preview_id']
    deleted = client.delete(path(doc), params={'expected_revision': doc['revision']})
    assert deleted.status_code == 200 and deleted.json() == {'mode': 'toolbox', 'deleted': True}
    assert not directory.exists() and client.get(path(doc)).status_code == 404
    assert original.read_bytes() == source_bytes
    assert {entry.name: entry.read_bytes() for entry in other_dir.iterdir()} == other_files
    assert own_preview not in app.state.energy.cards.previews and other_preview in app.state.energy.cards.previews
    assert [row['id'] for row in client.get(BASE + '/collections').json()['collections']] == [other['id']]


@pytest.mark.parametrize('entry_kind', ['original_file', 'directory', 'junction'])
def test_delete_refuses_unowned_files_and_links_without_partial_cleanup(energy_api, tmp_path, entry_kind):
    client, app = energy_api
    doc = create(client)
    directory = app.state.energy.directory(doc['id'])
    target = tmp_path / 'originals'
    target.mkdir()
    (target / 'OUTCAR').write_bytes(b'original scientific file')
    entry = directory / 'OUTCAR'
    if entry_kind == 'original_file':
        entry.write_bytes(b'user placed original')
    elif entry_kind == 'directory':
        entry.mkdir()
        (entry / 'OUTCAR').write_bytes(b'original in unexpected subdirectory')
    elif os.name == 'nt':
        entry = directory / ('es_' + uuid.uuid4().hex + '.bin')
        made = subprocess.run(['cmd', '/c', 'mklink', '/J', str(entry), str(target)], capture_output=True, text=True)
        assert made.returncode == 0, made.stdout + made.stderr
    else:
        entry = directory / ('es_' + uuid.uuid4().hex + '.bin')
        entry.symlink_to(target, target_is_directory=True)
    if entry_kind == 'junction':
        assert EnergyStore.linked(entry)
    before = metadata(app, doc).read_bytes()
    try:
        denied = client.delete(path(doc), params={'expected_revision': doc['revision']})
        assert denied.status_code == 409 and denied.json()['error']['code'] == 'ENERGY_TASK_PATH_UNSAFE'
        assert directory.exists() and metadata(app, doc).read_bytes() == before
        assert entry.exists() and (target / 'OUTCAR').read_bytes() == b'original scientific file'
    finally:
        if entry_kind == 'junction':
            if os.name == 'nt':
                entry.rmdir()
            else:
                entry.unlink()


def test_legacy_delete_without_revision_is_still_compatible(energy_api):
    client, _ = energy_api
    doc = success(client.post(BASE + '/collections', json={'title': '旧接口任务'}), 201)
    assert client.delete(path(doc)).status_code == 200
    assert client.get(path(doc)).status_code == 404


def test_task_sample_ids_are_rejected_across_card_and_shared_sample_boundaries(energy_api):
    client, app = energy_api
    doc = adsorption_cards(client)
    other = manual(client, create(client), '其他任务样本', {'Pt': 4, 'C': 1, 'O': 1}, -1)
    foreign = other['samples'][0]['id']
    unchanged = {record['id']: metadata(app, record).read_bytes() for record in (doc, other)}
    own = editable_config(card(doc, 'A'))
    foreign_configs = [
        {**own, 'clean_sample_id': foreign},
        {**own, 'adsorbate_sample_id': foreign},
        {**own, 'targets': [{'sample_id': foreign}]},
    ]
    for cfg in foreign_configs:
        denied = client.put(path(doc, '/cards/A'), json={'expected_revision': doc['revision'], 'card': cfg})
        assert denied.status_code == 400 and denied.json()['error']['code'] == 'ENERGY_SAMPLE_INVALID'
        assert denied.json()['error']['field_errors'][0]['sample_id'] == foreign
        denied = client.post(path(doc, '/cards'), json={
            'expected_revision': doc['revision'], 'card': {**cfg, 'id': 'foreign_card'}})
        assert denied.status_code == 400 and denied.json()['error']['code'] == 'ENERGY_SAMPLE_INVALID'
    denied = client.put(path(doc, '/samples/configuration'), json={
        'expected_revision': doc['revision'], 'samples': [{'sample_id': foreign, 'name': '串任务改名'}]})
    assert denied.status_code == 400 and denied.json()['error']['code'] == 'ENERGY_SAMPLE_INVALID'
    denied = client.post(path(doc, '/samples/removal-preview'), json={
        'expected_revision': doc['revision'], 'sample_ids': [foreign], 'workflow': 'cards'})
    assert denied.status_code == 400 and denied.json()['error']['code'] == 'ENERGY_SAMPLE_INVALID'
    # A card ID valid under one task has no authority under another task.
    denied = action(client, other, 'A', 'calculate')
    assert denied.status_code == 404 and denied.json()['error']['code'] == 'ENERGY_CARD_NOT_FOUND'
    for record in (doc, other):
        assert metadata(app, record).read_bytes() == unchanged[record['id']]
