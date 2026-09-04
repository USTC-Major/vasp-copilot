# -*- coding: utf-8 -*-
"""M31 agent 决策驱动执行层测试（全离线，协议文本走 FakeLLM）。"""

import json
from types import SimpleNamespace
import hashlib

import pytest

from ai_mode.agent import parse_turn, run_agent, run_agent_stream
from ai_mode.agent.runner import _strip_receipt_wait
from ai_mode.agent.protocol import INTENT_MARK, TOOL_MARK
from ai_mode.agent.tools import _CONSENT_PENDING, ToolExecutor
from ai_mode.config import AiModeConfig
from ai_mode.consent import claim_action, get_card, resolve_card
from ai_mode.llm.fake import FakeLLM
from ai_mode.projects import ProjectStore


def _intent(kind: str = "compute") -> str:
    return INTENT_MARK + json.dumps({"intent": kind}, ensure_ascii=False)


def _tool(name: str, **args) -> str:
    payload = json.dumps({"name": name, "args": args, "reason": "r"},
                         ensure_ascii=False)
    return TOOL_MARK + payload


def _approve_pending(executor: ToolExecutor, pending: str) -> str:
    assert pending.startswith(_CONSENT_PENDING)
    action_id = pending[len(_CONSENT_PENDING):]
    action = resolve_card(executor.store, executor.project_id,
                          executor.task_id, action_id, approved=True)
    assert action["state"] == "approved"
    return executor.execute_action(action_id)


def _write_complete_local_job(directory, *, script: str = "run.sh"):
    directory.mkdir(parents=True, exist_ok=True)
    for name in ("INCAR", "POSCAR", "KPOINTS", "POTCAR"):
        (directory / name).write_text(f"{name} test\n", encoding="utf-8")
    (directory / script).write_text("#!/bin/bash\n", encoding="utf-8")


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("VASP_AI_HOME", str(tmp_path / "home"))
    store = ProjectStore(tmp_path / "home")
    prj = store.create_project("agent 项目")
    tk = store.create_task(prj["id"], goal="结构优化")
    return SimpleNamespace(store=store, pid=prj["id"], tid=tk["id"],
                           cfg=AiModeConfig(data_dir=tmp_path / "data"),
                           tmp=tmp_path)


# ---------------- M56：终止计算流程 ----------------
def test_stop_monitor_cancels_and_stops_backfill(ctx):
    """stop_monitor：未终态作业置 canceled、phase=done、waiting 清空，
    回执给出 scancel 建议；后台监控不再扫到该任务。"""
    ex = ToolExecutor(store=ctx.store, project_id=ctx.pid, task_id=ctx.tid,
                      cfg=ctx.cfg)
    ctx.store.update_task(ctx.pid, ctx.tid, flow={
        "phase": "monitoring", "goal": "链式",
        "plan": {"strategy": "", "jobs": [
            {"key": "relax", "label": "结构优化", "kind": "relax",
             "requires": [], "status": "running", "slurm_id": 4201,
             "description": ""},
            {"key": "relax/static", "label": "静态", "kind": "static",
             "requires": ["relax"], "status": "waiting", "slurm_id": None,
             "description": ""}]},
        "waiting": ["relax/static"], "local_dir": "", "hpc_dir": ""})
    out = ex.handle("stop_monitor", {})
    assert "已终止本次计算流程" in out
    assert "relax（原状态 running）" in out
    assert "scancel 4201" in out
    flow = ctx.store.get_task(ctx.pid, ctx.tid)["flow"]
    assert flow["phase"] == "done"
    assert flow["waiting"] == []
    assert all(j["status"] == "canceled" for j in flow["plan"]["jobs"])
    assert ctx.store.monitoring_tasks() == []  # 监控线程不再扫到


def test_parse_turn_plain_prose():
    turn = parse_turn("你好，介绍一下你自己。")
    assert turn.intent == "chat"
    assert turn.prose == "你好，介绍一下你自己。"
    assert turn.tools == []


def test_parse_turn_intent_and_multiple_tools():
    text = (
        _intent() + "\n"
        + _tool("plan", jobs=[{"key": "r1", "label": "结构优化", "kind": "relax"}])
        + "\n"
        + _tool("write_input", filename="INCAR", content="EDIFF = 1e-5")
        + "\n我先规划并准备输入。"
    )
    turn = parse_turn(text)
    assert turn.intent == "compute"
    assert [t.name for t in turn.tools] == ["plan", "write_input"]
    assert "我先规划并准备输入。" in turn.prose
    assert "<<<" not in turn.prose


def test_parse_turn_nested_braces_in_args():
    text = _tool("plan", jobs=[{"key": "r1"}], note="a}b{c") + "\n收尾"
    turn = parse_turn(text)
    assert len(turn.tools) == 1
    assert turn.tools[0].name == "plan"
    assert turn.tools[0].args["note"] == "a}b{c"
    assert turn.prose == "收尾"


def test_agent_compute_plans_and_runs(ctx):
    llm = FakeLLM()
    llm.enqueue(
        _intent() + "\n"
        + _tool("plan", jobs=[{"key": "r1", "label": "结构优化", "kind": "relax"}])
        + "\n我先把结构优化作业规划出来。")
    llm.enqueue("规划完成；工作量已真实落库。")
    answer = run_agent(ctx.store, ctx.pid, ctx.tid, "帮我做一个结构优化",
                       cfg=ctx.cfg, llm_factory=lambda c: llm)
    assert "结构优化" in answer
    flow = ctx.store.get_task(ctx.pid, ctx.tid)["flow"]
    assert flow["phase"] == "running"
    assert flow["plan"]["jobs"][0]["label"] == "结构优化"
    local_dir = ctx.cfg.data_dir / "workspace" / f"{ctx.pid}__{ctx.tid}"
    assert not local_dir.exists()  # planning persists metadata only


def test_agent_write_input_persists(ctx):
    llm = FakeLLM()
    llm.enqueue(
        _tool("write_input", filename="INCAR",
              content="SYSTEM = relax\nENCUT = 520\n")
        + "\n已写入计算目录。")
    llm.enqueue("输入文件已准备好。")
    answer = run_agent(ctx.store, ctx.pid, ctx.tid, "帮我生成 INCAR",
                       cfg=ctx.cfg, llm_factory=lambda c: llm)
    assert "输入文件已准备好" in answer
    local_dir = ctx.cfg.data_dir / "workspace" / f"{ctx.pid}__{ctx.tid}"
    incar = local_dir / "INCAR"
    assert not incar.exists()


def test_propose_incar_requires_one_hash_bound_confirmation(ctx):
    ex = ToolExecutor(store=ctx.store, project_id=ctx.pid, task_id=ctx.tid,
                      cfg=ctx.cfg)
    out = ex.handle("propose_incar", {"entries": [
        {"tag": "SYSTEM", "value": "relax"},
        {"tag": "ENCUT", "value": 520},
        {"tag": "LWAVE", "value": False},
        {"tag": "MAGMOM", "value": [2, 2, 0]},
    ]})
    assert out.startswith(_CONSENT_PENDING)
    assert not (ex.local_dir() / "INCAR").exists()
    action = get_card(ctx.store, ctx.pid, ctx.tid,
                      out[len(_CONSENT_PENDING):])
    assert action["state"] == "pending"
    assert action["options"] == ["同意本次", "拒绝"]
    assert action["binding"]["relative_path"] == "INCAR"
    assert action["binding"]["content"] == (
        "SYSTEM = relax\nENCUT = 520\nLWAVE = .FALSE.\nMAGMOM = 2*2 0\n"
    )
    assert "proposal_sha256" in action["binding"]
    assert "+++ INCAR (proposal)" in action["summary"]


def test_propose_incar_rejects_duplicates_without_action(ctx):
    ex = ToolExecutor(store=ctx.store, project_id=ctx.pid, task_id=ctx.tid,
                      cfg=ctx.cfg)
    out = ex.handle("propose_incar", {"entries": [
        {"tag": "encut", "value": 400},
        {"tag": "ENCUT", "value": 520},
    ]})
    assert "AI_INCAR_DRAFT_INVALID" in out and "重复" in out
    flow = ctx.store.get_task(ctx.pid, ctx.tid).get("flow") or {}
    assert not ((flow.get("consent") or {}).get("actions"))
    assert not (ex.local_dir() / "INCAR").exists()


def test_propose_incar_rejects_binary_existing_file_without_preview(ctx):
    ex = ToolExecutor(store=ctx.store, project_id=ctx.pid, task_id=ctx.tid,
                      cfg=ctx.cfg)
    target = ex.local_dir() / "INCAR"
    target.parent.mkdir(parents=True, exist_ok=True)
    sentinel = b"PRIVATE_SENTINEL"
    target.write_bytes(b"ENCUT=400\x00" + sentinel)

    out = ex.handle("propose_incar", {"entries": [
        {"tag": "ENCUT", "value": 520},
    ]})

    assert "AI_INCAR_DRAFT_INVALID" in out
    assert sentinel.decode() not in out
    assert target.read_bytes().endswith(sentinel)
    flow = ctx.store.get_task(ctx.pid, ctx.tid).get("flow") or {}
    assert not ((flow.get("consent") or {}).get("actions"))


