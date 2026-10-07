"""HTTP contract tests with temporary app state and synthetic source bytes."""
import json
import time

import pytest
from fastapi.testclient import TestClient

from backend.toolbox.api import create_toolbox_app
from backend.tests.test_potcar_library import put, synthetic

BASE = '/api/v1/toolbox/potcar'


@pytest.fixture
def api(tmp_path):
    root = tmp_path / '中文源库 (API)'
    put(root, raw=synthetic(extra='BODY_NEVER_RETURNED'))
    app = create_toolbox_app(root=tmp_path / 'state', monitor_enabled=False)
    with TestClient(app) as client:
        yield client, app.state.toolbox.potcar, root


def register(client, root):
    response = client.post(BASE + '/libraries', json={
        'display_name': 'API 合成来源', 'root_path': str(root), 'version_note': None,
        'source_ack': {'confirmed': True},
        'expected_registry_revision': client.get(BASE + '/libraries').json()['revision']})
    assert response.status_code == 200, response.text
    assert response.json()['mode'] == 'toolbox'
    return response.json()['library']


def run_scan(client, library):
    response = client.post(BASE + f"/libraries/{library['library_id']}/scans", json={'expected_revision': library['revision']})
    assert response.status_code == 200, response.text
    sid = response.json()['scan']['scan_id']
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        scan = client.get(BASE + '/scans/' + sid).json()['scan']
        if scan['status'] not in {'queued', 'running'}:
            return scan
        time.sleep(.005)
    raise AssertionError('HTTP synthetic scan did not terminate')


def test_full_http_lifecycle_no_body(api):
    client, svc, root = api
    assert client.get(BASE + '/libraries').json() == {'mode': 'toolbox', 'libraries': [], 'default_library_id': None, 'revision': 0}
    discovered = client.post(BASE + '/discover', json={'root_path': str(root)})
    assert discovered.status_code == 200
    assert discovered.json()['requires_selection'] is False
    library = register(client, root)
    lid = library['library_id']
    assert library['index_revision'] is None
    assert run_scan(client, library)['status'] == 'succeeded'
    detail = client.get(BASE + '/libraries/' + lid).json()
    assert detail['library']['summary']['ready'] == 1
    data = client.get(BASE + f'/libraries/{lid}/datasets').json()
    assert data['datasets'][0]['element'] == 'Si'
    assert 'BODY_NEVER_RETURNED' not in json.dumps(data)
    patched = client.patch(BASE + '/libraries/' + lid, json={'expected_revision': detail['library']['revision'], 'display_name': '新名'}).json()
    assert patched['library']['source_ack'] == library['source_ack']
    default = client.put(BASE + '/default-library', json={'library_id': lid, 'expected_registry_revision': patched['revision']})
    assert default.status_code == 200
    assert client.get(BASE + '/libraries/' + lid).json()['library']['is_default'] is True
    deleted = client.delete(BASE + '/libraries/' + lid, params={'expected_revision': patched['library']['revision']})
    assert deleted.status_code == 200
    assert root.joinpath('Si/POTCAR').is_file()
    assert client.get(BASE + '/libraries').json()['default_library_id'] is None


def test_http_errors_use_toolbox_envelope_and_revision(api):
    client, svc, root = api
    library = register(client, root)
    lid = library['library_id']
    response = client.patch(BASE + '/libraries/' + lid, json={'expected_revision': 0, 'display_name': 'stale'})
    assert response.status_code == 409
    assert response.json() == {'mode': 'toolbox', 'ok': False, 'error': {
        'code': 'POTCAR_REVISION_CONFLICT', 'message': '库登记已更新；请刷新后重试', 'retryable': True}}
    invalid = client.patch(BASE + '/libraries/' + lid, json={'expected_revision': 1, 'root_path': str(root)})
    assert invalid.status_code == 400
    assert invalid.json()['error']['code'] == 'POTCAR_INVALID_REQUEST'
    assert client.get(BASE + '/libraries/' + 'f' * 32).status_code == 404
    assert client.get(BASE + '/scans/' + 'f' * 32).status_code == 404


def test_http_no_assembly_download_or_ai_registration(api):
    client, _, root = api
    for path in ('/previews', '/artifacts'):
        assert client.post(BASE + path, json={}).status_code == 404
    assert client.get(BASE + '/artifacts/anything/download').status_code == 404
    schema = client.get('/openapi.json').json()
    assert BASE + '/libraries' in schema['paths']
    assert all('/potcar/' not in path or '/artifacts' not in path for path in schema['paths'])


