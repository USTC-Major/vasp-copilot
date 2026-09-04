"""中枢对话入口（M31：LLM 决策驱动主路径）。

对齐总纲与 WORKFLOW v14 §3：智能模式 = LLM 端到端主导科学计算。是否开启计算
流程、何时规划、准备哪些输入、是否预检、停在哪个节点，全部由 LLM 通过决策循环
（agent.runner）自决并调用真实工具完成；系统只给方向参考 + 红线，不再有任何
固定模板或「固定 8 步锁死」。
本模块只保留最轻量分流：
- 流程已进入「待你确认提交」：文字确认只创建一次性卡片，真正提交必须经
  卡片批准、原子 claim 与 Orchestrator 的独立绑定校验；取消仍可直接处理。
  其余自由文本交还 LLM 决策循环——绝不代替用户执行 sbatch。
- 其余消息一律进 agent 决策循环：LLM 自决是闲聊直接回答，还是调用工具推进
  规划/输入/预检/草稿/监控/报告；LLM 不可用 = 整体瘫痪，不启动任何流程。
不接触任何密钥、不执行任何命令。
"""
from __future__ import annotations

import re
from typing import Callable

from .config import AiModeConfig, load_settings
from .consent import spawn_submit_card as _spawn_submit_card
from .llm.factory import build_client
from .projects import ProjectStore
from .agent.runner import run_agent, run_agent_stream

logger = __import__("logging").getLogger("ai_mode.chat")
__test__ = False  # pytest: 不收集本模块顶层 test_* 函数

#: 计算意图关键词（词、VASP 输入参数、计算类型）。保留给 classify() 兼容导出，
#: 不再作为路由依据（路由已交给 LLM 决策）。
_COMPUTE_KW = (
    "计算", "帮我算", "跑一下", "跑个", "上超算", "提交流程",
    "vasp", "vasprun", "能带", "态密度", "dos", "声子",
    "结构优化", "优化结构", "结构弛豫", "弛豫", "几何优化", "优化",
    "吸附", "缺陷", "表面", "磁性", "分子动力学", "动力学模拟", "aimd",
    "收敛测试", "自洽", "scf", "静态计算",
    "incar", "poscar", "potcar", "kpoints", "k点", "ediff", "encut",
    "ismear", "isif", "ibrion", "nsw", "potim", "lorbit",
    "结构文件", "parchg", "chgcar", "投影", "态密度计算", "能带计算",
)
_COMPUTE_PATTERNS = [re.compile(re.escape(kw), re.IGNORECASE)
                     for kw in _COMPUTE_KW]

_VIEW_SUBSTR = ("看看", "看一下", "查看", "读一下", "工作区有什么", "目录里有什么", "有哪些文件")
Intent = str  # "confirm" | "compute" | "chat"

_PUNCT = str.maketrans("", "", " \t\u3000！!？?。，,、.…·""''「」（）()【】[]（）")


CONFIRM_PHRASES = (
    "开始计算流程", "开始计算", "开始执行", "开始吧",
    "请开始", "走流程", "就这么做", "就这么办",
    "确认开始", "同意开始", "好的",
)
CONFIRM_WORDS = ("好", "嗯", "ok", "yes", "对", "是的", "可以",
                 "开始", "确认", "同意", "没关系")


def classify(content: str) -> Intent:
    """规则意图分流：确认起始 / 计算需求 / 普通聊天。

    仅作兼容导出/调试用途；实际路由已交给 LLM（见 reply/reply_stream）。
    """
    text = _norm(content)
    for phrase in CONFIRM_PHRASES:
        if phrase in text:
            return "confirm"
    if text in CONFIRM_WORDS:
        return "confirm"
    if any(k in text for k in _VIEW_SUBSTR):
        return "chat"
    for pattern in _COMPUTE_PATTERNS:
        if pattern.search(text):
            return "compute"
    return "chat"


def _norm(content: str) -> str:
    return (content or "").strip().lower().translate(_PUNCT)


def _default_llm_factory(_config: AiModeConfig):
    return build_client(_config)


