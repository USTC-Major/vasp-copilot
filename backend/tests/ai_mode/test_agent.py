# -*- coding: utf-8 -*-
"""M31 agent 决策驱动执行层测试（全离线，协议文本走 FakeLLM）。"""

import json
from types import SimpleNamespace

import pytest

from ai_mode.agent import parse_turn, run_agent, run_agent_stream
from ai_mode.agent.runner import _strip_receipt_wait
from ai_mode.agent.protocol import INTENT_MARK, TOOL_MARK
from ai_mode.agent.tools import _CONSENT_PENDING, ToolExecutor
from ai_mode.config import AiModeConfig
from ai_mode.llm.fake import FakeLLM
from ai_mode.projects import ProjectStore


def _intent(kind: str = "compute") -> str:
    return INTENT_MARK + json.dumps({"intent": kind}, ensure_ascii=False)


def _tool(name: str, **args) -> str:
    payload = json.dumps({"name": name, "args": args, "reason": "r"},
                         ensure_ascii=False)
    return TOOL_MARK + payload


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
    assert local_dir.is_dir()


def test_agent_write_input_persists(ctx):
    llm = FakeLLM()
    llm.enqueue(
        _tool("write_input", filename="INCAR",
              content="SYSTEM = relax\nENCUT = 520\n")
        + "\n已写入计算目录。")
    llm.enqueue("输入文件已准备好。")
    answer = run_agent(ctx.store, ctx.pid, ctx.tid, "帮我生成 INCAR",
                       cfg=ctx.cfg, llm_factory=lambda c: llm)
    assert "已写入" in answer
    local_dir = ctx.cfg.data_dir / "workspace" / f"{ctx.pid}__{ctx.tid}"
    incar = local_dir / "INCAR"
    assert incar.is_file()
    assert "ENCUT = 520" in incar.read_text(encoding="utf-8")


def test_agent_dangerous_command_requires_consent(ctx):
    # M47：目录内破坏性操作（rm 等）不再静默放行，而是「弹卡授权」。
    # rm -rf / 命中 HOLD -> 卡片落库 flow.consent，命令不执行。
    llm = FakeLLM()
    llm.enqueue(_tool("run_exec", command="rm -rf /") + "\n我尝试清理。")
    answer = run_agent(ctx.store, ctx.pid, ctx.tid, "帮我清理一下",
                       cfg=ctx.cfg, llm_factory=lambda c: llm,
                       auto_resume=False)
    assert "我尝试清理。" in answer
    flow = ctx.store.get_task(ctx.pid, ctx.tid)["flow"]
    cards = (flow.get("consent") or {}).get("cards") or {}
    assert cards, "rm -rf / 应生成待授权的门禁卡片"
    card = list(cards.values())[0]
    assert card["tool"] == "run_exec"
    assert card["args"]["command"] == "rm -rf /"
    assert card["kind"] == "workspace"
    assert card["batch_key"]


def test_agent_stream_dangerous_command_yields_card(ctx):
    # M47：流式渲染时，HOLD 命中要在流里产出 card 事件（前端据此弹卡）。
    llm = FakeLLM()
    llm.enqueue(_tool("run_exec", command="rm -rf /") + "\n我尝试清理。")
    events = list(run_agent_stream(ctx.store, ctx.pid, ctx.tid, "帮我清理一下",
                                   cfg=ctx.cfg, llm_factory=lambda c: llm,
                                   auto_resume=False))
    cards = [e["card"] for e in events if e["type"] == "card"]
    assert cards and cards[0]["args"]["command"] == "rm -rf /"
    assert events[-1]["type"] == "done"
    flow = ctx.store.get_task(ctx.pid, ctx.tid)["flow"]
    cons = flow.get("consent") or {}
    assert cons.get("cards")
