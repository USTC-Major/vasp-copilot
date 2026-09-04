# -*- coding: utf-8 -*-
"""LLM 决策驱动执行循环（M31）：intent 自决 + 工具决策循环。

把「是否开启计算流程 / 何时规划 / 准备什么输入 / 是否预检 / 停在哪一步」全部交给
LLM：系统只给方向参考（原 8 步降级为参考，可增删改序）+ 红线 + 工具清单，由 LLM
通过正文内嵌 ``<<<INTENT>>> / <<<TOOL>>>`` 标记自决调用真实工具（见 protocol/tools）。
- run_agent：非流式，返回完整回答文本（含思考性正文与工具操作回执）。
- run_agent_stream：流式，先流思考/正文（剥离协议标记），再流工具回执，保持 SSE 契约。
- LLM 不可用 = 整体瘫痪：不调用任何工具、不启动流程，如实提示。
"""
from __future__ import annotations

import logging
import re
import time
from typing import Callable, Optional

from ..config import AiModeConfig, load_settings
from ..llm.base import Message
from ..llm.errors import LLMError, LLMUnavailableError
from ..llm.factory import build_client
from ..projects import ProjectStore
from ..settings import ProjectSettingsStore, render_accuracy_text
from ..workspace import snapshot_workspace
from ..consent import get_card as _get_consent_card
from ..consent import spawn_submit_card as _spawn_submit_card
from .protocol import INTENT_MARK, TOOL_MARK, has_unclosed_marker, parse_turn
from .tools import _CONSENT_PENDING, ToolExecutor, tool_schema_text

logger = logging.getLogger("ai_mode.agent.runner")
__test__ = False

MAX_ROUNDS = 12

#: 授权卡自动续跑：等待用户点击同意/拒绝的超时与轮询间隔。
_CONSENT_TIMEOUT_SECONDS = 900
_CONSENT_POLL_INTERVAL = 0.5

#: 单轮决策回答的 token 上限（思考模型过长易被截断成悬空 TOOL 标记；见 has_unclosed_marker）。
#: 思考模型会把 reasoning 计入输出预算，3000 常被思考耗尽（正文只出开场白/complete 返回空），故提高到 8000。
AGENT_MAX_TOKENS = 8000

_STEP_REFERENCE = (
    "理解需求 → 规划作业 → 准备输入 → 搭建超算目录 → 提交前检查 → "
    "生成提交草稿 → 提交与监控 → 作业结束确认 → 结果与报告"
)

#: 协议标记字面量（与 protocol 一致；流式剥离用）。
_MARKERS = ("<<<INTENT>>>", "<<<TOOL>>>")


_ACT_CUES = ("开始执行", "开始操作", "实际操作", "开始处理", "执行操作", "执行命令", "立即执行", "直接调用", "现在调用", "尝试调用", "调用工具", "去调用", "开始调用", "先查看", "查看工作区", "查看一下", "去查看", "先检查", "检查一下", "去读取", "先获取", "动手", "推进", "现在开始", "开始推进", "加速推进", "实际执行", "真正执行", "去超算上", "去检查", "立即检查", "马上检查", "先确认", "再检查", "重新检查")


_ACT_NUDGE = "（提示：你上一条只是口头说要执行操作，但没有内嵌工具调用标记，系统没有任何真实工具被执行。若确实需要操作，请现在直接输出完整的 <<<TOOL>>>{\"name\": \"...\", \"args\": {...}}>>> 请求；若本就不需要工具，用纯正文直接说明即可，不要只口头承诺。）"


_EMPTY_NUDGE = "（提示：你刚才没有输出任何正文或工具调用，回复似乎中断/为空。请直接继续：要么调用工具推进，要么用纯正文把要说的说完。）"


_RECEIPT_STALL_NUDGE = (
    "（提示：你上一条只是「收到回执后继续/等回执再继续」这类等待性话术。"
    "但工具回执早已在你的上下文里（以「（工具回执 · …）」开头的消息），"
    "系统不会再有任何新回执，流程也不会自己继续。请现在二选一："
    "① 若还需要操作，就直接输出完整的 <<<TOOL>>>{\"name\": \"...\", \"args\": {...}}>>> 请求；"
    "② 若操作已完成，就立刻基于已有回执给出完整的最终结论。"
    "绝不要复述等待性话术，也不要再以「等回执」结尾。）"
)

#: 连续两轮都只有等待话术时的收尾说明（给用户看，代替刷屏的等待句）。
_STALL_STOP_NOTE = (
    "（AI 连续输出等待性回复，已停止等待。请补充具体指令，"
    "例如「查看 OUTCAR 结果」，我会直接基于已有回执作答。）"
)


