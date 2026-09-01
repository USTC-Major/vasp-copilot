# -*- coding: utf-8 -*-
"""M13 集成验证冒烟：真实 FastAPI 装配一次会话联调主链路（全离线）。

端到端演示路径（建项目→任务→（假超算）全流程/任意片段）由前端 AiFlow 测试
在演示层（MSW + 假进度/假提交）走通；此处用真实 app 冒烟后端装配正确性：
开关态护栏、配置掩码、设置写回与掩码、项目精度 CRUD、敏感键拒写。
安全回归（越界/恶意/超限/断连）由 exec/ssh/settings/tools/jobs/workflow
模块测试各自的注入假件覆盖，本文件不重复构造。
"""

import json

import pytest
from fastapi.testclient import TestClient

from ai_mode.server import create_ai_mode_app

GOOD_ACCURACY = [
    "relax 全流程：ENCUT=520，EDIFF 收敛到 1e-5",
    "DOS 计算用四面体 ISMEAR = -5",
]


@pytest.fixture
def client(monkeypatch, tmp_path):
    """一次真实 app 会话：已启用 + 假 LLM，方便跑通设置/项目设置链路。"""
    monkeypatch.setenv("VASP_AI_HOME", str(tmp_path))
    monkeypatch.setenv("ENABLE_AI_MODE", "true")
    monkeypatch.setenv("AI_MODE_LLM_PROVIDER", "fake")
    import ai_mode.projects as _projects_module
    _projects_module._store = None       # 每次测试间重置全局存储单例，避免跨文件污染
    monkeypatch.setenv("AI_MODE_LLM_API_KEY", "sk-e2e-topsecret")
    app = create_ai_mode_app()
    with TestClient(app) as c:
        yield c


def test_boot_config_masked_and_llm_offline(client):
    """启动即用：开关态/目录/配置掩码不泄密/假 LLM 离线可用。"""
    ping = client.get("/ai/v1/ping")
    assert ping.json()["enabled"] is True
    layout = client.get("/ai/v1/layout").json()["dirs"]
    assert "sessions" in layout
    conf = client.get("/ai/v1/config")
    assert conf.status_code == 200
    body = conf.text
    assert "sk-e2e-topsecret" not in body
    assert conf.json()["config"]["llm"]["api_key"] == "<redacted>"
    llm = client.get("/ai/v1/llm/status")
    assert llm.status_code == 200
    data = llm.json()
    assert data["ok"] is True and data["provider"] == "fake"


def test_settings_write_then_read_masked(client):
    """设置写回生效，读回仍掩码、不泄密；非法回写被拒。"""
    ok = client.put("/ai/v1/settings",
                    json={"max_jobs": 5, "ssh_host": "hpc.example.com"})
    assert ok.status_code == 200 and ok.json()["ok"] is True
    cur = client.get("/ai/v1/settings").json()["settings"]
    assert cur["max_jobs"] == 5
    assert cur["ssh"]["host"] == "hpc.example.com"
    assert cur["llm"]["api_key"] == "<redacted>"
    assert "sk-e2e-topsecret" not in json.dumps(ok.json())
    bad = client.request("PUT", "/ai/v1/settings", json={"max_jobs": 0})
    assert bad.status_code == 400


def test_settings_test_llm_offline(client):
    """设置页「测试连通」派发到假 LLM 提供方，离线可回 ok。"""
    r = client.post("/ai/v1/settings/test/llm")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True and data["provider"] == "fake"


def test_project_accuracy_crud_and_sensitive_reject(client):
    """项目额外设置（计算精度）写读删闭环；敏感 key 一律拒写。"""
    pid = "e2e_prj_nacl缺陷"
    resp = client.put(f"/ai/v1/projects/{pid}/settings",
                      json={"accuracy": GOOD_ACCURACY})
    assert resp.status_code == 200 and resp.json()["ok"] is True
    got = client.get(f"/ai/v1/projects/{pid}/settings").json()["settings"]
    assert got["accuracy"] == GOOD_ACCURACY
    assert "EDIFF" in got["accuracy"][0] and "ISMEAR" in got["accuracy"][1]
    bad = client.put(f"/ai/v1/projects/{pid}/settings", json={"accuracy": [
        "把 llm_api_key 和超算口令写进项目设置"]})
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "AI_MODE_BAD_PROJECT_SETTINGS"
    removed = client.delete(f"/ai/v1/projects/{pid}/settings")
    assert removed.status_code == 200 and removed.json()["deleted"] is True
    empty = client.get(f"/ai/v1/projects/{pid}/settings").json()["settings"]
    assert empty["accuracy"] == []


def test_real_project_task_message_roundtrip(client, monkeypatch, tmp_path):
    """真后端闭环：从空状态起步建项目→建任务→对话往返→删项目归零。"""
    empty = client.get("/ai/v1/projects").json()["projects"]
    assert empty == []
    assert client.get("/ai/v1/context").json()["ratio"] == 0.0

    created = client.post("/ai/v1/projects", json={
        "name": "E2E 闭环工程"})
    assert created.status_code == 200
    pid = created.json()["project"]["id"]
    assert created.json()["project"]["job_count"] == 0

    task = client.post(f"/ai/v1/projects/{pid}/tasks", json={
        "title": "能带计算",
        "goal": "基于优化结构做能带计算"})
    assert task.status_code == 200
    tid = task.json()["task"]["id"]
    assert task.json()["task"]["status"] == "idle"
    projects = client.get("/ai/v1/projects").json()["projects"]
    assert next(p for p in projects if p["id"] == pid)["job_count"] == 1

    import ai_mode.chat as _chat_module
    from ai_mode.llm.fake import FakeLLM as _FakeLLM
    monkeypatch.setattr(_chat_module, "_default_llm_factory",
                        lambda _cfg: _FakeLLM().on(
                            "EDIFF",
                            "收到：调整 EDIFF 收敛精度至 1e-4，我先规划作业并做提交前检查。"))
    sent = client.post(f"/ai/v1/projects/{pid}/tasks/{tid}/messages",
                       json={"content": "请把 EDIFF 改为 1e-4"})
    assert sent.status_code == 200
    assert "1e-4" in sent.json()["answer"]
    msgs = client.get(f"/ai/v1/projects/{pid}/tasks/{tid}/messages").json()["messages"]
    assert msgs[-2]["role"] == "user" and msgs[-1]["role"] == "assistant"

    assert client.get("/ai/v1/context").json()["ratio"] > 0.0
    assert client.get("/ai/v1/jobs/waiting").json()["count"] == 0

    assert client.delete(f"/ai/v1/projects/{pid}").json()["deleted"] is True
    assert client.get("/ai/v1/projects").json()["projects"] == []


def test_disabled_mode_guards_routes(monkeypatch, tmp_path):
    """开关关闭时设置/项目设置同样被护栏拦截（回归）。"""
    monkeypatch.setenv("VASP_AI_HOME", str(tmp_path))
    monkeypatch.setenv("ENABLE_AI_MODE", "false")
    app = create_ai_mode_app()
    with TestClient(app) as c:
        for method, url in [
            ("GET", "/ai/v1/settings"),
            ("PUT", "/ai/v1/settings"),
            ("POST", "/ai/v1/settings/test/llm"),
            ("GET", "/ai/v1/projects/x/settings"),
        ]:
            r = c.request(method, url, json={} if method in ("PUT", "POST") else None)
            assert r.status_code == 503, f"{method} {url} -> {r.status_code}"
            assert r.json()["error"]["code"] == "AI_MODE_DISABLED"