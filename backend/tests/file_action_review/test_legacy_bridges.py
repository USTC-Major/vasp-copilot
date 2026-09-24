from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.toolbox.contracts import ToolboxError


def _store_upload(api, action_id, state, identity):
    action = {
        "action_id": action_id,
        "kind": "hpc_upload",
        "state": state,
        "binding": {
            "action_id": action_id,
            "file_identity": identity,
            "remote_root": identity["root"]["requested_path"],
            "remote_relative_path": "job/result.txt",
        },
    }

    def update(flow):
        flow.setdefault("consent", {}).setdefault("actions", {})[action_id] = action

    api.app.state.toolbox.files._save(api.project_id, api.task_id, update)
    return action


def test_known_legacy_upload_unknown_blocks_overlapping_mkdir(file_api):
    owner = file_api.app.state.toolbox.files
    identity = owner.legacy_identity("/review/root", "job/result.txt")
    _store_upload(file_api, "legacy-unknown", "unknown", identity)

    with pytest.raises(ToolboxError) as caught:
        with owner.legacy_mkdir(
            "/review/root", "job", hpc=file_api.state.hpc, cfg=None
        ):
            pass
    assert caught.value.code == "DESTINATION_CONFLICT"


def test_active_legacy_upload_lease_blocks_ancestor_and_child_mkdir(file_api):
    owner = file_api.app.state.toolbox.files
    identity, session = owner.prepare_legacy_upload(
        "/review/root", "job/result.txt", hpc=file_api.state.hpc, cfg=None)
    action = _store_upload(file_api, "legacy-live", "executing", identity)
    action["binding"]["upload_session"] = session
    owner.register_legacy_upload(
        session, action["action_id"], file_api.state.hpc,
        (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat())

    with owner.legacy_upload(
        file_api.project_id,
        file_api.task_id,
        action["binding"],
        cfg=None,
    ):
        for root, name in [
            ("/review/root", "job"),
            ("/review/root", "job/result.txt/child"),
        ]:
            with pytest.raises(ToolboxError) as caught:
                with owner.legacy_mkdir(
                    root, name, hpc=file_api.state.hpc, cfg=None
                ):
                    pass
            assert caught.value.code == "DESTINATION_CONFLICT"


def test_no_audit_legacy_mkdir_keeps_compatibility_without_remote_probe(file_api):
    owner = file_api.app.state.toolbox.files
    original = owner.legacy_identity
    owner.legacy_identity = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("compatibility path must not probe the new helper")
    )
    try:
        with owner.legacy_mkdir(
            "/review/root", "fresh", hpc=file_api.state.hpc, cfg=None
        ):
            pass
    finally:
        owner.legacy_identity = original