def test_propose_incar_rejects_unknown_tag_without_action(ctx):
    ex = ToolExecutor(store=ctx.store, project_id=ctx.pid, task_id=ctx.tid,
                      cfg=ctx.cfg)
    out = ex.handle("propose_incar", {"entries": [
        {"tag": "NOT_A_REAL_VASP_TAG", "value": 1},
    ]})
    assert "AI_INCAR_UNKNOWN_TAG" in out
    flow = ctx.store.get_task(ctx.pid, ctx.tid).get("flow") or {}
    assert not ((flow.get("consent") or {}).get("actions"))


@pytest.mark.parametrize("value", [
    float("nan"), float("inf"), float("-inf"),
    [520, float("nan")], "safe\nENCUT = 1", "safe; ENCUT = 1",
    "safe # hidden", "safe ! hidden", "safe\rENCUT = 1",
])
def test_propose_incar_rejects_nonfinite_and_injected_values(ctx, value):
    ex = ToolExecutor(store=ctx.store, project_id=ctx.pid, task_id=ctx.tid,
                      cfg=ctx.cfg)
    out = ex.handle("propose_incar", {"entries": [
        {"tag": "ENCUT", "value": value},
    ]})
    assert "AI_INCAR_DRAFT_INVALID" in out
    flow = ctx.store.get_task(ctx.pid, ctx.tid).get("flow") or {}
    assert not ((flow.get("consent") or {}).get("actions"))


def test_incar_confirmation_is_single_use_and_atomic(ctx):
    ex = ToolExecutor(store=ctx.store, project_id=ctx.pid, task_id=ctx.tid,
                      cfg=ctx.cfg)
    pending = ex.handle("propose_incar", {"entries": [
        {"tag": "ENCUT", "value": 520},
        {"tag": "EDIFF", "value": 1e-6},
    ]})
    action_id = pending[len(_CONSENT_PENDING):]
    resolved = resolve_card(ctx.store, ctx.pid, ctx.tid, action_id,
                            approved=True)
    assert resolved["state"] == "approved"
    result = ex.execute_action(action_id)
    assert "已原子写入" in result
    target = ex.local_dir() / "INCAR"
    assert target.read_text(encoding="utf-8") == "ENCUT = 520\nEDIFF = 1e-06\n"
    assert get_card(ctx.store, ctx.pid, ctx.tid, action_id)["state"] == "executed"
    before = target.stat().st_mtime_ns
    assert "已原子写入" in ex.execute_action(action_id)
    assert target.stat().st_mtime_ns == before
    again = resolve_card(ctx.store, ctx.pid, ctx.tid, action_id, approved=True)
    assert again["conflict"] is True


def test_interrupted_action_blocks_a_second_execution(ctx):
    ex = ToolExecutor(store=ctx.store, project_id=ctx.pid, task_id=ctx.tid,
                      cfg=ctx.cfg)
    args = {"entries": [{"tag": "ENCUT", "value": 520}]}
    first_id = ex.handle("propose_incar", args)[len(_CONSENT_PENDING):]
    resolve_card(ctx.store, ctx.pid, ctx.tid, first_id, approved=True)
    assert claim_action(ctx.store, ctx.pid, ctx.tid, first_id)["state"] == "executing"

    second_id = ex.handle("propose_incar", args)[len(_CONSENT_PENDING):]
    resolve_card(ctx.store, ctx.pid, ctx.tid, second_id, approved=True)
    result = ex.execute_action(second_id)

    assert "存在结果未知" in result
    assert get_card(ctx.store, ctx.pid, ctx.tid, first_id)["state"] == "unknown"
    assert get_card(ctx.store, ctx.pid, ctx.tid, second_id)["state"] == "failed"
    assert not (ex.local_dir() / "INCAR").exists()


def test_incar_confirmation_fails_closed_if_binding_or_base_changes(ctx):
    ex = ToolExecutor(store=ctx.store, project_id=ctx.pid, task_id=ctx.tid,
                      cfg=ctx.cfg)
    target = ex.local_dir() / "INCAR"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("ENCUT = 400\n", encoding="utf-8")
    pending = ex.handle("propose_incar", {"entries": [
        {"tag": "ENCUT", "value": 520},
    ]})
    action_id = pending[len(_CONSENT_PENDING):]
    resolve_card(ctx.store, ctx.pid, ctx.tid, action_id, approved=True)
    target.write_text("ENCUT = 450\n", encoding="utf-8")
    assert "changed after preview" in ex.execute_action(action_id)
    assert target.read_text(encoding="utf-8") == "ENCUT = 450\n"
    assert get_card(ctx.store, ctx.pid, ctx.tid, action_id)["state"] == "failed"

    pending2 = ex.handle("propose_incar", {"entries": [
        {"tag": "ENCUT", "value": 600},
    ]})
    action2 = pending2[len(_CONSENT_PENDING):]
    flow = ctx.store.get_task(ctx.pid, ctx.tid)["flow"]
    flow["consent"]["actions"][action2]["binding"]["content"] = "ENCUT = 999\n"
    ctx.store.update_task(ctx.pid, ctx.tid, flow=flow)
    result2 = resolve_card(ctx.store, ctx.pid, ctx.tid, action2, approved=True)
    assert result2["tampered"] is True
    assert target.read_text(encoding="utf-8") == "ENCUT = 450\n"


def test_agent_dangerous_command_requires_consent(ctx):
    # 自由命令在解析后立即拒绝，不得生成任何可批准卡片。
    llm = FakeLLM()
    llm.enqueue(_tool("run_exec", command="rm -rf /") + "\n我尝试清理。")
    llm.enqueue("AI_FREEFORM_EXEC_DISABLED：自由命令执行已禁用。")
    answer = run_agent(ctx.store, ctx.pid, ctx.tid, "帮我清理一下",
                       cfg=ctx.cfg, llm_factory=lambda c: llm,
                       auto_resume=False)
    assert "AI_FREEFORM_EXEC_DISABLED" in answer
    flow = ctx.store.get_task(ctx.pid, ctx.tid).get("flow") or {}
    cards = (flow.get("consent") or {}).get("cards") or {}
    assert not cards, "自由命令必须直接拒绝，不能生成可批准卡片"
    assert "AI_FREEFORM_EXEC_DISABLED" in answer


def test_agent_stream_dangerous_command_yields_card(ctx):
    # 流式路径也必须拒绝自由命令，并且不得产出授权卡片。
    llm = FakeLLM()
    llm.enqueue(_tool("run_exec", command="rm -rf /") + "\n我尝试清理。")
    llm.enqueue("AI_FREEFORM_EXEC_DISABLED：自由命令执行已禁用。")
    events = list(run_agent_stream(ctx.store, ctx.pid, ctx.tid, "帮我清理一下",
                                   cfg=ctx.cfg, llm_factory=lambda c: llm,
                                   auto_resume=False))
    cards = [e["card"] for e in events if e["type"] == "card"]
    assert not cards
    assert any("AI_FREEFORM_EXEC_DISABLED" in str(e) for e in events)
    assert events[-1]["type"] == "done"
    flow = ctx.store.get_task(ctx.pid, ctx.tid).get("flow") or {}
    cons = flow.get("consent") or {}
    assert not cons.get("cards")


def test_kpoints_generator_is_deterministic_confirmed_and_single_use(ctx):
    ex = ToolExecutor(store=ctx.store, project_id=ctx.pid, task_id=ctx.tid,
                      cfg=ctx.cfg)
    ex.handle("plan", {"jobs": [{"key": "relax", "label": "结构优化",
                                  "kind": "relax"}]})
    target = ex.local_dir() / "relax" / "KPOINTS"
    pending = ex.handle("generate_kpoints", {
        "job_key": "relax", "grid": [6, 6, 4], "centering": "Gamma"})
    assert pending.startswith(_CONSENT_PENDING) and not target.exists()
    action_id = pending[len(_CONSENT_PENDING):]
    action = get_card(ctx.store, ctx.pid, ctx.tid, action_id)
    assert action["binding"]["execution_kind"] == "deterministic_kpoints_generator"
    assert "6 6 4" in action["summary"]
    assert "已原子写入" in _approve_pending(ex, pending)
    expected = "Generated by VASP-Doctor\n0\nGamma\n6 6 4\n0 0 0\n"
    assert target.read_text(encoding="utf-8") == expected
    before = target.read_bytes()
    assert "已原子写入" in ex.execute_action(action_id)
    assert target.read_bytes() == before


def test_script_attestation_fails_if_script_changes_before_execution(ctx):
    ex = ToolExecutor(store=ctx.store, project_id=ctx.pid, task_id=ctx.tid,
                      cfg=ctx.cfg)
    ex.handle("plan", {"jobs": [{"key": "relax", "label": "结构优化",
                                  "kind": "relax"}]})
    job_dir = ex.local_dir() / "relax"
    _write_complete_local_job(job_dir)
    script = job_dir / "run.sh"
    script.write_text("#!/bin/bash\nSECRET_COMMAND_SHOULD_NOT_RENDER\n",
                      encoding="utf-8")
    pending = ex.handle("draft", {})
    action_id = pending[len(_CONSENT_PENDING):]
    action = get_card(ctx.store, ctx.pid, ctx.tid, action_id)
    assert "SECRET_COMMAND_SHOULD_NOT_RENDER" not in action["summary"]
    resolve_card(ctx.store, ctx.pid, ctx.tid, action_id, approved=True)
    script.write_text("#!/bin/bash\nchanged-after-confirmation\n", encoding="utf-8")
    result = ex.execute_action(action_id)
    assert "操作失败且未重试" in result
    failed = get_card(ctx.store, ctx.pid, ctx.tid, action_id)
    assert failed["state"] == "failed"
    flow = ctx.store.get_task(ctx.pid, ctx.tid)["flow"]
    assert not flow.get("script_attestations") and not flow.get("draft")