def _receipt_stall(prose: str) -> bool:
    """命中「等收到工具回执再继续」这类中间态措辞（未真正收尾，需继续推进）。"""
    if not prose or "回执" not in prose:
        return False
    return any(link in prose
               for link in ("继续", "推进", "再", "接着", "稍后", "之后", "等待"))


#: 等待性话术判定词（与 _receipt_stall 一致）。
_WAIT_WORDS = ("继续", "推进", "等待", "稍后", "之后", "接着", "再")


def _strip_receipt_wait(text: str) -> str:
    """从正文里剔除「收到回执后继续/等回执」这类等待性句子，保留其余内容。

    按句号/问号/分号/换行切句，只删除同时含「回执」与等待词的句子；
    防止模型把等待话术复读进回答/落库（M49 防刷屏）。
    """
    if not text or "回执" not in text:
        return text
    kept: list[str] = []
    for sent in re.split(r"(?<=[。！？!?；;\n])", text):
        if "回执" in sent and any(w in sent for w in _WAIT_WORDS):
            continue
        kept.append(sent)
    return "".join(kept).strip()


def _promises_action(text):
    # 第一轮正文是否「口头承诺要执行操作」却没真正内嵌工具标记。
    if not text:
        return False
    return any(cue in text for cue in _ACT_CUES)


def offline_text(reason: str) -> str:
    return (
        "（当前 LLM 不可用，智能模式暂时无法智能对话）\n"
        "原因：%s\n"
        "请到「设置 → LLM」配置 base_url / api_key / model 后重试，"
        "配置后普通聊天会由真实模型回复。"
    ) % (reason or "未配置 LLM")


def _project_settings_text(store: ProjectStore, project_id: str) -> str:
    """项目计算任务设置 -> 给 AI 的高权重参考文本（未配置返回空串）。"""
    if not project_id:
        return ""
    try:
        accuracy = (ProjectSettingsStore(root=store.root).load(project_id)
                    .get("accuracy") or {})
    except Exception:  # noqa: BLE001
        return ""
    return render_accuracy_text(accuracy)


def _phase_guidance(phase: str) -> str:
    """按当前流程阶段注入针对性引导（仅「待确认」阶段需要额外行为约束）。"""
    if phase != "await_submit":
        return ""
    return (
        "\n\n【当前处在「待你确认提交」边界】\n"
        "提交草稿已生成、等待用户明确确认。用户在确认前可能要求补充/修改输入文件、"
        "提建议或临时干预（改参数、只提交部分作业、换思路等）。请直接响应用户："
        "读取工作区输入，并通过受限工具提出修改、重跑 precheck、重新生成 draft，"
        "然后再次停在"
        "「待确认」并请用户确认；没有用户要求不要擅自重启整条流程。"
        "用户若明说「确认提交」，系统只会生成当前草稿的一次性确认卡；取消则按原文处理。"
        "你无需也不得代执行 sbatch。"
        "流程到达「待确认」时系统会自动弹出确认卡片供用户点击，你只需简短说明已准备就绪即可。"
        "所有写入、复制、上传和脚本认领都必须等待各自一次性确认卡；不得生成或写入"
        "提交脚本，也不得把一次确认复用于另一操作。只读检查可直接继续。"
    )


