"""Workflow MP credentials follow the Toolbox owner, never the startup snapshot."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.api.v1 import deps, materials as mp_api, structure as structure_api
from app.services import materials_project as mp_service
from app.services.materials_project import MaterialsProjectClient as RealMPClient
from app.services.file_store import FileStore
from backend.toolbox import secrets
from backend.toolbox.config import load_settings, mp_environment_value


SETTINGS = '/api/v1/toolbox/settings'
SEARCH = '/api/v1/materials/search'
IMPORT = '/api/v1/materials/import'
DOC = {
    'material_id': 'mp-fake-si',
    'structure': {
        'lattice': {'matrix': [[3.0, 0.0, 0.0], [0.0, 3.0, 0.0], [0.0, 0.0, 3.0]]},
        'sites': [{'species': [{'element': 'Si'}], 'abc': [0.0, 0.0, 0.0]}],
    },
}


class FakeMP:
    keys: list[str] = []

    def __init__(self, api_key='', base_url='', timeout_seconds=40.0):
        self.keys.append(api_key)

    def search(self, criteria, limit=20):
        return [{'material_id': 'mp-fake-si', 'formula': 'Si'}]

    def get_structure_doc(self, material_id):
        assert material_id == 'mp-fake-si'
        return DOC

    def close(self):
        pass


@pytest.fixture
def runtime(tmp_path, monkeypatch, isolated_runtime_home):
    # The app module has a startup Settings snapshot. Give every mutable
    # service a private root and make that snapshot deliberately stale.
    monkeypatch.delenv('MP_API_KEY', raising=False)
    monkeypatch.setenv('ENABLE_AI_MODE', 'false')
    monkeypatch.setattr(deps.settings, 'data_dir', str(tmp_path / 'app-data'))
    monkeypatch.setattr(deps.settings.materials_project, 'api_key', 'stale-startup-only')
    monkeypatch.setattr(deps.settings.llm, 'enabled', False)
    monkeypatch.setattr(deps.settings.feature_flags, 'llm_enabled', False)
    store = FileStore(root=tmp_path / 'files', ttl_seconds=3600)
    monkeypatch.setattr(deps, 'file_store', store)
    monkeypatch.setattr(mp_api, 'file_store', store)
    monkeypatch.setattr(structure_api, 'file_store', store)
    monkeypatch.setattr(mp_api, 'get_explainer', lambda _settings: None)
    FakeMP.keys = []
    monkeypatch.setattr(mp_service, 'MaterialsProjectClient', FakeMP)
    with TestClient(app) as client:
        client.app.state.toolbox.monitor.stop()
        yield SimpleNamespace(client=client, store=store, root=client.app.state.toolbox.root)


def _search(runtime):
    return runtime.client.post(SEARCH, json={'query': 'Si'})


def _import(runtime):
    return runtime.client.post(IMPORT, json={'material_id': 'mp-fake-si'})


def _status(runtime):
    response = runtime.client.get(SETTINGS + '/secret-status')
    assert response.status_code == 200, response.text
    return response.json()['secrets']['mp']


def test_http_replace_clear_immediately_changes_search_and_import(runtime, caplog):
    assert runtime.client.get(SETTINGS).json()['settings']['materials_project']['configured'] is False
    missing = _search(runtime)
    assert missing.status_code == 422
    assert missing.json()['error']['code'] == 'MP_NOT_CONFIGURED'
    assert 'Toolbox 设置' in missing.json()['error']['message']
    assert _import(runtime).json()['error']['code'] == 'MP_NOT_CONFIGURED'
    assert FakeMP.keys == []

    for key in ('memory-mp-first', 'memory-mp-replacement'):
        saved = runtime.client.post(SETTINGS + '/secrets/mp', json={'value': key})
        assert saved.status_code == 200, saved.text
        assert _status(runtime) == {'configured': True, 'source': 'credential_store', 'manageable': True}
        assert runtime.client.get(SETTINGS).json()['settings']['materials_project']['configured'] is True
        result = _search(runtime)
        assert result.status_code == 200, result.text
        assert result.json()['data']['materials'][0]['material_id'] == 'mp-fake-si'
        imported = _import(runtime)
        assert imported.status_code == 200, imported.text
        data = imported.json()['data']
        assert data['summary']['formula'] == 'Si'
        record = runtime.store.get_structure(data['structure_id'])
        assert record.file_id == data['file_id']
        assert 'Si' in runtime.store.get_file(record.file_id).path.read_text(encoding='utf-8')
        continued = runtime.client.post('/api/v1/structure/analyze', json={'file_id': record.file_id})
        assert continued.status_code == 200, continued.text
        assert continued.json()['data']['summary']['formula'] == 'Si'
        assert FakeMP.keys[-2:] == [key, key]
        assert key not in result.text + imported.text + saved.text + caplog.text
        assert key not in (runtime.root / 'toolbox_config.json').read_text(encoding='utf-8')

    cleared = runtime.client.post(SETTINGS + '/secrets/mp', json={'value': ''})
    assert cleared.status_code == 200, cleared.text
    assert _status(runtime) == {'configured': False, 'source': 'none', 'manageable': True}
    assert runtime.client.get(SETTINGS).json()['settings']['materials_project']['configured'] is False
    previous_calls = len(FakeMP.keys)
    assert _search(runtime).json()['error']['code'] == 'MP_NOT_CONFIGURED'
    assert _import(runtime).json()['error']['code'] == 'MP_NOT_CONFIGURED'
    assert len(FakeMP.keys) == previous_calls


@pytest.mark.parametrize('aliases,expected', [
    ({'TOOLBOX_MP_API_KEY': 'env-toolbox', 'AI_MODE_MP_API_KEY': 'env-ai', 'MP_API_KEY': 'env-legacy'}, 'env-toolbox'),
    ({'TOOLBOX_MP_API_KEY': '', 'AI_MODE_MP_API_KEY': 'env-ai', 'MP_API_KEY': 'env-legacy'}, 'env-ai'),
    ({'TOOLBOX_MP_API_KEY': '', 'AI_MODE_MP_API_KEY': '', 'MP_API_KEY': 'env-legacy'}, 'env-legacy'),
])
def test_environment_alias_precedence_status_write_guard_and_no_disk(runtime, monkeypatch, aliases, expected):
    secrets.set_secret('mp_api_key', 'stored-mp')
    for name, value in aliases.items():
        monkeypatch.setenv(name, value)
    assert mp_environment_value() == expected
    assert load_settings(config_path=runtime.root / 'toolbox_config.json').mp_api_key == expected
    assert _status(runtime) == {'configured': True, 'source': 'environment', 'manageable': False}
    assert _search(runtime).status_code == 200
    assert _import(runtime).status_code == 200
    assert FakeMP.keys[-2:] == [expected, expected]
    for value in ('replacement', ''):
        blocked = runtime.client.post(SETTINGS + '/secrets/mp', json={'value': value})
        assert blocked.status_code == 400
        assert blocked.json()['error']['code'] == 'SECRET_ENV_MANAGED'
    assert runtime.client.put(SETTINGS, json={'max_jobs': 7}).status_code == 200
    saved = (runtime.root / 'toolbox_config.json').read_text(encoding='utf-8')
    assert all(env_key not in saved for env_key in aliases.values() if env_key)
    assert secrets.get_secret('mp_api_key') == 'stored-mp'


def test_legacy_value_is_eligible_only_until_managed_clear(runtime):
    legacy = runtime.root / 'config.json'
    legacy.write_text(json.dumps({'mp_api_key': 'old-local-mp'}), encoding='utf-8')
    assert _status(runtime)['source'] == 'local_config'
    assert _search(runtime).status_code == 200
    assert FakeMP.keys[-1] == 'old-local-mp'
    assert runtime.client.post(SETTINGS + '/secrets/mp', json={'value': ''}).status_code == 200
    canonical = runtime.root / 'toolbox_config.json'
    saved = json.loads(canonical.read_text(encoding='utf-8'))
    assert saved['mp_api_key'] == ''
    assert json.loads(legacy.read_text(encoding='utf-8'))['mp_api_key'] == ''
    # Reintroduce stale plaintext into the *selected* source. The managed
    # marker must still win; the read path does not mutate this test fixture.
    saved['mp_api_key'] = 'old-local-mp'
    canonical.write_text(json.dumps(saved), encoding='utf-8')
    previous_calls = len(FakeMP.keys)
    assert _status(runtime)['configured'] is False
    assert _search(runtime).json()['error']['code'] == 'MP_NOT_CONFIGURED'
    assert _import(runtime).json()['error']['code'] == 'MP_NOT_CONFIGURED'
    assert len(FakeMP.keys) == previous_calls


@pytest.mark.parametrize('route,payload', [(SEARCH, {'query': 'Si'}), (IMPORT, {'material_id': 'mp-fake-si'})])
def test_provider_read_failure_is_503_not_unconfigured(runtime, monkeypatch, route, payload):
    backend = secrets._get_backend()

    def failed_get(_account):
        raise RuntimeError('private-backend-diagnostic')

    monkeypatch.setattr(backend, 'get', failed_get)
    result = runtime.client.post(route, json=payload)
    assert result.status_code == 503
    assert result.json()['error']['code'] == 'MP_CREDENTIAL_UNAVAILABLE'
    assert 'private-backend-diagnostic' not in result.text
    assert not FakeMP.keys


def test_mp_read_ignores_unrelated_invalid_ssh_settings(runtime):
    (runtime.root / 'toolbox_config.json').write_text(json.dumps({'ssh_port': 'bad-port', 'mp_api_key': 'legacy-mp'}))
    assert _search(runtime).status_code == 200
    assert FakeMP.keys[-1] == 'legacy-mp'


@pytest.mark.parametrize('status', [401, 403])
def test_mp_auth_failure_is_not_mislabeled_as_missing_credential(runtime, monkeypatch, status):
    class FakeHttp:
        def request(self, *args, **kwargs):
            import httpx
            return httpx.Response(status)

        def close(self):
            pass

    monkeypatch.setattr(mp_service, 'MaterialsProjectClient', lambda api_key, base_url, timeout_seconds:
                        RealMPClient(api_key, base_url, timeout_seconds, client=FakeHttp()))
    assert runtime.client.post(SETTINGS + '/secrets/mp', json={'value': 'rejected-fake-key'}).status_code == 200
    result = _search(runtime)
    assert result.status_code == 409
    assert result.json()['error']['code'] == 'MP_AUTH_FAILED'
    assert 'rejected-fake-key' not in result.text
