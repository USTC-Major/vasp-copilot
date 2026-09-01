# -*- coding: utf-8 -*-
"""项目/任务/消息/上下文/等待队列真实后端测试（M13 真联通补齐）。"""

import pytest
from fastapi.testclient import TestClient

from ai_mode.projects import ProjectStore, get_project_store
from ai_mode.server import create_ai_mode_app


@pytest.fixture
def client(monkeypatch, tmp_path):
    """一次真实 app 会话：已启用 + 假 LLM + 隔离本地数据目录。"""
    monkeypatch.setenv("VASP_AI_HOME", str(tmp_path))
    monkeypatch.setenv("ENABLE_AI_MODE", "true")
    monkeypatch.setenv("AI_MODE_LLM_PROVIDER", "fake")
    import ai_mode.projects as _projects_module
    _projects_module._store = None       # 每次测试间重置全局存储单例，避免跨文件污染
    app = create_ai_mode_app()
    with TestClient(app) as c:
        yield c


def test_projects_empty_initial_state(client):
    projects = client.get("/ai/v1/projects")
    assert projects.status_code == 200
    body = projects.json()
    assert body["enabled"] is True
    assert body["projects"] == []

    ctx = client.get("/ai/v1/context")
    assert ctx.status_code == 200
    ctx_body = ctx.json()
    assert ctx_body["ratio"] == 0.0 and ctx_body["capacity"] == 65536

    wait = client.get("/ai/v1/jobs/waiting")
    assert wait.status_code == 200
    assert wait.json()["count"] == 0


def test_create_list_delete_project(client):
    created = client.post("/ai/v1/projects", json={
        "name": "NaCl 表面能", "description": "测试项目"})
    assert created.status_code == 200
    pid = created.json()["project"]["id"]
    assert created.json()["project"]["job_count"] == 0

    ids = [p["id"] for p in client.get("/ai/v1/projects").json()["projects"]]
    assert pid in ids

    deleted = client.delete(f"/ai/v1/projects/{pid}")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    ids = [p["id"] for p in client.get("/ai/v1/projects").json()["projects"]]
    assert pid not in ids


def test_create_task_updates_job_count_and_greeting(client):
    pid = client.post("/ai/v1/projects", json={
        "name": "能带项目"}).json()["project"]["id"]
    task = client.post(f"/ai/v1/projects/{pid}/tasks", json={
        "title": "能带计算",
        "goal": "基于优化结构做能带计算",
        "local_workspace": r"D:\calc\band",
    })
    assert task.status_code == 200
    t = task.json()["task"]
    assert t["project_id"] == pid
    assert t["status"] == "idle"
    assert t["local_workspace"] == r"D:\calc\band"

    tid = t["id"]
    prj = client.get("/ai/v1/projects").json()["projects"]
    updated = [p for p in prj if p["id"] == pid][0]
    assert updated["job_count"] == 1

    msgs = client.get(f"/ai/v1/projects/{pid}/tasks/{tid}/messages")
    assert msgs.status_code == 200
    assert msgs.json()["messages"] == []


def test_messages_send_roundtrip(client, monkeypatch):
    pid = client.post("/ai/v1/projects", json={
        "name": "消息项目"}).json()["project"]["id"]
    tid = client.post(f"/ai/v1/projects/{pid}/tasks", json={
        "goal": "结构优化"}).json()["task"]["id"]
    before = client.get(f"/ai/v1/projects/{pid}/tasks/{tid}/messages")
    assert before.status_code == 200
    n_before = len(before.json()["messages"])

    import ai_mode.chat as _chat_module
    from ai_mode.llm.fake import FakeLLM as _FakeLLM
    monkeypatch.setattr(_chat_module, "_default_llm_factory",
                        lambda _cfg: _FakeLLM().on(
                            "EDIFF",
                            "收到：调整 EDIFF 收敛精度至 1e-4，我先规划作业并做提交前检查。"))
    sent = client.post(f"/ai/v1/projects/{pid}/tasks/{tid}/messages",
                       json={"content": "请把 EDIFF 改为 1e-4"})
    assert sent.status_code == 200
    answer = sent.json()["answer"]
    assert "1e-4" in answer

    body = client.get(f"/ai/v1/projects/{pid}/tasks/{tid}/messages").json()["messages"]
    assert body[-2]["role"] == "user"
    assert body[-1]["role"] == "assistant"
    assert len(body) == n_before + 2


