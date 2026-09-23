"""Offline contract checks for the AI-only file adapter and legacy consent gate."""
import json

import pytest
from fastapi.testclient import TestClient

from backend.ai_mode.agent.runner import _stream_card
from backend.ai_mode.agent.tools import ToolExecutor, _CONSENT_PENDING
from backend.ai_mode import server


ACTION = 'a' * 32
ROOT = 'b' * 32
SCOPE = 'c' * 32


class Client:
    def __init__(self):
        self.calls = []
        self.reply = {}

    @staticmethod
    def task_path(project_id, task_id):
        return f'/projects/{project_id}/tasks/{task_id}'

    def tool(self, project_id, task_id, name, args):
        self.calls.append(('tool', name, args))
        return self.reply

    def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        return self.reply


def executor(client):
    return ToolExecutor(store=object(), project_id='p', task_id='t', client=client)


@pytest.mark.parametrize('name,args', [
    ('file_roots', {}), ('create_scope', {}), ('approve', {}),
    ('http', {'url': '/consents/x'}), ('shell', {'cmd': 'true'}),
    ('remote_file_status', {'action_id': '../x'}), ('remote_file_reconcile', {'action_id': ACTION, 'method': 'PUT'}),
    ('remote_file_context', {'include_secrets': True}),
    ('remote_inspect', {'path': '/tmp', 'view': {'text': True}}),
    ('remote_file_history', {'limit': True}),
])
def test_forbidden_and_malformed_are_rejected_before_http(name, args):
    client = Client()
    assert executor(client).handle(name, args).startswith(('[TOOL_FORBIDDEN]', '[INVALID_TOOL_ARGS]'))
    assert client.calls == []


def test_legacy_schema_tool_still_forwards():
    client = Client()
    client.reply = {'ok': True, 'result': 'phase=idle'}
    assert executor(client).handle('get_state', {}) == 'phase=idle'
    assert client.calls == [('tool', 'get_state', {})]


def test_inspect_data_is_visible_bounded_and_strips_tokens():
    client = Client()
    client.reply = {'ok': True, 'data': {'path': '/research/in', 'entries': ['POSCAR'], 'prepare_token': 'secret'}}
    note = executor(client).handle('remote_inspect', {'path': '/research/in', 'view': 'list'})
    assert '/research/in' in note and 'POSCAR' in note and 'secret' not in note


def test_plan_pending_and_terminal_replay():
    client = Client()
    args = {'scope_id': SCOPE, 'scope_version': 1, 'job_key': 'relax', 'attempt_id': 'attempt-1',
            'idempotency_key': 'stable', 'items': [{'item_id': 'one', 'op': 'write_text',
            'destination': {'root_id': ROOT, 'relative_path': 'relax/job.sh'},
            'on_conflict': 'fail', 'text': 'echo ok'}]}
    client.reply = {'ok': True, 'pending': {'card_id': ACTION, 'state': 'pending'}}
    assert executor(client).handle('remote_file_plan', args) == _CONSENT_PENDING + ACTION
    client.reply['pending'] = {'card_id': ACTION, 'kind': 'remote_file', 'state': 'executed', 'binding': {'manifest': {'items': [{'text': 'private'}]}}}
    note = executor(client).handle('remote_file_plan', args)
    assert 'executed' in note and 'private' not in note


def test_fixed_routes_and_minimal_projections():
    client = Client()
    ex = executor(client)
    client.reply = {'ok': True, 'flow': {'jobs': [{'key': 'relax', 'attempt_id': 'attempt-1', 'secret': 'bad'}]},
                    'file_roots': [{'root_id': ROOT, 'version': 1, 'requested_path': '/research', 'password': 'bad'}],
                    'file_scopes': [{'scope_id': SCOPE, 'version': 1, 'root_bindings': [{'root_id': ROOT, 'version': 1, 'destination_prefixes': ['relax']}],
                                     'max_operations': 2, 'max_total_bytes': 50, 'source_bindings': [{'requested_path': '/source'}]}],
                    'file_actions': []}
    context = ex.handle('remote_file_context', {})
    assert 'destination_prefixes' in context and 'max_total_bytes' in context and 'password' not in context
    assert client.calls[-1][:2] == ('GET', '/projects/p/tasks/t/detail')

    card = {'card_id': ACTION, 'action_id': ACTION, 'kind': 'remote_file', 'state': 'unknown',
            'binding': {'manifest': {'items': [{'text': 'private'}]}},
            'receipt': {'phase': 'finished', 'items': [{'item_id': 'one', 'state': 'committed'}]}}
    client.reply = {'ok': True, 'card': card}
    status = ex.handle('remote_file_status', {'action_id': ACTION})
    assert 'committed' in status and 'private' not in status
    assert client.calls[-1][:2] == ('GET', '/projects/p/tasks/t/consents/' + ACTION)

    client.reply = {'ok': True, 'data': {'active': [card], 'actions': [], 'next_cursor': None}}
    assert 'unknown' in ex.handle('remote_file_history', {'limit': 20, 'cursor': '0'})
    assert client.calls[-1] == ('GET', '/projects/p/tasks/t/file-actions', {'params': {'limit': 20, 'cursor': '0'}})
    client.reply = {'ok': True, 'data': card}
    assert 'unknown' in ex.handle('remote_file_reconcile', {'action_id': ACTION})
    assert client.calls[-1] == ('POST', '/projects/p/tasks/t/file-actions/' + ACTION + '/reconcile', {'json': {}})


