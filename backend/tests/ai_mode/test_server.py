"""智能模式服务器两态启动测试：开关关 -> 503 禁用；开关开 -> 配置汇总且密钥掩码。"""

from fastapi.testclient import TestClient

from ai_mode.server import create_ai_mode_app


def test_disabled_state_returns_503(monkeypatch, tmp_path):
    monkeypatch.setenv("VASP_AI_HOME", str(tmp_path))
    monkeypatch.setenv("ENABLE_AI_MODE", "false")
    app = create_ai_mode_app()
    with TestClient(app) as client:
        r = client.get("/ai/v1/ping")
        assert r.status_code == 200
        assert r.json()["enabled"] is False
        conf = client.get("/ai/v1/config")
        assert conf.status_code == 503
        assert conf.json()["error"]["code"] == "AI_MODE_DISABLED"


def test_enabled_state_returns_masked_config(monkeypatch, tmp_path):
    monkeypatch.setenv("VASP_AI_HOME", str(tmp_path))
    monkeypatch.setenv("ENABLE_AI_MODE", "true")
    monkeypatch.setenv("AI_MODE_MAX_JOBS", "8")
    monkeypatch.setenv("AI_MODE_LLM_API_KEY", "sk-verysecret")
    monkeypatch.setenv("AI_MODE_MP_API_KEY", "mp-verysecret")
    app = create_ai_mode_app()
    with TestClient(app) as client:
        r = client.get("/ai/v1/ping")
        assert r.json()["enabled"] is True
        conf = client.get("/ai/v1/config")
        assert conf.status_code == 200
        data = conf.json()["config"]
        assert data["max_jobs"] == 8
        assert data["llm"]["api_key"] == "<redacted>"
        assert data["materials_project"]["api_key"] == "<redacted>"
        body = conf.text
        assert "sk-verysecret" not in body
        assert "mp-verysecret" not in body


def test_layout_endpoint(monkeypatch, tmp_path):
    monkeypatch.setenv("VASP_AI_HOME", str(tmp_path))
    monkeypatch.setenv("ENABLE_AI_MODE", "false")
    app = create_ai_mode_app()
    with TestClient(app) as client:
        r = client.get("/ai/v1/layout")
        assert r.status_code == 200
        assert (tmp_path / "sessions").is_dir()
def _make_project_and_task(client, tmp_path) -> tuple[str, str]:
    r = client.post("/ai/v1/projects", json={"name": "流式聊天项目"})
    pid = r.json()["project"]["id"]
    r2 = client.post(
        f"/ai/v1/projects/{pid}/tasks",
        json={"goal": "结构优化", "title": "测试任务",
              "local_workspace": str(tmp_path / "ws"),
              "hpc_workspace": ""})
    tid = r2.json()["task"]["id"]
    return pid, tid


def test_stream_messages_endpoint_error_path(monkeypatch, tmp_path):
    """未配置 LLM 规则 -> 走真实 SSE：error 事件后 done，落库 assistant。"""
    monkeypatch.setenv("VASP_AI_HOME", str(tmp_path))
    monkeypatch.setenv("ENABLE_AI_MODE", "true")
    app = create_ai_mode_app()
    with TestClient(app) as client:
        pid, tid = _make_project_and_task(client, tmp_path)
        r = client.post(
            f"/ai/v1/projects/{pid}/tasks/{tid}/messages/stream",
            json={"content": "你好"})
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        assert "data:" in r.text
        msgs = client.get(
            f"/ai/v1/projects/{pid}/tasks/{tid}/messages").json()["messages"]
        assert any(m["role"] == "user" and m["content"] == "你好" for m in msgs)
        assert msgs[-1]["role"] == "assistant" and msgs[-1]["content"]


def test_stream_messages_endpoint_answer_path(monkeypatch, tmp_path):
    """模拟 reply_stream 输出 thinking/answer 增量，验证 SSE 透传与落库。"""
    monkeypatch.setenv("VASP_AI_HOME", str(tmp_path))
    monkeypatch.setenv("ENABLE_AI_MODE", "true")
    import ai_mode.server as server_module

    def fake_reply_stream(store, pid, tid, content, llm_factory=None,
                      should_stop=None):
        yield {"type": "thinking", "text": "先"}
        yield {"type": "answer", "text": "告诉我"}
        yield {"type": "answer", "text": "INCAR 内容"}
        yield {"type": "done", "answer": "告诉我INCAR 内容"}

    monkeypatch.setattr(server_module._chat, "reply_stream",
                        fake_reply_stream)
    app = create_ai_mode_app()
    with TestClient(app) as client:
        pid, tid = _make_project_and_task(client, tmp_path)
        r = client.post(
            f"/ai/v1/projects/{pid}/tasks/{tid}/messages/stream",
            json={"content": "看看工作区"})
        assert r.status_code == 200
        assert '"thinking"' in r.text
        assert '"answer"' in r.text
        assert '"done"' in r.text and '告诉我INCAR 内容' in r.text
        msgs = client.get(
            f"/ai/v1/projects/{pid}/tasks/{tid}/messages").json()["messages"]
        assert msgs[-1]["role"] == "assistant"
        assert msgs[-1]["content"] == "告诉我INCAR 内容"
        assert msgs[-1]["thinking"] == "先"


