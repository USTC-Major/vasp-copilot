"""RW3 backend regressions: isolated roots, memory credentials and no HPC dispatch."""
import copy
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.toolbox import secrets
from backend.toolbox.computation import digest, select_job
from backend.toolbox.contracts import ToolboxError


def test_other_toolbox_root_cannot_revive_globally_cleared_mp(tmp_path):
    from backend.toolbox.api import create_toolbox_app
    for name in ('a', 'b'):
        root = tmp_path / name
        root.mkdir()
        (root / 'config.json').write_text(json.dumps({'mp_api_key': 'fake-legacy-key'}))
    prefix = '/api/v1/toolbox/settings'
    with TestClient(create_toolbox_app(root=tmp_path / 'a', monitor_enabled=False)) as a, TestClient(
            create_toolbox_app(root=tmp_path / 'b', monitor_enabled=False)) as b:
        assert a.post(prefix + '/secrets/mp', json={'value': 'fake-new-key'}).status_code == 200
        assert b.get(prefix + '/secret-status').json()['secrets']['mp']['source'] == 'credential_store'
        assert a.post(prefix + '/secrets/mp', json={'value': ''}).json()['configured'] is False
        assert b.get(prefix + '/secret-status').json()['secrets']['mp']['configured'] is False
        assert b.put(prefix, json={'max_jobs': 3}).status_code == 200
        assert secrets.get_secret('mp_api_key') is None
        assert a.get(prefix + '/secret-status').json()['secrets']['mp']['configured'] is False


def test_other_ai_root_cannot_revive_globally_cleared_llm(tmp_path, monkeypatch):
    from ai_mode.server import create_ai_mode_app
    monkeypatch.setenv('ENABLE_AI_MODE', 'true')
    roots = [tmp_path / name for name in ('a', 'b')]
    for root in roots:
        root.mkdir()
        (root / 'ai_config.json').write_text(json.dumps({'llm_api_key': 'old-ai-key'}))
    monkeypatch.setenv('VASP_AI_HOME', str(roots[0]))
    with TestClient(create_ai_mode_app()) as a:
        monkeypatch.setenv('VASP_AI_HOME', str(roots[1]))
        with TestClient(create_ai_mode_app()) as b:
            monkeypatch.setenv('VASP_AI_HOME', str(roots[0]))
            assert a.put('/ai/v1/settings/secrets/llm', json={'action': 'replace', 'value': 'new-ai-key'}).status_code == 200
            monkeypatch.setenv('VASP_AI_HOME', str(roots[1]))
            assert b.get('/ai/v1/settings/secret-status').json()['secrets']['llm']['source'] == 'credential_store'
            monkeypatch.setenv('VASP_AI_HOME', str(roots[0]))
            assert a.put('/ai/v1/settings/secrets/llm', json={'action': 'clear'}).json()['configured'] is False
            monkeypatch.setenv('VASP_AI_HOME', str(roots[1]))
            assert b.get('/ai/v1/settings/secret-status').json()['secrets']['llm']['configured'] is False
            assert b.put('/ai/v1/settings', json={'llm_model': 'another-model'}).status_code == 200
            assert secrets.get_secret('llm_api_key') is None
            monkeypatch.setenv('VASP_AI_HOME', str(roots[0]))
            assert a.get('/ai/v1/settings/secret-status').json()['secrets']['llm']['configured'] is False


