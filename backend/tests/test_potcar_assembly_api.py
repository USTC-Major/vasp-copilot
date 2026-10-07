"""Dedicated HTTP assembly/download contract; all source data is synthetic."""
import json

import pytest
from fastapi.testclient import TestClient

from backend.toolbox.api import create_toolbox_app
from backend.tests.test_potcar_library_api import BASE, register, run_scan
from backend.tests.test_potcar_library import put, synthetic
from backend.tests.test_potcar_assembly import poscar, confirmation


@pytest.fixture
def api(tmp_path):
    root = tmp_path / '中文合成路径 (HTTP)'
    put(root, raw=synthetic(extra='PRIVATE_BODY_HTTP'))
    app = create_toolbox_app(root=tmp_path / 'state', monitor_enabled=False)
    with TestClient(app) as client:
        library = register(client, root)
        assert run_scan(client, library)['status'] == 'succeeded'
        yield client, app.state.toolbox.potcar, library['library_id'], root


def make_preview(api, **kw):
    client, svc, lid, _ = api
    return client.post(BASE + '/previews', json={'library_id': lid, 'index_revision': svc.datasets(lid)['index_revision'],
                                               'poscar_text': poscar(), **kw})


def test_http_confirm_download_metadata_boundaries(api):
    client, svc, lid, root = api
    response = make_preview(api)
    assert response.status_code == 200, response.text
    value = response.json()
    assert value['mode'] == 'toolbox'
    assert 'PRIVATE_BODY_HTTP' not in response.text and str(root) not in response.text
    response = client.post(BASE + '/artifacts', json=confirmation(value))
    assert response.status_code == 200, response.text
    out = response.json()['artifact']
    url = BASE + '/artifacts/' + out['artifact_id']
    meta = client.get(url)
    assert meta.json()['artifact'] == out
    assert 'PRIVATE_BODY_HTTP' not in meta.text
    for _ in range(2):
        download = client.get(url + '/download')
        assert download.status_code == 200
        assert download.content == synthetic(extra='PRIVATE_BODY_HTTP')
        assert download.headers['content-type'] == 'application/octet-stream'
        assert download.headers['cache-control'] == 'no-store'
        assert download.headers['content-disposition'] == 'attachment; filename="POTCAR"'
    assert client.post(BASE + '/artifacts', json=confirmation(value)).json()['artifact'] == out


def test_http_errors_and_unknown_fields_fail_closed(api):
    client, svc, lid, root = api
    value = make_preview(api).json()
    for updates, status, code in [({'confirmed_order_and_variants': False}, 400, 'POTCAR_CONFIRMATION_REQUIRED'),
                                  ({'selection_digest': 'f'*64}, 409, 'POTCAR_SELECTION_DIGEST_MISMATCH'),
                                  ({'preview_id': 'f'*32}, 404, 'POTCAR_PREVIEW_NOT_FOUND'),
                                  ({'body': 'NEVER_RETURN_THIS'}, 400, 'POTCAR_INVALID_REQUEST')]:
        response = client.post(BASE + '/artifacts', json={**confirmation(value), **updates})
        assert response.status_code == status
        assert response.json()['mode'] == 'toolbox'
        assert response.json()['error']['code'] == code
        assert 'NEVER_RETURN_THIS' not in response.text
    assert client.get(BASE + '/artifacts/' + 'f'*32).status_code == 404
    assert client.get(BASE + '/artifacts/' + 'f'*32 + '/download').status_code == 404
    assert make_preview(api, poscar_text='not a structure').json()['error']['code'] == 'POSCAR_INCOMPLETE'
    assert make_preview(api, body='NEVER_RETURN_THIS').status_code == 400


def test_no_generic_preview_or_ai_tool_registration(api):
    client, svc, lid, _ = api
    schema = client.get('/openapi.json').json()
    assert BASE + '/previews' in schema['paths']
    assert BASE + '/artifacts/{artifact_id}/download' in schema['paths']
    assert not any('/potcar/' in path and ('body' in path or 'content' in path) for path in schema['paths'])
    service_source = __import__('pathlib').Path(__file__).parents[1].joinpath('toolbox/service.py').read_text('utf-8')
    assert "'potcar_generate'" in service_source  # Existing forbidden AI action stays retired.


def test_store_failure_is_bounded_error_and_no_partial_download(api, monkeypatch):
    client, svc, lid, _ = api
    value = make_preview(api).json()
    def failure(*args):
        raise OSError('PRIVATE_BODY_HTTP')
    monkeypatch.setattr('backend.toolbox.potcar.assembly.os.rename', failure)
    response = client.post(BASE + '/artifacts', json=confirmation(value))
    assert response.status_code == 503
    assert response.json()['error']['code'] == 'POTCAR_STORE_INVALID'
    assert 'PRIVATE_BODY_HTTP' not in response.text
    assert list(svc.assembly.root.iterdir()) == []


@pytest.mark.parametrize('field,value', [('title', {}), ('status', []), ('relative_path', 123),
                                       ('zval', 'PRIVATE_BODY_HTTP'), ('issues', [{'code': [], 'message': 'PRIVATE_BODY_HTTP'}])])
def test_corrupt_index_types_are_recoverable_without_trace(api, field, value):
    client, svc, lid, _ = api
    path = svc._index_path(lid)
    stored = json.loads(path.read_text('utf-8'))
    stored['datasets'][0][field] = value
    path.write_text(json.dumps(stored), encoding='utf-8')
    response = client.post(BASE + '/previews', json={'library_id': lid, 'index_revision': stored['index_revision'],
                                                  'poscar_text': poscar()})
    assert response.status_code == 503
    assert response.json()['error']['code'] == 'POTCAR_STORE_INVALID'
    assert response.json()['error']['retryable'] is True
    assert 'PRIVATE_BODY_HTTP' not in response.text and 'Traceback' not in response.text
