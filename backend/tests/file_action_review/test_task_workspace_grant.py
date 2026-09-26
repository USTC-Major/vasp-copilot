"""「选定工作区＝授权」第二层：按任务工作区登记根 + 按作业派生文件范围。

派生只是**信封**：范围仍要逐项弹卡人工确认，用户撤销过的范围不会被复活。
"""
from __future__ import annotations

from backend.toolbox.api import record_task_file_grant
from backend.toolbox.config import ExecutionConfig
from backend.toolbox.service import ExecutionService

from .conftest import FakeRemoteFiles, FakeRemoteState

REMOTE = "/research/work"
OTHER_ROOT = "/research/user_area"


class _DistinctRoots(FakeRemoteFiles):
    """真实远端里两个不同目录的 inode 不同；假远端默认恒等，这里按路径区分。"""

    def inspect_root(self, path, *, root_id=None, version=1):
        value = super().inspect_root(path, root_id=root_id, version=version)
        inode = 3000 + abs(hash(path)) % 1000
        value["identity"]["inode"] = inode
        value["ancestors"][-1]["inode"] = inode
        return value


def _service(tmp_path, state=None):
    state = state or FakeRemoteState()
    config = ExecutionConfig(data_dir=tmp_path, max_jobs=4, poll_interval_seconds=60)
    service = ExecutionService(
        tmp_path,
        settings_loader=lambda: config,
        monitor_enabled=False,
        file_factory=lambda: FakeRemoteFiles(state),
    ).start()
    project = service.store.create_project("grant")["id"]
    task = service.store.create_task(project, "grant", hpc_workspace=REMOTE)["id"]
    return service, state, project, task


def _grant(service, project, task, remote=REMOTE):
    record_task_file_grant(service, project, {"id": task}, {"hpc_workspace": remote})


def _plan(service, project, task, jobs):
    flow = dict(service.require_task(project, task).get("flow") or {})
    flow["plan"] = {"strategy": "test", "jobs": jobs}
    service.store.update_task(project, task, flow=flow)


def _job(key, attempt, status="draft"):
    return {"key": key, "label": key, "kind": "static", "requires": [],
            "status": status, "attempt_id": attempt}


def _scopes(service, project, task):
    flow = service.require_task(project, task).get("flow") or {}
    return list((flow.get("consent") or {}).get("computation_scopes", {}).values())


def test_task_workspace_choice_registers_root_then_derives_one_scope_per_job(tmp_path):
    service, _state, project, task = _service(tmp_path)
    try:
        _grant(service, project, task)
        _plan(service, project, task, [_job("relax", "att-1"), _job("static", "att-2")])
        summary = service.files.ensure_task_file_grant(project, task)
        assert summary == {"root_registered": True, "scopes_created": 2}
        flow = service.require_task(project, task).get("flow") or {}
        assert [r["requested_path"] for r in flow["file_roots"]] == [REMOTE]
        assert flow["file_roots_version"] == 1
        assert flow["file_grant"]["pending_root"] is False
        scopes = {s["job_key"]: s for s in _scopes(service, project, task)}
        assert set(scopes) == {"relax", "static"}
        for key, scope in scopes.items():
            assert scope["attempt_id"] == ("att-1" if key == "relax" else "att-2")
            assert scope["state"] == "proposed"
            assert scope["approval_mode"] == "human"
            assert scope["allowed_operations"] == ["copy", "write_text", "mkdir"]
            assert scope["root_bindings"][0]["destination_prefixes"] == [key]
            assert scope["root_bindings"][0]["version"] == 1
            assert scope["source_bindings"] == []
            assert scope["derived_from"] == "task_workspace_choice"
        # 幂等：再跑一次不会多派生
        assert service.files.ensure_task_file_grant(project, task) == \
            {"root_registered": False, "scopes_created": 0}
        assert len(_scopes(service, project, task)) == 2
    finally:
        service.close()


def test_human_revoked_scope_is_never_recreated(tmp_path):
    service, _state, project, task = _service(tmp_path)
    try:
        _grant(service, project, task)
        _plan(service, project, task, [_job("relax", "att-1")])
        service.files.ensure_task_file_grant(project, task)
        scope = _scopes(service, project, task)[0]
        service.files.revoke(project, task, scope["scope_id"], {"expected_version": 1})
        revoked = _scopes(service, project, task)[0]
        assert revoked["state"] == "revoked" and revoked["revoked_by"] == "human"
        assert service.files.ensure_task_file_grant(project, task)["scopes_created"] == 0
        assert len(_scopes(service, project, task)) == 1
    finally:
        service.close()


def test_system_revoked_scope_is_replaced(tmp_path):
    """工作区/根变更导致的系统撤销不是用户意志，应能重新派生可用范围。"""
    service, _state, project, task = _service(tmp_path)
    try:
        _grant(service, project, task)
        _plan(service, project, task, [_job("relax", "att-1")])
        service.files.ensure_task_file_grant(project, task)
        flow = dict(service.require_task(project, task).get("flow") or {})
        next(iter(flow["consent"]["computation_scopes"].values())).update(
            state="revoked", revoked_by="system", reason="工作区已变更")
        service.store.update_task(project, task, flow=flow)
        assert service.files.ensure_task_file_grant(project, task)["scopes_created"] == 1
        assert len(_scopes(service, project, task)) == 2
    finally:
        service.close()


