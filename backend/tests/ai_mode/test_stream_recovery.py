"""Deterministic disconnect, duplicate-request and terminal persistence tests."""

import json
import queue
import socket
import threading
import time

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

from ai_mode import consent, server
from ai_mode.projects import ProjectStore
from ai_mode.streaming import ChatRun, GenerationBusy, generation_status, request_stop


@pytest.fixture
def task(tmp_path):
    store = ProjectStore(tmp_path)
    pid = store.create_project("recovery")["id"]
    tid = store.create_task(pid, goal="test")["id"]
    return store, pid, tid


def test_disconnect_does_not_stop_and_result_is_persisted_once(task):
    store, pid, tid = task
    run = ChatRun(*task)
    entered, release = threading.Event(), threading.Event()

    def source():
        entered.set()
        assert release.wait(3)
        assert not run.should_stop()
        yield {"type": "answer", "text": "real partial"}
        yield {"type": "done", "answer": "completed"}

    worker = threading.Thread(target=run.produce, args=(source,))
    worker.start()
    try:
        assert entered.wait(3)
        run.detach()
        assert generation_status(*task)["running"] is True
        with pytest.raises(GenerationBusy):
            ChatRun(*task)
    finally:
        release.set()
        worker.join(3)
    assert not worker.is_alive()
    assert [m["content"] for m in store.list_messages(pid, tid)] == ["completed"]
    assert generation_status(*task)["running"] is False
    assert not request_stop(pid, tid)


@pytest.mark.parametrize("raise_error", [True, False])
def test_unexpected_failure_or_missing_done_preserves_partial_and_sanitizes(task, raise_error):
    store, pid, tid = task
    run = ChatRun(*task)

    def source():
        yield {"type": "thinking", "text": "reason"}
        yield {"type": "answer", "text": "partial answer"}
        if raise_error:
            raise RuntimeError("sk-secret password remote-private")

    run.produce(source)
    message = store.list_messages(pid, tid)[-1]
    assert message["thinking"] == "reason"
    assert "partial answer" in message["content"]
    assert "生成意外中断" in message["content"]
    assert "sk-secret" not in message["content"]
    assert generation_status(*task)["state"] == "error"
    events = []
    while not run.events.empty():
        events.append(run.events.get_nowait())
    assert events[-2]["type"] == "done"
    assert "sk-secret" not in json.dumps(events)


def test_explicit_stop_survives_disconnect_until_producer_finishes(task):
    run = ChatRun(*task)
    assert request_stop(task[1], task[2])
    run.detach()
    assert run.should_stop()
    assert generation_status(*task)["state"] == "stopping"
    with pytest.raises(GenerationBusy):
        ChatRun(*task)
    run.produce(lambda: iter(()))
    assert generation_status(*task)["state"] == "stopped"


def test_overflow_is_explicit_and_does_not_lose_persisted_result(task):
    run = ChatRun(*task, capacity=2)

    def source():
        for _ in range(1000):
            yield {"type": "answer", "text": "x"}
        yield {"type": "done", "answer": "x" * 1000}

    run.produce(source)
    assert run.events.get_nowait()["code"] == "STREAM_OVERFLOW"
    assert run.events.get_nowait()["type"] == "_end"
    assert run.answer == "x" * 1000
    assert task[0].list_messages(task[1], task[2])[-1]["content"] == "x" * 1000


def test_terminal_event_is_visible_only_after_persistence(task):
    run = ChatRun(*task)
    run.produce(lambda: iter([{"type": "done", "answer": "final"}]))
    assert run.events.get_nowait()["type"] == "done"
    assert task[0].list_messages(task[1], task[2])[-1]["content"] == "final"
    assert generation_status(*task)["running"] is False


def test_restart_state_is_interrupted_without_replaying_tools(task):
    store, pid, tid = task
    store.update_task(pid, tid, generation={"run_id": "old", "state": "running"})
    restarted = ProjectStore(store.root)
    status = generation_status(restarted, pid, tid)
    assert status["running"] is False
    assert status["state"] == "interrupted"
    assert restarted.list_messages(pid, tid) == []
    assert restarted.get_task(pid, tid)["generation"]["state"] == "interrupted"