def test_stream_card_excludes_manifest():
    card = {'kind': 'remote_file', 'card_id': ACTION, 'state': 'pending',
            'binding': {'job_key': 'relax', 'manifest': {'items': [{'text': 'private'}]}}}
    projected = _stream_card(card)
    assert projected['kind'] == 'remote_file' and projected['card_id'] == ACTION
    assert 'private' not in json.dumps(projected)


@pytest.mark.parametrize('state', ['pending', 'approved', 'executing', 'unknown', 'executed'])
def test_ai_legacy_consent_refuses_file_cards(monkeypatch, state):
    client = Client()
    client.reply = {'card': {'kind': 'remote_file', 'card_id': ACTION, 'state': state}}
    store = type('Store', (), {'client': client})()
    monkeypatch.setenv('ENABLE_AI_MODE', 'true')
    monkeypatch.setattr(server, '_get_project_store', lambda: store)
    with TestClient(server.create_ai_mode_app()) as app:
        response = app.post('/ai/v1/projects/p/tasks/t/messages/consent',
                            json={'card_id': ACTION, 'approved': True})
    assert response.status_code == 403
    assert response.json()['error']['code'] == 'REMOTE_FILE_REVIEW_REQUIRED'
    assert [call[0] for call in client.calls] == ['GET']


def test_ai_legacy_consent_preserves_normal_card(monkeypatch):
    client = Client()
    def request(method, path, **kwargs):
        client.calls.append((method, path, kwargs))
        if method == 'GET':
            return {'card': {'kind': 'submit', 'card_id': ACTION, 'state': 'pending'}}
        return {'card': {'kind': 'submit', 'card_id': ACTION, 'state': 'executed'}}
    client.request = request
    store = type('Store', (), {'client': client})()
    monkeypatch.setenv('ENABLE_AI_MODE', 'true')
    monkeypatch.setattr(server, '_get_project_store', lambda: store)
    with TestClient(server.create_ai_mode_app()) as app:
        response = app.post('/ai/v1/projects/p/tasks/t/messages/consent',
                            json={'card_id': ACTION, 'approved': True})
    assert response.status_code == 200
    assert [call[0] for call in client.calls] == ['GET', 'POST']


def test_real_owner_receipt_projection_uses_helper_fields(tmp_path):
    """Exercise C's MemoryRemote worker through HTTP, then inspect planner status."""
    from backend.tests.file_action_integration.test_owner import World, future
    from backend.toolbox.api import create_toolbox_app
    from backend.toolbox.client import ToolboxClient
    from backend.toolbox.config import ExecutionConfig

    world = World()
    app = create_toolbox_app(root=tmp_path, settings_loader=lambda: ExecutionConfig(data_dir=tmp_path),
                             monitor_enabled=False, file_factory=world.factory)
    with TestClient(app) as http:
        svc = app.state.toolbox
        pid = svc.store.create_project('fixture')['id']
        tid = svc.store.create_task(pid, 'fixture')['id']
        svc.store.update_task(pid, tid, flow={'plan': {'jobs': [
            {'key': 'j', 'attempt_id': 'attempt', 'status': 'draft'}]}})
        base = f'/api/v1/toolbox/projects/{pid}/tasks/{tid}'
        roots = http.put(base + '/file-roots', json={'expected_version': 0, 'roots': [{'path': '/research'}]})
        assert roots.status_code == 200
        root = roots.json()['data']['roots'][0]
        scope = http.post(base + '/computation-scopes', json={
            'job_key': 'j', 'attempt_id': 'attempt',
            'root_bindings': [{'root_id': root['root_id'], 'version': 1, 'destination_prefixes': ['']}],
            'allowed_operations': ['write_text'], 'source_paths': [],
            'max_operations': 2, 'max_total_bytes': 100, 'expires_at': future(), 'approval_mode': 'human'})
        assert scope.status_code == 201
        scope = scope.json()['data']
        ai = executor(ToolboxClient(client=http))
        ai.project_id, ai.task_id = pid, tid
        args = {'scope_id': scope['scope_id'], 'scope_version': 1, 'job_key': 'j',
                'attempt_id': 'attempt', 'idempotency_key': 'one', 'items': [{
                    'item_id': 'text', 'op': 'write_text', 'text': 'fixture',
                    'destination': {'root_id': root['root_id'], 'relative_path': 'result.txt'},
                    'on_conflict': 'fail'}]}
        pending = ai.handle('remote_file_plan', args)
        assert pending.startswith(_CONSENT_PENDING)
        action_id = pending[len(_CONSENT_PENDING):]
        approved = http.post(base + '/consents/' + action_id, json={
            'approved': True, 'scope_confirmation': {'scope_id': scope['scope_id'], 'version': 1}})
        assert approved.status_code == 202
        assert svc.files.wait_idle()
        status = json.loads(ai.handle('remote_file_status', {'action_id': action_id}))
        row = status['receipt']['items'][0]
        assert row['state'] == 'committed'
        assert row['bytes_processed'] == 7
        assert row['target']['canonical_path'] == '/research/result.txt'
        assert 'manifest' not in status['binding']
        assert 'prepare_token' not in json.dumps(status)
