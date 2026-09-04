# -*- coding: utf-8 -*-
from concurrent.futures import ThreadPoolExecutor

"""中枢对话测试：路由（await_submit→真实提交入口）+ LLM 决策驱动 agent（全离线）。

对话与计算统一走 agent 决策循环：LLM 通过正文内嵌 <<<INTENT>>> / <<<TOOL>>> 标记
自决是否调用真实工具；不再有固定模板或固定 8 步。LLM 不可用 = 不启动任何流程。
"""

import pytest

from ai_mode.agent.protocol import INTENT_MARK, TOOL_MARK
from ai_mode.chat import classify, perform_submit, reply, reply_stream
from ai_mode.consent import get_card, list_cards, spawn_submit_card
from ai_mode.llm.fake import FakeLLM
from ai_mode.projects import ProjectStore


def _intent(kind: str = "compute") -> str:
    import json
    return INTENT_MARK + json.dumps({"intent": kind}, ensure_ascii=False)


def _tool(name: str, **args) -> str:
    import json
    payload = json.dumps({"name": name, "args": args, "reason": "r"},
                         ensure_ascii=False)
    return TOOL_MARK + payload


@pytest.fixture
def task(tmp_path, monkeypatch):
    monkeypatch.setenv("VASP_AI_HOME", str(tmp_path / "home"))
    store = ProjectStore(tmp_path / "home")
    prj = store.create_project("聊天项目")
    task = store.create_task(prj["id"], goal="结构优化")
    return store, prj["id"], task["id"]


def test_classify_routes_by_intent():
    assert classify("你好，在吗") == "chat"
    assert classify("帮我算一个结构优化") == "compute"
    assert classify("把 EDIFF 改成 1e-4") == "compute"
    assert classify("开始计算流程") == "confirm"
    assert classify("好的，开始吧") == "confirm"
    assert classify("看看 INCAR 内容") == "chat"     # 读文件不触发计算确认
    assert classify("查看工作区里有什么") == "chat"


def test_compute_agent_plans_immediately(tmp_path, monkeypatch):
    monkeypatch.setenv("VASP_AI_HOME", str(tmp_path / "home"))
    store = ProjectStore(tmp_path / "home")
    prj = store.create_project("聊天项目")
    tk = store.create_task(prj["id"], goal="结构优化")
    pid, tid = prj["id"], tk["id"]
    llm = FakeLLM()
    llm.enqueue(
        _intent() + "\n" + _tool("plan", jobs=[{"key": "r1",
                                                "label": "结构优化",
                                                "kind": "relax"}])
        + "\n我先规划这个结构优化任务。")
    llm.enqueue("规划已落库，接下来可准备输入。")
    answer = reply(store, pid, tid, "帮我算一个结构优化", llm_factory=lambda _c: llm)
    assert "已收到你的计算需求" not in answer
    assert "开始计算流程" not in answer
    assert "结构优化" in answer
    assert llm.calls
    assert llm.calls[0][-1]["content"] == "帮我算一个结构优化"
    updated = store.get_task(pid, tid)
    assert updated.get("pending_flow") is None
    assert updated["flow"]["phase"] == "running"
    assert updated["flow"]["plan"]["jobs"][0]["label"] == "结构优化"


def test_compute_llm_unavailable_does_not_start_flow(task):
    store, pid, tid = task
    llm = FakeLLM()
    answer = reply(store, pid, tid, "帮我算一个结构优化", llm_factory=lambda _c: llm)
    assert "LLM" in answer and "不可用" in answer
    updated = store.get_task(pid, tid)
    assert updated.get("flow") is None
    assert updated.get("pending_flow") is None


def test_await_submit_without_hpc_backend_creates_no_card(task, monkeypatch):
    store, pid, tid = task
    store.update_task(pid, tid, flow={
        "phase": "await_submit", "execution_mode": "None", "plan": {},
        "precheck": {"ok": True, "hard": True, "digest": "a" * 64},
    })
    answer = reply(store, pid, tid, "确认提交")
    assert "AI_HPC_BACKEND_UNAVAILABLE" in answer
    assert "未执行 sbatch" in answer
    assert list_cards(store, pid, tid) == []