def build_messages(store: ProjectStore, task: dict, history: list[dict],
                   content: str, *, limit: int = 40,
                   progress_note: str = "",
                   hpc_snapshot: str = "") -> list[Message]:
    """构造 agent 上下文：系统提示（方向参考+红线+工具+设置+双工作区快照）+ 历史 + 当前消息。"""
    goal = task.get("goal") or "（未填写）"
    # Prompt snapshots are metadata-only. File contents may enter the model
    # only through the explicit, policy-checked ws_read/hpc_read tools.
    _found, snapshot = snapshot_workspace(
        task.get("local_workspace") or "",
        max_preview_bytes=0, preview_total_cap=0,
    )
    system = (
        "你是 VASP-Doctor 智能模式的中枢 AI。用户正在一个计算任务里与你对话，"
        "你负责端到端主导科学计算：从看懂需求到规划、准备输入、提交前检查、"
        "提交与监控、结果报告，一条龙由你决策并真实操作。\n"
        f"任务目标：{goal}\n\n"
        "【执行方式 · 全程由你决策驱动】\n"
        "- 是否开启计算流程、何时规划、规划成什么、准备哪些输入、"
        "做不做提交前检查、什么阶段转给用户确认……一切都由你判断并自主调用工具完成，"
        "系统不会替你做决定，也不会在你调用工具前替你推进。\n"
        f"- 下面 8 步只是「方向参考」（防止你的思维乱跑浪费资源），不是固定顺序、"
        f"也不能照搬模板：{_STEP_REFERENCE}。你可以按实际需求增删改，调整顺序与深度。\n"
        "- 用户闲聊、问概念、谈设置时，直接认真回答即可；没有明确计算需求就不要启动"
        "计算流程，是否需要计算由你判断。\n"
        "- 你同时面对两个工作区，务必分清、分别用对应工具查看：\n"
        "  ① 本地工作区（local_workspace）：你本机上的目录，存放初始结构/输入文件，"
        "用 ws_list/ws_read 查看；输入只能通过受限草稿、确定性生成和用户确认流程准备；\n"
        "  ② 超算工作区（hpc_dir）：计算真正发生的地方（SSH 远端目录），"
        "查看超算上有哪些文件/目录一律用 hpc_list，看文件内容用 hpc_read——"
        "它们安全、有界、结构化。系统不向你提供任何本地或远程自由命令通道。"
        "把已登记文件传上去只能用 hpc_upload，并等待用户逐次确认；"
        "绝不要尝试用 scp/sftp/rsync 传文件——安全策略红线会直接拒绝。"
        "若收到「未连接超算」或「未设置超算工作区」的回执，说明 SSH 或 hpc_dir "
        "尚未配置：停止重试任何远端工具，转而引导用户完成配置。"
        "绝不把本地工作区的快照当成超算目录的内容，也不要为了看远端文件去翻本地工作区。\n"
        "  本地关键文件只读快照附在系统提示末尾（紧凑版，不铺满无关文件）。\n\n"
        "【工具调用方式】\n"
        "当你决定执行任何操作时，在回复正文里内嵌工具标记（一条消息可同时夹带多个，"
        "可跟在正文中）：\n"
        f"- 计算意图声明：{INTENT_MARK}{{\"intent\": \"compute\"}}"
        "（放在正文最前；普通闲聊可省略，系统会默认你是聊天）\n"
        f"- 工具请求：{TOOL_MARK}"
        "{{\"name\": \"工具名\", \"args\": {{...}}, \"reason\": \"为什么调它\"}}\n"
        "系统会真实执行该工具，并把执行回执作为下一条消息返回给你；你看到回执后系统会自动继续，"
        "你不需要（也不应该）把「等回执」「收到回执后继续」这类话写进回复。\n"
        "回执仅供你参考：最终回复里不要复述「工具回执 · xx」这类原文，只对工具结果做简洁总结。"
        "当你做完所有该做的操作，就用一段不含任何工具标记的纯正文总结结果并说明下一步"
        "（例如流程会自动弹出确认卡，你只需简短总结后等用户点卡），这段纯正文就是最终回复。\n\n"
        "可用工具：\n"
        + tool_schema_text()
        + "\n\n【红线（不可逾越）】\n"
        "1. 真实提交作业到超算必须由用户批准当前精确绑定的一次性确认卡，绝不代替用户执行 "
        "sbatch；你可以在确认前把规划/输入/预检/草稿全部准备好，并把流程停在「待确认」。\n"
        "2. 每步都基于真实工具回执如实汇报，绝不编造已完成的操作；操作失败如实说明。\n"
        "3. 不接触任何密钥/口令：SSH 密码、API key 等不会出现在你的上下文里，"
        "也不要向用户索要。\n"
        "4. 你不得生成、修改或写入提交脚本；只能列出已有候选，待用户核对路径与哈希后显式认领。\n"
    )
    system += (
        "\n【超算提交目录规范（重要 · 超算为主）】\n"
        "每个计算作业独占一个目录：在计算目录里用作业 key 作为子目录（如 relax/、"
        "static/），本地与超算两侧同名对应；计算与提交都发生在超算侧。\n"
        "提交脚本（*.sh）优先认超算作业目录里已有的唯一脚本——超算上文件已就绪时"
        "绝不要要求用户在本地重复准备；仅在本地存在时，必须先把该登记 artifact"
        "通过逐次确认的 hpc_upload 放入对应远端作业目录，再重新校验和认领。"
        "两边都没有时必须停止并请用户自行准备脚本。该作业的全部输入文件"
        "（INCAR/POSCAR/KPOINTS/POTCAR 等）也放同一"
        "目录。sbatch 以该作业目录为工作目录执行（`sbatch run.sh` 在该作业目录内发起），"
        "绝不在作业目录的上一级（工作区根）发起。多作业时各作业文件互不混放。"
        "复制用户输入只能引用已登记 artifact；INCAR/KPOINTS 只能走专用结构化流程；"
        "把文件传到超算用 hpc_upload 并等待确认。\n"
    )
    system += (
        "\n【作业依赖与目录嵌套（重要）】\n"
        "有先后依赖的作业（前序的输出是后继的输入，如 relax 的 CONTCAR 供 static、"
        "static 的 CHGCAR 供 dos/band）：规划（plan）时必须用 requires 声明依赖，"
        "依赖链作业的 key/目录用嵌套路径依次往下建（relax → relax/static → "
        "relax/static/dos），后继作业目录在前序作业目录里面，不要并列；互相独立的"
        "作业才并列。前序 completed 后系统只提示下游已解锁，必须重新经用户确认后"
        "才能提交；前序失败则后续阻断。\n"
        "作业目录名必须原样使用规划时声明的完整嵌套 key（如 relax/static/dos）；"
        "所有结构化文件操作的 job_key 不在规划内会被系统拒绝，"
        "绝不自创 relax/dos、dos 这类变体目录。\n"
        "依赖闸门产生的 waiting 与「暂不提交/等待前置」回执是计划内的正常状态："
        "收到后无需诊断「为什么它没跑」、无需反复检查，一句话说明在等哪个前序"
        "即可；前序完成后提示用户重新确认，不得自动补提。\n"
        "后台监控会持续自动推进直到全部结束。若用户明确表示终止/放弃/换思路"
        "（如「不做了」「算了」「换个方案」），立即调用 stop_monitor 终止流程，"
        "不要再补提或规划；已在超算运行的作业按回执里的 scancel 建议引导用户。\n"
        "产物交接：后继作业的提交脚本里必须包含复制上游产物的命令，例如 static 的 "
        "脚本里有 `cp ../CONTCAR POSCAR`，dos/band（ICHARG=10/11，需要读 CHGCAR）的 "
        "脚本里有 `cp ../CHGCAR .`——ICHARG>10 的作业缺上游 CHGCAR 必然秒败。"
        "你只能提醒用户检查其自有脚本，不得代为生成或修改。\n"
    )
    phase_note = _phase_guidance((task.get("flow") or {}).get("phase") or "")
    if phase_note:
        system += phase_note
    if progress_note:
        system += "\n\n【当前作业实时进度】\n" + progress_note
    settings_text = _project_settings_text(store, task.get("project_id"))
    if settings_text:
        system += "\n\n" + settings_text
    system += (
        "\n\n【任务本地工作区只读快照 · 紧凑版】\n"
        "每次消息实时从磁盘生成，只列关键计算文件与目录概览，"
        "无关/二进制/工程临时文件已折叠省略；这不限制你读取与操作工作区，"
        "需要全文/细节时用 ws_read 读取具体文件。\n"
        "回复规范：不要复述、不要整段粘贴这份快照或文件清单，引用文件时说相对路径；"
        "用户只关心简洁的最终回答。\n"
        + snapshot
    )
    if hpc_snapshot:
        system += (
            "\n\n【超算工作区只读快照 · 实时经 SSH 生成】\n"
            "这是超算工作区（hpc_dir）——计算发生地——的当前内容，与上面的本地工作区"
            "快照是两个不同的目录：涉及超算上已有什么文件/结果时，以本段为准，"
            "细节用 hpc_list/hpc_read 查看；本地文件请回看上一段，两者不要混活。\n"
            + hpc_snapshot
        )
    messages: list[Message] = [{"role": "system", "content": system}]
    for item in (history or [])[-limit:]:
        role = item.get("role")
        if role not in ("user", "assistant"):
            continue
        text = (item.get("content") or "").strip()
        messages.append({"role": role, "content": text[:2000]})
    messages.append({"role": "user", "content": (content or "")[:2000]})
    return messages