def test_agent_submit_stops_at_await_submit(ctx):
    llm = FakeLLM()
    llm.enqueue(_tool("plan", jobs=[{"key": "r1", "label": "结构优化",
                                     "kind": "relax"}]))
    llm.enqueue(_tool("draft") + "\n我生成了提交草稿。")
    llm.enqueue("草稿已停在待确认；真实提交需你确认。")
    local_dir = ctx.cfg.data_dir / "workspace" / f"{ctx.pid}__{ctx.tid}"
    relax = local_dir / "relax"
    _write_complete_local_job(relax)
    answer = run_agent(ctx.store, ctx.pid, ctx.tid, "帮我算结构优化",
                       cfg=ctx.cfg, llm_factory=lambda c: llm,
                       auto_resume=False)
    flow = ctx.store.get_task(ctx.pid, ctx.tid)["flow"]
    assert "我生成了提交草稿" in answer
    assert flow["phase"] == "running"
    assert not flow.get("draft")
    actions = (flow.get("consent") or {}).get("actions") or {}
    assert list(actions.values())[-1]["kind"] == "script_attestation"


def test_agent_stream_auto_submit_card(ctx):
    llm = FakeLLM()
    llm.enqueue(
        _tool("plan", jobs=[{"key": "r1", "label": "结构优化", "kind": "relax"}])
        + "\n我先规划结构优化。")
    llm.enqueue(_tool("draft") + "\n草稿已生成，等待确认。")
    local_dir = ctx.cfg.data_dir / "workspace" / f"{ctx.pid}__{ctx.tid}"
    relax = local_dir / "relax"
    _write_complete_local_job(relax)
    events = list(run_agent_stream(ctx.store, ctx.pid, ctx.tid, "帮我算结构优化",
                                   cfg=ctx.cfg, llm_factory=lambda c: llm,
                                   auto_resume=False))
    cards = [e["card"] for e in events if e["type"] == "card"]
    assert cards
    assert cards[-1]["kind"] == "script_attestation"
    assert "认领" in cards[-1]["summary"]
    assert events[-1]["type"] == "done"
    flow = ctx.store.get_task(ctx.pid, ctx.tid)["flow"]
    assert flow["phase"] == "running"
    assert not flow.get("draft")


def test_agent_llm_unavailable_no_flow(ctx):
    llm = FakeLLM()   # 无规则/队列 -> LLMUnavailableError
    answer = run_agent(ctx.store, ctx.pid, ctx.tid, "帮我算结构优化",
                       cfg=ctx.cfg, llm_factory=lambda c: llm)
    assert "LLM" in answer and "不可用" in answer
    assert ctx.store.get_task(ctx.pid, ctx.tid).get("flow") is None


def test_agent_stream_chat_no_tools(ctx):
    llm = FakeLLM().on("你好", "你好！有什么需要帮忙的吗？")
    events = list(run_agent_stream(ctx.store, ctx.pid, ctx.tid, "你好",
                                   cfg=ctx.cfg, llm_factory=lambda c: llm))
    answers = "".join(e["text"] for e in events if e["type"] == "answer")
    assert answers == "你好！有什么需要帮忙的吗？"
    assert events[-1]["type"] == "done"
    assert events[-1]["answer"] == "你好！有什么需要帮忙的吗？"


def test_agent_stream_compute_emits_tool_notes(ctx):
    llm = FakeLLM()
    llm.enqueue(
        _intent() + "\n" + _tool("plan", jobs=[{"key": "r1",
                                                "label": "能带计算",
                                                "kind": "band"}])
        + "\n先规划能带计算。")
    llm.enqueue("草稿已完成，停在待确认。")
    events = list(run_agent_stream(ctx.store, ctx.pid, ctx.tid, "帮我算一个能带",
                                   cfg=ctx.cfg, llm_factory=lambda c: llm))
    answers = "".join(e["text"] for e in events if e["type"] == "answer")
    statuses = "".join(e["text"] for e in events if e["type"] == "status")
    assert "能带计算" in answers
    assert "已规划" in statuses
    assert "已规划" not in answers
    assert "TOOL" not in "".join(e.get("text", "") for e in events
                                 if e["type"] == "answer")
    assert events[-1]["type"] == "done"
    assert "已规划" not in (events[-1].get("answer") or "")
    flow = ctx.store.get_task(ctx.pid, ctx.tid)["flow"]
    assert flow["plan"]["jobs"][0]["label"] == "能带计算"


def test_executor_draft_persists_await_submit(ctx):
    ex = ToolExecutor(store=ctx.store, project_id=ctx.pid, task_id=ctx.tid,
                      cfg=ctx.cfg)
    ex.handle("plan", {"jobs": [{"key": "r1", "label": "结构优化",
                                 "kind": "relax"}]})
    relax = ex.local_dir() / "relax"
    _write_complete_local_job(relax)
    pending = ex.handle("draft", {})
    assert not ctx.store.get_task(ctx.pid, ctx.tid)["flow"].get("draft")
    assert "已认领" in _approve_pending(ex, pending)
    out = ex.handle("draft", {})
    assert "已生成提交草稿" in out
    flow = ctx.store.get_task(ctx.pid, ctx.tid)["flow"]
    assert flow["phase"] == "await_submit"
    assert flow.get("draft")


def test_executor_unknown_tool_returns_help(ctx):
    ex = ToolExecutor(store=ctx.store, project_id=ctx.pid, task_id=ctx.tid,
                      cfg=ctx.cfg)
    out = ex.handle("no_such_tool", {})
    assert "AI_TOOL_NOT_ALLOWED" in out


def test_executor_monitor_without_ssh_degrades(ctx):
    ex = ToolExecutor(store=ctx.store, project_id=ctx.pid, task_id=ctx.tid,
                      cfg=ctx.cfg)
    out = ex.handle("monitor", {})
    assert "未连接超算" in out


def test_executor_run_exec_safe_command_allowed(ctx):
    ex = ToolExecutor(store=ctx.store, project_id=ctx.pid, task_id=ctx.tid,
                      cfg=ctx.cfg)
    out = ex.handle("run_exec", {"command": "pwd"})
    assert "AI_FREEFORM_EXEC_DISABLED" in out


def test_stream_cleaner_strips_marks_across_chunks():
    from ai_mode.agent.runner import _StreamCleaner
    text = "head<<<TOOL>>>{\"name\": \"x\"}tail"
    c = _StreamCleaner()
    out = []
    for ch in [text[i:i + 3] for i in range(0, len(text), 3)]:
        part = c.add(ch)
        if part:
            out.append(part)
    out.append(c.flush())
    joined = "".join(out)
    assert "<<<TOOL>>>" not in joined
    assert joined == "headtail"

def test_agent_truncated_tool_marker_self_heals(ctx):
    # LLM 输出被截断：工具请求未闭合 -> 决策循环提示重发 -> 重新执行 plan
    llm = FakeLLM()
    llm.enqueue("I plan this: " + TOOL_MARK + '{"name": "plan"')
    llm.enqueue(_tool("plan", jobs=[{"key": "r1", "label": "relax_job",
                                     "kind": "relax"}]) + "\nnudged plan.")
    llm.enqueue("plan done.")
    answer = run_agent(ctx.store, ctx.pid, ctx.tid, "run a relax calc",
                       cfg=ctx.cfg, llm_factory=lambda c: llm)
    assert "plan done." in answer
    assert "<<<" not in answer
    flow = ctx.store.get_task(ctx.pid, ctx.tid)["flow"]
    assert flow["phase"] == "running"
    assert flow["plan"]["jobs"][0]["label"] == "relax_job"


def test_agent_stream_truncated_marker_not_leaked(ctx):
    # 流式首轮被截断：悬空 TOOL 标记不得泄漏给用户，重发后真实执行 plan
    llm = FakeLLM()
    llm.enqueue("I plan this: " + TOOL_MARK + '{"name": "plan"')
    llm.enqueue(_tool("plan", jobs=[{"key": "r1", "label": "relax_job",
                                     "kind": "relax"}]) + "\nreplanned.")
    llm.enqueue("plan done.")
    events = list(run_agent_stream(ctx.store, ctx.pid, ctx.tid,
                                   "run a relax calc",
                                   cfg=ctx.cfg, llm_factory=lambda c: llm))
    answers = "".join(e.get("text", "") for e in events if e["type"] == "answer")
    statuses = "".join(e.get("text", "") for e in events if e["type"] == "status")
    assert "<<<" not in answers
    assert "TOOL" not in answers
    assert "replanned." in answers
    assert "relax_job" in statuses
    assert "relax_job" not in answers
    assert events[-1]["type"] == "done"
    flow = ctx.store.get_task(ctx.pid, ctx.tid)["flow"]
    assert flow["plan"]["jobs"][0]["label"] == "relax_job"


def test_agent_stream_stop_emits_stopped(ctx):
    llm = FakeLLM()
    llm.enqueue(_intent() + "\n" + _tool("plan", jobs=[{"key": "r1",
                                                        "label": "结构优化",
                                                        "kind": "relax"}])
                + "\n我先规划结构优化。")
    halted = {"stop": False}

    def _should_stop():
        return halted["stop"]

    gen = run_agent_stream(ctx.store, ctx.pid, ctx.tid, "帮我算结构优化",
                           cfg=ctx.cfg, llm_factory=lambda c: llm,
                           should_stop=_should_stop)
    events = [next(gen)]
    halted["stop"] = True
    events.extend(gen)
    stops = [e for e in events if e["type"] == "stopped"]
    assert stops and stops[0]["answer"]
    idx = events.index(stops[0])
    tails = [e for e in events[idx + 1:] if e["type"] != "stopped"]
    assert not tails