def test_task_context_counts_messages(tmp_path):
    store = ProjectStore(tmp_path)
    pid = store.create_project("能带项目", "描述")["id"]
    tid = store.create_task(pid, goal="结构优化")["id"]
    baseline = store.task_context(pid, tid)["used"]
    store.append_message(pid, tid, "user", "你好" * 30)
    store.append_message(pid, tid, "assistant", "收到" * 30,
                         thinking="思考" * 20)
    grown = store.task_context(pid, tid)
    assert grown["used"] > baseline
    assert grown["capacity"] > 0 and 0 <= grown["ratio"] <= 1


def test_project_and_task_not_found(client):
    tasks = client.get("/ai/v1/projects/nope/tasks")
    assert tasks.status_code == 404
    assert tasks.json()["error"]["code"] == "AI_MODE_PROJECT_NOT_FOUND"

    msgs = client.get("/ai/v1/projects/nope/tasks/nope/messages")
    assert msgs.status_code == 404
    assert msgs.json()["error"]["code"] == "AI_MODE_PROJECT_NOT_FOUND"


def test_bad_requests(client):
    no_name = client.post("/ai/v1/projects", json={"name": "  "})
    assert no_name.status_code == 400

    pid = client.post("/ai/v1/projects", json={
        "name": "合法项目"}).json()["project"]["id"]
    no_goal = client.post(f"/ai/v1/projects/{pid}/tasks", json={"goal": ""})
    assert no_goal.status_code == 200

    tid = client.post(f"/ai/v1/projects/{pid}/tasks", json={
        "goal": "结构优化"}).json()["task"]["id"]
    no_content = client.post(f"/ai/v1/projects/{pid}/tasks/{tid}/messages",
                             json={"content": ""})
    assert no_content.status_code == 400


def test_disabled_mode_guards_projects(monkeypatch, tmp_path):
    monkeypatch.setenv("VASP_AI_HOME", str(tmp_path))
    monkeypatch.setenv("ENABLE_AI_MODE", "false")
    app = create_ai_mode_app()
    with TestClient(app) as c:
        r = c.get("/ai/v1/projects")
        assert r.status_code == 503
        assert r.json()["error"]["code"] == "AI_MODE_DISABLED"
        r2 = c.get("/ai/v1/context")
        assert r2.status_code == 503


def test_store_persists_across_instances(tmp_path):
    s1 = ProjectStore(tmp_path)
    s1.create_project("持久化项目", "跨实例可见")
    pid = s1.create_project("第二个项目", "")["id"]
    assert s1.delete_project(pid) is True            # 删除生效
    assert s1.delete_project(pid) is False           # 不存在返回 False
    s2 = ProjectStore(tmp_path)                      # 新实例读到落盘结果
    names = {p["name"] for p in s2.list_projects()}
    assert "持久化项目" in names and "第二个项目" not in names
    assert s2.get_project(pid) is None

def test_singleton_reused(tmp_path, monkeypatch):
    monkeypatch.setenv("VASP_AI_HOME", str(tmp_path))
    a = get_project_store()
    b = get_project_store()
    assert a is b

def test_list_tasks_decorates_last_message_and_context(tmp_path):
    store = ProjectStore(tmp_path)
    pid = store.create_project("ContactBookProject", "")["id"]
    tid = store.create_task(pid, title="Task One", goal="relax")["id"]
    target = [t for t in store.list_tasks(pid) if t["id"] == tid][0]
    assert target.get("last_message") is None       # 无欢迎语：新建任务无消息
    assert target["context_ratio"] == 0.0
    store.append_message(pid, tid, "user", "请优化结构")
    store.append_message(pid, tid, "assistant", "已完成结构优化")
    target2 = [t for t in store.list_tasks(pid) if t["id"] == tid][0]
    assert target2["last_message"] == "已完成结构优化"
    assert 0.0 < target2["context_ratio"] <= 1.0


