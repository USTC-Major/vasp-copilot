from __future__ import annotations

import threading

from backend.toolbox.config import ExecutionConfig
from backend.toolbox.service import ExecutionService

from .conftest import FakeRemoteFiles, FakeRemoteState


def test_old_blocked_observation_cannot_save_after_owner_close_and_reacquire(tmp_path):
    state = FakeRemoteState(block_root=threading.Event())
    config = ExecutionConfig(data_dir=tmp_path, poll_interval_seconds=60)
    first = ExecutionService(
        tmp_path,
        settings_loader=lambda: config,
        monitor_enabled=False,
        file_factory=lambda: FakeRemoteFiles(state),
    ).start()
    project = first.store.create_project("lifecycle")["id"]
    task = first.store.create_task(project, "lifecycle")["id"]
    failures = []
    returned = []

    def old_request():
        try:
            returned.append(
                first.files.set_roots(
                    project,
                    task,
                    {"expected_version": 0, "roots": [{"path": "/review/root"}]},
                )
            )
        except BaseException as exc:
            failures.append(exc)

    request = threading.Thread(target=old_request)
    request.start()
    assert state.root_entered.wait(3)
    first.close()

    second = ExecutionService(
        tmp_path,
        settings_loader=lambda: config,
        monitor_enabled=False,
        file_factory=lambda: FakeRemoteFiles(FakeRemoteState()),
    ).start()
    try:
        state.block_root.set()
        request.join(5)
        assert not request.is_alive()
        assert returned == [] and len(failures) == 1
        assert getattr(failures[0], "code", None) == "FILE_ACTION_BUSY"
        flow = second.require_task(project, task).get("flow") or {}
        assert flow.get("file_roots_version", 0) == 0
        assert flow.get("file_roots", []) == []
    finally:
        second.close()