def test_http_replace_only_ack_and_relink_no_ack(api, tmp_path):
    client, _, root = api
    library = register(client, root)
    lid = library['library_id']
    result = client.post(BASE + f'/libraries/{lid}/relink', json={'root_path': str(root), 'expected_revision': 1})
    assert result.status_code == 409
    assert result.json()['error']['code'] == 'POTCAR_SOURCE_UNVERIFIED'
    assert run_scan(client, library)['status'] == 'succeeded'
    detail = client.get(BASE + '/libraries/' + lid).json()['library']
    other = tmp_path / 'other'
    put(other, raw=synthetic(extra='different'))
    result = client.post(BASE + f'/libraries/{lid}/relink', json={'root_path': str(other), 'expected_revision': detail['revision']})
    assert result.status_code == 409
    assert result.json()['error']['code'] == 'POTCAR_SOURCE_MISMATCH'
    result = client.post(BASE + f'/libraries/{lid}/replace-source', json={'root_path': str(other), 'expected_revision': detail['revision'], 'source_ack': {'confirmed': True}})
    assert result.status_code == 200
    assert result.json()['library']['index_revision'] is None


@pytest.mark.parametrize('payload', [
    {'root_path': 'relative'}, {'root_path': None}, {'root_path': 12}, {'root_path': 'x\x00y'}, {'root_path': '', 'unknown': True},
])
def test_http_invalid_discovery_has_no_server_trace(api, payload):
    client, _, _ = api
    response = client.post(BASE + '/discover', json=payload)
    assert response.status_code == 400
    assert response.json()['mode'] == 'toolbox'
    assert 'Traceback' not in response.text


def test_http_page_limit_and_cursor(api):
    client, _, root = api
    put(root, 'Fe/POTCAR', synthetic('Fe'))
    library = register(client, root)
    lid = library['library_id']
    run_scan(client, library)
    first = client.get(BASE + f'/libraries/{lid}/datasets', params={'limit': 1}).json()
    assert first['next_cursor']
    second = client.get(BASE + f'/libraries/{lid}/datasets', params={'limit': 1, 'cursor': first['next_cursor']}).json()
    assert second['index_revision'] == first['index_revision']
    assert second['next_cursor'] is None
    assert client.get(BASE + f'/libraries/{lid}/datasets', params={'limit': 101}).status_code == 422
    assert client.get(BASE + f'/libraries/{lid}/datasets', params={'cursor': 'bad'}).status_code == 400


@pytest.mark.parametrize('original', [b'{ broken JSON', b'{"schema_version":1,"revision":0,"libraries":[],"scans":{}}',
    b'{"schema_version":1,"revision":0,"libraries":{},"scans":{}}'])
def test_damaged_optional_registry_keeps_other_toolbox_services_available(tmp_path, original):
    root = tmp_path / 'state'
    path = root / 'potcar' / 'registry.json'
    path.parent.mkdir(parents=True)
    path.write_bytes(original)
    app = create_toolbox_app(root=root, monitor_enabled=False)
    with TestClient(app) as client:
        assert client.get('/api/v1/toolbox/projects').status_code == 200
        assert client.post('/api/v1/toolbox/projects', json={'name': 'unrelated tools still available'}).status_code == 200
        response = client.get(BASE + '/libraries')
        assert response.status_code == 503
        assert response.json()['error']['code'] == 'POTCAR_STORE_INVALID'
    assert path.read_bytes() == original


@pytest.mark.parametrize('original', [b'{ damaged index', b'{"source_token":"missing-fields"}', b'{}'])
def test_damaged_index_fails_closed_without_clearing_files(api, original):
    client, svc, root = api
    library = register(client, root)
    run_scan(client, library)
    path = svc._index_path(library['library_id'])
    if original == b'{"source_token":"missing-fields"}':
        original = json.dumps({'source_token': svc._data['libraries'][library['library_id']]['source_token']}).encode()
    path.write_bytes(original)
    response = client.get(BASE + f"/libraries/{library['library_id']}/datasets")
    assert response.status_code == 503
    assert response.json()['error']['code'] == 'POTCAR_STORE_INVALID'
    assert client.get('/api/v1/toolbox/projects').status_code == 200
    assert path.read_bytes() == original


def test_index_cannot_smuggle_non_metadata_fields_into_api(api):
    client, svc, root = api
    library = register(client, root)
    run_scan(client, library)
    path = svc._index_path(library['library_id'])
    data = json.loads(path.read_text(encoding='utf-8'))
    data['datasets'][0]['body'] = 'DO_NOT_RETURN_UNTRUSTED_INDEX_BODY'
    original = json.dumps(data).encode()
    path.write_bytes(original)
    response = client.get(BASE + f"/libraries/{library['library_id']}/datasets")
    assert response.status_code == 503
    assert 'DO_NOT_RETURN_UNTRUSTED_INDEX_BODY' not in response.text
    assert path.read_bytes() == original