def test_patch_task_updates_selected_fields(client):
    pid = client.post("/ai/v1/projects", json={
        "name": "EditProject"}).json()["project"]["id"]
    tid = client.post(f"/ai/v1/projects/{pid}/tasks", json={
        "goal": "Original goal", "title": "Old Title",
        "local_workspace": r"D:\\ws\\a"}).json()["task"]["id"]
    r = client.patch(f"/ai/v1/projects/{pid}/tasks/{tid}", json={
        "title": "New Title", "goal": "New goal",
        "local_workspace": r"D:\\ws\\b", "hpc_workspace": "/hpcdir/x"})
    assert r.status_code == 200
    t = r.json()["task"]
    assert t["title"] == "New Title"
    assert t["goal"] == "New goal"
    assert t["local_workspace"] == r"D:\\ws\\b"
    assert t["hpc_workspace"] == "/hpcdir/x"
    r2 = client.patch(f"/ai/v1/projects/{pid}/tasks/{tid}",
                      json={"goal": "Only goal"})
    assert r2.status_code == 200
    t2 = r2.json()["task"]
    assert t2["goal"] == "Only goal"
    assert t2["title"] == "New Title"


def test_patch_task_bad_requests(client):
    pid = client.post("/ai/v1/projects", json={
        "name": "ValidateProject"}).json()["project"]["id"]
    tid = client.post(f"/ai/v1/projects/{pid}/tasks", json={
        "goal": "relax"}).json()["task"]["id"]
    assert client.patch(f"/ai/v1/projects/{pid}/tasks/{tid}",
                        json={"goal": "   "}).status_code == 400
    assert client.patch(f"/ai/v1/projects/{pid}/tasks/{tid}",
                        json={}).status_code == 400
    assert client.patch(f"/ai/v1/projects/{pid}/tasks/{tid}",
                        json={"title": "  "}).status_code == 400
    assert client.patch(f"/ai/v1/projects/{pid}/tasks/tsk_nope",
                        json={"title": "x"}).status_code == 404


def test_delete_task_cascade_and_task_isolation(client):
    pid = client.post("/ai/v1/projects", json={
        "name": "DeleteProject"}).json()["project"]["id"]
    ta = client.post(f"/ai/v1/projects/{pid}/tasks", json={
        "goal": "Task A goal"}).json()["task"]["id"]
    tb = client.post(f"/ai/v1/projects/{pid}/tasks", json={
        "goal": "Task B goal"}).json()["task"]["id"]
    assert len(client.get(f"/ai/v1/projects/{pid}/tasks")
               .json()["tasks"]) == 2
    assert client.delete(
        f"/ai/v1/projects/{pid}/tasks/{ta}").status_code == 200
    remain = client.get(f"/ai/v1/projects/{pid}/tasks").json()["tasks"]
    assert [t["id"] for t in remain] == [tb]
    prj = [p for p in client.get("/ai/v1/projects").json()["projects"]
           if p["id"] == pid][0]
    assert prj["job_count"] == 1
    assert client.get(
        f"/ai/v1/projects/{pid}/tasks/{ta}/messages").status_code == 404
    msgs = client.get(
        f"/ai/v1/projects/{pid}/tasks/{tb}/messages").json()["messages"]
    assert len(msgs) == 0   # 无欢迎语：新建任务无消息
    assert client.delete(
        f"/ai/v1/projects/{pid}/tasks/{ta}").status_code == 404


def test_store_delete_task_missing_returns_none(tmp_path):
    store = ProjectStore(tmp_path)
    pid = store.create_project("IsolatedProject", "")["id"]
    assert store.delete_task(pid, "tsk_zzz_no") is None