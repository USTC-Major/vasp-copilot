"""RT03 CSV usability, shared validation and explicit-value round trips."""
import copy
import csv
import io

from backend.tests.test_postprocessing_energy import BASE, BASIS, configure, energy_api, manual
from backend.tests.test_energy_review_flow import action, adsorption_review, create_typed, error, sample_rows, success


def csv_data(rows, headers=None, bom=False):
    columns = headers or ['name', 'composition', 'energy_basis', 'energy_ev', 'unit', 'relative_path', 'reference_note']
    out = io.StringIO(newline='')
    writer = csv.writer(out)
    writer.writerow(columns)
    writer.writerows(rows)
    return out.getvalue().encode('utf-8-sig' if bom else 'utf-8')


def request_csv(client, doc, data, preview=False, basis=None):
    params = {'expected_revision': doc['revision']}
    if basis is not None:
        params['energy_basis'] = basis
    return client.post(f"{BASE}/collections/{doc['id']}/csv" + ('/preview' if preview else ''), params=params, content=data)


def table(client, doc, basis=BASIS, source='effective'):
    return client.get(f"{BASE}/collections/{doc['id']}/samples.csv", params={'energy_basis': basis, 'value_source': source})


def records(response):
    assert response.status_code == 200, response.text
    assert response.content.startswith(b'\xef\xbb\xbf')
    return list(csv.DictReader(io.StringIO(response.content.decode('utf-8-sig'))))


def test_template_readable_bom_chinese_quoted_preview_import_and_legacy_json(energy_api):
    client, app = energy_api
    doc = create_typed(client, 'formation')
    response = client.get(BASE + '/csv-template', params={'energy_basis': BASIS})
    assert response.status_code == 200 and response.content.startswith(b'\xef\xbb\xbf')
    assert BASIS in response.headers['content-disposition']
    reader = csv.reader(io.StringIO(response.content.decode('utf-8-sig')))
    headers = next(reader)
    assert list(reader) == []  # Headers only: no synthetic scientific rows.
    data = csv_data([['中文,目标', 'Al:2 O:1', '', -20, 'eV', '中文 目录/OUTCAR', '人工说明\n第二行'],
                     ['旧JSON', '{"Al":2}', BASIS, -8, 'eV', '', '人工原表']], headers, bom=True)
    path = app.state.energy.directory(doc['id']) / 'metadata.json'
    before = path.read_bytes()
    response = request_csv(client, doc, data, preview=True, basis=BASIS)
    assert response.status_code == 200, response.text
    preview = response.json()['preview']
    assert preview['can_import'] and preview['row_count'] == preview['valid_count'] == 2
    assert preview['rows'][0]['composition'] == {'Al': 2, 'O': 1}
    assert preview['rows'][0]['row_number'] == 2 and preview['rows'][1]['row_number'] == 4
    assert path.read_bytes() == before
    response = request_csv(client, doc, data, basis=BASIS)
    assert response.status_code == 201, response.text
    imported = response.json()['collection']
    assert [sample['energy_basis'] for sample in imported['samples']] == [BASIS, BASIS]
    assert imported['samples'][0]['source']['relative_path'] == '中文 目录/OUTCAR'
    assert imported['samples'][0]['parsed']['composition'] == {'Al': 2, 'O': 1}
    assert imported['samples'][1]['source']['kind'] == 'csv'
    assert not imported['locked'] and all(not sample['confirmed'] and not sample['accepted_warnings'] for sample in imported['samples'])


def test_file_basis_authority_default_only_fills_blank_conflicts_and_atomic_recheck(energy_api):
    client, app = energy_api
    doc = create_typed(client)
    other = 'without_entropy_ev'
    data = csv_data([['A', 'Pt:4', BASIS, -100, 'eV', '', ''], ['B', 'Pt:4', other, -101, 'eV', '', '']])
    preview = request_csv(client, doc, data, preview=True).json()['preview']
    assert preview['can_import'] and [row['energy_basis'] for row in preview['rows']] == [BASIS, other]
    conflict = request_csv(client, doc, data, preview=True, basis=BASIS).json()['preview']
    assert not conflict['can_import'] and conflict['valid_count'] == 1
    assert conflict['rows'][1]['issues'][0]['code'] == 'ENERGY_BASIS_CONFLICT'
    response = request_csv(client, doc, data, basis=BASIS)
    assert response.status_code == 400 and response.json()['error']['code'] == 'ENERGY_BASIS_CONFLICT'
    assert app.state.energy.read(doc['id']) == doc
    # A valid client preview cannot bless a subsequently changed byte payload.
    changed = csv_data([['A', 'Pt:4', BASIS, -100, 'eV', '', ''], ['B', 'Pt:4', other, '', 'eV', '', '']])
    assert request_csv(client, doc, changed).status_code == 400
    assert app.state.energy.read(doc['id']) == doc
    response = request_csv(client, doc, data)
    assert response.status_code == 201, response.text
    assert [s['energy_basis'] for s in response.json()['collection']['samples']] == [BASIS, other]


