"""RW2 full HTTP contracts: fake credentials and isolated configuration only."""
import json

import pytest
from fastapi.testclient import TestClient

from ai_mode.config import load_settings as ai_load
from ai_mode.server import create_ai_mode_app
from ai_mode.settings.global_api import mask_config, update_from_patch, writable_fields
from backend.toolbox import secrets
from backend.toolbox.api import create_toolbox_app
from backend.toolbox.config import load_settings as toolbox_load

PREFIX = '/api/v1/toolbox'
REMOVED = {'auto_approve_kinds': ['copy_inputs'], 'allow_potcar_assembly': True,
           'allow_script_deploy': True, 'submit_script_template': '/home/u/run.sh'}


class ControlledBackend(secrets.MemoryBackend):
    fail = None
    noop = None

    def get(self, account):
        if self.fail == 'get':
            raise RuntimeError('private backend diagnostic')
        return super().get(account)

    def set(self, account, value):
        if self.fail == 'set':
            raise RuntimeError('private backend diagnostic')
        if self.noop != 'set':
            super().set(account, value)

    def delete(self, account):
        if self.fail == 'delete':
            raise RuntimeError('private backend diagnostic')
        if self.noop != 'delete':
            super().delete(account)


@pytest.fixture
def backend():
    value = ControlledBackend()
    secrets.configure_backend(value)
    return value


def toolbox(root):
    return create_toolbox_app(root=root, monitor_enabled=False)


def test_mp_http_replace_reload_clear_and_other_app_visibility(tmp_path, backend):
    root = tmp_path / 'owner'
    root.mkdir()
    legacy = root / 'config.json'
    legacy.write_text(json.dumps({'mp_api_key': 'legacy-mp', 'max_jobs': 9,
                                  'reviewer': {'enabled': False}, **REMOVED}), encoding='utf-8')
    # Toolbox deliberately permits only one execution owner per root; a
    # separate app root still shares the system credential backend.
    with TestClient(toolbox(root)) as first, TestClient(toolbox(tmp_path / 'second-owner')) as second:
        assert first.post(PREFIX + '/settings/secrets/mp', json={'value': 'new-mp'}).status_code == 200
        assert toolbox_load(config_path=root / 'toolbox_config.json', env={}).mp_api_key == 'new-mp'
        assert second.get(PREFIX + '/settings/secret-status').json()['secrets']['mp']['configured'] is True
        assert 'legacy-mp' not in legacy.read_text(encoding='utf-8')
        assert json.loads(legacy.read_text(encoding='utf-8'))['reviewer'] == {'enabled': False}
        assert first.post(PREFIX + '/settings/secrets/mp', json={'value': ''}).json()['configured'] is False
        assert second.get(PREFIX + '/settings/secret-status').json()['secrets']['mp']['configured'] is False
    with TestClient(toolbox(root)) as restarted:
        assert restarted.get(PREFIX + '/settings/secret-status').json()['secrets']['mp']['configured'] is False
        assert restarted.get(PREFIX + '/settings').json()['settings']['max_jobs'] == 9
    # The historical file cannot revive cleared credentials even if the canonical file is absent.
    (root / 'toolbox_config.json').unlink()
    assert toolbox_load(config_path=root / 'toolbox_config.json', env={}).mp_api_key == ''