def test_agent_stream_stop_in_loop(ctx):
    llm = FakeLLM()
    llm.enqueue(_intent() + "\n" + _tool("plan", jobs=[{"key": "r1",
                                                        "label": "结构优化",
                                                        "kind": "relax"}])
                + "\n我先规划。")
    llm.enqueue("规划已完成。")
    halted = {"stop": False}
    events = []
    for ev in run_agent_stream(ctx.store, ctx.pid, ctx.tid, "帮我算结构优化",
                               cfg=ctx.cfg, llm_factory=lambda c: llm,
                               should_stop=lambda: halted["stop"]):
        events.append(ev)
        if ev["type"] == "status":
            halted["stop"] = True
    stops = [e for e in events if e["type"] == "stopped"]
    assert stops
    idx = events.index(stops[0])
    tails = [e for e in events[idx + 1:] if e["type"] != "stopped"]
    assert not tails

def test_agent_stream_action_promise_nudges_tool(ctx):
    # M42 regression: opening that only promises action (no TOOL marker)
    # must trigger the _ACT_NUDGE second round so the reply keeps going.
    llm = FakeLLM()
    llm.enqueue("好的，我现在开始实际操作。先查看当前计算流程状态和工作区文件，确认环境情况。")
    llm.enqueue(_intent() + "\n" + _tool("plan", jobs=[{"key": "r1", "label": "结构优化", "kind": "relax"}]) + "\n" + "我现在开始规划结构优化。")
    llm.enqueue("规划完成；接下来继续推进。")

    events = list(run_agent_stream(ctx.store, ctx.pid, ctx.tid, "帮我继续推进 H2 能量计算",
                                   cfg=ctx.cfg, llm_factory=lambda c: llm))
    answers = "".join(e.get("text", "") for e in events if e["type"] == "answer")
    statuses = "".join(e.get("text", "") for e in events if e["type"] == "status")
    assert "开始实际操作" in answers
    assert "我现在开始规划结构优化。" in answers
    assert "规划完成；接下来继续推进。" in answers
    assert "已规划" in statuses
    assert events[-1]["type"] == "done"
    assert (events[-1].get("answer") or "").strip()
    flow = ctx.store.get_task(ctx.pid, ctx.tid)["flow"]
    assert flow["plan"]["jobs"][0]["label"] == "结构优化"




def test_agent_receipt_stall_keeps_stream_going(ctx):
    # M49 回归：模型只输出「收到回执后继续」却没发工具标记时，流程必须继续，
    # 不能把「等回执」当成最终答案直接 done。
    llm = FakeLLM()
    llm.enqueue("我先确认两个作业是否已跑完，然后从 OUTCAR 提取最终能量结果。\n收到回执后继续。")
    llm.enqueue(_tool("monitor", jobs=["static"]) + "\n我现在检查作业状态。")
    llm.enqueue("作业已完成，最终能量为 -6.7564 eV。")
    events = list(run_agent_stream(ctx.store, ctx.pid, ctx.tid, "提取结果",
                                   cfg=ctx.cfg, llm_factory=lambda c: llm))
    answers = "".join(e.get("text", "") for e in events if e["type"] == "answer")
    statuses = "".join(e.get("text", "") for e in events if e["type"] == "status")
    assert "收到回执后继续" in answers
    assert "-6.7564 eV" in answers
    assert "已规划" not in answers
    assert events[-1]["type"] == "done"
    assert (events[-1].get("answer") or "").strip()


def test_agent_receipt_stall_nudges_nonstream(ctx):
    # 非流式同规则：首轮「收到回执后继续」-> 续跑调用工具并给出结论；
    # 等待话术被 _strip_receipt_wait 剥离，不出现在最终回答里。
    llm = FakeLLM()
    llm.enqueue("收到回执后继续。")
    plan_req = _tool("plan", jobs=[{"key": "r1", "label": "结构优化",
                                    "kind": "relax"}])
    llm.enqueue(_intent() + "\n" + plan_req + "\n开始规划。")
    llm.enqueue("规划完成。")
    answer = run_agent(ctx.store, ctx.pid, ctx.tid, "继续",
                       cfg=ctx.cfg, llm_factory=lambda c: llm)
    assert "规划完成。" in answer
    assert "收到回执后继续。" not in answer
    assert "<<<" not in answer

def test_agent_receipt_stall_nudges_only_once(ctx):
    # 同一条纯正文连续两轮都命中「收到回执后继续」：只提示一次，第二轮仍未给出
    # 工具调用或结论就收尾；等待话术被剥离，用户只看到明确的停止说明，
    # 绝不刷屏（M49：此前每轮都 nudge + 复读，用户看到几百遍）。
    llm = FakeLLM()
    llm.enqueue("收到回执后继续。")
    llm.enqueue("收到回执后继续。")
    llm.enqueue("这一条不应被消费。")  # 若继续循环会绕过 guard 弹出一条空档
    answer = run_agent(ctx.store, ctx.pid, ctx.tid, "继续",
                       cfg=ctx.cfg, llm_factory=lambda c: llm)
    assert "收到回执后继续。" not in answer
    assert "已停止等待" in answer
    assert "不应被消费" not in answer
    assert len(llm.calls) == 2


def test_strip_receipt_wait_keeps_normal_sentences():
    # 只剔除同时含「回执」与等待词的句子，其余内容原样保留。
    text = ("我先查询作业状态和输出文件。收到回执后继续。"
            "结果在 OUTCAR 里，稍后我汇总给你。")
    out = _strip_receipt_wait(text)
    assert "收到回执后继续" not in out
    assert "我先查询作业状态和输出文件。" in out
    assert "结果在 OUTCAR 里，稍后我汇总给你。" in out


def test_agent_stream_receipt_stall_stops_with_note(ctx):
    # 流式：首轮等待话术已实时流出（无法撤回），但后续轮被剥离；
    # 连续两轮仍只有等待话术时收尾并给出停止说明，不再刷屏。
    llm = FakeLLM()
    llm.enqueue("收到回执后继续。")   # 首轮流式
    llm.enqueue("收到回执后继续。")   # 后续轮 1 -> nudge 一次
    llm.enqueue("收到回执后继续。")   # 后续轮 2 -> 停止说明并收尾
    llm.enqueue("这一条不应被消费。")
    events = list(run_agent_stream(ctx.store, ctx.pid, ctx.tid, "继续",
                                   cfg=ctx.cfg, llm_factory=lambda c: llm))
    answers = "".join(e.get("text", "") for e in events if e["type"] == "answer")
    assert "已停止等待" in answers
    assert "不应被消费" not in answers
    assert answers.count("收到回执后继续。") == 1  # 仅首轮流式无法撤回的那一次
    assert events[-1]["type"] == "done"
    assert len(llm.calls) == 3


def test_agent_writes_into_user_workspace(ctx):
    """Legacy generic writes remain side-effect free even in a user workspace."""
    ws = ctx.tmp / "myws"
    ws.mkdir(parents=True, exist_ok=True)
    tk = ctx.store.create_task(ctx.pid, goal="structure relax",
                               local_workspace=str(ws))
    local = SimpleNamespace(store=ctx.store, pid=ctx.pid, tid=tk["id"],
                            cfg=ctx.cfg)
    llm = FakeLLM()
    llm.enqueue(_intent() + "\n"
                + _tool("plan", jobs=[{"key": "r1", "label": "relax",
                                       "kind": "relax"}])
                + "\nplanning relax.")
    llm.enqueue(_tool("write_input", filename="INCAR",
                      content="SYSTEM = relax\nENCUT = 520\n")
                + "\n已写入计算目录。")
    llm.enqueue("Input file is ready in the user workspace.")
    answer = run_agent(local.store, local.pid, local.tid, "write INCAR",
                       cfg=local.cfg, llm_factory=lambda c: llm)
    assert "Input file is ready" in answer
    incar = ws / "INCAR"
    assert not incar.exists()
    private = local.cfg.data_dir / "workspace" / f"{local.pid}__{local.tid}"
    assert not (private / "INCAR").exists()
    flow = local.store.get_task(local.pid, local.tid)["flow"]
    assert flow["local_dir"] == str(ws.expanduser().resolve())


def test_copy_inputs_same_workspace_no_self_copy(ctx):
    """工作区即计算目录时，copy_inputs 不再自我复制（避免 SameFileError），
    直接确认文件就位即可。"""
    ws = ctx.tmp / "myws"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "POSCAR").write_text("POSCAR fake\n", encoding="utf-8")
    tk = ctx.store.create_task(ctx.pid, goal="structure relax",
                               local_workspace=str(ws))
    exec_ = ToolExecutor(store=ctx.store, project_id=ctx.pid, task_id=tk["id"],
                         cfg=ctx.cfg)
    exec_.handle("get_state", {})
    flow = ctx.store.get_task(ctx.pid, tk["id"])["flow"]
    artifact_id = next(iter(flow["artifacts"]))
    result = exec_.handle("copy_inputs", {"artifact_ids": [artifact_id],
                                          "job_key": ""})
    assert "本来就在目标目录" in result
    assert (ws / "POSCAR").is_file()
    assert not ((flow.get("consent") or {}).get("actions"))