def test_registration_appends_and_preserves_user_roots(tmp_path):
    service, _state, project, task = _service(tmp_path)
    try:
        service.files.factory = lambda: _DistinctRoots(FakeRemoteState())
        service.files.set_roots(project, task,
                                {"expected_version": 0, "roots": [{"path": OTHER_ROOT}]})
        _grant(service, project, task)
        assert service.files.ensure_task_file_grant(project, task)["root_registered"] is True
        flow = service.require_task(project, task).get("flow") or {}
        assert sorted(r["requested_path"] for r in flow["file_roots"]) == \
            sorted([OTHER_ROOT, REMOTE])
    finally:
        service.close()


def test_user_removed_root_is_not_resurrected(tmp_path):
    """用户自己换过根（没有待登记标记）时，自动授权不得覆盖或复活。"""
    service, _state, project, task = _service(tmp_path)
    try:
        _grant(service, project, task)
        service.files.ensure_task_file_grant(project, task)
        service.files.set_roots(project, task,
                                {"expected_version": 1, "roots": [{"path": OTHER_ROOT}]})
        assert service.files.ensure_task_file_grant(project, task)["root_registered"] is False
        flow = service.require_task(project, task).get("flow") or {}
        assert [r["requested_path"] for r in flow["file_roots"]] == [OTHER_ROOT]
        assert _scopes(service, project, task) == []
    finally:
        service.close()


def test_cleared_workspace_derives_nothing(tmp_path):
    service, _state, project, task = _service(tmp_path)
    try:
        _plan(service, project, task, [_job("relax", "att-1")])
        _grant(service, project, task, remote="")
        assert service.files.ensure_task_file_grant(project, task) == \
            {"root_registered": False, "scopes_created": 0}
        flow = service.require_task(project, task).get("flow") or {}
        assert not flow.get("file_roots") and _scopes(service, project, task) == []
    finally:
        service.close()


class _BrokenRemote:
    def endpoint(self):
        raise RuntimeError("ssh down")

    def inspect_root(self, path, *, root_id=None, version=1):
        raise RuntimeError("ssh down")


def test_remote_failure_only_skips_the_grant(tmp_path):
    service, _state, project, task = _service(tmp_path)
    try:
        _grant(service, project, task)
        _plan(service, project, task, [_job("relax", "att-1")])
        service.files.factory = lambda: _BrokenRemote()
        assert service.files.ensure_task_file_grant(project, task) == \
            {"root_registered": False, "scopes_created": 0}
        flow = service.require_task(project, task).get("flow") or {}
        assert not flow.get("file_roots") and _scopes(service, project, task) == []
    finally:
        service.close()


def test_only_pending_jobs_get_scopes(tmp_path):
    """只有待准备作业自动获得写范围；在途作业不再自动授权写。"""
    service, _state, project, task = _service(tmp_path)
    try:
        _grant(service, project, task)
        _plan(service, project, task, [
            _job("relax", "att-1"),
            _job("running", "att-2", status="running"),
        ])
        assert service.files.ensure_task_file_grant(project, task)["scopes_created"] == 1
        assert [s["job_key"] for s in _scopes(service, project, task)] == ["relax"]
    finally:
        service.close()


def test_legacy_task_with_a_workspace_is_backfilled_once(tmp_path):
    """升级前建的任务没有授权记录：按任务里已有的工作区补一次，并留下来源标注。"""
    service, _state, project, task = _service(tmp_path)
    try:
        _plan(service, project, task, [_job("relax", "att-1")])
        summary = service.files.ensure_task_file_grant(project, task)
        assert summary == {"root_registered": True, "scopes_created": 1}
        flow = service.require_task(project, task).get("flow") or {}
        assert flow["file_grant"]["recorded_by"] == "legacy_task_backfill"
        assert flow["file_grant"]["active"] is True
        assert [r["requested_path"] for r in flow["file_roots"]] == [REMOTE]
        assert service.files.ensure_task_file_grant(project, task) == \
            {"root_registered": False, "scopes_created": 0}
    finally:
        service.close()


def test_plan_lands_scopes_without_any_manual_configuration(tmp_path):
    """规划是 AI 的公开入口：规划落地后范围就位，用户不必再手配研究根/范围。"""
    service, _state, project, task = _service(tmp_path)
    try:
        _grant(service, project, task)
        result = service.execute(project, task, 'plan', {
            "strategy": "先优化再静态",
            "jobs": [{"key": "relax", "label": "结构优化", "kind": "relax"},
                     {"key": "relax/static", "label": "静态自洽", "kind": "static",
                      "requires": ["relax"]}],
        })
        assert result['ok'] is True
        flow = service.require_task(project, task).get("flow") or {}
        assert [r["requested_path"] for r in flow["file_roots"]] == [REMOTE]
        scopes = {s["job_key"]: s for s in _scopes(service, project, task)}
        assert set(scopes) == {"relax", "relax/static"}
        for key, scope in scopes.items():
            job = next(j for j in flow["plan"]["jobs"] if j["key"] == key)
            assert scope["attempt_id"] == job["attempt_id"]
            assert scope["root_bindings"][0]["destination_prefixes"] == [key]
    finally:
        service.close()