def test_llm_full_ai_route_legacy_replace_then_clear(tmp_path, backend, monkeypatch):
    monkeypatch.setenv('ENABLE_AI_MODE', 'true')
    monkeypatch.setenv('VASP_AI_HOME', str(tmp_path))
    legacy = tmp_path / 'config.json'
    legacy.write_text(json.dumps({'llm_api_key': 'old-llm', 'llm_model': 'model-local',
                                  'max_jobs': 8, **REMOVED}), encoding='utf-8')
    with TestClient(create_ai_mode_app()) as first, TestClient(create_ai_mode_app()) as second:
        result = first.put('/ai/v1/settings/secrets/llm', json={'action': 'replace', 'value': 'new-llm'})
        assert result.status_code == 200, result.text
        assert secrets.get_secret('llm_api_key') == 'new-llm'
        assert ai_load().llm_api_key == 'new-llm'
        assert second.get('/ai/v1/settings/secret-status').json()['secrets']['llm']['configured'] is True
        assert first.put('/ai/v1/settings/secrets/llm', json={'action': 'clear'}).json()['configured'] is False
        assert second.get('/ai/v1/settings/secret-status').json()['secrets']['llm']['configured'] is False
    assert 'old-llm' not in legacy.read_text(encoding='utf-8')
    assert 'new-llm' not in (tmp_path / 'ai_config.json').read_text(encoding='utf-8')
    assert json.loads(legacy.read_text(encoding='utf-8'))['max_jobs'] == 8
    (tmp_path / 'ai_config.json').unlink()
    assert ai_load().llm_api_key == ''


@pytest.mark.parametrize('operation', ['set', 'delete'])
@pytest.mark.parametrize('failure', ['exception', 'noop'])
def test_mp_backend_failure_is_not_http_success_or_disk_clear(tmp_path, backend, operation, failure):
    path = tmp_path / 'toolbox_config.json'
    path.write_text(json.dumps({'mp_api_key': 'legacy-mp'}), encoding='utf-8')
    backend.set(secrets.ACCOUNTS['mp_api_key'], 'previous-mp')
    before = path.read_bytes()
    with TestClient(toolbox(tmp_path)) as client:
        setattr(backend, 'fail' if failure == 'exception' else 'noop', operation)
        response = client.post(PREFIX + '/settings/secrets/mp', json={'value': 'next-mp' if operation == 'set' else ''})
        assert response.status_code == 503, response.text
        assert response.json()['error']['code'] == 'SECRET_STORAGE_FAILED'
        assert path.read_bytes() == before
        assert 'private backend diagnostic' not in response.text
        assert client.get(PREFIX + '/settings/secret-status').json()['secrets']['mp']['configured'] is True


def test_environment_override_is_never_migrated_and_blocks_secret_routes(tmp_path, backend, monkeypatch):
    monkeypatch.setenv('TOOLBOX_MP_API_KEY', 'env-mp')
    with TestClient(toolbox(tmp_path)) as client:
        assert client.put(PREFIX + '/settings', json={'max_jobs': 7}).status_code == 200
        assert client.post(PREFIX + '/settings/secrets/mp', json={'value': ''}).status_code == 400
        assert client.post(PREFIX + '/settings/secrets/mp', json={'value': 'replacement'}).status_code == 400
        assert secrets.get_secret('mp_api_key') is None
        assert 'env-mp' not in (tmp_path / 'toolbox_config.json').read_text(encoding='utf-8')
        assert client.get(PREFIX + '/settings/secret-status').json()['secrets']['mp']['source'] == 'environment'


def test_empty_toolbox_env_does_not_hide_ai_mp_environment(tmp_path, backend, monkeypatch):
    monkeypatch.setenv('TOOLBOX_MP_API_KEY', '')
    monkeypatch.setenv('AI_MODE_MP_API_KEY', 'ai-env-mp')
    assert toolbox_load(config_path=tmp_path / 'toolbox_config.json').mp_api_key == 'ai-env-mp'
    with TestClient(toolbox(tmp_path)) as client:
        assert client.put(PREFIX + '/settings', json={'max_jobs': 7}).status_code == 200
        assert secrets.get_secret('mp_api_key') is None
        assert 'ai-env-mp' not in (tmp_path / 'toolbox_config.json').read_text(encoding='utf-8')


def test_existing_store_not_overwritten_by_stale_legacy_settings_save(tmp_path, backend):
    path = tmp_path / 'toolbox_config.json'
    path.write_text(json.dumps({'mp_api_key': 'old-mp', 'poll_interval_seconds': 80}), encoding='utf-8')
    backend.set(secrets.ACCOUNTS['mp_api_key'], 'new-mp')
    with TestClient(toolbox(tmp_path)) as client:
        assert client.put(PREFIX + '/settings', json={'max_jobs': 5}).status_code == 200
    assert secrets.get_secret('mp_api_key') == 'new-mp'
    assert json.loads(path.read_text(encoding='utf-8'))['mp_api_key'] == ''