def _join_answer(parts: list[str]) -> str:
    parts = [p.strip() for p in parts if p and p.strip()]
    return "\n\n".join(parts).strip() or "（模型未返回有效内容）"


def _receipt_message(name: str, note: str) -> dict:
    """把工具执行回执包装为 LLM 可读的观察消息。

    OpenAI 兼容网关不接受缺 ``tool_call_id`` 的 role="tool"，本协议用文本标记
    （非原生 function calling），故回执以带工具名的 user 角色观察返回给 LLM。
    """
    return {"role": "user", "content": f"（工具回执 · {name}）\n{note}"}

def _strip_residual_markers(text: str) -> str:
    """剥离残留未闭合的协议标记（截断输出），只保留其前缀正文。"""
    text = (text or "").strip()
    for marker in _MARKERS:
        cut = text.split(marker, 1)[0]
        if cut != text:
            return cut.strip()
    return text


def _make_executor(store, project_id, task_id, *, cfg, orch_factory,
                   should_stop=None) -> ToolExecutor:
    return ToolExecutor(store=store, project_id=project_id, task_id=task_id,
                        cfg=cfg, orch_factory=orch_factory,
                        should_stop=should_stop)


def _progress_note(executor: ToolExecutor) -> str:
    """监控态下自动推进一次真实 squeue 进度，保证 LLM 看到的作业状态不落后。"""
    try:
        return executor.auto_pump() or ""
    except Exception:  # noqa: BLE001
        logger.warning("进度自动刷新失败", exc_info=True)
        return ""


