"""Independent review of the AI HTTP consent facade for file cards."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from ai_mode import server


ACTION_ID = "a" * 32


class RecordingToolbox:
    def __init__(self, kind):
        self.kind = kind
        self.calls = []

    @staticmethod
    def task_path(project_id, task_id):
        return f"/projects/{project_id}/tasks/{task_id}"

    def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        if method == "GET":
            return {"card": {"card_id": ACTION_ID, "action_id": ACTION_ID, "kind": self.kind, "state": "pending"}}
        if method == "POST":
            return {"ok": True, "card": {"card_id": ACTION_ID, "kind": self.kind, "state": "executed"}, "result": "approved"}
        raise AssertionError((method, path))


@pytest.fixture
def ai_client(monkeypatch, tmp_path):
    monkeypatch.setenv("ENABLE_AI_MODE", "true")
    monkeypatch.setenv("VASP_AI_HOME", str(tmp_path / "ai"))
    toolbox = RecordingToolbox("remote_file")
    store = SimpleNamespace(
        client=toolbox,
        get_task=lambda *_: {"id": "task"},
        append_message=lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(server, "_get_project_store", lambda: store)
    return TestClient(server.create_ai_mode_app()), toolbox


@pytest.mark.parametrize("approved", [True, False])
def test_ai_consent_cannot_decide_file_card_even_when_scope_already_active(ai_client, approved):
    client, toolbox = ai_client
    response = client.post(
        "/ai/v1/projects/project/tasks/task/messages/consent",
        json={"card_id": ACTION_ID, "approved": approved},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "REMOTE_FILE_REVIEW_REQUIRED"
    assert "Toolbox" in response.json()["error"]["message"]
    assert [method for method, _path, _kwargs in toolbox.calls] == ["GET"]


def test_old_non_file_card_still_uses_existing_ai_consent_path(ai_client):
    client, toolbox = ai_client
    toolbox.kind = "submit"
    response = client.post(
        "/ai/v1/projects/project/tasks/task/messages/consent",
        json={"card_id": ACTION_ID, "approved": True},
    )
    assert response.status_code == 200
    assert response.json()["card"]["kind"] == "submit"
    assert [method for method, _path, _kwargs in toolbox.calls] == ["GET", "POST"]