def test_submit_action_stays_executing_through_save_then_finishes_once(
        task, monkeypatch):
    from ai_mode.orchestrator import Orchestrator

    store, pid, tid = task
    flow = {"phase": "await_submit", "hpc_dir": "/remote/work",
            "execution_mode": "None",
            "precheck": {"ok": True, "hard": True,
                         "digest": "a" * 64},
            "draft": [{"job_key": "relax", "script_sha256": "abc"}],
            "plan": {"jobs": [{"key": "relax", "status": "draft"}]}}
    store.update_task(pid, tid, flow=flow)
    card = spawn_submit_card(store, pid, tid)
    calls = []

    class FakeOrchestrator:
        execution_mode = "None"

        def _submit(self, store_, project_id, task_id, current):
            calls.append("sbatch")
            action = get_card(store_, project_id, task_id, card["action_id"])
            assert action["state"] == "executing"
            current["phase"] = "monitoring"
            current["plan"]["jobs"][0]["status"] = "submitted"
            current["plan"]["jobs"][0]["submission_state"] = "submitted"
            current["plan"]["jobs"][0]["submission_action_id"] = card["action_id"]
            store_.update_task(project_id, task_id, flow=current)
            return "submitted once"

    monkeypatch.setattr(
        Orchestrator, "from_settings",
        classmethod(lambda cls, cfg: FakeOrchestrator()))
    assert perform_submit(store, pid, tid, card["action_id"], True) == \
        "submitted once"
    assert get_card(store, pid, tid, card["action_id"])["state"] == "executed"
    assert "不会重复提交" in perform_submit(
        store, pid, tid, card["action_id"], True)
    assert calls == ["sbatch"]


def test_concurrent_submit_confirmations_call_scheduler_at_most_once(
        task, monkeypatch):
    from ai_mode.orchestrator import Orchestrator

    store, pid, tid = task
    store.update_task(pid, tid, flow={
        "phase": "await_submit", "hpc_dir": "/remote/work",
        "execution_mode": "None",
        "precheck": {"ok": True, "hard": True,
                     "digest": "a" * 64},
        "draft": [{"job_key": "relax", "script_sha256": "abc"}],
        "plan": {"jobs": [{"key": "relax", "status": "draft"}]},
    })
    card = spawn_submit_card(store, pid, tid)
    calls = []

    class FakeOrchestrator:
        execution_mode = "None"

        def _submit(self, store_, project_id, task_id, current):
            calls.append("sbatch")
            current["phase"] = "monitoring"
            job = current["plan"]["jobs"][0]
            job.update(status="submitted", submission_state="submitted",
                       submission_action_id=card["action_id"])
            store_.update_task(project_id, task_id, flow=current)
            return "submitted once"

    monkeypatch.setattr(
        Orchestrator, "from_settings",
        classmethod(lambda cls, cfg: FakeOrchestrator()))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(
            lambda _n: perform_submit(store, pid, tid, card["action_id"], True),
            range(2),
        ))

    assert calls == ["sbatch"]
    assert results.count("submitted once") == 1
    assert get_card(store, pid, tid, card["action_id"])["state"] == "executed"


def test_monitoring_goes_to_agent(task):
    store, pid, tid = task
    store.update_task(pid, tid, flow={"phase": "monitoring", "plan": {}})
    llm = FakeLLM().on("进度如何", "我帮你查一下作业进度。")
    answer = reply(store, pid, tid, "进度如何", llm_factory=lambda _c: llm)
    assert answer == "我帮你查一下作业进度。"


def test_confirm_without_pending_falls_back_to_chat(task):
    store, pid, tid = task
    store.append_message(pid, tid, "user", "开始计算流程")
    llm = FakeLLM().on("开始计算流程", "想开始什么？我没收到计算需求。")
    answer = reply(store, pid, tid, "开始计算流程",
                   llm_factory=lambda _c: llm)
    assert answer == "想开始什么？我没收到计算需求。"
    assert store.get_task(pid, tid).get("pending_flow") is None