def _flow_phase(store, project_id: str, task_id: str) -> str:
    """读取任务当前流程阶段（每次从存储实时取，避免陈旧快照）。"""
    task = store.get_task(project_id, task_id) or {}
    return (task.get("flow") or {}).get("phase") or ""


def _submit_card_events(store, project_id: str, task_id: str,
                        parts: list[str]):
    """流程刚进入 await_submit 时，自动产出「确认提交」卡 + 收尾事件。

    让用户从「看完摘要→打字确认」改成「看完摘要→点卡确认」，避免逐小步停顿。
    """
    card = _spawn_submit_card(store, project_id, task_id)
    yield {"type": "card", "card": card}
    yield {"type": "done", "answer": _join_answer(parts)}


def _wait_card_decision(store, project_id: str, task_id: str, card_id: str,
                        req, *, executor: ToolExecutor,
                        should_stop) -> tuple[str, str]:
    """Wait for one action decision and execute its immutable binding once.

    The original tool request is intentionally never replayed.
    """
    del req
    deadline = time.monotonic() + _CONSENT_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if should_stop is not None and should_stop():
            return ("stopped", "")
        action = _get_consent_card(store, project_id, task_id, card_id)
        if action is None:
            return ("failed", "确认操作不存在，未执行")
        state = action.get("state")
        if state == "approved":
            return ("executed", executor.execute_action(card_id))
        if state == "executed":
            return ("executed", str(action.get("result") or "操作已执行"))
        if state == "rejected":
            return ("denied", "已拒绝该操作授权")
        if state in {"expired", "failed", "unknown"}:
            return (state, str(action.get("result") or "操作未执行"))
        time.sleep(_CONSENT_POLL_INTERVAL)
    return ("timeout", "等待授权超时，已停止该操作")


def run_agent(store, project_id, task_id, content, *,
              llm_factory: Optional[Callable[[AiModeConfig], object]] = None,
              cfg: Optional[AiModeConfig] = None,
              orch_factory: Optional[Callable[[], object]] = None,
              max_rounds: int = MAX_ROUNDS,
              should_stop: Optional[Callable[[], bool]] = None,
              auto_resume: bool = True) -> str:
    """非流式：LLM 决策循环，返回最终回答文本（纯正文，不含工具回执）。"""
    cfg = cfg or load_settings()
    task = store.get_task(project_id, task_id)
    if task is None:
        raise ValueError(f"任务不存在: {task_id}")
    executor = _make_executor(store, project_id, task_id, cfg=cfg,
                              orch_factory=orch_factory,
                              should_stop=should_stop)
    progress = ""
    if (task.get("flow") or {}).get("phase") == "monitoring":
        progress = _progress_note(executor)
    history = store.list_messages(project_id, task_id)
    messages = build_messages(store, task, history, content,
                              progress_note=progress,
                              hpc_snapshot=executor.hpc_snapshot())
    try:
        llm = (llm_factory or (lambda c: build_client(c)))(cfg)
    except LLMError as exc:
        return offline_text(str(exc))
    try:
        return _decision_loop(executor, llm, messages, max_rounds=max_rounds,
                              should_stop=should_stop, auto_resume=auto_resume)
    except LLMUnavailableError as exc:
        return offline_text(str(exc))
    except LLMError as exc:
        return offline_text(str(exc))
    finally:
        _close_llm(llm)


