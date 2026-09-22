"""Independent persistence and deletion boundaries for C file actions."""

from __future__ import annotations

import pytest

from backend.toolbox.contracts import ToolboxError
from backend.toolbox.projects import ProjectStore


def _task_with_action(tmp_path, *, kind: str, state: str, binding: dict | None = None):
    store = ProjectStore(tmp_path)
    project = store.create_project("review")
    task = store.create_task(project["id"], title="file audit")
    flow = {
        "consent": {
            "actions": {
                "a" * 32: {
                    "action_id": "a" * 32,
                    "card_id": "a" * 32,
                    "kind": kind,
                    "state": state,
                    "binding": binding or {},
                }
            }
        }
    }
    store.update_task(project["id"], task["id"], flow=flow)
    return project["id"], task["id"]


@pytest.mark.parametrize("delete_target", ["task", "project"])
def test_legacy_unidentified_upload_unknown_survives_restart_and_blocks_deletion(
    tmp_path, delete_target
):
    """Dropping this record would remove the global legacy fail-closed barrier."""
    project_id, task_id = _task_with_action(
        tmp_path,
        kind="hpc_upload",
        state="executing",
        binding={
            "operation": "hpc_upload",
            "remote_root": "/legacy/root",
            "remote_relative_path": "job/input.dat",
        },
    )

    restarted = ProjectStore(tmp_path)
    action = restarted.get_task(project_id, task_id)["flow"]["consent"]["actions"][
        "a" * 32
    ]
    assert action["state"] == "unknown"
    assert action["legacy_file_unknown"] is True

    with pytest.raises(ToolboxError) as caught:
        if delete_target == "task":
            restarted.delete_task(project_id, task_id)
        else:
            restarted.delete_project(project_id)
    assert caught.value.code == "FILE_AUDIT_RETAINED"

    preserved = ProjectStore(tmp_path).get_task(project_id, task_id)
    assert preserved is not None
    assert (
        preserved["flow"]["consent"]["actions"]["a" * 32]["legacy_file_unknown"]
        is True
    )


def test_terminal_legacy_upload_without_c_audit_keeps_original_delete_behavior(tmp_path):
    project_id, task_id = _task_with_action(
        tmp_path,
        kind="hpc_upload",
        state="executed",
        binding={
            "operation": "hpc_upload",
            "remote_root": "/legacy/root",
            "remote_relative_path": "job/input.dat",
        },
    )

    store = ProjectStore(tmp_path)
    deleted = store.delete_task(project_id, task_id)
    assert deleted is not None
    assert store.get_task(project_id, task_id) is None