def test_plan_remaps_opaque_keys_to_semantic(ctx):
    """M46：plan 里晦涩的 r1/s1 key 会被归一为可读语义名（relax/static）。"""
    ex = ToolExecutor(store=ctx.store, project_id=ctx.pid, task_id=ctx.tid,
                      cfg=ctx.cfg)
    out = ex.handle("plan", {"jobs": [
        {"key": "r1", "label": "结构优化", "kind": "relax"},
        {"key": "s1", "label": "静态自洽", "kind": "static"},
    ]})
    assert "已规划 2 条作业" in out
    flow = ctx.store.get_task(ctx.pid, ctx.tid)["flow"]
    assert [j["key"] for j in flow["plan"]["jobs"]] == ["relax", "static"]
    assert [j["label"] for j in flow["plan"]["jobs"]] == ["结构优化", "静态自洽"]
    # 缺 label 但带 kind 时：补中文语义 label，key 用其语义名（band）
    ex.handle("plan", {"jobs": [{"key": "job1", "kind": "band"}]})
    flow = ctx.store.get_task(ctx.pid, ctx.tid)["flow"]
    assert flow["plan"]["jobs"][0]["label"] == "能带计算"
    assert flow["plan"]["jobs"][0]["key"] == "band"


def test_executor_select_jobs_skip_and_reactivate(ctx):
    """M46：select_jobs 标记跳过/选回；draft 只给非跳过作业生成草稿。"""
    ex = ToolExecutor(store=ctx.store, project_id=ctx.pid, task_id=ctx.tid,
                      cfg=ctx.cfg)
    ex.handle("plan", {"jobs": [
        {"key": "r1", "label": "结构优化", "kind": "relax"},
        {"key": "s1", "label": "静态自洽", "kind": "static"},
    ]})
    relax = ex.local_dir() / "relax"
    _write_complete_local_job(relax)
    out = ex.handle("select_jobs",
                    {"submit": ["relax"], "skip": ["static"]})
    assert "本次提交" in out and "跳过" in out
    flow = ctx.store.get_task(ctx.pid, ctx.tid)["flow"]
    statuses = {j["key"]: j["status"] for j in flow["plan"]["jobs"]}
    assert statuses == {"relax": "draft", "static": "skipped"}
    pending = ex.handle("draft", {})
    assert "已认领" in _approve_pending(ex, pending)
    out = ex.handle("draft", {})
    assert "已跳过：static" in out
    flow = ctx.store.get_task(ctx.pid, ctx.tid)["flow"]
    assert [d["job_key"] for d in flow["draft"]] == ["relax"]
    ex.handle("select_jobs", {"submit": ["static"]})    # 用户改主意选回
    flow = ctx.store.get_task(ctx.pid, ctx.tid)["flow"]
    assert flow["plan"]["jobs"][1]["status"] == "draft"


def test_executor_select_jobs_by_label(ctx):
    """M46：select_jobs 也可按中文 label 匹配作业。"""
    ex = ToolExecutor(store=ctx.store, project_id=ctx.pid, task_id=ctx.tid,
                      cfg=ctx.cfg)
    ex.handle("plan", {"jobs": [
        {"key": "relax_step", "label": "结构优化", "kind": "relax"},
        {"key": "static_step", "label": "静态自洽", "kind": "static"},
    ]})
    ex.handle("select_jobs", {"skip": ["静态自洽"]})
    flow = ctx.store.get_task(ctx.pid, ctx.tid)["flow"]
    statuses = {j["key"]: j["status"] for j in flow["plan"]["jobs"]}
    assert statuses == {"relax_step": "draft", "static_step": "skipped"}

def test_agent_stream_consent_approve_resumes(ctx):
    """A disabled free-form command creates no approval surface or side effect."""
    llm = FakeLLM()
    llm.enqueue(_tool("run_exec", command="mkdir -p ../consented_out")
                + "\n我把目录写到工作区外。")
    llm.enqueue("AI_FREEFORM_EXEC_DISABLED：自由命令执行已禁用。")
    events = list(run_agent_stream(
        ctx.store, ctx.pid, ctx.tid, "帮我执行越界写",
        cfg=ctx.cfg, llm_factory=lambda c: llm,
    ))
    kinds = [e["type"] for e in events]
    assert "card" not in kinds
    assert events[-1]["type"] == "done"
    assert "AI_FREEFORM_EXEC_DISABLED" in str(events)
    assert not (ctx.cfg.data_dir / "workspace" / "consented_out").exists()


def test_agent_stream_consent_deny_ends(ctx):
    """Stable denial terminates normally without creating a consent action."""
    llm = FakeLLM()
    llm.enqueue(_tool("run_exec", command="mkdir -p ../evil") + "\n越界写。")
    llm.enqueue("AI_FREEFORM_EXEC_DISABLED：未写入。")
    events = list(run_agent_stream(
        ctx.store, ctx.pid, ctx.tid, "帮我越界写",
        cfg=ctx.cfg, llm_factory=lambda c: llm,
    ))
    kinds = [e["type"] for e in events]
    assert kinds.count("done") == 1
    assert events[-1]["type"] == "done"
    assert "AI_FREEFORM_EXEC_DISABLED" in str(events)
    assert not any(e["type"] == "card" for e in events)
    tail_idx = next(i for i, e in enumerate(events) if e["type"] == "done")
    assert not [e for e in events[tail_idx + 1:]]
def test_agent_write_input_and_draft_per_job_dir(ctx):
    """Legacy generic input writing cannot mutate a planned job directory."""
    ex = ToolExecutor(store=ctx.store, project_id=ctx.pid, task_id=ctx.tid,
                      cfg=ctx.cfg)
    ex.handle("plan", {"jobs": [{"key": "relax", "label": "结构优化",
                                 "kind": "relax"}]})
    out = ex.handle("write_input", {
        "filename": "INCAR", "content": "SYSTEM = x\n", "dir": "relax"})
    assert "AI_TOOL_NOT_ALLOWED" in out
    local = ex.local_dir() / "relax" / "INCAR"
    assert not local.exists()
    relax = ex.local_dir() / "relax"
    _write_complete_local_job(relax)
    pending = ex.handle("draft", {})
    assert "已认领" in _approve_pending(ex, pending)
    out2 = ex.handle("draft", {})
    assert "已生成提交草稿" in out2
    flow = ctx.store.get_task(ctx.pid, ctx.tid)["flow"]
    drafts = flow.get("draft") or []
    assert drafts and drafts[0]["job_key"] == "relax"
    assert drafts[0]["dir"].replace("\\", "/").endswith("/relax")
    assert drafts[0]["submit_cmd"] == "sbatch run.sh"


def test_write_input_rejects_sh_script(ctx):
    """红线：write_input 不能写 *.sh 提交脚本，脚本必须由用户自己提供。"""
    ex = ToolExecutor(store=ctx.store, project_id=ctx.pid, task_id=ctx.tid,
                      cfg=ctx.cfg)
    out = ex.handle("write_input", {"filename": "run.sh",
                                    "content": "#!/bin/bash\nsrun vasp_std\n"})
    assert "AI_TOOL_NOT_ALLOWED" in out
    assert not (ex.local_dir() / "run.sh").exists()


def test_draft_without_user_script_blocks_with_prompt(ctx):
    """本地与超算都没有 *.sh 时，draft 不进 await_submit，停在阻塞态并给路径指引。"""
    ex = ToolExecutor(store=ctx.store, project_id=ctx.pid, task_id=ctx.tid,
                      cfg=ctx.cfg)
    ex.handle("plan", {"jobs": [{"key": "relax", "label": "结构优化",
                                 "kind": "relax"}]})
    out = ex.handle("draft", {})
    assert "无法生成提交草稿" in out
    assert "超算作业目录" in out and "系统不会代写" in out
    flow = ctx.store.get_task(ctx.pid, ctx.tid)["flow"]
    assert flow["phase"] == "blocked"


def test_draft_rejects_empty_user_script(ctx):
    """空 *.sh 不能被当作用户已提供且已认领的提交脚本。"""
    ex = ToolExecutor(store=ctx.store, project_id=ctx.pid, task_id=ctx.tid,
                      cfg=ctx.cfg)
    ex.handle("plan", {"jobs": [{"key": "relax", "label": "结构优化",
                                   "kind": "relax"}]})
    relax = ex.local_dir() / "relax"
    _write_complete_local_job(relax)
    (relax / "run.sh").write_bytes(b"")

    out = ex.handle("draft", {})

    assert "提交脚本为空" in out
    flow = ctx.store.get_task(ctx.pid, ctx.tid)["flow"]
    assert flow["phase"] == "blocked"
    assert not flow.get("draft")
    assert not ((flow.get("consent") or {}).get("actions"))
    assert not flow.get("draft")