def _decision_loop(executor: ToolExecutor, llm, messages: list[Message], *,
                   max_rounds: int, max_tokens: int = AGENT_MAX_TOKENS,
                   should_stop: Optional[Callable[[], bool]] = None,
                   auto_resume: bool = True) -> str:
    """决策循环主体：LLM 自决意图/工具；返回最终给用户的完整回答文本（纯正文，不含工具回执）。"""
    parts: list[str] = []
    tool_count = 0
    nudged = False
    trunc_nudged = False
    empty_nudged = False
    stall_nudged = False
    rounds = 0
    while rounds < max_rounds:
        rounds += 1
        if should_stop and should_stop():
            break
        result = llm.complete(list(messages), max_tokens=max_tokens)
        text = (result.text or "").strip()
        if not text:
            if not empty_nudged:
                empty_nudged = True
                messages.append({"role": "user", "content": _EMPTY_NUDGE})
                continue
            break
        turn = parse_turn(text)
        if has_unclosed_marker(text):
            if not turn.tools:
                if not trunc_nudged:
                    trunc_nudged = True
                    messages.append({
                        "role": "user",
                        "content": "（提示：你上一条输出的协议请求被截断/未闭合，未能执行。若你确实要调用工具，请完整重发该请求；若本就不需要工具，用纯正文直接说明即可。）",
                    })
                    continue
            turn.prose = _strip_residual_markers(turn.prose)
        prose = _strip_receipt_wait(turn.prose)
        if prose:
            parts.append(prose)
        if not turn.tools:
            if _receipt_stall(turn.prose):
                if not stall_nudged:
                    stall_nudged = True
                    messages.append({"role": "user", "content": _RECEIPT_STALL_NUDGE})
                    continue
                parts.append(_STALL_STOP_NOTE)
                break
            if (turn.intent == "compute" or _promises_action(turn.prose)) and tool_count == 0 and not nudged:
                nudged = True
                messages.append({
                    "role": "user",
                    "content": ("（提示：你声明了计算意图却没有调用任何工具。"
                                "如果确实需要计算，请直接调用 plan 等工具开始真实操作；"
                                "如果只是描述/提问而不需要计算，用纯正文说明即可。）"),
                })
                continue
            break
        # 先记录 AI 的工具调用回合，再执行工具并回填回执
        messages.append({"role": "assistant", "content": text[:3000]})
        abort_loop = False
        for req in turn.tools:
            note = executor.handle(req.name, req.args)
            tool_count += 1
            if note.startswith(_CONSENT_PENDING):
                if not auto_resume:
                    abort_loop = True
                    break
                card_id = note[len(_CONSENT_PENDING):]
                state, note2 = _wait_card_decision(
                    executor.store, executor.project_id, executor.task_id,
                    card_id, req, executor=executor,
                    should_stop=should_stop)
                if state == "executed":
                    messages.append(_receipt_message(req.name, note2))
                    continue
                abort_loop = True
                if state == "denied":
                    parts.append("（该操作被用户拒绝，已停止）")
                elif state == "timeout":
                    parts.append("（等待授权超时，已停止操作）")
                break
            messages.append(_receipt_message(req.name, note))
        if abort_loop:
            break
        if should_stop and should_stop():
            break
    return _join_answer(parts)


# ---------------- 流式（SSE） ----------------
def _trailing_marker_prefix(text: str) -> int:
    """返回 text 末尾能成为某个协议标记前缀的最大长度（含可能的不完整开头）。"""
    best = 0
    for marker in _MARKERS:
        max_len = min(len(marker), len(text))
        for size in range(1, max_len + 1):
            if text.endswith(marker[:size]):
                best = max(best, size)
    return best


def _find_marker(text: str, pos: int) -> int:
    idx = -1
    for marker in _MARKERS:
        found = text.find(marker, pos)
        if found != -1 and (idx == -1 or found < idx):
            idx = found
    return idx


def _mark_json_end(text: str, marker_idx: int):
    """从标记起点起找闭合 JSON 的结束位置（下一下标）；截断/未配平返回 None。"""
    end_m = text.find(">>>", marker_idx)
    if end_m == -1:
        return None
    i = text.find("{", end_m + 3)
    if i == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    j = i
    while j < len(text):
        ch = text[j]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return j + 1
        j += 1
    return None


def _partition_stream(buf: str) -> tuple[str, str]:
    """把流式缓冲区分为「可安全发布的纯文本」与「需继续持有的尾部」。"""
    parts: list[str] = []
    pos = 0
    while True:
        marker_idx = _find_marker(buf, pos)
        if marker_idx == -1:
            pending = _trailing_marker_prefix(buf[pos:])
            safe_len = len(buf) - pos - pending
            parts.append(buf[pos:pos + safe_len])
            return "".join(parts), buf[pos + safe_len:]
        parts.append(buf[pos:marker_idx])
        end = _mark_json_end(buf, marker_idx)
        if end is None:
            return "".join(parts), buf[marker_idx:]
        pos = end


class _StreamCleaner:
    """流式协议标记剥离器：add(chunk) 返回可发布的干净增量；flush() 收尾。"""

    def __init__(self) -> None:
        self._buf = ""

    def add(self, text: str) -> str:
        self._buf += (text or "")
        safe, keep = _partition_stream(self._buf)
        self._buf = keep or ""
        return safe

    def flush(self) -> str:
        safe, keep = _partition_stream(self._buf)
        self._buf = keep or ""
        # 尾部若是残留未闭合/被截断的协议标记，不再放给用户（避免裸标记泄漏）；由决策循环用 has_unclosed_marker 检测后提示模型重发完整请求。
        return safe


def _close_llm(llm) -> None:
    close = getattr(llm, "close", None)
    if close:
        try:
            close()
        except Exception:  # noqa: BLE001
            pass