def _make_orchestrator(llm_factory: Callable[[AiModeConfig], object]):
    from .orchestrator import Orchestrator
    return Orchestrator.from_settings(load_settings(),
                                      llm_factory=llm_factory)


def _await_submit_gate_passes(content: str) -> bool:
    """await_submit 阶段的确定性闸门。

    只有明确「确认提交 / 取消」才放行给 Orchestrator（真实 sbatch 只经此入口）；
    其余自由文本（补充输入、提建议、干预、追问）一律交还 LLM 决策循环处理，
    停在同一流程等待确认，绝不越红线代替用户执行。
    """
    from .orchestrator import _is_cancel, _is_true_answer

    return _is_true_answer(content) or _is_cancel(content)


#: spawn_submit_card 已迁至 consent.py（runner 与 chat 共用），此处保留模块级别名。
spawn_submit_card = _spawn_submit_card


def perform_submit(store: ProjectStore, project_id: str, task_id: str,
                   card_id: str, approved: bool, note: str = "") -> str:
    """Serialize one complete submit decision with all other task mutations."""
    from .consent import task_lock

    with task_lock(project_id, task_id):
        return _perform_submit_locked(store, project_id, task_id, card_id,
                                      approved, note)


def _perform_submit_locked(store: ProjectStore, project_id: str, task_id: str,
                           card_id: str, approved: bool, note: str = "") -> str:
    """Resolve and claim a single-use submit action before any scheduler call."""
    from .consent import claim_action, finish_action, get_card, resolve_card
    from .orchestrator import Orchestrator

    card = get_card(store, project_id, task_id, card_id)
    if card is None:
        return "确认卡片不存在或已处理，请重新发起确认。"
    resolved = resolve_card(store, project_id, task_id, card_id,
                            approved=approved, note=note)
    if resolved.get("conflict") or resolved.get("expired") or resolved.get("tampered"):
        return "该确认已处理、过期或失效；不会重复提交。"
    flow = (store.get_task(project_id, task_id) or {}).get("flow") or {}
    orch = Orchestrator.from_settings(load_settings())
    if approved:
        if flow.get("phase") != "await_submit":
            action = claim_action(store, project_id, task_id, card_id)
            if action is not None:
                finish_action(store, project_id, task_id, card_id,
                              state="failed",
                              result="流程已离开待提交阶段，未执行 sbatch")
            return "流程已不在「待你确认提交」环节，无法提交；请查看当前状态。"
        action = claim_action(store, project_id, task_id, card_id)
        if action is None:
            return "该确认已失效或已被使用；不会重复提交。"
        # Claiming is persisted before any scheduler call.  Reload so every
        # orchestrator save carries the authoritative ``executing`` action
        # instead of overwriting it with the pre-claim snapshot.
        flow = (store.get_task(project_id, task_id) or {}).get("flow") or {}
        binding = action.get("binding") or {}
        current_mode = str(getattr(orch, "execution_mode",
                                   flow.get("execution_mode") or "None"))
        if (binding.get("project_id") != project_id
                or binding.get("task_id") != task_id
                or binding.get("remote_root") != str(flow.get("hpc_dir") or flow.get("local_dir") or "").strip()
                or binding.get("drafts") != (flow.get("draft") or [])
                or binding.get("execution_mode") != current_mode
                or binding.get("precheck_digest") != str(
                    (flow.get("precheck") or {}).get("digest") or "")):
            result = "提交目标或草稿在确认后发生变化；已拒绝执行，sbatch=0。"
            finish_action(store, project_id, task_id, card_id,
                          state="failed", result=result)
            return result
        try:
            result = orch._submit(store, project_id, task_id, dict(flow))
        except Exception as exc:
            finish_action(store, project_id, task_id, card_id,
                          state="unknown",
                          result=f"提交结果不确定且不会自动重试：{type(exc).__name__}")
            raise
        current = (store.get_task(project_id, task_id) or {}).get("flow") or {}
        uncertain = any(
            job.get("submission_state") == "unknown"
            for job in ((current.get("plan") or {}).get("jobs") or []))
        submitted = any(
            job.get("submission_action_id") == card_id
            and job.get("submission_state") == "submitted"
            for job in ((current.get("plan") or {}).get("jobs") or []))
        finish_action(store, project_id, task_id, card_id,
                      state=("unknown" if uncertain else
                             "executed" if submitted else "failed"),
                      result=result)
        return result
    action = claim_action(store, project_id, task_id, card_id)
    if action is not None:
        # Defensive only: rejected cards cannot normally be claimed.
        finish_action(store, project_id, task_id, card_id,
                      state="failed", result="提交已取消")
    return orch._on_await_submit(store, project_id, task_id, dict(flow),
                                 "取消")