def test_preview_collects_empty_nonfinite_unit_composition_path_and_limits_errors(energy_api, monkeypatch):
    client, app = energy_api
    doc = create_typed(client)
    values = [('valid', 'Pt:4', BASIS, -1, 'eV', '', ''), ('empty', 'Pt:4', BASIS, '', 'eV', '', ''),
              ('nan', 'Pt:4', BASIS, 'NaN', 'eV', '', ''), ('inf', 'Pt:4', BASIS, 'Inf', 'eV', '', ''),
              ('unit', 'Pt:4', BASIS, -1, 'Ry', '', ''), ('count', 'Pt:0', BASIS, -1, 'eV', '', ''),
              ('basis', 'Pt:4', '', -1, 'eV', '', ''), ('path', 'Pt:4', BASIS, -1, 'eV', 'C:/private/OUTCAR', '')]
    data = csv_data(values)
    preview = request_csv(client, doc, data, preview=True).json()['preview']
    assert preview['row_count'] == 8 and preview['valid_count'] == 1 and not preview['can_import']
    assert all(row['issues'] for row in preview['rows'][1:])
    assert preview['rows'][2]['energy_ev'] is None and preview['rows'][3]['energy_ev'] is None
    assert request_csv(client, doc, data).status_code == 400
    assert app.state.energy.read(doc['id']) == doc
    from backend.toolbox.postprocessing.energy import store as module
    monkeypatch.setattr(module, 'MAX_SAMPLES', 1)
    limited = request_csv(client, doc, data, preview=True).json()['preview']
    assert not limited['can_import'] and any(issue['code'] == 'ENERGY_QUOTA' for issue in limited['issues'])
    assert request_csv(client, doc, data).status_code == 413
    assert app.state.energy.read(doc['id']) == doc
    assert request_csv(client, doc, b'x' * (1024 * 1024 + 1), preview=True).status_code == 413


def test_sample_export_original_effective_composition_and_energy_no_fallback(energy_api):
    client, _ = energy_api
    doc = manual(client, create_typed(client), '原始样本', {'Pt': 4}, -100)
    rows = sample_rows(doc)
    rows[0]['override'] = {'composition': {'Pt': 5}, 'energy_fields': {BASIS: -110},
                           'energy_basis': BASIS, 'unit': 'eV', 'note': '人工修订能量与计数'}
    doc = configure(client, doc, [], rows)
    original = records(table(client, doc, source='original'))[0]
    effective = records(table(client, doc))[0]
    assert (original['composition'], float(original['energy_ev'])) == ('Pt:4', -100)
    assert (effective['composition'], float(effective['energy_ev'])) == ('Pt:5', -110)
    assert float(effective['original_energy_ev']) == -100 and effective['override_note'] == '人工修订能量与计数'
    missing = table(client, doc, 'without_entropy_ev', 'original')
    assert missing.status_code == 400 and missing.json()['error']['code'] == 'ENERGY_FIELD_REQUIRED'
    assert '第2行' in missing.json()['error']['message']
    rows = sample_rows(doc)
    rows[0]['override'] = {'energy_fields': {BASIS: None, 'without_entropy_ev': -90},
                           'energy_basis': 'without_entropy_ev', 'unit': 'eV', 'note': '明确缺少选定字段'}
    doc = configure(client, doc, [], rows)
    assert table(client, doc).status_code == 400  # Parsed -100 must not fill explicit effective null.
    assert float(records(table(client, doc, source='original'))[0]['energy_ev']) == -100


def test_samples_roundtrip_formula_text_negative_numbers_notes_and_unknown_provenance(energy_api):
    client, app = energy_api
    doc = manual(client, create_typed(client), '=SUM(1,2)', {'Pt': 4}, -100)
    doc['samples'][0]['source'].update(reference_note='@原始说明', remote_directory='D:/private/secret科研',
                                     identity_file='D:/private/secret-key')
    app.state.energy.save(doc)
    rows = sample_rows(doc)
    rows[0]['override'] = {'energy_fields': {BASIS: -110}, 'energy_basis': BASIS, 'unit': 'eV', 'note': '=人工修订说明'}
    doc = configure(client, doc, [], rows)
    exported = table(client, doc)
    row = records(exported)[0]
    assert row['name'] == "'=SUM(1,2)" and row['reference_note'] == "'@原始说明"
    assert row['override_note'] == "'=人工修订说明" and float(row['energy_ev']) == -110
    assert row['energy_ev'].startswith('-')  # Negative numeric cells are never escaped as formula text.
    assert b'secret' not in exported.content
    target = create_typed(client)
    preview = request_csv(client, target, exported.content, preview=True).json()['preview']
    assert preview['can_import']
    response = request_csv(client, target, exported.content)
    assert response.status_code == 201, response.text
    imported = response.json()['collection']
    sample = imported['samples'][0]
    assert sample['parsed']['energy_fields'][BASIS] == -110
    assert sample['source']['kind'] == 'csv' and sample['parsed']['status']['completion'] == 'unknown'
    assert sample['override'] is None and sample['role'] is None and not sample['included']
    assert not sample['confirmed'] and not sample['accepted_warnings'] and not imported['locked']
    assert sample['source']['reference_note'] == '@原始说明'
    metadata = sample['source']['csv_metadata']
    assert metadata['value_source'] == 'effective'
    assert metadata['override_notes'] == ['=人工修订说明']
    assert any('CSV回导不继承' in note for note in metadata['source_notes'])
    assert metadata['original_energies'] == [{'energy_basis': BASIS, 'energy_ev': -100}]
    assert records(table(client, imported))[0]['name'] == row['name']


