"""「选定工作区即授权」：建任务时登记文件根并记录任务级授权（best-effort）。"""
from types import SimpleNamespace

from backend.toolbox.api import grant_task_file_transfer


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

    def set_roots(self, project_id, task_id, payload):
        if self.fail:
            raise RuntimeError("ssh down")
        self.calls.append(payload)


def test_task_creation_registers_root_and_grant():
    files, store = _Files(), _Store()
    svc = SimpleNamespace(files=files, store=store)
    grant_task_file_transfer(svc, "prj", {"id": "tsk"}, {"hpc_workspace": "/remote/work"})
    assert files.calls == [{"expected_version": 0, "roots": [{"path": "/remote/work"}]}]
    grant = store.flow["file_grant"]
    assert grant["granted_by"] == "task_workspace_choice"
    assert grant["hpc_workspace"] == "/remote/work"
    assert grant["operations"] == ["copy", "write_text", "mkdir"]
    assert grant["active"] is True


def test_grant_is_skipped_without_remote_dir_or_file_layer():
    files, store = _Files(), _Store()
    svc = SimpleNamespace(files=files, store=store)
    grant_task_file_transfer(svc, "prj", {"id": "tsk"}, {})
    grant_task_file_transfer(SimpleNamespace(files=None, store=store),
                             "prj", {"id": "tsk"}, {"hpc_workspace": "/remote/work"})
    assert files.calls == [] and store.flow == {}


def test_grant_failure_never_breaks_task_creation():
    """远端不可达时只跳过授权，不抛异常、不影响任务本身。"""
    files, store = _Files(fail=True), _Store()
    svc = SimpleNamespace(files=files, store=store)
    grant_task_file_transfer(svc, "prj", {"id": "tsk"}, {"hpc_workspace": "/remote/work"})
    assert store.flow == {}