def test_removed_settings_ignore_legacy_and_reject_external_updates(tmp_path, backend):
    path = tmp_path / 'toolbox_config.json'
    path.write_text(json.dumps({**REMOVED, 'max_jobs': 6, 'poll_interval_seconds': 75}), encoding='utf-8')
    before = path.read_bytes()
    cfg = toolbox_load(config_path=path, env={})
    assert not set(REMOVED) & cfg.model_dump().keys()
    ai_cfg = ai_load(config_path=path, env={})
    assert not set(REMOVED) & mask_config(ai_cfg).keys()
    assert 'message_char_limit' in mask_config(ai_cfg)
    assert not set(REMOVED) & set(writable_fields())
    with TestClient(toolbox(tmp_path)) as client:
        for field, value in REMOVED.items():
            response = client.put(PREFIX + '/settings', json={field: value})
            assert response.status_code == 400
            with pytest.raises(ValueError, match='未知设置字段'):
                update_from_patch(ai_cfg, {field: value})
        settings = client.get(PREFIX + '/settings').json()['settings']
        assert settings['max_jobs'] == 6 and settings['poll_interval_seconds'] == 75
        assert not set(REMOVED) & settings.keys()
    assert path.read_bytes() == before


def test_no_secret_read_cache_masks_external_backend_changes(backend):
    assert secrets.get_secret('llm_api_key') is None
    backend.set(secrets.ACCOUNTS['llm_api_key'], 'another-app-value')
    assert secrets.get_secret('llm_api_key') == 'another-app-value'
    backend.delete(secrets.ACCOUNTS['llm_api_key'])
    assert secrets.get_secret('llm_api_key') is None


def test_read_failure_does_not_report_unconfigured(tmp_path, backend):
    with TestClient(toolbox(tmp_path)) as client:
        backend.fail = 'get'
        response = client.get(PREFIX + '/settings/secret-status')
        assert response.status_code == 503
        assert response.json()['error']['code'] == 'SECRET_STORAGE_FAILED'
        assert 'private backend diagnostic' not in response.text


def test_legacy_mp_migrates_on_regular_settings_save(tmp_path, backend):
    legacy = tmp_path / 'config.json'
    legacy.write_text(json.dumps({'mp_api_key': 'legacy-migrate', 'max_jobs': 12}), encoding='utf-8')
    with TestClient(toolbox(tmp_path)) as client:
        assert client.put(PREFIX + '/settings', json={'max_jobs': 13}).status_code == 200
    assert secrets.get_secret('mp_api_key') == 'legacy-migrate'
    assert json.loads(legacy.read_text(encoding='utf-8'))['mp_api_key'] == ''


def test_llm_environment_only_runtime_and_existing_local_value_migrates(tmp_path, backend, monkeypatch):
    from ai_mode.config import save_settings
    path = tmp_path / 'ai_config.json'
    path.write_text(json.dumps({'llm_api_key': 'local-before-env'}), encoding='utf-8')
    monkeypatch.setenv('AI_MODE_LLM_API_KEY', 'env-llm')
    cfg = ai_load(config_path=path)
    assert cfg.llm_api_key == 'env-llm'
    save_settings(cfg, config_path=path)
    assert secrets.get_secret('llm_api_key') == 'local-before-env'
    assert 'env-llm' not in path.read_text(encoding='utf-8')
    monkeypatch.delenv('AI_MODE_LLM_API_KEY')
    assert ai_load(config_path=path).llm_api_key == 'local-before-env'


