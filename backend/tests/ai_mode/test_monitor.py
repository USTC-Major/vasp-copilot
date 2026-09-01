"""M55 后台监控线程测试：定时推进、变化才落消息、异常丢弃连接缓存。"""

from __future__ import annotations

import threading

import pytest

from ai_mode.monitor import MonitorLoop, clamp_interval
from ai_mode.orchestrator import _task_lock
from ai_mode.projects import ProjectStore


def _mk_monitoring_task(store: ProjectStore) -> tuple[str, str]:
    prj = store.create_project("监控项目")
    tk = store.create_task(prj["id"], goal="relax+static+dos")
    store.update_task(prj["id"], tk["id"], flow={
        "phase": "monitoring",
        "goal": "relax+static+dos",
        "plan": {"strategy": "链式", "jobs": [
            {"key": "relax", "label": "结构优化", "kind": "relax",
             "requires": [], "status": "running", "slurm_id": 4201,
             "description": ""},
            {"key": "relax/static", "label": "静态", "kind": "static",
             "requires": ["relax"], "status": "waiting", "slurm_id": None,
             "description": ""},
        ]},
        "local_dir": "", "hpc_dir": "/home/u/vasp",
    })
    return prj["id"], tk["id"]


class FakeOrch:
    """monitor 一次：前序完成 → 补提 static 并把状态推进一格。"""

    def __init__(self):
        self.calls = 0

    def monitor(self, store, project_id, task_id, flow):
        self.calls += 1
        task = store.get_task(project_id, task_id) or {}
        flow = dict(task.get("flow") or {})
        jobs = (flow.get("plan") or {}).get("jobs") or []
        if self.calls == 1:
            jobs[0]["status"] = "completed"
            jobs[1]["status"] = "running"
        else:
            jobs[1]["status"] = "completed"
        flow["plan"]["jobs"] = jobs
        if all(j["status"] in ("completed", "failed", "blocked", "canceled")
               for j in jobs):
            flow["phase"] = "done"
        store.update_task(project_id, task_id, flow=flow)
        return "relax：completed；relax/static：自动补提"


class BoomOrch:
    def monitor(self, store, project_id, task_id, flow):
        raise ConnectionError("ssh down")


def test_clamp_interval_bounds():
    assert clamp_interval(0) == 10          # 下限保护
    assert clamp_interval(999999) == 3600   # 上限保护
    assert clamp_interval(None) == 60       # 缺省
    assert clamp_interval("abc") == 60      # 非法
    assert clamp_interval(45) == 45         # 合法值原样


def test_tick_appends_message_only_on_change(tmp_path):
    """状态变化轮落一条 [自动监控] 消息；稳态轮不再追加。"""
    store = ProjectStore(tmp_path)
    pid, tid = _mk_monitoring_task(store)
    fake = FakeOrch()
    loop = MonitorLoop(orch_factory=lambda p, t, cfg: fake)

    assert loop.tick(store) == 1
    msgs = store.list_messages(pid, tid)
    assert len(msgs) == 1
    assert msgs[0]["role"] == "assistant"
    assert msgs[0]["content"].startswith("[自动监控]")
    task = store.get_task(pid, tid)
    assert task["flow"]["plan"]["jobs"][0]["status"] == "completed"

    # 第二轮：static completed → phase done，又一条消息
    assert loop.tick(store) == 1
    msgs = store.list_messages(pid, tid)
    assert len(msgs) == 2
    assert store.get_task(pid, tid)["flow"]["phase"] == "done"

    # 第三轮：已终态，monitoring_tasks 扫不到（phase=done），无新增
    assert loop.tick(store) == 0
    assert len(store.list_messages(pid, tid)) == 2


def test_tick_skips_non_monitoring_tasks(tmp_path):
    """running/blocked 等阶段不进监控扫描。"""
    store = ProjectStore(tmp_path)
    prj = store.create_project("闲聊项目")
    tk = store.create_task(prj["id"], goal="随便聊聊")
    store.update_task(prj["id"], tk["id"], flow={"phase": "running"})
    called = []

    def factory(p, t, cfg):
        called.append((p, t))
        raise AssertionError("不应构造 Orchestrator")

    loop = MonitorLoop(orch_factory=factory)
    assert loop.tick(store) == 0
    assert called == []


def test_tick_skips_deleted_task(tmp_path, monkeypatch):
    """M56：扫描后发现任务已删除（竞态）——不炸、不建连接、清缓存。"""
    store = ProjectStore(tmp_path)
    pid, _tid = _mk_monitoring_task(store)

    def ghost_tasks():
        return [(pid, "tsk_ghost")]

    monkeypatch.setattr(store, "monitoring_tasks", ghost_tasks)
    built = []

    def factory(p, t, cfg):
        built.append((p, t))
        return FakeOrch()

    loop = MonitorLoop(orch_factory=factory)
    assert loop.tick(store) == 1   # 扫到 1 个但跳过
    assert built == []             # 没有构造 Orchestrator
    assert loop._orcs == {}


def test_tick_orch_failure_drops_cache(tmp_path):
    """monitor 异常不崩轮次，且丢弃该任务的连接缓存供下轮重建。"""
    store = ProjectStore(tmp_path)
    pid, tid = _mk_monitoring_task(store)
    loop = MonitorLoop(orch_factory=lambda p, t, cfg: BoomOrch())

    assert loop.tick(store) == 1  # 不抛异常
    assert store.get_task(pid, tid)["flow"]["plan"]["jobs"][0]["status"] == "running"
    assert loop._orcs == {}       # 缓存已丢弃


def test_task_lock_is_per_task():
    """同一任务返回同一把锁，不同任务互不影响。"""
    l1 = _task_lock("p", "t1")
    assert _task_lock("p", "t1") is l1
    assert _task_lock("p", "t2") is not l1
    # 锁可用（非重入死锁）
    with l1:
        pass
    acquired = threading.Event()

    def grab():
        with l1:
            acquired.set()

    t = threading.Thread(target=grab, daemon=True)
    t.start()
    assert acquired.wait(2)
