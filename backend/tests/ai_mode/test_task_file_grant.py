"""「选定工作区＝授权」第一层：建任务/改任务时只写本地记录，不连远端。"""
from types import SimpleNamespace

from backend.toolbox.api import (hydrate_task_file_grant, record_task_file_grant,
                                  schedule_task_file_grant_hydration)


class _Store:
    def __init__(self):
        self.flow = {}

    def get_task(self, project_id, task_id):
        return {"id": task_id, "flow": dict(self.flow)}

    def update_task(self, project_id, task_id, flow=None):
        self.flow = dict(flow or {})


class _Files:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def ensure_task_file_grant(self, project_id, task_id):
        self.calls.append((project_id, task_id))
        if self.fail:
            raise RuntimeError("ssh down")
        return {"root_registered": True, "scopes_created": 2}


def _svc(files=None):
    return SimpleNamespace(files=files, store=_Store())


def test_task_creation_records_grant_without_touching_remote():
    """建任务只写本地授权记录：远端登记延后到后台，绝不拖慢响应。"""
    files, svc = _Files(), _svc()
    svc.files = files
    record_task_file_grant(svc, "prj", {"id": "tsk"}, {"hpc_workspace": "/remote/work"})
    grant = svc.store.flow["file_grant"]
    assert grant["granted_by"] == "task_workspace_choice"
    assert grant["hpc_workspace"] == "/remote/work"
    assert grant["operations"] == ["copy", "write_text", "mkdir"]
    assert grant["active"] is True
    assert grant["pending_root"] is True
    assert files.calls == []  # 记录阶段不产生任何远端调用


def test_clearing_the_workspace_records_an_opt_out():
    """把超算工作区清空＝显式收回授权，之后不再自动登记根或派生范围。"""
    svc = _svc(_Files())
    record_task_file_grant(svc, "prj", {"id": "tsk"}, {"hpc_workspace": ""})
    grant = svc.store.flow["file_grant"]
    assert grant["active"] is False and grant["pending_root"] is False


def test_grant_is_untouched_when_the_task_has_no_remote_workspace():
    svc = _svc(_Files())
    record_task_file_grant(svc, "prj", {"id": "tsk"}, {"title": "只有标题"})
    assert svc.store.flow == {}


def test_hydration_is_best_effort_and_never_raises():
    """超算连不上（或没有文件层）时安静跳过，不改变任务本身。"""
    svc = _svc(_Files(fail=True))
    assert hydrate_task_file_grant(svc, "prj", "tsk") == {}
    assert hydrate_task_file_grant(SimpleNamespace(files=None, store=_Store()),
                                   "prj", "tsk") == {}


def test_scheduled_hydration_runs_in_the_background():
    files, svc = _Files(), _svc()
    svc.files = files
    schedule_task_file_grant_hydration(svc, "prj", "tsk")
    for _ in range(200):
        if files.calls:
            break
        import time
        time.sleep(0.01)
    assert files.calls == [("prj", "tsk")]