@pytest.mark.parametrize('field', ['mp_api_key', 'llm_api_key'])
def test_cross_root_legacy_migration_only_before_secret_is_managed(tmp_path, field):
    from ai_mode.config import load_settings, save_settings
    from ai_mode.settings.global_api import secret_status
    a, b = tmp_path / 'a.json', tmp_path / 'b.json'
    for path in (a, b):
        path.write_text(json.dumps({field: 'old-local'}))
    first = load_settings(config_path=a, env={})
    stale = load_settings(config_path=b, env={})
    assert getattr(first, field) == 'old-local'
    save_settings(first, config_path=a)
    assert secrets.get_secret(field) == 'old-local'
    secrets.set_secret(field, 'new-shared')
    assert getattr(load_settings(config_path=b, env={}), field) == 'new-shared'
    secrets.delete_secret(field)
    # The stale object must not revive its local key through status or save.
    kind = 'mp' if field.startswith('mp') else 'llm'
    assert secret_status(stale, env={})[kind]['configured'] is False
    assert getattr(load_settings(config_path=b, env={}), field) == ''
    save_settings(stale, config_path=b)
    assert secrets.get_secret(field) is None
    # A new process/backend object reads the durable state, not an in-process cache.
    backend = secrets._get_backend()
    secrets.configure_backend(secrets.MemoryBackend(dict(backend._values)))
    assert getattr(load_settings(config_path=b, env={}), field) == ''
    secrets.set_secret(field, 'explicit-replacement')
    assert getattr(load_settings(config_path=b, env={}), field) == 'explicit-replacement'


def test_llm_owner_status_never_reads_retired_ai_ssh(tmp_path, monkeypatch):
    import ai_mode.server as server
    import ai_mode.settings.global_api as global_api
    monkeypatch.setenv('ENABLE_AI_MODE', 'true')
    monkeypatch.setenv('VASP_AI_HOME', str(tmp_path))
    monkeypatch.setenv('AI_MODE_SSH_HOST', 'stale.example')
    monkeypatch.setenv('AI_MODE_SSH_USERNAME', 'stale-user')
    secrets.set_secret('llm_api_key', 'fake-healthy-llm')
    class BrokenSSH:
        def get_password(self, *args):
            pytest.fail('AI LLM status must not read retired AI-owned SSH')
    monkeypatch.setattr(global_api, '_default_credential_store', lambda: BrokenSSH())
    calls = []
    class FakeOwner:
        def request(self, method, path, **kwargs):
            calls.append((method, path))
            return {'secrets': {'mp': {'configured': False}, 'ssh': {'configured': False}}}
    monkeypatch.setattr(server, '_get_project_store', lambda: SimpleNamespace(client=FakeOwner()))
    with TestClient(server.create_ai_mode_app()) as client:
        response = client.get('/ai/v1/settings/secret-status')
        assert response.status_code == 200, response.text
        assert response.json()['secrets']['llm']['source'] == 'credential_store'
        assert response.json()['secrets']['ssh']['configured'] is False
    assert calls == [('GET', '/settings/secret-status')]


def test_llm_status_does_not_read_ai_side_mp_backend(tmp_path, monkeypatch):
    import ai_mode.server as server
    monkeypatch.setenv('ENABLE_AI_MODE', 'true')
    monkeypatch.setenv('VASP_AI_HOME', str(tmp_path))
    class OwnerBackend(secrets.MemoryBackend):
        reject_mp = False
        def get(self, account):
            if self.reject_mp and account in {secrets.ACCOUNTS['mp_api_key'], secrets.MANAGED_ACCOUNTS['mp_api_key']}:
                pytest.fail('MP credential status belongs to the Toolbox owner')
            return super().get(account)
    backend = OwnerBackend()
    secrets.configure_backend(backend)
    secrets.set_secret('llm_api_key', 'healthy-model-key')
    class FakeOwner:
        def request(self, *args, **kwargs):
            return {'secrets': {'mp': {'configured': False}, 'ssh': {'configured': False}}}
    monkeypatch.setattr(server, '_get_project_store', lambda: SimpleNamespace(client=FakeOwner()))
    with TestClient(server.create_ai_mode_app()) as client:
        backend.reject_mp = True  # Exercise the owner-specific request, after startup.
        response = client.get('/ai/v1/settings/secret-status')
        assert response.status_code == 200, response.text
        assert response.json()['secrets']['llm']['configured'] is True


