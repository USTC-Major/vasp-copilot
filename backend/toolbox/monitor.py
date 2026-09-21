"""Owner-managed periodic monitor. Writes execution events independently of chat."""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Optional

from .config import AiModeConfig, load_settings
from .projects import ProjectStore
from .consent import task_lock
from datetime import datetime, timezone

logger = logging.getLogger("toolbox.monitor")

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
    """单实例后台监控循环（Toolbox owner进程内一个线程）。"""

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
                name="toolbox-monitor")
            self._thread.start()
            logger.info("后台监控线程已启动")

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join()
        with self._orch_lock:
            for orch in self._orcs.values():
                hpc = getattr(orch, 'hpc', None)
                if hpc is not None and hasattr(hpc, 'close'):
                    try:
                        hpc.close()
                    except Exception:
                        pass
            self._orcs.clear()

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
               int(cfg.ssh_port or 22), cfg.scheduler_backend,
               cfg.ssh_identity_file, cfg.ssh_known_hosts_path)
        with self._orch_lock:
            orch = self._orcs.get(key)
            if orch is None:
                orch = self._orch_factory(project_id, task_id, cfg)
                self._orcs[key] = orch
            return orch

    def _drop_orch(self, project_id: str, task_id: str,
                   cfg: AiModeConfig) -> None:
        key = (project_id, task_id, cfg.ssh_host or "", cfg.ssh_username or "",
               int(cfg.ssh_port or 22), cfg.scheduler_backend,
               cfg.ssh_identity_file, cfg.ssh_known_hosts_path)
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
                with task_lock(project_id, task_id):
                    task = store.get_task(project_id, task_id)
                    if task is None or (task.get('flow') or {}).get('phase') != 'monitoring':
                        continue
                    before = _flow_signature(task.get("flow") or {})
                    orch = self._orch_for(project_id, task_id, cfg)
                    text = (orch.monitor(store, project_id, task_id, None) or "").strip()
                    after_task = store.get_task(project_id, task_id) or {}
                    flow = after_task.get('flow') or {}
                    after = _flow_signature(flow)
                    now = datetime.now(timezone.utc).isoformat()
                    state = flow.setdefault('monitor', {})
                    state.update(last_attempt_at=now, interval_seconds=clamp_interval(cfg.poll_interval_seconds), remote_cancelled=False)
                    if flow.get('monitor_error') or getattr(orch, 'hpc', None) is None:
                        previous_error = state.get('last_error')
                        error = flow.get('monitor_error') or 'HPC backend unavailable'
                        if isinstance(error, dict):
                            error = error.get('message') or str(error)
                        state.update(state='error', last_error=str(error))
                        if state['last_error'] != previous_error:
                            store.append_event(project_id, task_id, 'monitor.error', state['last_error'])
                    else:
                        state.update(state='monitoring' if flow.get('phase') == 'monitoring' else 'idle',
                                     last_success_at=now, last_error='')
                    store.update_task(project_id, task_id, flow=flow)
                    if after != before and text:
                        store.append_event(project_id, task_id, 'monitor', text)
            except Exception as exc:  # noqa: BLE001
                logger.warning("任务 %s/%s 监控失败: %s",
                               project_id, task_id, type(exc).__name__)
                # 连接类故障：丢弃缓存的 Orchestrator，下轮重建
                self._drop_orch(project_id, task_id, cfg)
                with task_lock(project_id, task_id):
                    task = store.get_task(project_id, task_id)
                    if task:
                        flow = task.get('flow') or {}
                        flow.setdefault('monitor', {}).update(state='error',
                            last_attempt_at=datetime.now(timezone.utc).isoformat(), last_error=type(exc).__name__)
                        store.update_task(project_id, task_id, flow=flow)
                        store.append_event(project_id, task_id, 'monitor.error', type(exc).__name__)
                logger.debug("已丢弃 %s/%s 的监控连接缓存", project_id, task_id)
        return len(tasks)


# Monitor instances are owned by ExecutionService, never by import side effects.
