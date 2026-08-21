from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.v1.deps import settings
from app.llm import get_explainer, reset_explainer, runtime, set_explainer
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    yield
    reset_explainer()
    runtime.set_active(None)


def test_chat_unconfigured_reports_hint():
    r = client.post("/api/v1/chat", json={"message": "你好"})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["usable"] is False
    assert "未启用" in data["answer"]


def test_chat_blank_message():
    r = client.post("/api/v1/chat", json={"message": "   "})
    assert r.status_code == 200
    data = r.json()["data"]
    assert "请输入内容" in data["answer"]


def test_chat_with_fake_explainer_forwards_history():
    calls: dict = {}

    class FakeExplainer:
        def chat_general(self, question, history=None):
            calls["question"] = question
            calls["history"] = history
            return "好的，" + question

    set_explainer(FakeExplainer())
    r = client.post("/api/v1/chat", json={
        "message": "帮我看看 SCF 不收敛",
        "history": [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好，有什么可以帮你？"},
            {"role": "system", "content": "不应透传"},
        ],
    })
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["usable"] is True
    assert data["answer"] == "好的，帮我看看 SCF 不收敛"
    assert calls["question"] == "帮我看看 SCF 不收敛"
    # 只透传 user/assistant 且有内容的消息
    assert len(calls["history"]) == 2


def test_chat_degraded_on_exception():
    class BoomExplainer:
        def chat_general(self, question, history=None):
            raise RuntimeError("boom")

    set_explainer(BoomExplainer())
    r = client.post("/api/v1/chat", json={"message": "hi"})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["degraded"] is True
    assert "暂不可用" in data["answer"]


def test_chat_uses_runtime_config_explainer():
    # 保存运行期配置后，get_explainer 应返回非空实例（不触发真实网络）
    client.post("/api/v1/llm/config", json={
        "base_url": "http://127.0.0.1:8001/v1",
        "api_key": "sk-test-abc",
        "model": "my-local-model",
    })
    explainer = get_explainer(settings)
    assert explainer is not None


def test_history_save_and_get():
    client.delete("/api/v1/chat/history")
    r = client.post("/api/v1/chat/history", json={
        "messages": [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好，有什么可以帮你？"},
        ]
    })
    assert r.status_code == 200
    assert len(r.json()["data"]["messages"]) == 2
    g = client.get("/api/v1/chat/history")
    assert g.status_code == 200
    msgs = g.json()["data"]["messages"]
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"


def test_history_clear():
    client.post("/api/v1/chat/history", json={
        "messages": [{"role": "user", "content": "x"}],
    })
    r = client.delete("/api/v1/chat/history")
    assert r.status_code == 200
    g = client.get("/api/v1/chat/history")
    assert g.json()["data"]["messages"] == []


def test_history_persists_to_disk(tmp_path):
    client.post("/api/v1/chat/history", json={
        "messages": [{"role": "user", "content": "persist me"}],
    })
    assert tmp_path.joinpath("chat_history.json").is_file()
    g = client.get("/api/v1/chat/history")
    assert g.json()["data"]["messages"][0]["content"] == "persist me"


def test_history_ignores_invalid_roles():
    r = client.post("/api/v1/chat/history", json={
        "messages": [
            {"role": "user", "content": "ok"},
            {"role": "system", "content": "skip me"},
            {"role": "assistant", "content": ""},
        ]
    })
    assert r.status_code == 200
    msgs = r.json()["data"]["messages"]
    assert len(msgs) == 1 and msgs[0]["content"] == "ok"