"""事件驱动的自动唤醒：让 AI 在后台事件发生时自己继续干活（不必等用户说话）。

产品目标（用户明确要求）：提交需求后，用户除了点确认卡之外不需要任何操作——
不催 AI、不在超算上手工敲命令、中途不再输入内容。

本模块是一个后台轮询器：读 Toolbox 的事件流与任务状态，发现"有意义的变化"就自动跑一轮
agent（不写入伪造的用户消息，只把结果作为 assistant 消息落进对话），于是用户看到的是一条条
主动汇报与推进，而不是停在那里等他开口。

保守策略（避免烧 token / 重复打扰）：
- 只在这些事件唤醒：作业进入终态、出现失败的卡/动作、整条流程到达终态（出报告）、
  有新弹出的待确认卡；
- 正在生成中不唤醒；终态（done/blocked/canceled）只唤醒一次；同一任务有冷却时间；
  每个任务有唤醒次数上限（超过就不再自动唤醒，避免死循环刷屏）。
"""
from __future__ import annotations

import json
import logging
import threading
import time

from .agent.runner import run_agent
from .projects import ProjectStore
from .streaming import generation_status

logger = logging.getLogger("ai_mode.auto_wake")

#: 轮询间隔（秒）与冷却/上限。
INTERVAL_SECONDS = 15
COOLDOWN_SECONDS = 45
MAX_WAKES_PER_TASK = 12

_TERMINAL_PHASES = {"done", "blocked", "canceled"}


def _task_events(store, client, project_id: str, task_id: str, after: int) -> list[dict]:
    path = client.task_path(project_id, task_id) + f"/events?after={int(after)}"
    payload = client.request("GET", path)
    return list((payload or {}).get("events") or [])


def _detail(store, client, project_id: str, task_id: str) -> dict:
    return client.request("GET", client.task_path(project_id, task_id) + "/detail") or {}


def wake_prompt(detail: dict, events: list[dict], *, phase: str) -> str | None:
    """把"发生了什么"翻译成一句给 AI 的系统事件；没什么值得唤醒的就返回 None。"""
    flow = detail.get("flow") or {}
    jobs = flow.get("jobs") or []
    lines: list[str] = []
    for job in jobs:
        status = str(job.get("status") or "")
        if status in {"completed", "failed", "not_converged", "unknown"}:
            lines.append(f"{job.get('key')}={status}")
    failures = [event for event in events
                if str(event.get("kind") or "") in {"consent.failed", "monitor.error"}]
    pending = [card for card in (detail.get("consents") or [])
               if card.get("kind") == "submit"]
    if not lines and not failures and not pending and phase not in _TERMINAL_PHASES:
        return None
    facts = []
    if lines:
        facts.append("作业状态：" + "、".join(lines))
    if pending:
        facts.append("有一张待确认的提交卡在等用户点确认")
    if failures:
        facts.append("刚出现失败：" + "；".join(
            str(event.get("message") or "")[:120] for event in failures[:3]))
    if phase in _TERMINAL_PHASES:
        facts.append(f"整条流程已到达终态（phase={phase}）")
    return (
        "（系统事件，非用户输入）" + "；".join(facts) + "。\n"
        "请按这条事件自己判断下一步并直接执行到底：能自动重试或换路的（上传身份变化、卡片作废、"
        "缺上游产物等）就自己重做，不要停下来问用户；仍缺 POTCAR 就先 generate_potcar、缺脚本就先"
        "deploy_submit_script、缺上游产物等系统自动带入。\n"
        "然后用 2–5 句中文告诉用户：现在到哪一步、有没有需要他确认的卡（只点确认即可）、"
        "以及最终结果在哪里。不要重复播报监控状态，不要让他手工操作超算或再输入内容。"
    )