@pytest.mark.parametrize('failure', ['exception', 'noop'])
def test_managed_state_write_failure_prevents_successful_clear(tmp_path, failure):
    from backend.toolbox.api import create_toolbox_app
    class BrokenState(secrets.MemoryBackend):
        def set(self, account, value):
            if account == secrets.MANAGED_ACCOUNTS['mp_api_key']:
                if failure == 'exception':
                    raise RuntimeError('private state backend diagnostic')
                return
            super().set(account, value)
    backend = BrokenState({secrets.ACCOUNTS['mp_api_key']: 'stored-before-clear'})
    secrets.configure_backend(backend)
    with TestClient(create_toolbox_app(root=tmp_path, monitor_enabled=False)) as client:
        response = client.post('/api/v1/toolbox/settings/secrets/mp', json={'value': ''})
        assert response.status_code == 503
        assert 'stored-before-clear' not in response.text
        assert 'private state backend diagnostic' not in response.text
        assert secrets.get_secret('mp_api_key') == 'stored-before-clear'
        assert client.get('/api/v1/toolbox/settings/secret-status').json()['secrets']['mp']['configured'] is True


def test_managed_clear_keeps_environment_override_runtime_only(tmp_path, monkeypatch):
    from backend.toolbox.config import load_settings, save_settings
    path = tmp_path / 'toolbox_config.json'
    path.write_text(json.dumps({'mp_api_key': 'obsolete-local'}))
    secrets.delete_secret('mp_api_key')
    monkeypatch.setenv('TOOLBOX_MP_API_KEY', 'runtime-env-key')
    cfg = load_settings(config_path=path)
    assert cfg.mp_api_key == 'runtime-env-key'
    save_settings(cfg, config_path=path)
    assert secrets.get_secret('mp_api_key') is None
    assert 'runtime-env-key' not in path.read_text()
    monkeypatch.delenv('TOOLBOX_MP_API_KEY')
    assert load_settings(config_path=path).mp_api_key == ''


def _historical_action(state='executed', dispatch='not_dispatched', limit=1):
    job = {'key': 'static', 'attempt_id': 'attempt-1', 'status': 'draft'}
    binding = {'job_key': job['key'], 'attempt_id': job['attempt_id'], 'scope_id': 'scope-1'}
    action = {'kind': 'submit', 'action_id': 'action-1', 'state': state,
              'dispatch_state': dispatch, 'binding': binding, 'binding_hash': digest(binding)}
    scope = {'job_key': job['key'], 'attempt_id': job['attempt_id'], 'submit_limit': limit}
    return {'plan': {'jobs': [job]}, 'consent': {'actions': {'action-1': action},
            'computation_scopes': {'scope-1': scope}}}, job


def test_proven_no_dispatch_receipt_allows_fresh_user_confirmation():
    flow, job = _historical_action()
    assert select_job(flow, {'job_key': job['key'], 'attempt_id': job['attempt_id']}) is job


@pytest.mark.parametrize('change', ['unknown', 'executing', 'dispatching', 'dispatched',
                                  'missing_marker', 'consumed_scope', 'missing_scope',
                                  'wrong_attempt', 'bad_binding_hash', 'legacy_identity'])
def test_missing_submission_identity_stays_blocked_without_complete_no_dispatch_evidence(change):
    flow, job = _historical_action()
    action = flow['consent']['actions']['action-1']
    scope = flow['consent']['computation_scopes']['scope-1']
    if change in {'unknown', 'executing'}:
        action['state'] = change
    elif change in {'dispatching', 'dispatched'}:
        action['dispatch_state'] = change
    elif change == 'missing_marker':
        action.pop('dispatch_state')
    elif change == 'consumed_scope':
        scope['submit_limit'] = 0
    elif change == 'missing_scope':
        flow['consent']['computation_scopes'] = {}
    elif change == 'wrong_attempt':
        scope['attempt_id'] = 'another-attempt'
    elif change == 'bad_binding_hash':
        action['binding_hash'] = 'changed'
    else:
        action['binding'].pop('attempt_id')
        action['binding_hash'] = digest(action['binding'])
    before = copy.deepcopy(flow)
    with pytest.raises(ToolboxError) as error:
        select_job(flow, {'job_key': job['key'], 'attempt_id': job['attempt_id']})
    assert error.value.code == 'SUBMISSION_UNKNOWN'
    assert flow == before