def test_chat_routes_to_llm_with_context(task):
    store, pid, tid = task
    store.append_message(pid, tid, "user", "你好")
    llm = FakeLLM().on("你好", "你好！有什么需要我帮忙的吗？")
    answer = reply(store, pid, tid, "你好", llm_factory=lambda _c: llm)
    assert answer == "你好！有什么需要我帮忙的吗？"
    assert llm.calls                         # complete 被真实调用
    merged = "\n".join(m["content"] for m in llm.calls[0])
    assert "任务目标" in merged              # 带任务背景
    assert llm.calls[0][-1]["content"] == "你好"
    assert llm.calls[0][-1]["role"] == "user"


def test_chat_llm_unavailable_honest_notice(task):
    store, pid, tid = task
    store.append_message(pid, tid, "user", "你好")
    llm = FakeLLM()                          # 未预设规则 -> LLMUnavailableError
    answer = reply(store, pid, tid, "你好", llm_factory=lambda _c: llm)
    assert "LLM" in answer
    assert "不可用" in answer
    assert "已收到你的计算需求" not in answer


def test_reply_unknown_task_raises(tmp_path):
    store = ProjectStore(tmp_path / "home")
    with pytest.raises(ValueError):
        reply(store, "prj_x", "tsk_x", "你好")


def test_chat_context_includes_workspace_snapshot(tmp_path):
    store = ProjectStore(tmp_path / "home")
    prj = store.create_project("聊天项目")
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "INCAR").write_text("SYSTEM = fe2o3\nENCUT = 520\n", encoding="utf-8")
    (ws / "POSCAR").write_text("Fe2O3\n1.0\n1 0 0\n", encoding="utf-8")
    task = store.create_task(prj["id"], goal="结构优化",
                             local_workspace=str(ws))
    pid, tid = prj["id"], task["id"]
    store.append_message(pid, tid, "user", "你好，看一下工作区里有什么")
    llm = FakeLLM().on("工作区", "我看到工作区里有 INCAR。")
    answer = reply(store, pid, tid, "你好，看一下工作区里有什么",
                   llm_factory=lambda _c: llm)
    assert answer == "我看到工作区里有 INCAR。"
    merged = "\n".join(m["content"] for m in llm.calls[0])
    assert "任务本地工作区只读快照" in merged
    assert "[工作区快照]" in merged
    assert "INCAR" in merged
    assert "SYSTEM = fe2o3" not in merged
    assert "POSCAR" in merged
    assert "已收到你的计算需求" not in merged


def test_chat_context_without_workspace_is_honest(tmp_path):
    store = ProjectStore(tmp_path / "home")
    prj = store.create_project("聊天项目")
    task = store.create_task(prj["id"], goal="结构优化")   # 无本地工作区
    pid, tid = prj["id"], task["id"]
    store.append_message(pid, tid, "user", "看看工作区")
    llm = FakeLLM().on("工作区", "这个任务还没设置工作区。")
    answer = reply(store, pid, tid, "看看工作区",
                   llm_factory=lambda _c: llm)
    assert answer == "这个任务还没设置工作区。"
    merged = "\n".join(m["content"] for m in llm.calls[0])
    assert "工作区未设置" in merged


def test_reply_stream_chat_emits_events(task):
    store, pid, tid = task
    store.append_message(pid, tid, "user", "你好")
    llm = FakeLLM().on("你好", "你好！有什么需要帮忙的吗？")
    events = list(reply_stream(store, pid, tid, "你好",
                               llm_factory=lambda _c: llm))
    answers = "".join(e["text"] for e in events if e["type"] == "answer")
    assert answers == "你好！有什么需要帮忙的吗？"
    done = [e for e in events if e["type"] == "done"]
    assert done and done[-1]["answer"] == "你好！有什么需要帮忙的吗？"
    assert not [e for e in events if e["type"] == "error"]