def test_stream_messages_endpoint_stopped_persists(monkeypatch, tmp_path):
    """reply_stream 产出 status/stopped 时：SSE 透传 stopped，部分正文落库。"""
    monkeypatch.setenv("VASP_AI_HOME", str(tmp_path))
    monkeypatch.setenv("ENABLE_AI_MODE", "true")
    import ai_mode.server as server_module

    def fake_reply_stream(store, pid, tid, content, llm_factory=None,
                          should_stop=None):
        yield {"type": "status", "text": "已规划"}
        yield {"type": "answer", "text": "部分正文"}
        yield {"type": "stopped", "answer": "部分正文"}

    monkeypatch.setattr(server_module._chat, "reply_stream",
                        fake_reply_stream)
    app = create_ai_mode_app()
    with TestClient(app) as client:
        pid, tid = _make_project_and_task(client, tmp_path)
        r = client.post(
            f"/ai/v1/projects/{pid}/tasks/{tid}/messages/stream",
            json={"content": "看看工作区"})
        assert r.status_code == 200
        assert "\"status\"" in r.text
        assert "\"stopped\"" in r.text
        msgs = client.get(
            f"/ai/v1/projects/{pid}/tasks/{tid}/messages").json()["messages"]
        assert msgs[-1]["role"] == "assistant"
        assert msgs[-1]["content"] == "部分正文"


def test_stop_endpoint_returns_stopped(monkeypatch, tmp_path):
    """停止端点：有活跃流 -> stopped=true；无活跃流 -> stopped=false。"""
    monkeypatch.setenv("VASP_AI_HOME", str(tmp_path))
    monkeypatch.setenv("ENABLE_AI_MODE", "true")
    import ai_mode.server as server_module
    app = create_ai_mode_app()
    with TestClient(app) as client:
        pid, tid = _make_project_and_task(client, tmp_path)
        server_module._ACTIVE_STOPS[(pid, tid)] = False
        r = client.post(
            f"/ai/v1/projects/{pid}/tasks/{tid}/messages/stop")
        assert r.status_code == 200
        assert r.json()["stopped"] is True
        server_module._ACTIVE_STOPS.pop((pid, tid), None)
        r2 = client.post(
            f"/ai/v1/projects/{pid}/tasks/{tid}/messages/stop")
        assert r2.status_code == 200
        assert r2.json()["stopped"] is False


def test_task_detail_empty_flow(monkeypatch, tmp_path):
    """详情接口：未开始计算流程时 flow 为空对象、jobs 空、404 正常。"""
    monkeypatch.setenv("VASP_AI_HOME", str(tmp_path))
    monkeypatch.setenv("ENABLE_AI_MODE", "true")
    app = create_ai_mode_app()
    with TestClient(app) as client:
        pid, tid = _make_project_and_task(client, tmp_path)
        r = client.get(f"/ai/v1/projects/{pid}/tasks/{tid}/detail")
        assert r.status_code == 200
        data = r.json()["flow"]
        assert data["phase"] == ""
        assert data["jobs"] == []
        assert data["waiting"] == []
        r404 = client.get(f"/ai/v1/projects/{pid}/tasks/tsk_nosuch/detail")
        assert r404.status_code == 404


def test_task_detail_returns_flow_summary(monkeypatch, tmp_path):
    """详情接口：注入 flow 后返回 phase/多作业/依赖/等待原因/report 概要。"""
    monkeypatch.setenv("VASP_AI_HOME", str(tmp_path))
    monkeypatch.setenv("ENABLE_AI_MODE", "true")
    import ai_mode.server as server_module
    app = create_ai_mode_app()
    with TestClient(app) as client:
        pid, tid = _make_project_and_task(client, tmp_path)
        flow = {
            "phase": "monitoring",
            "goal": "结构优化+静态+DOS",
            "plan": {"strategy": "链式", "jobs": [
                {"key": "relax", "label": "结构优化", "kind": "relax",
                 "requires": [], "status": "completed", "slurm_id": 4201,
                 "description": ""},
                {"key": "relax/static", "label": "静态", "kind": "static",
                 "requires": ["relax"], "status": "running", "slurm_id": 4202,
                 "description": ""},
                {"key": "relax/static/dos", "label": "DOS", "kind": "dos",
                 "requires": ["relax/static"], "status": "waiting",
                 "slurm_id": None, "description": ""},
            ]},
            "waiting": ["relax/static/dos"],
            "local_dir": str(tmp_path / "ws"),
            "hpc_dir": "/home/u/vasp",
        }
        server_module._get_project_store().update_task(
            pid, tid, flow=flow)
        r = client.get(f"/ai/v1/projects/{pid}/tasks/{tid}/detail")
        assert r.status_code == 200
        data = r.json()["flow"]
        assert data["phase"] == "monitoring"
        assert data["strategy"] == "链式"
        assert [j["key"] for j in data["jobs"]] == \
            ["relax", "relax/static", "relax/static/dos"]
        assert data["jobs"][1]["status"] == "running"
        assert data["jobs"][1]["slurm_id"] == 4202
        assert data["jobs"][2]["requires"] == ["relax/static"]
        assert data["waiting"] == ["relax/static/dos"]
        assert data["hpc_dir"] == "/home/u/vasp"