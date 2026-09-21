from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
import threading

import httpx
import pytest


@pytest.mark.parametrize(
    "payload",
    [
        {"schema_version": 1, "messages": [], "task_metadata": {}},
        {"schema_version": 1, "messages": {}, "task_metadata": []},
        {
            "schema_version": 1,
            "messages": {"project:task": {"role": "user", "content": "bad"}},
            "task_metadata": {},
        },
    ],
    ids=["messages-not-object", "metadata-not-object", "message-group-not-list"],
)
def test_invalid_chat_store_schema_is_read_only_and_releases_owner(tmp_path, payload):
    from backend.ai_mode.chat_store import ChatStore
    from backend.toolbox.owner import ProcessOwner

    root = tmp_path / "invalid-chat-schema"
    root.mkdir()
    path = root / "chat_store.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    original = path.read_bytes()

    with pytest.raises((TypeError, ValueError)):
        ChatStore(root)

    assert path.read_bytes() == original
    recovered_owner = ProcessOwner(root, "chat").acquire()
    recovered_owner.close()


def test_closed_chat_store_rejects_late_writes_before_owner_recovery(tmp_path):
    from backend.ai_mode.chat_store import ChatStore

    root = tmp_path / "chat-lifecycle"
    original = ChatStore(root)
    original.append("project", "task", "user", "before close")
    original.close()

    with pytest.raises(RuntimeError, match="closed"):
        original.append("project", "task", "assistant", "late producer")
    with pytest.raises(RuntimeError, match="closed"):
        original.update("project", "task", {"generation": {"state": "done"}})

    recovered = ChatStore(root)
    try:
        assert [item["content"] for item in recovered.messages("project", "task")] == [
            "before close"
        ]
    finally:
        recovered.close()


def test_chat_completion_and_status_do_not_require_toolbox_http(tmp_path):
    from backend.ai_mode.projects import ProjectStore as AiProjectStore
    from backend.ai_mode.streaming import ChatRun, generation_status
    from backend.toolbox.contracts import ToolboxError

    class OfflineToolboxClient:
        calls = 0

        def request(self, *_args, **_kwargs):
            self.calls += 1
            raise ToolboxError("TOOLBOX_UNAVAILABLE", "offline", 503)

        def close(self):
            return None

    offline = OfflineToolboxClient()
    store = AiProjectStore(client=offline, chat_root=tmp_path / "offline-chat")
    try:
        run = ChatRun(store, "project", "task")
        run.finish("answer survives", "local thinking", state="done")

        assert offline.calls == 0
        assert store.list_messages("project", "task")[-1]["content"] == "answer survives"
        assert generation_status(store, "project", "task") == {
            "run_id": run.run_id,
            "state": "done",
            "running": False,
        }
        assert offline.calls == 0
    finally:
        store.close()


def test_consent_polling_waits_for_executed_and_does_not_reexecute(monkeypatch):
    import backend.ai_mode.agent.runner as runner

    states = iter([
        {"state": "approved", "result": "only approved"},
        {"state": "executing", "result": "still running"},
        {"state": "executed", "result": "executed once"},
    ])

    class ReadOnlyExecutor:
        def execute_action(self, _card_id):
            raise AssertionError("AI adapter must never execute or promote a card")

    monkeypatch.setattr(runner, "_get_consent_card", lambda *_args: next(states))
    monkeypatch.setattr(runner, "_CONSENT_POLL_INTERVAL", 0)

    state, note = runner._wait_card_decision(
        None,
        "project",
        "task",
        "card",
        None,
        executor=ReadOnlyExecutor(),
        should_stop=None,
    )
    assert (state, note) == ("executed", "executed once")


@pytest.mark.parametrize("state", ["expired", "failed", "unknown"])
def test_terminal_consent_reason_is_not_reported_as_timeout(monkeypatch, state):
    import backend.ai_mode.agent.runner as runner
    from backend.ai_mode.agent.protocol import TOOL_MARK

    reason = f"distinct {state} evidence; do not retry"
    monkeypatch.setattr(
        runner,
        "_get_consent_card",
        lambda *_args: {"state": state, "result": reason},
    )
    resolved_state, note = runner._wait_card_decision(
        None,
        "project",
        "task",
        "card",
        None,
        executor=object(),
        should_stop=None,
    )
    assert resolved_state == state
    assert note == reason
    assert "timeout" not in note.lower()
    assert "超时" not in note

    class PendingExecutor:
        store = object()
        project_id = "project"
        task_id = "task"

        def handle(self, _name, _args):
            return runner._CONSENT_PENDING + "card"

    class OneToolLlm:
        def complete(self, _messages, **_kwargs):
            return SimpleNamespace(text=TOOL_MARK + json.dumps({
                "name": "copy_inputs", "args": {}, "reason": "review",
            }))

    monkeypatch.setattr(
        runner,
        "_wait_card_decision",
        lambda *_args, **_kwargs: (state, reason),
    )
    answer = runner._decision_loop(
        PendingExecutor(),
        OneToolLlm(),
        [],
        max_rounds=1,
    )
    assert reason in answer
    assert runner._CONSENT_PENDING not in answer
    assert "等待授权超时" not in answer


def test_slow_toolbox_proxy_request_does_not_block_ai_ping(monkeypatch):
    import backend.ai_mode.server as server

    entered = threading.Event()
    release = threading.Event()

    class SlowStore:
        def list_projects(self):
            entered.set()
            assert release.wait(2), "test failed to release slow Toolbox response"
            return []

    monkeypatch.setattr(server, "_get_project_store", lambda: SlowStore())
    monkeypatch.setattr(server, "load_settings", lambda: SimpleNamespace(enabled=True))
    app = server.create_ai_mode_app()

    async def exercise():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://ai.test") as client:
            loop = asyncio.get_running_loop()
            started_at = loop.time()

            async def delayed_ping():
                await asyncio.sleep(0.05)
                response = await client.get("/ai/v1/ping")
                return response, loop.time() - started_at

            timer = threading.Timer(0.7, release.set)
            timer.start()
            slow = asyncio.create_task(client.get("/ai/v1/projects"))
            ping = asyncio.create_task(delayed_ping())
            try:
                ping_response, latency = await asyncio.wait_for(ping, timeout=0.35)
                assert ping_response.status_code == 200
                assert latency < 0.30
                assert entered.is_set()
            finally:
                release.set()
                timer.cancel()
                slow_response = await slow
                assert slow_response.status_code == 200

    asyncio.run(exercise())