class AutoWakeLoop:
    """后台线程：发现有意义的事件就替用户"催"AI 继续（AI 的输出落成 assistant 消息）。"""

    def __init__(self, *, interval: int = INTERVAL_SECONDS):
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._guard = threading.Lock()
        self._waking: set[tuple[str, str]] = set()

    def start(self, store: ProjectStore) -> None:
        with self._guard:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, args=(store,),
                                            name="ai-auto-wake", daemon=True)
            self._thread.start()
            logger.info("事件驱动自动唤醒线程已启动")

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=5)

    def _run(self, store: ProjectStore) -> None:
        while not self._stop.is_set():
            if self._stop.wait(self.interval):
                break
            try:
                self.tick(store)
            except Exception:  # noqa: BLE001 - 单轮失败不影响下一轮
                logger.exception("自动唤醒轮次失败（下轮重试）")

    def tick(self, store: ProjectStore) -> list[str]:
        """扫描所有任务，必要时唤醒 AI；返回被唤醒的任务 id。"""
        client = store.client
        woken: list[str] = []
        try:
            projects = (client.request("GET", "/projects") or {}).get("projects") or []
        except Exception:  # noqa: BLE001
            return woken
        for project in projects:
            project_id = str(project.get("id") or "")
            try:
                tasks = (client.request(
                    "GET", f"/projects/{project_id}/tasks") or {}).get("tasks") or []
            except Exception:  # noqa: BLE001
                continue
            for task in tasks:
                task_id = str(task.get("id") or "")
                if not project_id or not task_id:
                    continue
                state = store.generation_metadata(project_id, task_id).get("auto_wake") or {}
                if state.get("stopped"):
                    continue          # 用户已停止该任务：不再自动唤醒它
                if int(state.get("count") or 0) >= MAX_WAKES_PER_TASK:
                    continue
                if time.time() - float(state.get("at") or 0) < COOLDOWN_SECONDS:
                    continue
                if generation_status(store, project_id, task_id).get("running"):
                    continue
                with self._guard:
                    if (project_id, task_id) in self._waking:
                        continue
                    self._waking.add((project_id, task_id))
                try:
                    if self._maybe_wake(store, project_id, task_id, state):
                        woken.append(task_id)
                finally:
                    with self._guard:
                        self._waking.discard((project_id, task_id))
        return woken

    def _maybe_wake(self, store: ProjectStore, project_id: str, task_id: str,
                    state: dict) -> bool:
        detail = _detail(store, store.client, project_id, task_id)
        flow = detail.get("flow") or {}
        phase = str(flow.get("phase") or "")
        if not flow.get("jobs"):
            return False
        # 用户停止（stop_monitor）后，任务里会出现 canceled 作业或终态阶段：
        # 这种情况只提醒"已停止"一次，然后彻底不再自动唤醒——否则 AI 会把流程重新拉起来，
        # 表现为"说了停止还在不断弹卡"。
        stopped = (phase in _TERMINAL_PHASES
                   or any(str(job.get("status") or "") == "canceled"
                          for job in flow.get("jobs") or []))
        if stopped:
            if state.get("stopped"):
                return False
            store.update_generation(project_id, task_id, {
                "auto_wake": {**state, "stopped": True, "last_phase": phase}})
            return False
        last_id = int(state.get("last_event_id") or 0)
        events = _task_events(store, store.client, project_id, task_id, last_id)
        newest = max([int(event.get("id") or 0) for event in events] or [last_id])
        # 同一阶段只唤醒一次（避免终态后反复唤醒刷屏）
        if phase in _TERMINAL_PHASES and state.get("last_phase") == phase:
            store.update_generation(project_id, task_id, {
                "auto_wake": {**state, "last_event_id": newest, "last_phase": phase}})
            return False
        prompt = wake_prompt(detail, events, phase=phase)
        if prompt is None:
            store.update_generation(project_id, task_id, {
                "auto_wake": {**state, "last_event_id": newest, "last_phase": phase}})
            return False
        try:
            answer = run_agent(store, project_id, task_id, prompt)
        except Exception as exc:  # noqa: BLE001 - 唤醒失败不影响后台
            logger.warning("自动唤醒 %s/%s 失败: %s", project_id, task_id,
                           type(exc).__name__)
            return False
        if answer:
            store.append_message(project_id, task_id, role="assistant",
                                 content=str(answer))
        store.update_generation(project_id, task_id, {
            "auto_wake": {"last_event_id": newest, "last_phase": phase,
                          "at": time.time(),
                          "count": int(state.get("count") or 0) + 1}})
        return True


def task_state_snapshot(store: ProjectStore, project_id: str, task_id: str) -> str:
    """调试/测试用：把唤醒判定所需的状态压成一行。"""
    detail = _detail(store, store.client, project_id, task_id)
    flow = detail.get("flow") or {}
    return json.dumps({"phase": flow.get("phase"),
                       "jobs": [(j.get("key"), j.get("status"))
                                for j in flow.get("jobs") or []]},
                      ensure_ascii=False)