def run_agent_stream(store, project_id, task_id, content, *,
                     llm_factory=None, cfg=None, orch_factory=None,
                     max_rounds: int = MAX_ROUNDS,
                     should_stop: Optional[Callable[[], bool]] = None,
                     auto_resume: bool = True):
    """流式：先流思考/正文（剥离协议标记），工具回执走 status 事件（不入最终回答）；支持 stopped。"""
    cfg = cfg or load_settings()
    task = store.get_task(project_id, task_id)
    if task is None:
        raise ValueError(f"任务不存在: {task_id}")
    executor = _make_executor(store, project_id, task_id, cfg=cfg,
                              orch_factory=orch_factory,
                              should_stop=should_stop)
    progress = ""
    if (task.get("flow") or {}).get("phase") == "monitoring":
        progress = _progress_note(executor)
    history = store.list_messages(project_id, task_id)
    messages = build_messages(store, task, history, content,
                              progress_note=progress,
                              hpc_snapshot=executor.hpc_snapshot())
    try:
        llm = (llm_factory or (lambda c: build_client(c)))(cfg)
    except LLMError as exc:
        msg = offline_text(str(exc))
        yield {"type": "error", "message": msg}
        yield {"type": "done", "answer": msg}
        return
    try:
        # 第一轮：流式思考 + 正文。正文累积到首轮完整文本，便于解析工具请求。
        thinker = _StreamCleaner()
        answerer = _StreamCleaner()
        first_raw: list[str] = []
        streamed_answers: list[str] = []
        for chunk in llm.stream(list(messages), max_tokens=AGENT_MAX_TOKENS):
            chunk = dict(chunk)
            kind = chunk.get("type", "answer")
            bit = chunk.get("text") or ""
            if should_stop and should_stop():
                yield {"type": "stopped", "answer": _join_answer(streamed_answers)}
                return
            if kind == "thinking":
                clean = thinker.add(bit)
                if clean:
                    yield {"type": "thinking", "text": clean}
            else:
                first_raw.append(bit)
                clean = answerer.add(bit)
                if clean:
                    streamed_answers.append(clean)
                    yield {"type": "answer", "text": clean}
        tail_thinking = thinker.flush()
        if tail_thinking:
            yield {"type": "thinking", "text": tail_thinking}
        first_text = "".join(first_raw).strip()
        first_turn = parse_turn(first_text)
        parts: list[str] = []
        _first_prose = _strip_receipt_wait(first_turn.prose)
        if _first_prose:
            parts.append(_first_prose)
        tool_count = 0
        if first_raw:
            messages.append({"role": "assistant", "content": first_text[:3000]})
        nudged = False
        if has_unclosed_marker(first_text) and first_text:
            stripped = _strip_residual_markers(first_turn.prose)
            if stripped != first_turn.prose:
                first_turn.prose = stripped
                if parts:
                    parts[-1] = stripped
            if not first_turn.tools:
                nudged = True
                messages.append({
                    "role": "user",
                    "content": "（提示：你上一条输出的协议请求被截断/未闭合，未能执行。若你确实要调用工具，请完整重发该请求；若本就不需要工具，用纯正文直接说明即可。）",
                })
        elif first_turn.intent == "compute" and not first_turn.tools and first_raw:
            nudged = True
            messages.append({
                "role": "user",
                "content": ("（提示：你声明了计算意图却没有调用任何工具。"
                            "如果确实需要计算，请直接调用 plan 等工具开始真实操作；"
                            "如果只是描述/提问而不需要计算，用纯正文说明即可。）"),
            })
        # 第一轮正文常只「口头承诺要执行操作」却没内嵌工具标记：补一次提示让模型下轮真正发出工具请求，
        # 避免只输出开场白就 done（用户视角即「回复中断、没有下文」）。
        if not first_turn.tools and not nudged and _promises_action(first_turn.prose):
            nudged = True
            messages.append({"role": "user", "content": _ACT_NUDGE})
        if not first_turn.tools and not nudged and _receipt_stall(first_turn.prose):
            nudged = True
            messages.append({"role": "user", "content": _RECEIPT_STALL_NUDGE})

        phase_before = _flow_phase(store, project_id, task_id)
        for req in first_turn.tools:
            note = executor.handle(req.name, req.args)
            tool_count += 1
            if note.startswith(_CONSENT_PENDING):
                card_id = note[len(_CONSENT_PENDING):]
                card = _get_consent_card(store, project_id, task_id, card_id)
                if card:
                    yield {"type": "card", "card": card}
                if not auto_resume:
                    yield {"type": "done", "answer": _join_answer(parts)}
                    return
                state, note2 = _wait_card_decision(
                    store, project_id, task_id, card_id, req,
                    executor=executor, should_stop=should_stop)
                if state == "executed":
                    note = note2
                    messages.append(_receipt_message(req.name, note))
                    yield {"type": "status", "text": note}
                    continue
                if state == "stopped":
                    yield {"type": "stopped", "answer": _join_answer(parts)}
                    return
                tail = ("该操作被用户拒绝，已停止" if state == "denied"
                        else "等待授权超时，已停止操作")
                parts.append(tail)
                yield {"type": "status", "text": tail}
                yield {"type": "done", "answer": _join_answer(parts)}
                return
            messages.append(_receipt_message(req.name, note))
            yield {"type": "status", "text": note}
        if (_flow_phase(store, project_id, task_id) == "await_submit"
                and phase_before != "await_submit"):
            yield from _submit_card_events(store, project_id, task_id, parts)
            return
        needs_loop = bool(first_turn.tools) or nudged or not first_raw
        if not needs_loop:
            yield {"type": "done", "answer": _join_answer(parts)}
            return
        # 后续轮：非流式决策循环
        rounds = 0
        trunc_nudged = False
        empty_nudged = False
        stall_nudged = False
        while rounds < max_rounds:
            rounds += 1
            if should_stop and should_stop():
                yield {"type": "stopped", "answer": _join_answer(parts)}
                return
            result = llm.complete(list(messages), max_tokens=AGENT_MAX_TOKENS)
            text = (result.text or "").strip()
            if not text:
                if not empty_nudged:
                    empty_nudged = True
                    messages.append({"role": "user", "content": _EMPTY_NUDGE})
                    continue
                break
            turn = parse_turn(text)
            if has_unclosed_marker(text):
                if not turn.tools:
                    if not trunc_nudged:
                        trunc_nudged = True
                        messages.append({
                            "role": "user",
                            "content": "（提示：你上一条输出的协议请求被截断/未闭合，未能执行。若你确实要调用工具，请完整重发该请求；若本就不需要工具，用纯正文直接说明即可。）",
                        })
                        continue
                turn.prose = _strip_residual_markers(turn.prose)
            prose = _strip_receipt_wait(turn.prose)
            if prose:
                parts.append(prose)
                yield {"type": "answer", "text": prose}
            if not turn.tools:
                if _receipt_stall(turn.prose):
                    if not stall_nudged:
                        stall_nudged = True
                        messages.append({"role": "user", "content": _RECEIPT_STALL_NUDGE})
                        continue
                    parts.append(_STALL_STOP_NOTE)
                    yield {"type": "answer", "text": _STALL_STOP_NOTE}
                    break
                if (turn.intent == "compute" or _promises_action(turn.prose)) and tool_count == 0 and not nudged:
                    nudged = True
                    messages.append({
                        "role": "user",
                        "content": ("（提示：你声明了计算意图却没有调用任何工具。"
                                    "如果确实需要计算，请直接调用 plan 等工具开始真实"
                                    "操作；如果只是描述/提问，用纯正文说明即可。）"),
                    })
                    continue
                break
            messages.append({"role": "assistant", "content": text[:3000]})
            phase_before = _flow_phase(store, project_id, task_id)
            for req in turn.tools:
                note = executor.handle(req.name, req.args)
                tool_count += 1
                if note.startswith(_CONSENT_PENDING):
                    card_id = note[len(_CONSENT_PENDING):]
                    card = _get_consent_card(store, project_id, task_id,
                                             card_id)
                    if card:
                        yield {"type": "card", "card": card}
                    if not auto_resume:
                        yield {"type": "done", "answer": _join_answer(parts)}
                        return
                    state, note2 = _wait_card_decision(
                        store, project_id, task_id, card_id, req,
                        executor=executor, should_stop=should_stop)
                    if state == "executed":
                        note = note2
                        messages.append(_receipt_message(req.name, note))
                        yield {"type": "status", "text": note}
                        continue
                    if state == "stopped":
                        yield {"type": "stopped", "answer": _join_answer(parts)}
                        return
                    tail = ("该操作被用户拒绝，已停止" if state == "denied"
                            else "等待授权超时，已停止操作")
                    parts.append(tail)
                    yield {"type": "status", "text": tail}
                    yield {"type": "done", "answer": _join_answer(parts)}
                    return
                messages.append(_receipt_message(req.name, note))
                yield {"type": "status", "text": note}
            if (_flow_phase(store, project_id, task_id) == "await_submit"
                    and phase_before != "await_submit"):
                yield from _submit_card_events(store, project_id, task_id, parts)
                return
        yield {"type": "done", "answer": _join_answer(parts)}
    except LLMUnavailableError as exc:
        msg = offline_text(str(exc))
        yield {"type": "error", "message": msg}
        yield {"type": "done", "answer": msg}
    except LLMError as exc:
        msg = offline_text(str(exc))
        yield {"type": "error", "message": msg}
        yield {"type": "done", "answer": msg}
    finally:
        _close_llm(llm)
