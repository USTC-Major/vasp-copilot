"""Independent D review: model tool calls cannot become consent or arbitrary HTTP."""

from types import SimpleNamespace

import pytest

from ai_mode.agent.tools import ToolExecutor

ACTION_ID = "a" * 32
SCOPE_ID = "b" * 32
ROOT_ID = "c" * 32
PLAN_ARGS = {
    "scope_id": SCOPE_ID, "scope_version": 1, "job_key": "a", "attempt_id": "attempt-a",
    "idempotency_key": "key-a",
    "items": [{
        "item_id": "text-a", "op": "write_text", "text": "safe text",
        "destination": {"root_id": ROOT_ID, "relative_path": "a/note.txt"},
        "on_conflict": "fail",
    }],
}


class RecordingClient:
    def __init__(self):
        self.calls = []

    @staticmethod
    def task_path(project_id, task_id):
        return f"/projects/{project_id}/tasks/{task_id}"

    def tool(self, project_id, task_id, name, args):
        self.calls.append(("tool", project_id, task_id, name, args))
        if name == "remote_inspect":
            return {"ok": True, "data": {"path": "/readonly/info.txt", "text": "fixture content"}}
        if name == "remote_file_plan":
            return {"ok": True, "pending": {"card_id": ACTION_ID, "state": "pending"}, "data": {"state": "pending"}}
        return {"ok": True, "result": "existing tool result"}

    def request(self, method, path, **kwargs):
        self.calls.append(("request", method, path, kwargs))
        if path.endswith("/detail"):
            return {
                "ok": True,
                "flow": {"jobs": [{"key": "a", "attempt_id": "attempt-a"}]},
                "file_roots": [{"root_id": ROOT_ID, "version": 1}],
                "file_scopes": [{"scope_id": SCOPE_ID, "version": 1, "state": "proposed"}],
                "file_actions": [{"action_id": ACTION_ID, "state": "pending"}],
            }
        if path.endswith("/file-actions"):
            return {"ok": True, "data": {"active": [], "actions": [], "next_cursor": None}}
        if path.endswith("/reconcile"):
            return {"ok": True, "data": {"action_id": ACTION_ID, "state": "unknown"}}
        return {"ok": True, "card": {"action_id": ACTION_ID, "kind": "remote_file", "state": "unknown"}}


@pytest.fixture
def planner():
    client = RecordingClient()
    executor = ToolExecutor(
        store=SimpleNamespace(client=client), project_id="project", task_id="task", client=client
    )
    return executor, client


@pytest.mark.parametrize(
    "name,args",
    [
        ("create_scope", {"approval_mode": "human"}),
        ("put_roots", {"roots": [{"path": "/"}]}),
        ("approve", {"card_id": ACTION_ID}),
        ("resolve", {"card_id": ACTION_ID, "approved": True}),
        ("revoke", {"scope_id": SCOPE_ID}),
        ("http_request", {"method": "POST", "path": f"/consents/{ACTION_ID}"}),
        ("hpc_exec", {"command": "true"}),
        ("begin", {"action_id": ACTION_ID}),
        ("commit", {"action_id": ACTION_ID}),
        ("potcar_generate", {}),
    ],
)
def test_untrusted_names_do_not_issue_http(planner, name, args):
    executor, client = planner
    result = executor.handle(name, args)
    assert "[TOOL_FORBIDDEN]" in result
    assert client.calls == []


@pytest.mark.parametrize(
    "name,args",
    [
        ("remote_inspect", {"path": "/readonly/info.txt", "view": "text", "expected_evidence": {"inode": 1}}),
        ("remote_file_plan", {**PLAN_ARGS, "scope_confirmation": {"scope_id": SCOPE_ID, "version": 1}}),
        ("remote_file_context", {"url": "/consents/file-card"}),
        ("remote_file_status", {"action_id": ACTION_ID, "method": "POST"}),
        ("remote_file_reconcile", {"action_id": ACTION_ID, "body": {"force": True}}),
    ],
)
def test_file_tool_arguments_reject_permission_fields_before_http(planner, name, args):
    executor, client = planner
    result = executor.handle(name, args)
    assert "[INVALID_TOOL_ARGS]" in result
    assert client.calls == []


def test_allowed_file_tool_results_are_visible_and_plan_only_proposes(planner):
    executor, client = planner
    inspect = executor.handle("remote_inspect", {"path": "/readonly/info.txt", "view": "text"})
    proposed = executor.handle("remote_file_plan", PLAN_ARGS)
    assert "fixture content" in inspect
    assert proposed == "__CONSENT_PENDING__" + ACTION_ID
    assert [call[3] for call in client.calls] == ["remote_inspect", "remote_file_plan"]


def test_fixed_status_tools_use_only_their_routes(planner):
    executor, client = planner
    for name, args in (
        ("remote_file_context", {}),
        ("remote_file_status", {"action_id": ACTION_ID}),
        ("remote_file_history", {"limit": 20}),
        ("remote_file_reconcile", {"action_id": ACTION_ID}),
    ):
        result = executor.handle(name, args)
        assert isinstance(result, str) and result
    requests = [call for call in client.calls if call[0] == "request"]
    assert [(call[1], call[2]) for call in requests] == [
        ("GET", "/projects/project/tasks/task/detail"),
        ("GET", f"/projects/project/tasks/task/consents/{ACTION_ID}"),
        ("GET", "/projects/project/tasks/task/file-actions"),
        ("POST", f"/projects/project/tasks/task/file-actions/{ACTION_ID}/reconcile"),
    ]
    assert requests[-1][3].get("json") == {}