@pytest.mark.parametrize('operation', ['set', 'delete'])
@pytest.mark.parametrize('failure', ['exception', 'noop'])
def test_llm_http_failure_preserves_config_and_reports_error(tmp_path, backend, monkeypatch, operation, failure):
    monkeypatch.setenv('ENABLE_AI_MODE', 'true')
    monkeypatch.setenv('VASP_AI_HOME', str(tmp_path))
    path = tmp_path / 'ai_config.json'
    path.write_text(json.dumps({'llm_api_key': 'legacy-llm'}), encoding='utf-8')
    backend.set(secrets.ACCOUNTS['llm_api_key'], 'stored-llm')
    before = path.read_bytes()
    with TestClient(create_ai_mode_app()) as client:
        setattr(backend, 'fail' if failure == 'exception' else 'noop', operation)
        response = client.put('/ai/v1/settings/secrets/llm', json={
            'action': 'replace' if operation == 'set' else 'clear', 'value': 'replacement'})
        _assert_sanitized_storage_503(response, tmp_path,
                                      'legacy-llm', 'stored-llm', 'replacement')
        assert path.read_bytes() == before
        assert 'private backend diagnostic' not in response.text
        assert client.get('/ai/v1/settings/secret-status').json()['secrets']['llm']['configured'] is True


def _assert_sanitized_storage_503(response, root, *values, code='AI_MODE_SECRET_STORAGE_FAILED'):
    assert response.status_code == 503, response.text
    assert response.json()['error']['code'] == code
    for diagnostic in ('private backend diagnostic', 'private filesystem diagnostic',
                       str(root), root.as_posix(), *values):
        assert diagnostic not in response.text


@pytest.mark.parametrize('route', ['status', 'get_settings', 'secret_update', 'generic_update'])
def test_llm_read_failure_http_returns_sanitized_503(tmp_path, backend, monkeypatch, route):
    monkeypatch.setenv('ENABLE_AI_MODE', 'true')
    monkeypatch.setenv('VASP_AI_HOME', str(tmp_path))
    backend.set(secrets.ACCOUNTS['llm_api_key'], 'read-secret')
    with TestClient(create_ai_mode_app()) as client:
        backend.fail = 'get'
        if route == 'status':
            response = client.get('/ai/v1/settings/secret-status')
        elif route == 'get_settings':
            response = client.get('/ai/v1/settings')
        elif route == 'secret_update':
            response = client.put('/ai/v1/settings/secrets/llm', json={'action': 'clear'})
        else:
            response = client.put('/ai/v1/settings', json={'llm_model': 'new-model'})
        _assert_sanitized_storage_503(response, tmp_path, 'read-secret')


@pytest.mark.parametrize('operation', ['replace', 'clear', 'generic_update'])
@pytest.mark.parametrize('failure_point', ['atomic_write', 'directory_create'])
def test_llm_config_disk_failure_http_returns_sanitized_503(tmp_path, backend, monkeypatch, operation, failure_point):
    from pathlib import Path
    from backend.toolbox import storage
    monkeypatch.setenv('ENABLE_AI_MODE', 'true')
    monkeypatch.setenv('VASP_AI_HOME', str(tmp_path))
    path = tmp_path / 'ai_config.json'
    path.write_text(json.dumps({'llm_api_key': '', 'llm_model': 'old-model'}), encoding='utf-8')
    backend.set(secrets.ACCOUNTS['llm_api_key'], 'stored-before-disk-failure')
    before = path.read_bytes()
    with TestClient(create_ai_mode_app()) as client:
        def fail_write(*args, **kwargs):
            raise OSError('private filesystem diagnostic: ' + str(tmp_path))
        if failure_point == 'atomic_write':
            monkeypatch.setattr(storage, 'atomic_json', fail_write)
        else:
            original_mkdir = Path.mkdir
            def fail_parent_directory(self, *args, **kwargs):
                if self == tmp_path:
                    return fail_write()
                return original_mkdir(self, *args, **kwargs)
            monkeypatch.setattr(Path, 'mkdir', fail_parent_directory)
        if operation == 'generic_update':
            response = client.put('/ai/v1/settings', json={'llm_model': 'new-model'})
        else:
            response = client.put('/ai/v1/settings/secrets/llm', json={
                'action': operation, 'value': 'replacement-before-disk-failure'})
        _assert_sanitized_storage_503(response, tmp_path, 'stored-before-disk-failure',
                                      'replacement-before-disk-failure')
        assert path.read_bytes() == before
        expected = None if operation == 'clear' else (
            'replacement-before-disk-failure' if operation == 'replace' else 'stored-before-disk-failure')
        assert secrets.get_secret('llm_api_key') == expected
        status = client.get('/ai/v1/settings/secret-status').json()['secrets']['llm']
        assert status['configured'] == bool(expected)