def test_agent_submit_stops_at_await_submit(ctx):
    llm = FakeLLM()
    llm.enqueue(_tool("plan", jobs=[{"key": "r1", "label": "结构优化",
                                     "kind": "relax"}]))
    llm.enqueue(_tool("draft") + "\n我生成了提交草稿。")
    llm.enqueue("草稿已停在待确认；真实提交需你确认。")
    local_dir = ctx.cfg.data_dir / "workspace" / f"{ctx.pid}__{ctx.tid}"
    relax = local_dir / "relax"
    relax.mkdir(parents=True, exist_ok=True)
    (relax / "run.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    answer = run_agent(ctx.store, ctx.pid, ctx.tid, "帮我算结构优化",
                       cfg=ctx.cfg, llm_factory=lambda c: llm)
    flow = ctx.store.get_task(ctx.pid, ctx.tid)["flow"]
    assert flow["phase"] == "await_submit"
    assert flow.get("draft")


def test_agent_stream_auto_submit_card(ctx):
    # 问题3：draft 后不再停在文字等待，流式自动产出「确认提交」确认卡。
    llm = FakeLLM()
    llm.enqueue(
        _tool("plan", jobs=[{"key": "r1", "label": "结构优化", "kind": "relax"}])
        + "\n我先规划结构优化。")
    llm.enqueue(_tool("draft") + "\n草稿已生成，等待确认。")
    local_dir = ctx.cfg.data_dir / "workspace" / f"{ctx.pid}__{ctx.tid}"
    relax = local_dir / "relax"
    relax.mkdir(parents=True, exist_ok=True)
    (relax / "run.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    events = list(run_agent_stream(ctx.store, ctx.pid, ctx.tid, "帮我算结构优化",
                                   cfg=ctx.cfg, llm_factory=lambda c: llm))
    cards = [e["card"] for e in events if e["type"] == "card"]
    assert cards
    assert cards[-1]["kind"] == "submit"
    assert "确认提交" in cards[-1]["summary"]
    assert events[-1]["type"] == "done"
    flow = ctx.store.get_task(ctx.pid, ctx.tid)["flow"]
    assert flow["phase"] == "await_submit"


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
    relax.mkdir(parents=True, exist_ok=True)
    (relax / "run.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    out = ex.handle("draft", {})
    assert "已生成提交草稿" in out
    flow = ctx.store.get_task(ctx.pid, ctx.tid)["flow"]
    assert flow["phase"] == "await_submit"
    assert flow.get("draft")


def test_executor_unknown_tool_returns_help(ctx):
    ex = ToolExecutor(store=ctx.store, project_id=ctx.pid, task_id=ctx.tid,
                      cfg=ctx.cfg)
    out = ex.handle("no_such_tool", {})
    assert "未知工具" in out


def test_executor_monitor_without_ssh_degrades(ctx):
    ex = ToolExecutor(store=ctx.store, project_id=ctx.pid, task_id=ctx.tid,
                      cfg=ctx.cfg)
    out = ex.handle("monitor", {})
    assert "未连接超算" in out


def test_executor_run_exec_safe_command_allowed(ctx):
    ex = ToolExecutor(store=ctx.store, project_id=ctx.pid, task_id=ctx.tid,
                      cfg=ctx.cfg)
    out = ex.handle("run_exec", {"command": "pwd"})
    assert "拒绝" not in out


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
    """M42.5 回归：任务设了 local_workspace 时，AI 写操作必须落在该工作区，
    而不是私有草稿目录；flow.local_dir 也指向该工作区。"""
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
    assert "已写入" in answer
    incar = ws / "INCAR"
    assert incar.is_file()
    assert "ENCUT = 520" in incar.read_text(encoding="utf-8")
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
    result = exec_.tool_copy_inputs({"filenames": ["POSCAR"]})
    assert "已复制" in result and "缺失" not in result
    assert (ws / "POSCAR").is_file()

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
    relax.mkdir(parents=True, exist_ok=True)
    (relax / "run.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    out = ex.handle("select_jobs",
                    {"submit": ["relax"], "skip": ["static"]})
    assert "本次提交" in out and "跳过" in out
    flow = ctx.store.get_task(ctx.pid, ctx.tid)["flow"]
    statuses = {j["key"]: j["status"] for j in flow["plan"]["jobs"]}
    assert statuses == {"relax": "draft", "static": "skipped"}
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
    """问题「同意后继续」：授权卡被同意后，当前运行继续执行，不再中断等用户下一句。"""
    import threading
    import time

    from ai_mode.consent import resolve_card

    llm = FakeLLM()
    llm.enqueue(_tool("run_exec", command="mkdir -p ../consented_out")
                + "\n我把目录写到工作区外。")
    llm.enqueue("已在授权下完成，继续收尾。")
    events: list[dict] = []
    found = {"id": None}

    def _consume():
        for ev in run_agent_stream(ctx.store, ctx.pid, ctx.tid, "帮我执行越界写",
                                   cfg=ctx.cfg, llm_factory=lambda c: llm):
            if ev["type"] == "card":
                found["id"] = ev["card"]["card_id"]
            events.append(ev)

    thread = threading.Thread(target=_consume, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while found["id"] is None and time.monotonic() < deadline:
        time.sleep(0.02)
    assert found["id"] is not None, "等待授权期间应产出卡片"
    resolve_card(ctx.store, ctx.pid, ctx.tid, found["id"], approved=True)
    thread.join(timeout=10)
    assert not thread.is_alive(), "同意后运行应自行完成，无需外部打断"
    kinds = [e["type"] for e in events]
    assert "card" in kinds and "status" in kinds
    assert events[-1]["type"] == "done"
    assert (ctx.cfg.data_dir / "workspace" / "consented_out").is_dir()


def test_agent_stream_consent_deny_ends(ctx):
    """问题「拒绝才中断」：用户拒绝授权后运行结束，给出终止说明，不再继续。"""
    import threading
    import time

    from ai_mode.consent import resolve_card

    llm = FakeLLM()
    llm.enqueue(_tool("run_exec", command="mkdir -p ../evil") + "\n越界写。")
    llm.enqueue("已写入。")
    events: list[dict] = []
    found = {"id": None}

    def _consume():
        for ev in run_agent_stream(ctx.store, ctx.pid, ctx.tid, "帮我越界写",
                                   cfg=ctx.cfg, llm_factory=lambda c: llm):
            if ev["type"] == "card":
                found["id"] = ev["card"]["card_id"]
            events.append(ev)

    thread = threading.Thread(target=_consume, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while found["id"] is None and time.monotonic() < deadline:
        time.sleep(0.02)
    assert found["id"] is not None
    resolve_card(ctx.store, ctx.pid, ctx.tid, found["id"], approved=False)
    thread.join(timeout=10)
    assert not thread.is_alive()
    kinds = [e["type"] for e in events]
    assert kinds.count("done") == 1
    assert events[-1]["type"] == "done"
    assert "拒绝" in (events[-1].get("answer") or "")
    tail_idx = next(i for i, e in enumerate(events) if e["type"] == "done")
    assert not [e for e in events[tail_idx + 1:]]
def test_agent_write_input_and_draft_per_job_dir(ctx):
    """写输入到作业子目录（dir=作业 key），draft 把该作业目录记为提交目录，
    保证「输入文件与提交脚本在同一路径」。"""
    ex = ToolExecutor(store=ctx.store, project_id=ctx.pid, task_id=ctx.tid,
                      cfg=ctx.cfg)
    ex.handle("plan", {"jobs": [{"key": "relax", "label": "结构优化",
                                 "kind": "relax"}]})
    out = ex.handle("write_input", {
        "filename": "INCAR", "content": "SYSTEM = x\n", "dir": "relax"})
    assert "已写入" in out and "relax" in out
    local = ex.local_dir() / "relax" / "INCAR"
    assert local.is_file()
    relax = ex.local_dir() / "relax"
    relax.mkdir(parents=True, exist_ok=True)
    (relax / "run.sh").write_text("#!/bin/bash\n", encoding="utf-8")
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
    assert "拒绝写入提交脚本" in out
    assert "用户自己提供" in out
    assert not (ex.local_dir() / "run.sh").exists()


def test_draft_without_user_script_blocks_with_prompt(ctx):
    """本地与超算都没有 *.sh 时，draft 不进 await_submit，停在阻塞态并给路径指引。"""
    ex = ToolExecutor(store=ctx.store, project_id=ctx.pid, task_id=ctx.tid,
                      cfg=ctx.cfg)
    ex.handle("plan", {"jobs": [{"key": "relax", "label": "结构优化",
                                 "kind": "relax"}]})
    out = ex.handle("draft", {})
    assert "无法生成提交草稿" in out
    assert "超算作业目录" in out and "hpc_write_script" in out
    flow = ctx.store.get_task(ctx.pid, ctx.tid)["flow"]
    assert flow["phase"] == "blocked"
    assert not flow.get("draft")


# ---------------- M48：双工作区分辨（本地 vs 超算） ----------------
class _FakeHpc:
    """同签名假 SSHManager（list_dir_info/read_file/run/write_file）。"""

    def __init__(self, dirs, files):
        self._dirs = dirs
        self._files = dict(files)
        self.written: dict[str, bytes] = {}
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
        self.written[remote] = bytes(data)
        return len(data)

    def stat(self, remote):
        # 对齐真实 SFTP stat：目录、_files 里的文件、
        # 以及目录列表条目里的文件均返回 dict
        p = str(remote).replace("\\", "/").rstrip("/")
        if p in self._dirs or p in self._files:
            return {}
        parent, _, name = p.rpartition("/")
        for info in self._dirs.get(parent, []):
            if info.get("name") == name:
                return {}
        return None


def _hpc_task(ctx, tmp_path):
    ws = tmp_path / "localws"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "INCAR").write_text("SYSTEM = local\n", encoding="utf-8")
    tk = ctx.store.create_task(ctx.pid, goal="超算计算",
                               local_workspace=str(ws),
                               hpc_workspace="/remote/work")
    return tk, ws


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


# ---------------- M50/M57：受限上传 hpc_upload（SFTP，免弹卡） --------
def test_executor_hpc_upload_direct_without_card(ctx, tmp_path):
    """M57 用户政策：上传免弹卡（通道本身受控），直接上传不生成卡片。"""
    tk, ws = _hpc_task(ctx, tmp_path)
    (ws / "extra.bin").write_bytes(b"\x00\x01POSCAR-ish")
    root = "/remote/work"
    hpc = _FakeHpc(dirs={root: []}, files={})
    ex = ToolExecutor(store=ctx.store, project_id=ctx.pid, task_id=tk["id"],
                      cfg=ctx.cfg, orch=SimpleNamespace(hpc=hpc))
    out = ex.handle("hpc_upload", {"source": "extra.bin"})
    assert "已上传" in out
    assert hpc.written[f"{root}/extra.bin"] == b"\x00\x01POSCAR-ish"
    assert any("mkdir -p" in c for c in hpc.mkdir_calls)
    flow = (ctx.store.get_task(ctx.pid, tk["id"]) or {}).get("flow") or {}
    assert not ((flow.get("consent") or {}).get("cards"))


def test_executor_hpc_upload_rejects_unsafe_and_missing(ctx, tmp_path):
    """越界/缺参/缺文件/未连超算：如实拒绝或说明，绝不写远端。"""
    tk, ws = _hpc_task(ctx, tmp_path)
    root = "/remote/work"
    hpc = _FakeHpc(dirs={root: []}, files={})
    ex = ToolExecutor(store=ctx.store, project_id=ctx.pid, task_id=tk["id"],
                      cfg=ctx.cfg, orch=SimpleNamespace(hpc=hpc))
    out_esc = ex.handle("hpc_upload", {"source": "../escape.txt"})
    assert ("拒绝" in out_esc) or ("非法" in out_esc)
    assert "非法" in ex.handle("hpc_upload", {"source": "a.txt",
                                              "dest": "/abs/dest"})
    assert "缺少参数" in ex.handle("hpc_upload", {})
    assert "不存在" in ex.handle("hpc_upload", {"source": "missing.txt"})
    assert hpc.written == {}
    # 未连超算：如实说明
    ex2 = ToolExecutor(store=ctx.store, project_id=ctx.pid, task_id=tk["id"],
                       cfg=ctx.cfg, orch=SimpleNamespace(hpc=None))
    assert "未连接超算" in ex2.handle("hpc_upload", {"source": "INCAR"})


def test_upload_classified_deny_but_scp_always_denied(ctx, tmp_path):
    """红线不变：run_exec/hpc_exec 执行 scp 一律 DENY，弹卡也不行。"""
    tk, _ws = _hpc_task(ctx, tmp_path)
    hpc = _FakeHpc(dirs={"/remote/work": []}, files={})
    ex = ToolExecutor(store=ctx.store, project_id=ctx.pid, task_id=tk["id"],
                      cfg=ctx.cfg, orch=SimpleNamespace(hpc=hpc))
    out = ex.handle("run_exec",
                    {"command": "scp INCAR user@hpc:/remote/work/"})
    assert "拒绝" in out
    assert "不放行" in out
    out2 = ex.handle("hpc_exec", {"command": "scp a.txt b.txt"})
    assert "拒绝" in out2
    assert hpc.written == {}


# ---------------- M51：超算为主（远端脚本优先 + hpc_write_script） ------
def test_draft_uses_remote_script_when_local_missing(ctx, tmp_path):
    """超算作业目录里已有唯一 *.sh、本地没有：draft 直接用远端脚本，不再阻塞。"""
    tk, _ws = _hpc_task(ctx, tmp_path)
    root = "/remote/work"
    hpc = _FakeHpc(
        dirs={root: [{"name": "sub_vasp.sh", "is_dir": False, "size": 88}],
              f"{root}/relax": [
                  {"name": "INCAR", "is_dir": False, "size": 20},
                  {"name": "sub_vasp.sh", "is_dir": False, "size": 88}]},
        files={f"{root}/relax/sub_vasp.sh": b"#!/bin/bash\nsbatch vasp\n"})
    ex = ToolExecutor(store=ctx.store, project_id=ctx.pid, task_id=tk["id"],
                      cfg=ctx.cfg, orch=SimpleNamespace(hpc=hpc))
    ex.handle("plan", {"jobs": [{"key": "relax", "label": "结构优化",
                                 "kind": "relax"}]})
    out = ex.handle("draft", {})
    assert "已生成提交草稿" in out and "超算作业目录" in out
    flow = ctx.store.get_task(ctx.pid, tk["id"])["flow"]
    assert flow["phase"] == "await_submit"
    draft = flow["draft"][0]
    assert draft["script_source"] == "remote"
    assert draft["script_name"] == "sub_vasp.sh"
    assert draft["dir"] == f"{root}/relax"
    assert "sbatch vasp" in draft["script_text"]


def test_hpc_write_script_direct_without_card(ctx, tmp_path):
    """M57 用户政策：hpc_write_script 免弹卡直接写入（M51 逐次弹卡废止）。"""
    tk, _ws = _hpc_task(ctx, tmp_path)
    root = "/remote/work"
    hpc = _FakeHpc(dirs={root: []}, files={})
    ex = ToolExecutor(store=ctx.store, project_id=ctx.pid, task_id=tk["id"],
                      cfg=ctx.cfg, orch=SimpleNamespace(hpc=hpc))
    script = "#!/bin/bash\n#SBATCH -N 1\nsrun vasp_std\n"
    # 非法 filename 直接拒（红线不变）
    out_bad = ex.handle("hpc_write_script", {"filename": "x.txt",
                                             "content": script})
    assert "非法" in out_bad
    out = ex.handle("hpc_write_script", {"dir": "relax",
                                         "filename": "sub_vasp.sh",
                                         "content": script})
    assert "已把提交脚本写入超算" in out
    assert hpc.written[f"{root}/relax/sub_vasp.sh"] == script.encode()
    flow = (ctx.store.get_task(ctx.pid, tk["id"]) or {}).get("flow") or {}
    assert not ((flow.get("consent") or {}).get("cards"))


def test_hpc_exec_cleans_vaspkit_temp_files(ctx, tmp_path):
    """M57：vaspkit 命令执行后自动清理 *.err/*.log；非 vaspkit 不清理。"""
    tk, _ws = _hpc_task(ctx, tmp_path)
    hpc = _FakeHpc(dirs={"/remote/work": []}, files={})
    ex = ToolExecutor(store=ctx.store, project_id=ctx.pid, task_id=tk["id"],
                      cfg=ctx.cfg, orch=SimpleNamespace(hpc=hpc))
    out = ex.handle("hpc_exec", {"command": "vaspkit -task 301"})
    assert "远端命令已执行" in out
    assert any("-delete" in c and "'*.err'" in c and "'*.log'" in c
               for c in hpc.runs)
    n_after_vaspkit = len(hpc.runs)
    ex.handle("hpc_exec", {"command": "squeue -u me"})
    assert len(hpc.runs) == n_after_vaspkit + 1   # 无额外清理命令
    assert "-delete" not in hpc.runs[-1]


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
        files={})
    ex = ToolExecutor(store=ctx.store, project_id=ctx.pid, task_id=tk["id"],
                      cfg=ctx.cfg, orch=SimpleNamespace(hpc=hpc))
    ex.handle("plan", {"jobs": [{"key": "relax", "label": "结构优化",
                                 "kind": "relax"}]})
    out = ex.handle("precheck", {})
    assert "（超算）" in out
    assert "INCAR 存在（超算）" in out
    assert "提交脚本 sub_vasp.sh 存在（超算）" in out
    flow = ctx.store.get_task(ctx.pid, tk["id"])["flow"]
    assert flow["precheck"]["ok"] is True


def test_build_messages_distinguishes_two_workspaces(ctx, tmp_path):
    """系统提示同时注入本地与超算快照，并明确指示两者不要混活。"""
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
    assert "SYSTEM = remote" in system
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
    """write_input 的 dir 支持嵌套路径（relax/static），父目录自动创建。"""
    ex = ToolExecutor(store=ctx.store, project_id=ctx.pid, task_id=ctx.tid,
                      cfg=ctx.cfg)
    out = ex.handle("write_input", {"filename": "INCAR",
                                    "content": "ICHARG = 11",
                                    "dir": "relax/static"})
    assert "relax/static" in out
    target = ex.local_dir() / "relax" / "static" / "INCAR"
    assert target.is_file()
    assert "ICHARG" in target.read_text(encoding="utf-8")


# ---------------- M54：作业目录白名单 + 等待不诊断 ----------------
def test_write_input_rejects_off_plan_dir(ctx):
    """多作业规划后，dir 不在规划内（如自创 relax/dos）被拒绝并列出合法目录。"""
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
    assert "不是任何已规划作业的目录" in out
    assert "relax/static/dos" in out
    assert not (ex.local_dir() / "relax" / "dos").exists()
    # 合法嵌套 key 仍放行
    ok = ex.handle("write_input", {"filename": "INCAR",
                                   "content": "ICHARG = 11",
                                   "dir": "relax/static/dos"})
    assert "relax/static/dos" in ok
    # 空 dir（写共享文件到工作区根）放行
    shared = ex.handle("write_input", {"filename": "README.txt",
                                       "content": "x", "dir": ""})
    assert (ex.local_dir() / "README.txt").is_file() or "README" in shared


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
    out = ex.handle("copy_inputs", {"filenames": ["POSCAR"],
                                    "dir": "static"})
    assert "不是任何已规划作业的目录" in out
    assert not (ex.local_dir() / "static").exists()