def test_reply_stream_compute_emits_prose_and_ops(tmp_path, monkeypatch):
    monkeypatch.setenv("VASP_AI_HOME", str(tmp_path / "home"))
    store = ProjectStore(tmp_path / "home")
    prj = store.create_project("聊天项目")
    tk = store.create_task(prj["id"], goal="能带计算")
    pid, tid = prj["id"], tk["id"]
    llm = FakeLLM()
    llm.enqueue(
        _intent() + "\n" + _tool("plan", jobs=[{"key": "r1",
                                                "label": "能带计算",
                                                "kind": "band"}])
        + "\n先规划能带计算。")
    llm.enqueue("草稿已停在待确认阶段；真实提交需要你确认。")
    events = list(reply_stream(store, pid, tid, "帮我算一个能带",
                               llm_factory=lambda _c: llm))
    answers = "".join(e["text"] for e in events if e["type"] == "answer")
    statuses = "".join(e["text"] for e in events if e["type"] == "status")
    assert "能带计算" in answers
    assert "已规划" in statuses
    assert "已规划" not in answers
    assert "开始计算流程" not in answers
    assert events[-1]["type"] == "done"
    updated = store.get_task(pid, tid)
    assert updated.get("pending_flow") is None
    assert updated["flow"]["phase"] == "running"
    assert updated["flow"]["plan"]["jobs"][0]["label"] == "能带计算"


def test_reply_stream_compute_llm_unavailable_no_ops(task):
    store, pid, tid = task
    llm = FakeLLM()
    events = list(reply_stream(store, pid, tid, "帮我算一个能带",
                               llm_factory=lambda _c: llm))
    errs = [e for e in events if e["type"] == "error"]
    assert errs and "不可用" in errs[0]["message"]
    done = [e for e in events if e["type"] == "done"]
    assert done and "不可用" in done[-1]["answer"]
    assert store.get_task(pid, tid).get("flow") is None


def test_reply_stream_llm_unavailable_honest(task):
    store, pid, tid = task
    store.append_message(pid, tid, "user", "你好")
    llm = FakeLLM()   # 未预设规则 -> LLMUnavailableError
    events = list(reply_stream(store, pid, tid, "你好",
                               llm_factory=lambda _c: llm))
    errs = [e for e in events if e["type"] == "error"]
    assert errs and "不可用" in errs[0]["message"]
    done = [e for e in events if e["type"] == "done"]
    assert done and "不可用" in done[-1]["answer"]


def test_chat_context_includes_project_settings(tmp_path):
    from ai_mode.settings import ProjectSettingsStore
    store = ProjectStore(tmp_path / "home")
    prj = store.create_project("聊天项目")
    task = store.create_task(prj["id"], goal="结构优化")
    pid, tid = prj["id"], task["id"]
    ProjectSettingsStore(root=tmp_path / "home").save(
        pid, ["DOS 计算用四面体 ISMEAR = -5"])
    store.append_message(pid, tid, "user", "你好")
    llm = FakeLLM().on("你好", "你好！")
    answer = reply(store, pid, tid, "你好", llm_factory=lambda _c: llm)
    assert answer == "你好！"
    merged = "\n".join(m["content"] for m in llm.calls[0])
    assert "计算任务设置" in merged
    assert "ISMEAR" in merged and "-5" in merged
    assert "要求与指引" in merged


def test_compute_agent_includes_project_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("VASP_AI_HOME", str(tmp_path / "home"))
    from ai_mode.settings import ProjectSettingsStore
    store = ProjectStore(tmp_path / "home")
    prj = store.create_project("聊天项目")
    tk = store.create_task(prj["id"], goal="态密度计算")
    pid, tid = prj["id"], tk["id"]
    ProjectSettingsStore(root=tmp_path / "home").save(
        pid, ["态密度计算用四面体 ISMEAR = -5"])
    llm = FakeLLM()
    llm.enqueue(
        _intent() + "\n" + _tool("plan", jobs=[{"key": "r1",
                                                "label": "态密度计算",
                                                "kind": "dos"}])
        + "\n我会参考项目设置来规划。")
    llm.enqueue("已参考设置完成规划。")
    answer = reply(store, pid, tid, "帮我算一个态密度", llm_factory=lambda _c: llm)
    assert "已参考设置完成规划" in answer
    merged = "\n".join(m["content"] for m in llm.calls[0])
    assert "计算任务设置" in merged
    assert "ISMEAR" in merged and "-5" in merged
    assert "要求与指引" in merged