@pytest.mark.parametrize('failure_point', ['read', 'atomic_write'])
def test_toolbox_generic_settings_failure_http_returns_sanitized_503(tmp_path, backend, monkeypatch, failure_point):
    from backend.toolbox import storage
    with TestClient(toolbox(tmp_path)) as client:
        if failure_point == 'read':
            backend.fail = 'get'
        else:
            def fail_write(*args, **kwargs):
                raise OSError('private filesystem diagnostic: ' + str(tmp_path))
            monkeypatch.setattr(storage, 'atomic_json', fail_write)
        response = client.put(PREFIX + '/settings', json={'max_jobs': 7})
        _assert_sanitized_storage_503(response, tmp_path, code='SECRET_STORAGE_FAILED')


@pytest.mark.parametrize('application', ['ai', 'toolbox'])
def test_generic_settings_migration_failure_http_returns_sanitized_503(tmp_path, backend, monkeypatch, application):
    if application == 'ai':
        monkeypatch.setenv('ENABLE_AI_MODE', 'true')
        monkeypatch.setenv('VASP_AI_HOME', str(tmp_path))
        path = tmp_path / 'ai_config.json'
        path.write_text(json.dumps({'llm_api_key': 'unmigrated-local-secret'}), encoding='utf-8')
        app, route, payload = create_ai_mode_app(), '/ai/v1/settings', {'llm_model': 'new-model'}
        code = 'AI_MODE_SECRET_STORAGE_FAILED'
    else:
        path = tmp_path / 'toolbox_config.json'
        path.write_text(json.dumps({'mp_api_key': 'unmigrated-local-secret'}), encoding='utf-8')
        app, route, payload = toolbox(tmp_path), PREFIX + '/settings', {'max_jobs': 7}
        code = 'SECRET_STORAGE_FAILED'
    before = path.read_bytes()
    with TestClient(app) as client:
        backend.fail = 'set'
        response = client.put(route, json=payload)
        _assert_sanitized_storage_503(response, tmp_path, 'unmigrated-local-secret', code=code)
        assert path.read_bytes() == before


def test_file_write_failure_does_not_claim_secret_update_success(tmp_path, backend, monkeypatch):
    from backend.toolbox import storage
    with TestClient(toolbox(tmp_path)) as client:
        def fail_write(*args, **kwargs):
            raise OSError('private filesystem diagnostic')
        monkeypatch.setattr(storage, 'atomic_json', fail_write)
        response = client.post(PREFIX + '/settings/secrets/mp', json={'value': 'partially-saved'})
        assert response.status_code == 503
        assert 'private filesystem diagnostic' not in response.text
        assert secrets.get_secret('mp_api_key') == 'partially-saved'
        assert client.get(PREFIX + '/settings/secret-status').json()['secrets']['mp']['configured'] is True


def test_installed_keyring_without_provider_allows_basic_headless_app(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace
    monkeypatch.setitem(sys.modules, 'keyring', SimpleNamespace(get_keyring=lambda: SimpleNamespace(priority=0)))
    monkeypatch.setenv('ENABLE_AI_MODE', 'false')
    secrets.configure_backend(None)
    assert secrets.get_secret('mp_api_key') is None
    assert secrets.get_secret('llm_api_key') is None
    with TestClient(toolbox(tmp_path)) as client:
        assert client.get(PREFIX + '/settings').status_code == 200
        assert client.get(PREFIX + '/settings/secret-status').json()['secrets']['mp']['configured'] is False
        assert client.post(PREFIX + '/settings/secrets/mp', json={'value': ''}).status_code == 503
    with TestClient(create_ai_mode_app()) as client:
        result = client.get('/ai/v1/settings')
        assert result.status_code == 503
        assert result.json()['error']['code'] == 'AI_MODE_DISABLED'