def reply(store: ProjectStore, project_id: str, task_id: str, content: str,
          *, llm_factory: Callable[[AiModeConfig], object] | None = None,
          should_stop=None) -> str:
    """对一条用户消息给出中枢答复（LLM 决策驱动主路径）。

    - 流程已在「待你确认提交」：取消可直接处理；提交只创建确认卡。
    - 其余消息统一进 agent 决策循环：LLM 自决聊天/计算，绝无固定模板。
    """
    task = store.get_task(project_id, task_id)
    if task is None:
        raise ValueError(f"任务不存在: {task_id}")
    factory = llm_factory or _default_llm_factory
    flow = task.get("flow") or {}
    if (flow.get("phase") == "await_submit"
            and _await_submit_gate_passes(content)):
        from .orchestrator import _is_cancel

        orch = _make_orchestrator(factory)
        mode = orch.sync_execution_mode(store, project_id, task_id)
        if _is_cancel(content):
            return orch.handle(store, project_id, task_id, content)
        if mode == "None":
            return ("[AI_HPC_BACKEND_UNAVAILABLE] 当前未配置 HPC 执行后端；"
                    "未生成提交确认卡，也未执行 sbatch。请先配置 SSH。")
        try:
            spawn_submit_card(store, project_id, task_id)
        except ValueError as exc:
            return str(exc)
        return ("已生成单次提交确认卡；只有在卡片中确认后才会执行 sbatch。"
                "本条文字消息未提交任何作业。")
    return run_agent(store, project_id, task_id, content,
                     llm_factory=factory,
                     orch_factory=lambda: _make_orchestrator(factory),
                     should_stop=should_stop)


def reply_stream(store: ProjectStore, project_id: str, task_id: str,
                 content: str, *, llm_factory=None, should_stop=None):
    """reply() 的 SSE 事件流版本：先流 LLM 思考/正文，再流工具回执；done 落结果。

    与 reply() 同一分流；非确认提交路径由 run_agent_stream 产出事件
    （type=thinking/answer/error/done），服务器据其落库 assistant。
    """
    task = store.get_task(project_id, task_id)
    if task is None:
        raise ValueError(f"任务不存在: {task_id}")
    factory = llm_factory or _default_llm_factory
    flow = task.get("flow") or {}
    if (flow.get("phase") == "await_submit"
            and _await_submit_gate_passes(content)):
        from .orchestrator import _is_cancel

        orch = _make_orchestrator(factory)
        mode = orch.sync_execution_mode(store, project_id, task_id)
        if _is_cancel(content):
            answer = orch.handle(store, project_id, task_id, content)
            yield {"type": "answer", "text": answer}
            yield {"type": "done", "answer": answer or "（执行结果为空）"}
            return
        if mode == "None":
            answer = ("[AI_HPC_BACKEND_UNAVAILABLE] 当前未配置 HPC 执行后端；"
                      "未生成提交确认卡，也未执行 sbatch。请先配置 SSH。")
            yield {"type": "answer", "text": answer}
            yield {"type": "done", "answer": answer}
            return
        try:
            card = spawn_submit_card(store, project_id, task_id)
        except ValueError as exc:
            answer = str(exc)
            yield {"type": "answer", "text": answer}
            yield {"type": "done", "answer": answer}
            return
        yield {"type": "card", "card": card}
        yield {"type": "done", "answer": ""}
        return
    yield from run_agent_stream(store, project_id, task_id, content,
                                llm_factory=factory,
                                orch_factory=lambda: _make_orchestrator(factory),
                                should_stop=should_stop)
