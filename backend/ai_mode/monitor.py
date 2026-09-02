"""后台监控线程（M55）。

提交完成后 AI 不再等用户「下一条消息」才推进：本线程按
``poll_interval_seconds``（全局设置，默认 60s，下限 10s）周期扫描所有
``phase == "monitoring"`` 的任务，调用 Orchestrator.monitor（= 依赖闸门
补提 + squeue 实况 + 终态收尾 + 报告生成），直到作业全部终态（done）。

设计要点：
- 状态只在发生变化（作业状态/阶段变化）时才向聊天落一条 assistant 消息，
  避免稳态轮询刷屏（M49 教训）。
- 与用户消息触发的推进共用 orchestrator 的 per-task 锁，不并发双提交。
- SSH 连接按任务+账号缓存复用；连接异常时丢弃缓存下轮重建。
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Optional

from .config import AiModeConfig, load_settings
from .projects import ProjectStore

logger = logging.getLogger("ai_mode.monitor")

#: 轮询间隔下限（秒）：防止误设过小打爆超算 SSH；上限一小时。
MIN_INTERVAL_SECONDS = 10
MAX_INTERVAL_SECONDS = 3600


def clamp_interval(seconds: Any) -> int:
    """把配置的 poll_interval_seconds 收敛到合法区间。"""
    try:
        value = int(seconds)
    except (TypeError, ValueError):
        return 60
    return max(MIN_INTERVAL_SECONDS, min(MAX_INTERVAL_SECONDS, value))


def _flow_signature(flow: dict) -> tuple:
    """作业状态签名：状态集合或阶段变化视为「有新进展」。"""
    flow = flow or {}
    jobs = (flow.get("plan") or {}).get("jobs") or []
    sig = tuple(
        (str(j.get("key") or ""), str(j.get("status") or ""))
        for j in jobs if isinstance(j, dict)
    )
    return (str(flow.get("phase") or ""), sig)


class MonitorLoop:
    """单实例后台监控循环（ai_mode 服务进程内一个线程）。"""

    def __init__(self, *,
                 orch_factory: Optional[Callable[[str, str, AiModeConfig], Any]] = None,
                 settings_loader: Callable[[], AiModeConfig] = load_settings):
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._start_guard = threading.Lock()
        self._orch_lock = threading.Lock()
        self._orcs: dict[tuple[str, str, str, str, int], Any] = {}
        self._orch_factory = orch_factory or self._default_orch_factory
        self._load_settings = settings_loader

    @staticmethod
    def _default_orch_factory(project_id: str, task_id: str,
                              cfg: AiModeConfig) -> Any:
        from .orchestrator import Orchestrator
        return Orchestrator.from_settings(cfg)

    # ---------------- 生命周期 ----------------
    def start(self, store: ProjectStore) -> None:
        """启动监控线程（幂等；已启动则忽略）。"""
        with self._start_guard:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run, args=(store,), daemon=True,
                name="ai-mode-monitor")
            self._thread.start()
            logger.info("后台监控线程已启动")

    def stop(self) -> None:
        self._stop.set()

    # ---------------- 主循环 ----------------
    def _run(self, store: ProjectStore) -> None:
        while not self._stop.is_set():
            interval = clamp_interval(self._load_settings().poll_interval_seconds)
            if self._stop.wait(interval):
                break
            try:
                self.tick(store)
            except Exception:  # noqa: BLE001
                logger.exception("监控轮次失败（下轮重试）")

    def _orch_for(self, project_id: str, task_id: str,
                  cfg: AiModeConfig) -> Any:
        """按任务+SSH 账号缓存 Orchestrator，复用 SSH 连接。"""
        key = (project_id, task_id, cfg.ssh_host or "", cfg.ssh_username or "",
               int(cfg.ssh_port or 22))
        with self._orch_lock:
            orch = self._orcs.get(key)
            if orch is None:
                orch = self._orch_factory(project_id, task_id, cfg)
                self._orcs[key] = orch
            return orch

    def _drop_orch(self, project_id: str, task_id: str,
                   cfg: AiModeConfig) -> None:
        key = (project_id, task_id, cfg.ssh_host or "", cfg.ssh_username or "",
               int(cfg.ssh_port or 22))
        with self._orch_lock:
            self._orcs.pop(key, None)

    def _drop_all_orch(self, project_id: str, task_id: str) -> None:
        """任务已删除/终止：丢弃它名下所有连接缓存。"""
        with self._orch_lock:
            for key in [k for k in self._orcs
                        if k[0] == project_id and k[1] == task_id]:
                self._orcs.pop(key, None)

    def tick(self, store: ProjectStore) -> int:
        """推进一轮：对每个 monitoring 任务调用 monitor；返回处理任务数。"""
        tasks = store.monitoring_tasks()
        for project_id, task_id in tasks:
            cfg = self._load_settings()
            try:
                task = store.get_task(project_id, task_id)
                if task is None:
                    # M56：任务已被删除——清理连接缓存，自然退出监控
                    logger.info("任务 %s/%s 已不存在，停止监控它",
                                project_id, task_id)
                    self._drop_all_orch(project_id, task_id)
                    continue
                before = _flow_signature(task.get("flow") or {})
                orch = self._orch_for(project_id, task_id, cfg)
                text = (orch.monitor(store, project_id, task_id, None) or "").strip()
                after_task = store.get_task(project_id, task_id) or {}
                after = _flow_signature(after_task.get("flow") or {})
                if after != before and text:
                    store.append_message(
                        project_id, task_id, "assistant",
                        f"[自动监控] {text}")
            except Exception as exc:  # noqa: BLE001
                logger.warning("任务 %s/%s 监控失败: %s",
                               project_id, task_id, type(exc).__name__)
                # 连接类故障：丢弃缓存的 Orchestrator，下轮重建
                self._drop_orch(project_id, task_id, cfg)
                logger.debug("已丢弃 %s/%s 的监控连接缓存", project_id, task_id)
        return len(tasks)


#: 进程内单例（server lifespan 启停）。
monitor_loop = MonitorLoop()