# ---------------- M48：双工作区分辨（本地 vs 超算） ----------------
class _FakeHpc:
    """同签名假 SSHManager（list_dir_info/read_file/run/write_file）。"""

    execution_mode = "Fake"

    def __init__(self, dirs, files):
        self._dirs = dirs
        self._files = dict(files)
        self.written: dict[str, bytes] = {}
        self.write_calls: list[str] = []
        self.mkdir_calls: list[str] = []
        self.runs: list[str] = []

    def list_dir_info(self, remote):
        if remote not in self._dirs:
            raise RuntimeError(f"no such directory: {remote}")
        return self._dirs[remote]

    def read_file(self, remote, *, max_bytes=None):
        if remote not in self._files:
            raise RuntimeError(f"no such file: {remote}")
        data = self._files[remote]
        return data[:max_bytes] if max_bytes else data

    def run(self, command, *, cwd=None, timeout=None):
        self.runs.append(command)
        if command.startswith("mkdir -p "):
            self.mkdir_calls.append(command)
        return 0, "", ""

    def write_file(self, remote, data):
        self.write_calls.append(remote)
        self.written[remote] = bytes(data)
        return len(data)

    def atomic_write_file(self, remote, data, *, expected_sha256):
        payload = bytes(data)
        if hashlib.sha256(payload).hexdigest() != expected_sha256:
            raise RuntimeError("hash mismatch")
        self.write_calls.append(remote)
        self.written[remote] = payload
        self._files[remote] = payload
        return len(payload)

    def sha256_file(self, remote):
        return hashlib.sha256(self._files[remote]).hexdigest()

    def mkdir(self, remote):
        self.mkdir_calls.append(remote)
        self._dirs.setdefault(remote, [])

    def stat(self, remote):
        # 对齐真实 SFTP stat：目录、_files 里的文件、
        # 以及目录列表条目里的文件均返回 dict
        p = str(remote).replace("\\", "/").rstrip("/")
        if p in self._dirs:
            return {"size": 0, "is_dir": True, "is_file": False}
        if p in self._files:
            return {"size": len(self._files[p]), "is_dir": False,
                    "is_file": True}
        parent, _, name = p.rpartition("/")
        for info in self._dirs.get(parent, []):
            if info.get("name") == name:
                return {"size": int(info.get("size") or 0),
                        "is_dir": bool(info.get("is_dir")),
                        "is_file": not bool(info.get("is_dir"))}
        return None


def _hpc_task(ctx, tmp_path):
    ws = tmp_path / "localws"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "INCAR").write_text("SYSTEM = local\n", encoding="utf-8")
    tk = ctx.store.create_task(ctx.pid, goal="超算计算",
                               local_workspace=str(ws),
                               hpc_workspace="/remote/work")
    return tk, ws


def _remote_vasp_inputs(root: str, job_key: str) -> dict[str, bytes]:
    return {f"{root}/{job_key}/{name}": f"{name} input\n".encode()
            for name in ("INCAR", "POSCAR", "KPOINTS", "POTCAR")}


def test_executor_hpc_list_and_read(ctx, tmp_path):
    """hpc_list/hpc_read 能直接查看超算工作区（与 ws_* 对应），且拒绝越界路径。"""
    tk, _ws = _hpc_task(ctx, tmp_path)
    root = "/remote/work"
    hpc = _FakeHpc(
        dirs={root: [
            {"name": "relax", "is_dir": True, "size": 0},
            {"name": "OUTCAR", "is_dir": False, "size": 500},
        ],
            f"{root}/relax": [
                {"name": "OSZICAR", "is_dir": False, "size": 120},
            ]},
        files={f"{root}/OUTCAR": b"  converged yes\n",
               f"{root}/relax/OSZICAR": b"DAV:  10\n"})
    ex = ToolExecutor(store=ctx.store, project_id=ctx.pid, task_id=tk["id"],
                      cfg=ctx.cfg, orch=SimpleNamespace(hpc=hpc))
    out_list = ex.handle("hpc_list", {})
    assert "【超算目录】/remote/work" in out_list
    assert "relax/（目录）" in out_list and "OUTCAR（500 B）" in out_list
    out_sub = ex.handle("hpc_list", {"path": "relax"})
    assert "OSZICAR（120 B）" in out_sub
    out_read = ex.handle("hpc_read", {"path": "OUTCAR"})
    assert "converged yes" in out_read
    out_read_sub = ex.handle("hpc_read", {"path": "relax/OSZICAR"})
    assert "DAV:  10" in out_read_sub
    # 越界/非法路径一律拒绝
    assert "非法" in ex.handle("hpc_read", {"path": "../etc/passwd"})
    assert "非法" in ex.handle("hpc_list", {"path": "/abs/path"})
    assert "非法" in ex.handle("hpc_read", {"path": "C:/Users/x"})


def test_executor_hpc_tools_report_honestly_when_unavailable(ctx, tmp_path):
    """未连超算/未设超算工作区时，hpc_* 如实说明，不伪造。"""
    tk, _ws = _hpc_task(ctx, tmp_path)
    ex = ToolExecutor(store=ctx.store, project_id=ctx.pid, task_id=tk["id"],
                      cfg=ctx.cfg, orch=SimpleNamespace(hpc=None))
    assert "未连接超算" in ex.handle("hpc_list", {})
    assert "未连接超算" in ex.handle("hpc_read", {"path": "INCAR"})
    tk2 = ctx.store.create_task(ctx.pid, goal="无超算目录")
    ex2 = ToolExecutor(store=ctx.store, project_id=ctx.pid, task_id=tk2["id"],
                       cfg=ctx.cfg, orch=SimpleNamespace(hpc=_FakeHpc({}, {})))
    assert "未设置超算工作区" in ex2.handle("hpc_list", {})
    assert ex2.hpc_snapshot() == ""


def test_sensitive_and_non_text_local_reads_fail_closed(ctx):
    ws = ctx.tmp / "sensitive-ws"
    ws.mkdir()
    ctx.store.update_task(ctx.pid, ctx.tid, local_workspace=str(ws))
    ex = ToolExecutor(store=ctx.store, project_id=ctx.pid, task_id=ctx.tid,
                      cfg=ctx.cfg)
    sentinel = b"TOP_SECRET_SENTINEL"
    denied = ("POTCAR", "POTCAR.gz", "WAVECAR", "CHGCAR.1", "id_rsa",
              ".env.local", "config.json", "cluster.pem", "mystery.dat")
    for name in denied:
        (ws / name).write_bytes(sentinel)
        out = ex.handle("ws_read", {"path": name})
        assert "DENIED" in out
        assert sentinel.decode() not in out
    listing = ex.handle("ws_list", {})
    assert "POTCAR" in listing
    assert sentinel.decode() not in listing
    (ws / "OUTCAR").write_bytes(b"ok\x00" + sentinel)
    binary = ex.handle("ws_read", {"path": "OUTCAR"})
    assert "AI_BINARY_FILE_DENIED" in binary and sentinel.decode() not in binary
    (ws / "OUTCAR").write_bytes(b"x" * 12001 + sentinel)
    large = ex.handle("ws_read", {"path": "OUTCAR"})
    assert "AI_FILE_TOO_LARGE" in large and sentinel.decode() not in large


def test_sensitive_and_non_text_remote_reads_fail_closed(ctx, tmp_path):
    tk, _ws = _hpc_task(ctx, tmp_path)
    root = "/remote/work"
    sentinel = b"REMOTE_SECRET_SENTINEL"
    denied_names = (
        "POTCAR.spec", "WAVECAR", "CHGCAR.old", "id_ed25519",
        ".env", "credentials.json", "token.key", "unknown.bin")
    files = {f"{root}/{name}": sentinel for name in denied_names}
    files[f"{root}/OUTCAR"] = b"text\x01" + sentinel
    hpc = _FakeHpc(dirs={root: [
        {"name": name, "is_dir": False, "size": len(sentinel)}
        for name in denied_names
    ]}, files=files)
    ex = ToolExecutor(store=ctx.store, project_id=ctx.pid, task_id=tk["id"],
                      cfg=ctx.cfg, orch=SimpleNamespace(hpc=hpc))
    for name in ("POTCAR.spec", "WAVECAR", "CHGCAR.old", "id_ed25519",
                 ".env", "credentials.json", "token.key", "unknown.bin"):
        out = ex.handle("hpc_read", {"path": name})
        assert "DENIED" in out and sentinel.decode() not in out
    binary = ex.handle("hpc_read", {"path": "OUTCAR"})
    assert "AI_BINARY_FILE_DENIED" in binary
    assert sentinel.decode() not in binary
    snapshot = ex.hpc_snapshot()
    assert "POTCAR.spec" in snapshot
    assert sentinel.decode() not in snapshot


# ---------------- M50/M57：受限上传 hpc_upload（SFTP，免弹卡） --------
def test_executor_hpc_upload_direct_without_card(ctx, tmp_path):
    """Raw model-supplied source paths cannot initiate an upload."""
    tk, ws = _hpc_task(ctx, tmp_path)
    (ws / "extra.bin").write_bytes(b"\x00\x01POSCAR-ish")
    root = "/remote/work"
    hpc = _FakeHpc(dirs={root: []}, files={})
    ex = ToolExecutor(store=ctx.store, project_id=ctx.pid, task_id=tk["id"],
                      cfg=ctx.cfg, orch=SimpleNamespace(hpc=hpc))
    out = ex.handle("hpc_upload", {"source": "extra.bin"})
    assert "AI_ARTIFACT_REQUIRED" in out
    assert not hpc.written
    assert not hpc.mkdir_calls
    flow = (ctx.store.get_task(ctx.pid, tk["id"]) or {}).get("flow") or {}
    assert not ((flow.get("consent") or {}).get("cards"))


