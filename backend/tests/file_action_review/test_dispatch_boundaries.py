from __future__ import annotations

import threading

import pytest

from backend.toolbox.config import ExecutionConfig
from backend.toolbox.contracts import ToolboxError

from .test_manual_http_flow import _plan, _setup_scope


def _make_executing(api):
    root, scope = _setup_scope(api)
    action = _plan(api, root, scope, key="dispatch-review").json()["pending"]
    owner = api.app.state.toolbox.files

    def update(flow):
        flow["consent"]["computation_scopes"][scope["scope_id"]]["state"] = "active"
        current = flow["consent"]["actions"][action["action_id"]]
        current["state"] = "executing"
        current["receipt"]["phase"] = "connecting"

    owner._save(api.project_id, api.task_id, update)
    return owner, action


def test_dispatch_guard_serializes_settings_mutation_through_send_gate(file_api):
    owner, action = _make_executing(file_api)
    entered = threading.Event()
    release = threading.Event()
    update_attempted = threading.Event()
    updated = threading.Event()
    failures = []

    def dispatch():
        try:
            with owner._dispatch(
                file_api.project_id, file_api.task_id, action["action_id"], "begin", None
            ):
                entered.set()
                assert release.wait(3)
        except BaseException as exc:  # evidence must retain thread failures
            failures.append(exc)

    def settings_update():
        update_attempted.set()
        with file_api.app.state.toolbox._guard:
            updated.set()

    sender = threading.Thread(target=dispatch)
    sender.start()
    assert entered.wait(3)
    updater = threading.Thread(target=settings_update)
    updater.start()
    assert update_attempted.wait(3)
    assert not updated.wait(0.1)
    release.set()
    sender.join(3)
    updater.join(3)
    assert not sender.is_alive() and not updater.is_alive()
    assert updated.is_set() and failures == []


def test_production_dispatch_rechecks_bound_configuration(file_api):
    owner, action = _make_executing(file_api)
    service = file_api.app.state.toolbox
    previous_factory = owner.factory
    previous_loader = service.settings_loader
    owner.factory = None
    service.settings_loader = lambda: ExecutionConfig(
        data_dir=service.root,
        ssh_host="different.invalid",
        ssh_username="different-user",
        scheduler_backend="slurm",
    )
    try:
        with pytest.raises(ToolboxError) as caught:
            with owner._dispatch(
                file_api.project_id,
                file_api.task_id,
                action["action_id"],
                "begin",
                None,
            ):
                raise AssertionError("mismatched configuration must not reach send")
        assert caught.value.code == "ENDPOINT_CHANGED"
    finally:
        owner.factory = previous_factory
        service.settings_loader = previous_loader


def test_legacy_upload_rejects_observer_different_from_writer(file_api):
    owner = file_api.app.state.toolbox.files
    identity = owner.legacy_identity("/review/root", "job/result.txt")
    binding = {
        "action_id": "wrong-writer",
        "file_identity": identity,
        "remote_root": "/review/root",
        "remote_relative_path": "job/result.txt",
    }
    with pytest.raises(ToolboxError) as caught:
        with owner.legacy_upload(
            file_api.project_id,
            file_api.task_id,
            binding,
            hpc=object(),
            cfg=None,
        ):
            raise AssertionError("different actual writer must not receive a lease")
    assert caught.value.code == "ENDPOINT_CHANGED"