def test_csv_physical_row_numbers_skip_blank_records_and_preserve_multiline_start(energy_api):
    client, app = energy_api
    doc = create_typed(client)
    data = (csv_data([]).decode() + '\n' + f'A,Pt:4,{BASIS},-1,eV,,\n\n' +
            f'B,Pt:4,{BASIS},-2,eV,,\n' +
            f'"中文目标",Pt:4,{BASIS},-3,eV,,"说明\n第二行"\n\n' +
            f'错误,Pt:4,{BASIS},NaN,eV,,\n').encode('utf-8')
    preview = request_csv(client, doc, data, preview=True).json()['preview']
    assert [row['row_number'] for row in preview['rows']] == [3, 5, 6, 9]
    assert preview['rows'][2]['name'] == '中文目标'
    assert preview['rows'][2]['reference_note'] == '说明\n第二行'
    assert preview['valid_count'] == 3 and not preview['can_import']
    response = request_csv(client, doc, data)
    assert response.status_code == 400 and '第9行' in response.json()['error']['message']
    assert app.state.energy.read(doc['id']) == doc


def test_maximum_reference_note_roundtrip_keeps_bounded_flat_metadata_without_growth(energy_api):
    client, app = energy_api
    doc = create_typed(client)
    note, override_note = '@' + '原' * 5999, '=' + '覆' * 1999
    response = client.post(f"{BASE}/collections/{doc['id']}/manual", json={
        'expected_revision': doc['revision'], 'name': "'@满长度样本", 'composition': {'Pt': 4},
        'energy_fields': {BASIS: -100}, 'energy_basis': BASIS, 'unit': 'eV', 'reference_note': note})
    assert response.status_code == 201, response.text
    doc = response.json()['collection']
    rows = sample_rows(doc)
    rows[0]['override'] = {'energy_fields': {BASIS: -110}, 'energy_basis': BASIS, 'unit': 'eV', 'note': override_note}
    doc = configure(client, doc, [], rows)
    metadata = None
    for cycle in range(3):
        exported = table(client, doc)
        target = create_typed(client)
        preview = request_csv(client, target, exported.content, preview=True).json()['preview']
        assert preview['can_import'], preview
        assert preview['rows'][0]['reference_note'] == note
        response = request_csv(client, target, exported.content)
        assert response.status_code == 201, response.text
        doc = response.json()['collection']
        source = doc['samples'][0]['source']
        assert doc['samples'][0]['name'] == "'@满长度样本"  # Literal apostrophes remain distinct from protective escaping.
        assert source['reference_note'] == note and len(source['reference_note']) == 6000
        assert source['csv_metadata']['override_notes'] == [override_note]
        assert source['csv_metadata']['value_source'] == 'effective'
        assert len(source['csv_metadata']['source_notes']) == 1
        assert source['csv_metadata']['original_energies'][0] == {'energy_basis': BASIS, 'energy_ev': -100}
        if cycle == 2:
            assert source['csv_metadata'] == metadata  # Re-export does not nest or append repeated notes.
        metadata = source['csv_metadata']
    # Metadata bounds are independent, and reject the entire import without truncating the note.
    row = records(table(client, doc))[0]
    row['source_note'] = '来' * 6001
    row['csv_text_encoding'] = ''
    data = csv_data([list(row.values())], list(row), bom=True)
    before = app.state.energy.read(doc['id'])
    preview = request_csv(client, doc, data, preview=True).json()['preview']
    assert not preview['can_import'] and any('source_note最多6000字' in issue['message'] for issue in preview['rows'][0]['issues'])
    assert request_csv(client, doc, data).status_code == 400
    assert app.state.energy.read(doc['id']) == before


def test_csv_preview_lock_revision_and_sample_download_without_results(energy_api):
    client, _ = energy_api
    doc = adsorption_review(client)
    saved = copy.deepcopy(doc)
    doc = success(action(client, doc, 'lock'))
    assert doc['result'] is None and len(records(table(client, doc))) == 4
    payload = csv_data([['新样本', 'Pt:4', BASIS, -100, 'eV', '', '']])
    error(request_csv(client, doc, payload, preview=True), 'ENERGY_LOCKED')
    error(request_csv(client, saved, payload, preview=True), 'ENERGY_REVISION_CONFLICT')
    error(request_csv(client, doc, payload), 'ENERGY_LOCKED')
    assert len(records(table(client, success(action(client, doc, 'unlock'))))) == 4