def test_registered_artifact_upload_is_confirmed_and_single_use(ctx, tmp_path):
    tk, ws = _hpc_task(ctx, tmp_path)
    root = "/remote/work"
    hpc = _FakeHpc(dirs={root: []}, files={})
    ex = ToolExecutor(store=ctx.store, project_id=ctx.pid, task_id=tk["id"],
                      cfg=ctx.cfg, orch=SimpleNamespace(hpc=hpc))
    ex.handle("plan", {"jobs": [{"key": "relax", "label": "结构优化",
                                  "kind": "relax"}]})
    ex.handle("get_state", {})
    flow = ctx.store.get_task(ctx.pid, tk["id"])["flow"]
    artifact_id = next(aid for aid, item in flow["artifacts"].items()
                       if item["name"] == "INCAR")
    pending = ex.handle("hpc_upload", {"artifact_id": artifact_id,
                                        "job_key": "relax"})
    assert pending.startswith(_CONSENT_PENDING)
    assert not hpc.written and not hpc.write_calls
    action_id = pending[len(_CONSENT_PENDING):]
    action = get_card(ctx.store, ctx.pid, tk["id"], action_id)
    assert action["binding"]["source_sha256"] == ex._sha256_file(ws / "INCAR")
    assert action["binding"]["execution_mode"] == "Fake"
    assert action["options"] == ["同意本次", "拒绝"]
    assert "已通过 SFTP 上传" in _approve_pending(ex, pending)
    assert hpc.written[f"{root}/relax/INCAR"] == (ws / "INCAR").read_bytes()
    assert hpc.write_calls == [f"{root}/relax/INCAR"]
    assert hpc.sha256_file(f"{root}/relax/INCAR") == \
        action["binding"]["source_sha256"]
    ex.execute_action(action_id)
    assert hpc.write_calls == [f"{root}/relax/INCAR"]


def test_registered_artifact_copy_waits_for_confirmation(ctx):
    ws = ctx.tmp / "copy-ws"
    ws.mkdir()
    (ws / "POSCAR").write_text("POSCAR source\n", encoding="utf-8")
    tk = ctx.store.create_task(ctx.pid, goal="copy", local_workspace=str(ws))
    ex = ToolExecutor(store=ctx.store, project_id=ctx.pid, task_id=tk["id"],
                      cfg=ctx.cfg)
    ex.handle("plan", {"jobs": [{"key": "relax", "label": "结构优化",
                                  "kind": "relax"}]})
    ex.handle("get_state", {})
    flow = ctx.store.get_task(ctx.pid, tk["id"])["flow"]
    artifact_id = next(iter(flow["artifacts"]))
    pending = ex.handle("copy_inputs", {"artifact_ids": [artifact_id],
                                        "job_key": "relax"})
    target = ws / "relax" / "POSCAR"
    assert pending.startswith(_CONSENT_PENDING) and not target.exists()
    assert "已原子复制" in _approve_pending(ex, pending)
    assert target.read_text(encoding="utf-8") == "POSCAR source\n"


def test_executor_hpc_upload_rejects_unsafe_and_missing(ctx, tmp_path):
    """越界/缺参/缺文件/未连超算：如实拒绝或说明，绝不写远端。"""
    tk, ws = _hpc_task(ctx, tmp_path)
    root = "/remote/work"
    hpc = _FakeHpc(dirs={root: []}, files={})
    ex = ToolExecutor(store=ctx.store, project_id=ctx.pid, task_id=tk["id"],
                      cfg=ctx.cfg, orch=SimpleNamespace(hpc=hpc))
    out_esc = ex.handle("hpc_upload", {"source": "../escape.txt"})
    assert "AI_ARTIFACT_REQUIRED" in out_esc
    assert "AI_ARTIFACT_REQUIRED" in ex.handle(
        "hpc_upload", {"source": "a.txt", "dest": "/abs/dest"})
    assert "AI_ARTIFACT_REQUIRED" in ex.handle("hpc_upload", {})
    assert "AI_ARTIFACT_REQUIRED" in ex.handle(
        "hpc_upload", {"source": "missing.txt"})
    assert hpc.written == {}
    # 未连超算：如实说明
    ex2 = ToolExecutor(store=ctx.store, project_id=ctx.pid, task_id=tk["id"],
                       cfg=ctx.cfg, orch=SimpleNamespace(hpc=None))
    assert "AI_ARTIFACT_REQUIRED" in ex2.handle(
        "hpc_upload", {"source": "INCAR"})


def test_upload_classified_deny_but_scp_always_denied(ctx, tmp_path):
    """红线不变：run_exec/hpc_exec 执行 scp 一律 DENY，弹卡也不行。"""
    tk, _ws = _hpc_task(ctx, tmp_path)
    hpc = _FakeHpc(dirs={"/remote/work": []}, files={})
    ex = ToolExecutor(store=ctx.store, project_id=ctx.pid, task_id=tk["id"],
                      cfg=ctx.cfg, orch=SimpleNamespace(hpc=hpc))
    out = ex.handle("run_exec",
                    {"command": "scp INCAR user@hpc:/remote/work/"})
    assert "AI_FREEFORM_EXEC_DISABLED" in out
    out2 = ex.handle("hpc_exec", {"command": "scp a.txt b.txt"})
    assert "AI_FREEFORM_EXEC_DISABLED" in out2
    assert hpc.written == {}


# ---------------- M51：超算为主（远端脚本优先 + hpc_write_script） ------
def test_draft_uses_remote_script_when_local_missing(ctx, tmp_path):
    tk, _ws = _hpc_task(ctx, tmp_path)
    root = "/remote/work"
    hpc = _FakeHpc(
        dirs={root: [{"name": "sub_vasp.sh", "is_dir": False, "size": 88}],
              f"{root}/relax": [
                  {"name": "INCAR", "is_dir": False, "size": 20},
                  {"name": "POSCAR", "is_dir": False, "size": 30},
                  {"name": "KPOINTS", "is_dir": False, "size": 10},
                  {"name": "POTCAR", "is_dir": False, "size": 40},
                  {"name": "sub_vasp.sh", "is_dir": False, "size": 88}]},
        files={**_remote_vasp_inputs(root, "relax"),
               f"{root}/relax/sub_vasp.sh": b"#!/bin/bash\nsbatch vasp\n"})
    ex = ToolExecutor(store=ctx.store, project_id=ctx.pid, task_id=tk["id"],
                      cfg=ctx.cfg, orch=SimpleNamespace(hpc=hpc))
    ex.handle("plan", {"jobs": [{"key": "relax", "label": "结构优化",
                                 "kind": "relax"}]})
    pending = ex.handle("draft", {})
    assert pending.startswith(_CONSENT_PENDING)
    assert not ctx.store.get_task(ctx.pid, tk["id"])["flow"].get("draft")
    action = get_card(ctx.store, ctx.pid, tk["id"],
                      pending[len(_CONSENT_PENDING):])
    assert "sbatch vasp" not in action["summary"]
    assert "已认领" in _approve_pending(ex, pending)
    out = ex.handle("draft", {})
    assert "已生成提交草稿" in out and "超算作业目录" in out
    flow = ctx.store.get_task(ctx.pid, tk["id"])["flow"]
    assert flow["phase"] == "await_submit"
    draft = flow["draft"][0]
    assert draft["script_source"] == "remote"
    assert draft["script_name"] == "sub_vasp.sh"
    assert draft["dir"] == f"{root}/relax"
    assert "script_text" not in draft
    assert len(draft["script_sha256"]) == 64


def test_hpc_write_script_direct_without_card(ctx, tmp_path):
    """LLM must never be able to create arbitrary remote scripts."""
    tk, _ws = _hpc_task(ctx, tmp_path)
    root = "/remote/work"
    hpc = _FakeHpc(dirs={root: []}, files={})
    ex = ToolExecutor(store=ctx.store, project_id=ctx.pid, task_id=tk["id"],
                      cfg=ctx.cfg, orch=SimpleNamespace(hpc=hpc))
    script = "#!/bin/bash\n#SBATCH -N 1\nsrun vasp_std\n"
    out_bad = ex.handle("hpc_write_script", {"filename": "x.txt",
                                             "content": script})
    assert "AI_TOOL_NOT_ALLOWED" in out_bad
    out = ex.handle("hpc_write_script", {"dir": "relax",
                                         "filename": "sub_vasp.sh",
                                         "content": script})
    assert "AI_TOOL_NOT_ALLOWED" in out
    assert not hpc.written
    assert not hpc.runs
    flow = (ctx.store.get_task(ctx.pid, tk["id"]) or {}).get("flow") or {}
    assert not ((flow.get("consent") or {}).get("cards"))


def test_hpc_exec_cleans_vaspkit_temp_files(ctx, tmp_path):
    """The legacy remote command tool is a stable, side-effect-free denial."""
    tk, _ws = _hpc_task(ctx, tmp_path)
    hpc = _FakeHpc(dirs={"/remote/work": []}, files={})
    ex = ToolExecutor(store=ctx.store, project_id=ctx.pid, task_id=tk["id"],
                      cfg=ctx.cfg, orch=SimpleNamespace(hpc=hpc))
    out = ex.handle("hpc_exec", {"command": "vaspkit -task 301"})
    assert "AI_FREEFORM_EXEC_DISABLED" in out
    out_again = ex.handle("hpc_exec", {"command": "squeue -u me"})
    assert "AI_FREEFORM_EXEC_DISABLED" in out_again
    assert not hpc.runs