def test_messages_restore_pending_card_and_duplicate_http_is_rejected(task, monkeypatch):
    store, pid, tid = task
    monkeypatch.setenv("ENABLE_AI_MODE", "true")
    monkeypatch.setenv("VASP_AI_HOME", str(store.root))
    monkeypatch.setattr(server, "_get_project_store", lambda: store)
    action = consent.card_payload(tool="hpc_upload", args={}, risk="high", reason="test",
                                  batch_key="test", kind="workspace", summary="test")
    consent.save_card(store, pid, tid, {}, action)
    run = ChatRun(*task)
    try:
        # No lifespan needed: the actual route contract is under test, no HPC.
        client = TestClient(server.create_ai_mode_app())
        url = f"/ai/v1/projects/{pid}/tasks/{tid}/messages"
        data = client.get(url).json()
        assert data["generation"]["running"] is True
        assert data["pending_actions"][0]["card_id"] == action["card_id"]
        for suffix in ("", "/stream"):
            response = client.post(url + suffix, json={"content": "duplicate"})
            assert response.status_code == 409
            assert response.json()["error"]["code"] == "GENERATION_RUNNING"
        assert store.list_messages(pid, tid) == []
    finally:
        run.finish("", state="stopped")


def test_persistence_failure_never_emits_successful_done(task, monkeypatch):
    run = ChatRun(*task)

    def fail(*args, **kwargs):
        raise OSError("private disk path")

    monkeypatch.setattr(task[0], "append_message", fail)
    run.produce(lambda: iter([{"type": "done", "answer": "final"}]))
    events = []
    while True:
        try:
            events.append(run.events.get_nowait())
        except queue.Empty:
            break
    assert not any(e["type"] == "done" for e in events)
    assert any(e.get("code") == "GENERATION_PERSIST_FAILED" for e in events)
    assert generation_status(*task)["running"] is False


def test_final_poll_cannot_return_idle_with_pre_completion_messages(task, monkeypatch):
    store, pid, tid = task
    monkeypatch.setenv("ENABLE_AI_MODE", "true")
    monkeypatch.setenv("VASP_AI_HOME", str(store.root))
    monkeypatch.setattr(server, "_get_project_store", lambda: store)

    def completes_at_status_read(*args):
        store.append_message(pid, tid, "assistant", "completed at poll boundary")
        return {"running": False, "state": "done"}

    monkeypatch.setattr(server, "generation_status", completes_at_status_read)
    client = TestClient(server.create_ai_mode_app())
    data = client.get(f"/ai/v1/projects/{pid}/tasks/{tid}/messages").json()
    assert data["generation"]["running"] is False
    assert data["messages"][-1]["content"] == "completed at poll boundary"


def test_real_http_disconnect_and_poll_recovery_three_cycles(task, monkeypatch):
    """Real loopback TCP + uvicorn, deterministic reply fixture, zero HPC."""
    store, pid, tid = task
    monkeypatch.setenv("ENABLE_AI_MODE", "true")
    monkeypatch.setenv("VASP_AI_HOME", str(store.root))
    monkeypatch.setattr(server, "_get_project_store", lambda: store)
    release = threading.Event()

    def source(store, pid, tid, content, should_stop=None):
        yield {"type": "status", "text": "test fixture running; no LLM/HPC"}
        assert release.wait(5)
        assert not should_stop(), "HTTP disconnect must not request stop"
        yield {"type": "done", "answer": "fixture complete " + content}

    monkeypatch.setattr(server._chat, "reply_stream", source)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    service = uvicorn.Server(uvicorn.Config(server.create_ai_mode_app(), lifespan="off", log_level="error"))
    worker = threading.Thread(target=service.run, kwargs={"sockets": [listener]}, daemon=True)
    worker.start()
    url = f"http://127.0.0.1:{port}/ai/v1/projects/{pid}/tasks/{tid}/messages"
    try:
        deadline = time.monotonic() + 5
        while not service.started and time.monotonic() < deadline:
            time.sleep(0.01)
        assert service.started
        with httpx.Client(timeout=5, trust_env=False) as client:
            for cycle in range(3):
                release.clear()
                with client.stream("POST", url + "/stream", json={"content": str(cycle)}) as response:
                    assert response.status_code == 200
                    assert any('"status"' in row for row in response.iter_lines())
                # Closing response simulates navigating away or a dropped tab.
                assert client.get(url).json()["generation"]["running"] is True
                duplicate = client.post(url + "/stream", json={"content": "must not append"})
                assert duplicate.status_code == 409
                release.set()
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    result = client.get(url).json()
                    if not result["generation"]["running"]:
                        break
                    time.sleep(0.01)
                assert result["generation"]["running"] is False
                assert result["messages"][-1]["content"] == "fixture complete " + str(cycle)
            assert len(store.list_messages(pid, tid)) == 6
    finally:
        release.set()
        service.should_exit = True
        worker.join(5)
        listener.close()
    assert not worker.is_alive()