def _no_orch_cls(seen):
    class NoOrch:
        def handle(self, *args, **kwargs):
            seen.append("orchestrator.handle called")
            return "（旧单选模板回复）"

    return NoOrch()


def test_await_submit_free_text_goes_to_agent(task, monkeypatch):
    """M45：await_submit 阶段自由文本（补充输入/建议/干预）交还 LLM，不再被单选模板挡死。"""
    import ai_mode.chat as chat_module
    store, pid, tid = task
    store.update_task(pid, tid, flow={
        "phase": "await_submit", "execution_mode": "None", "plan": {},
        "precheck": {"ok": True, "hard": True, "digest": "a" * 64},
    })
    seen = []
    monkeypatch.setattr(chat_module, "_make_orchestrator",
                        lambda _f: _no_orch_cls(seen))
    llm = FakeLLM().on("把 INCAR 的 ENCUT 改成 600",
                       "已在 INCAR 中把 ENCUT 调整为 600，提交草稿已重新生成。"
                       "确认无误后回复「确认提交」即可。")
    answer = reply(store, pid, tid, "把 INCAR 的 ENCUT 改成 600",
                   llm_factory=lambda _c: llm)
    assert "已在 INCAR 中把 ENCUT 调整为 600" in answer
    assert "当前处于「提交前检查通过，待你确认提交」环节" not in answer
    assert seen == []
    assert store.get_task(pid, tid)["flow"]["phase"] == "await_submit"


def test_await_submit_confirm_cancel_still_route_to_orchestrator(task, monkeypatch):
    import ai_mode.chat as chat_module
    store, pid, tid = task
    store.update_task(pid, tid, flow={
        "phase": "await_submit", "execution_mode": "None", "plan": {},
        "precheck": {"ok": True, "hard": True, "digest": "a" * 64},
    })
    seen = []

    class FakeOrch:
        execution_mode = "None"

        def sync_execution_mode(self, store_, p, t):
            del store_, p, t
            return self.execution_mode

        def handle(self, store_, p, t, content):
            seen.append(content)
            return "推进:" + content

    monkeypatch.setattr(chat_module, "_make_orchestrator", lambda _f: FakeOrch())
    answer = reply(store, pid, tid, "确认提交",
                   llm_factory=lambda _c: FakeLLM())
    assert "AI_HPC_BACKEND_UNAVAILABLE" in answer
    assert list_cards(store, pid, tid) == []
    assert reply(store, pid, tid, "取消",
                 llm_factory=lambda _c: FakeLLM()) == "推进:取消"
    assert seen == ["取消"]


def test_reply_stream_await_submit_free_text_goes_to_agent(task, monkeypatch):
    import ai_mode.chat as chat_module
    store, pid, tid = task
    store.update_task(pid, tid, flow={"phase": "await_submit", "plan": {}})
    seen = []
    monkeypatch.setattr(chat_module, "_make_orchestrator",
                        lambda _f: _no_orch_cls(seen))
    llm = FakeLLM().on("工作区里还没有 POTCAR",
                       "工作区当前没有 POTCAR 文件；提交前需补上。"
                       "你可以用工具生成或自行上传后，再回复「确认提交」。")
    events = list(reply_stream(store, pid, tid, "工作区里还没有 POTCAR",
                               llm_factory=lambda _c: llm))
    answers = "".join(e["text"] for e in events if e["type"] == "answer")
    assert "POTCAR" in answers
    assert "当前处于「提交前检查通过，待你确认提交」环节" not in answers
    assert seen == []
    assert events[-1]["type"] == "done"