def test_precheck_sees_remote_script_and_files(ctx, tmp_path):
    """precheck 远端视角：文件只在超算上也算 [ok]（标注超算），不再误报本地缺失。"""
    tk, _ws = _hpc_task(ctx, tmp_path)
    root = "/remote/work"
    hpc = _FakeHpc(
        dirs={f"{root}/relax": [
            {"name": "INCAR", "is_dir": False, "size": 20},
            {"name": "POSCAR", "is_dir": False, "size": 30},
            {"name": "KPOINTS", "is_dir": False, "size": 10},
            {"name": "POTCAR", "is_dir": False, "size": 40},
            {"name": "sub_vasp.sh", "is_dir": False, "size": 88}]},
        files={**_remote_vasp_inputs(root, "relax"),
               f"{root}/relax/sub_vasp.sh": b"#!/bin/bash\nsrun vasp_std\n"})
    ex = ToolExecutor(store=ctx.store, project_id=ctx.pid, task_id=tk["id"],
                      cfg=ctx.cfg, orch=SimpleNamespace(hpc=hpc))
    ex.handle("plan", {"jobs": [{"key": "relax", "label": "结构优化",
                                 "kind": "relax"}]})
    pending = ex.handle("draft", {})
    assert "已认领" in _approve_pending(ex, pending)
    out = ex.handle("precheck", {})
    assert "（超算）" in out
    assert "INCAR 非空且哈希已绑定（超算）" in out
    assert "提交脚本 sub_vasp.sh 存在（超算）" in out
    flow = ctx.store.get_task(ctx.pid, tk["id"])["flow"]
    assert flow["precheck"]["ok"] is True


def test_build_messages_distinguishes_two_workspaces(ctx, tmp_path):
    """Prompt inventories both workspaces without implicitly reading contents."""
    from ai_mode.agent.runner import build_messages

    tk, ws = _hpc_task(ctx, tmp_path)
    root = "/remote/work"
    hpc = _FakeHpc(
        dirs={root: [{"name": "INCAR", "is_dir": False, "size": 20}]},
        files={f"{root}/INCAR": "SYSTEM = remote\n".encode()})
    ex = ToolExecutor(store=ctx.store, project_id=ctx.pid, task_id=tk["id"],
                      cfg=ctx.cfg, orch=SimpleNamespace(hpc=hpc))
    task = ctx.store.get_task(ctx.pid, tk["id"])
    messages = build_messages(ctx.store, task, [], "看看超算上有什么",
                              hpc_snapshot=ex.hpc_snapshot())
    system = messages[0]["content"]
    assert "① 本地工作区" in system and "② 超算工作区" in system
    assert "不要为了看远端文件去翻本地工作区" in system
    assert "【任务本地工作区只读快照 · 紧凑版】" in system
    assert "【超算工作区只读快照 · 实时经 SSH 生成】" in system
    assert "[超算工作区快照] /remote/work" in system
    assert "INCAR（20 B）" in system
    assert "SYSTEM = local" not in system
    assert "SYSTEM = remote" not in system
    # 未提供 hpc 快照时不得出现该段落
    messages2 = build_messages(ctx.store, task, [], "你好")
    assert "【超算工作区只读快照" not in messages2[0]["content"]


def test_run_agent_injects_hpc_snapshot(ctx, tmp_path):
    """run_agent 全链路：LLM 系统提示里能看到超算快照，从而先看超算再看本地。"""
    tk, _ws = _hpc_task(ctx, tmp_path)
    root = "/remote/work"
    hpc = _FakeHpc(
        dirs={root: [
            {"name": "POSCAR", "is_dir": False, "size": 10},
            {"name": "OUTCAR", "is_dir": False, "size": 30},
        ]},
        files={})
    llm = FakeLLM()
    llm.enqueue("超算工作区里已有 POSCAR 与 OUTCAR，无需重复准备。")
    answer = run_agent(ctx.store, ctx.pid, tk["id"], "看看超算上有什么文件",
                       cfg=ctx.cfg, llm_factory=lambda c: llm,
                       orch_factory=lambda: SimpleNamespace(hpc=hpc))
    assert "POSCAR" in answer
    system = llm.calls[0][0]["content"]
    assert "【超算工作区只读快照 · 实时经 SSH 生成】" in system
    assert "[超算工作区快照] /remote/work" in system
    assert "OUTCAR（30 B）" in system


# ---------------- M52：依赖链 + 嵌套作业目录 ----------------
def test_plan_nested_keys_and_requires(ctx):
    """依赖链作业用嵌套 key 依次往下建，requires 声明依赖并保留。"""
    ex = ToolExecutor(store=ctx.store, project_id=ctx.pid, task_id=ctx.tid,
                      cfg=ctx.cfg)
    out = ex.handle("plan", {"strategy": "链式", "jobs": [
        {"key": "relax", "label": "结构优化", "kind": "relax"},
        {"key": "relax/static", "label": "静态自洽", "kind": "static",
         "requires": ["relax"]},
        {"key": "relax/static/dos", "label": "DOS", "kind": "dos",
         "requires": ["relax/static"]}]})
    assert "已规划 3 条作业" in out
    flow = ctx.store.get_task(ctx.pid, ctx.tid)["flow"]
    keys = [j["key"] for j in flow["plan"]["jobs"]]
    assert keys == ["relax", "relax/static", "relax/static/dos"]
    assert flow["plan"]["jobs"][1]["requires"] == ["relax"]
    assert flow["plan"]["jobs"][2]["requires"] == ["relax/static"]


def test_plan_rejects_cyclic_and_unknown_requires(ctx):
    """依赖成环 / 未知前置：plan 拒绝并给出修正提示，不落库。"""
    ex = ToolExecutor(store=ctx.store, project_id=ctx.pid, task_id=ctx.tid,
                      cfg=ctx.cfg)
    out = ex.handle("plan", {"jobs": [
        {"key": "a", "label": "A", "requires": ["b"]},
        {"key": "b", "label": "B", "requires": ["a"]}]})
    assert "规划不合法" in out and "成环" in out
    flow = ctx.store.get_task(ctx.pid, ctx.tid).get("flow") or {}
    assert not ((flow.get("plan") or {}).get("jobs"))
    out2 = ex.handle("plan", {"jobs": [
        {"key": "a", "label": "A", "requires": ["ghost"]}]})
    assert "未知前置" in out2
    flow2 = ctx.store.get_task(ctx.pid, ctx.tid).get("flow") or {}
    assert not ((flow2.get("plan") or {}).get("jobs"))


def test_write_input_supports_nested_dir(ctx):
    """Nested paths do not bypass the generic-write denial."""
    ex = ToolExecutor(store=ctx.store, project_id=ctx.pid, task_id=ctx.tid,
                      cfg=ctx.cfg)
    out = ex.handle("write_input", {"filename": "INCAR",
                                    "content": "ICHARG = 11",
                                    "dir": "relax/static"})
    assert "AI_TOOL_NOT_ALLOWED" in out
    target = ex.local_dir() / "relax" / "static" / "INCAR"
    assert not target.exists()


# ---------------- M54：作业目录白名单 + 等待不诊断 ----------------
def test_write_input_rejects_off_plan_dir(ctx):
    """All legacy generic writes are rejected, on-plan or off-plan."""
    ex = ToolExecutor(store=ctx.store, project_id=ctx.pid, task_id=ctx.tid,
                      cfg=ctx.cfg)
    ex.handle("plan", {"strategy": "链式", "jobs": [
        {"key": "relax", "label": "结构优化", "kind": "relax"},
        {"key": "relax/static", "label": "静态自洽", "kind": "static",
         "requires": ["relax"]},
        {"key": "relax/static/dos", "label": "DOS", "kind": "dos",
         "requires": ["relax/static"]}]})
    out = ex.handle("write_input", {"filename": "KPOINTS",
                                    "content": "Gamma\n", "dir": "relax/dos"})
    assert "AI_TOOL_NOT_ALLOWED" in out
    assert not (ex.local_dir() / "relax" / "dos").exists()
    # Even a previously on-plan target must remain denied.
    ok = ex.handle("write_input", {"filename": "INCAR",
                                   "content": "ICHARG = 11",
                                   "dir": "relax/static/dos"})
    assert "AI_TOOL_NOT_ALLOWED" in ok
    # The workspace root is not a bypass either.
    shared = ex.handle("write_input", {"filename": "README.txt",
                                       "content": "x", "dir": ""})
    assert "AI_TOOL_NOT_ALLOWED" in shared
    assert not (ex.local_dir() / "README.txt").exists()


def test_copy_inputs_rejects_off_plan_dir(ctx):
    """copy_inputs 同样受作业目录白名单约束。"""
    ws = ctx.tmp / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "POSCAR").write_text("POSCAR", encoding="utf-8")
    ctx.store.update_task(ctx.pid, ctx.tid, local_workspace=str(ws))
    ex = ToolExecutor(store=ctx.store, project_id=ctx.pid, task_id=ctx.tid,
                      cfg=ctx.cfg)
    ex.handle("plan", {"strategy": "链式", "jobs": [
        {"key": "relax", "label": "结构优化", "kind": "relax"},
        {"key": "relax/static", "label": "静态自洽", "kind": "static",
         "requires": ["relax"]}]})
    ex.handle("get_state", {})
    flow = ctx.store.get_task(ctx.pid, ctx.tid)["flow"]
    artifact_id = next(iter(flow["artifacts"]))
    out = ex.handle("copy_inputs", {"artifact_ids": [artifact_id],
                                    "job_key": "static"})
    assert "不是任何已规划作业的目录" in out
    assert not (ex.local_dir() / "static").exists()
