"""Independent Linux evidence for owner recovery and real POSIX failure boundaries."""

from __future__ import annotations

import copy
import datetime as dt
import errno
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import threading

import pytest
from fastapi.testclient import TestClient

from backend.toolbox.api import create_toolbox_app
from backend.toolbox.config import ExecutionConfig
from backend.toolbox.ssh import file_helper as helper
from backend.toolbox.ssh.connection import SSHManager
from backend.toolbox.ssh.remote_files import RemoteFiles
from backend.tests.remote_file_foundation.test_adapter_posix import Channel, Client
from backend.tests.remote_file_review.test_posix_helper import (
    _HelperProcess,
    _manifest,
    _permit,
)


pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="requires actual Linux file descriptors, inode identity, fsync, and renameat2",
)


class _FaultChannel(Channel):
    def exec_command(self, command):
        argv = shlex.split(command)
        assert argv[:4] == ["python3", "-I", "-u", "-c"] and len(argv) == 5
        source = self.transport.transform(argv[4])
        self.transport.commands.append(command)
        self.process = subprocess.Popen(
            [*argv[:4], source],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )


class _FaultClient(Client):
    def __init__(self, transform):
        super().__init__()
        self.transform = transform

    def open_session(self, **kwargs):
        del kwargs
        return _FaultChannel(self)


def _owner_app(owner_root: Path, *, transform=lambda source: source):
    def factory():
        client = _FaultClient(transform)
        manager = SSHManager(client_factory=lambda: client)
        manager.switch(host="fixture.invalid", username="fixture")
        return RemoteFiles(manager, scheduler_target={"scheduler": "fixture"})

    return create_toolbox_app(
        root=owner_root,
        settings_loader=lambda: ExecutionConfig(data_dir=owner_root),
        monitor_enabled=False,
        file_factory=factory,
    )


def _plan_owner_action(client, service, roots: list[Path], items: list[dict]):
    project = service.store.create_project("review")["id"]
    task = service.store.create_task(project, "review")["id"]
    service.store.update_task(
        project,
        task,
        flow={
            "plan": {
                "jobs": [{"key": "job", "attempt_id": "attempt", "status": "draft"}]
            }
        },
    )
    base = f"/api/v1/toolbox/projects/{project}/tasks/{task}"
    root_response = client.put(
        base + "/file-roots",
        json={"expected_version": 0, "roots": [{"path": str(path)} for path in roots]},
    )
    assert root_response.status_code == 200, root_response.text
    registered = root_response.json()["data"]["roots"]
    for item, root in zip(items, registered):
        item["destination"]["root_id"] = root["root_id"]
    scope_response = client.post(
        base + "/computation-scopes",
        json={
            "job_key": "job",
            "attempt_id": "attempt",
            "root_bindings": [
                {
                    "root_id": root["root_id"],
                    "version": root["version"],
                    "destination_prefixes": [""],
                }
                for root in registered
            ],
            "allowed_operations": sorted({item["op"] for item in items}),
            "source_paths": [],
            "max_operations": len(items),
            "max_total_bytes": sum(len(item.get("text", "").encode()) for item in items),
            "expires_at": _future(),
            "approval_mode": "human",
        },
    )
    assert scope_response.status_code == 201, scope_response.text
    scope = scope_response.json()["data"]
    planned = client.post(
        base + "/tools",
        json={
            "name": "remote_file_plan",
            "args": {
                "scope_id": scope["scope_id"],
                "scope_version": scope["version"],
                "job_key": "job",
                "attempt_id": "attempt",
                "idempotency_key": "linux-owner-" + service.files.owner_id,
                "items": items,
            },
        },
    )
    assert planned.status_code == 200, planned.text
    return base, scope, planned.json()["data"], registered


def _approve_owner(client, base, scope, action):
    response = client.post(
        base + "/consents/" + action["action_id"],
        json={
            "approved": True,
            "scope_confirmation": {
                "scope_id": scope["scope_id"],
                "version": scope["version"],
            },
        },
    )
    assert response.status_code == 202, response.text


@pytest.mark.posix_critical
def test_linux_owner_copy_source_changed_during_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, required_linux_capabilities: dict
):
    assert required_linux_capabilities
    root, source_dir = tmp_path / "root", tmp_path / "source"
    root.mkdir()
    source_dir.mkdir()
    source = source_dir / "WAVECAR"
    source.write_bytes(b"a" * (helper.CHUNK * 2 + 17))
    manifest = _manifest(
        root,
        action_id="1" * 32,
        operation="copy",
        source_path=source,
        destination="WAVECAR",
    )
    transaction = helper.FileTransaction(manifest, remaining_seconds=60)
    original_read = helper.os.read
    source_identity = (source.stat().st_dev, source.stat().st_ino)
    changed = False

    def interleaved_read(fd, size):
        nonlocal changed
        data = original_read(fd, size)
        current = os.fstat(fd)
        if data and not changed and (current.st_dev, current.st_ino) == source_identity:
            changed = True
            with source.open("r+b", buffering=0) as stream:
                stream.seek(helper.CHUNK + 3)
                stream.write(b"Z")
                os.fsync(stream.fileno())
        return data

    monkeypatch.setattr(helper.os, "read", interleaved_read)
    with pytest.raises(helper.FileError) as caught:
        transaction.prepare_next()
    assert caught.value.code == "SOURCE_CHANGED"
    assert changed and not (root / "WAVECAR").exists()
    transaction.close()


@pytest.mark.posix_critical
@pytest.mark.parametrize(
    "fault",
    [
        "ENOSPC",
        "EDQUOT",
        "EACCES",
        "prepared_receipt_fsync",
        "pre_publish_target_race",
        "post_publish_fsync",
        "committed_receipt_fsync",
    ],
)
def test_linux_owner_errno_and_fsync_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    required_linux_capabilities: dict,
    fault: str,
):
    assert required_linux_capabilities
    if fault in {"ENOSPC", "EDQUOT", "EACCES"}:
        marker = "                    write_all(temp_fd, data)"
        replacement = (
            f"                    raise OSError(errno.{fault}, 'injected {fault}')"
        )
    elif fault in {"prepared_receipt_fsync", "committed_receipt_fsync"}:
        marker = "            write_all(fd, data)\n            os.fsync(fd)"
        suffix = "prepared.json" if fault.startswith("prepared") else "committed.json"
        replacement = (
            "            write_all(fd, data)\n"
            f"            if name.endswith('{suffix}'):\n"
            "                raise OSError(errno.EIO, 'injected receipt fsync failure')\n"
            "            os.fsync(fd)"
        )
    elif fault == "pre_publish_target_race":
        marker = "            rename_noreplace(parent, temporary, target_name)"
        replacement = (
            "            injected = os.open(target_name, os.O_WRONLY | os.O_CREAT | "
            "os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)\n"
            "            os.close(injected)\n"
            "            rename_noreplace(parent, temporary, target_name)"
        )
    else:
        marker = "            os.fsync(parent)\n            target.update("
        replacement = (
            "            raise OSError(errno.EIO, 'injected post-publish fsync')\n"
            "            target.update("
        )

    def transform(source):
        assert marker in source
        return source.replace(marker, replacement, 1)

    root = tmp_path / fault
    root.mkdir()
    app = _owner_app(tmp_path / (fault + "-owner"), transform=transform)
    with TestClient(app) as client:
        service = app.state.toolbox
        item = {
            "item_id": "item",
            "op": "write_text",
            "text": fault,
            "destination": {"root_id": "filled-by-test", "relative_path": "result.txt"},
        }
        base, scope, action, _ = _plan_owner_action(client, service, [root], [item])
        _approve_owner(client, base, scope, action)
        assert service.files.wait_idle(10)
        saved = client.get(base + "/consents/" + action["action_id"]).json()["card"]
        if fault in {
            "ENOSPC",
            "EDQUOT",
            "EACCES",
            "prepared_receipt_fsync",
            "pre_publish_target_race",
        }:
            assert saved["state"] == "failed"
            assert saved["receipt"]["released"] == {
                "operations": 1,
                "bytes": len(fault),
            }
            assert saved["receipt"]["spent"] == {"operations": 0, "bytes": 0}
            if fault == "pre_publish_target_race":
                assert (root / "result.txt").read_bytes() == b""
            else:
                assert not (root / "result.txt").exists()
        else:
            assert saved["state"] == "unknown"
            assert saved["receipt"]["held_unknown"] == {
                "operations": 1,
                "bytes": len(fault),
            }
            assert (root / "result.txt").read_text(encoding="utf-8") == fault


@pytest.mark.posix_critical
def test_linux_owner_cleanup_and_multi_root_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, required_linux_capabilities: dict
):
    assert required_linux_capabilities
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    app = _owner_app(tmp_path / "multi-owner")
    with TestClient(app) as client:
        service = app.state.toolbox
        items = [
            {
                "item_id": "first",
                "op": "write_text",
                "text": "first",
                "destination": {"root_id": "filled", "relative_path": "first.txt"},
            },
            {
                "item_id": "second",
                "op": "write_text",
                "text": "second",
                "destination": {"root_id": "filled", "relative_path": "second.txt"},
            },
        ]
        base, scope, action, _ = _plan_owner_action(
            client, service, [first, second], items
        )
        second.rmdir()
        _approve_owner(client, base, scope, action)
        assert service.files.wait_idle(10)
        saved = client.get(base + "/consents/" + action["action_id"]).json()["card"]
        first_audit = first / helper.names(action["action_id"])["action_directory"]
        assert saved["state"] == "failed"
        assert saved["receipt"]["released"] == {"operations": 2, "bytes": 11}
        assert str(first_audit) in saved["receipt"]["leftovers"]
        assert first_audit.is_dir()
        assert not (first / "first.txt").exists()

    cleanup_root = tmp_path / "cleanup"
    cleanup_root.mkdir()
    fsync_marker = "                os.fsync(temp_fd)"
    cleanup_marker = "    def _remove_owned(parent, name, expected):\n        try:"

    def cleanup_fault(source):
        assert fsync_marker in source and cleanup_marker in source
        source = source.replace(
            fsync_marker,
            "                raise OSError(errno.EIO, 'injected temp fsync')",
            1,
        )
        return source.replace(
            cleanup_marker,
            "    def _remove_owned(parent, name, expected):\n        return False\n        try:",
            1,
        )

    app = _owner_app(tmp_path / "cleanup-owner", transform=cleanup_fault)
    with TestClient(app) as client:
        service = app.state.toolbox
        item = {
            "item_id": "cleanup",
            "op": "write_text",
            "text": "cleanup",
            "destination": {"root_id": "filled", "relative_path": "result.txt"},
        }
        base, scope, action, _ = _plan_owner_action(
            client, service, [cleanup_root], [item]
        )
        _approve_owner(client, base, scope, action)
        assert service.files.wait_idle(10)
        saved = client.get(base + "/consents/" + action["action_id"]).json()["card"]
        staging = cleanup_root / action["binding"]["manifest"]["items"][0]["names"]["staging_name"]
        assert saved["state"] == "failed"
        assert str(staging) in saved["receipt"]["leftovers"]
        assert staging.exists() and not (cleanup_root / "result.txt").exists()

    # The helper may remove only the inode it created. Replacing its staging
    # directory entry while the helper still holds the old fd must preserve the
    # foreign object and report it for operator cleanup.
    replaced_root = tmp_path / "cleanup-replaced"
    replaced_root.mkdir()
    replaced_manifest = _manifest(
        replaced_root,
        action_id="a" * 32,
        operation="write_text",
        text="owned-stage",
        destination="result.txt",
    )
    child = _HelperProcess()
    child.request(
        {"op": "begin", "manifest": replaced_manifest, "remaining_seconds": 60}
    )
    prepared = child.request({"op": "prepare_next"})
    staging = replaced_root / replaced_manifest["items"][0]["names"]["staging_name"]
    original_inode = (prepared["temporary_evidence"]["device"], prepared["temporary_evidence"]["inode"])
    staging.unlink()
    staging.write_bytes(b"foreign-replacement")
    assert (staging.stat().st_dev, staging.stat().st_ino) != original_inode
    aborted = child.request({"op": "abort"})
    child.close()
    assert str(staging) in aborted["leftovers"]
    assert staging.read_bytes() == b"foreign-replacement"


@pytest.mark.posix_critical
def test_linux_two_helpers_noreplace_owner_receipts(
    tmp_path: Path, required_linux_capabilities: dict
):
    assert required_linux_capabilities
    root = tmp_path / "root"
    root.mkdir()
    first_manifest = _manifest(
        root,
        action_id="7" * 32,
        operation="write_text",
        text="winner",
        destination="shared.txt",
    )
    second_manifest = _manifest(
        root,
        action_id="8" * 32,
        operation="write_text",
        text="loser",
        destination="shared.txt",
    )
    children = [_HelperProcess(), _HelperProcess()]
    manifests = [first_manifest, second_manifest]
    prepared = []
    for child, manifest in zip(children, manifests):
        child.request({"op": "begin", "manifest": manifest, "remaining_seconds": 60})
        prepared.append(child.request({"op": "prepare_next"}))

    barrier = threading.Barrier(2)
    outcomes: list[tuple[str, object]] = []
    lock = threading.Lock()

    def publish(index):
        barrier.wait(timeout=5)
        try:
            value = children[index].request(
                {
                    "op": "commit",
                    "permit": _permit(manifests[index], prepared[index]),
                    "remaining_seconds": 30,
                }
            )
            outcome = ("committed", value)
        except AssertionError as exc:
            outcome = ("rejected", str(exc))
        with lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=publish, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert all(not thread.is_alive() for thread in threads)
    for child in children:
        child.close()

    assert [kind for kind, _ in outcomes].count("committed") == 1
    assert [kind for kind, _ in outcomes].count("rejected") == 1
    assert "DESTINATION_CONFLICT" in next(
        str(value) for kind, value in outcomes if kind == "rejected"
    )
    winner = next(
        index
        for index, manifest in enumerate(manifests)
        if helper.reconcile(manifest, item_id="item")["state"] == "confirmed"
    )
    loser = 1 - winner
    assert (root / "shared.txt").read_text(encoding="utf-8") == ["winner", "loser"][winner]
    assert helper.reconcile(manifests[loser], item_id="item")["state"] == "unknown"


def _future(minutes=10):
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=minutes)).isoformat()


@pytest.mark.posix_critical
def test_linux_owner_reconcile_after_local_receipt_loss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, required_linux_capabilities: dict
):
    assert required_linux_capabilities
    owner_root, remote_root = tmp_path / "owner", tmp_path / "remote"
    remote_root.mkdir()
    app = _owner_app(owner_root)
    with TestClient(app) as client:
        service = app.state.toolbox
        project = service.store.create_project("review")["id"]
        task = service.store.create_task(project, "review")["id"]
        service.store.update_task(
            project,
            task,
            flow={
                "plan": {
                    "jobs": [
                        {"key": "job", "attempt_id": "attempt", "status": "draft"}
                    ]
                }
            },
        )
        base = f"/api/v1/toolbox/projects/{project}/tasks/{task}"
        roots = client.put(
            base + "/file-roots",
            json={"expected_version": 0, "roots": [{"path": str(remote_root)}]},
        )
        assert roots.status_code == 200, roots.text
        root = roots.json()["data"]["roots"][0]
        scope_response = client.post(
            base + "/computation-scopes",
            json={
                "job_key": "job",
                "attempt_id": "attempt",
                "root_bindings": [
                    {
                        "root_id": root["root_id"],
                        "version": root["version"],
                        "destination_prefixes": [""],
                    }
                ],
                "allowed_operations": ["write_text"],
                "source_paths": [],
                "max_operations": 1,
                "max_total_bytes": 100,
                "expires_at": _future(),
                "approval_mode": "human",
            },
        )
        assert scope_response.status_code == 201, scope_response.text
        scope = scope_response.json()["data"]
        planned = client.post(
            base + "/tools",
            json={
                "name": "remote_file_plan",
                "args": {
                    "scope_id": scope["scope_id"],
                    "scope_version": scope["version"],
                    "job_key": "job",
                    "attempt_id": "attempt",
                    "idempotency_key": "lost-local-receipt",
                    "items": [
                        {
                            "item_id": "item",
                            "op": "write_text",
                            "text": "persisted remotely\n",
                            "destination": {
                                "root_id": root["root_id"],
                                "relative_path": "result.txt",
                            },
                        }
                    ],
                },
            },
        )
        assert planned.status_code == 200, planned.text
        action = planned.json()["data"]
        original_update = service.files._update_action
        lost = False

        def lose_committed_save(project_id, task_id, action_id, change):
            nonlocal lost
            current = service.files._flow(project_id, task_id)["consent"]["actions"][action_id]
            probe = copy.deepcopy(current)
            change(probe)
            newly_committed = any(
                item.get("state") == "committed"
                for item in (probe.get("receipt") or {}).get("items", [])
            ) and not any(
                item.get("state") == "committed"
                for item in (current.get("receipt") or {}).get("items", [])
            )
            if newly_committed and not lost:
                lost = True
                raise OSError(errno.EIO, "injected local committed receipt loss")
            return original_update(project_id, task_id, action_id, change)

        monkeypatch.setattr(service.files, "_update_action", lose_committed_save)
        approved = client.post(
            base + "/consents/" + action["action_id"],
            json={
                "approved": True,
                "scope_confirmation": {
                    "scope_id": scope["scope_id"],
                    "version": scope["version"],
                },
            },
        )
        assert approved.status_code == 202, approved.text
        assert service.files.wait_idle(10)
        saved = client.get(base + "/consents/" + action["action_id"]).json()["card"]
        assert saved["state"] == "unknown"
        assert (remote_root / "result.txt").read_text(encoding="utf-8") == "persisted remotely\n"

    restarted = _owner_app(owner_root)
    with TestClient(restarted) as client:
        response = client.post(
            base + "/file-actions/" + action["action_id"] + "/reconcile", json={}
        )
        assert response.status_code == 200, response.text
        assert response.json()["data"]["state"] == "executed"
        assert (remote_root / "result.txt").read_text(encoding="utf-8") == "persisted remotely\n"
        preview = client.post(
            base + "/tools",
            json={
                "name": "remote_inspect",
                "args": {"path": str(remote_root / "result.txt"), "view": "text"},
            },
        )
        assert preview.status_code == 200, preview.text
        assert preview.json()["data"]["text"] == "persisted remotely\n"

    # A damaged remote receipt remains unknown through the trusted B reconcile path.
    corrupt_root = tmp_path / "corrupt"
    corrupt_root.mkdir()
    corrupt_manifest = _manifest(
        corrupt_root,
        action_id="9" * 32,
        operation="write_text",
        text="receipt",
        destination="receipt.txt",
    )
    transaction = helper.FileTransaction(corrupt_manifest, remaining_seconds=60)
    prepared = transaction.prepare_next()
    committed = transaction.commit(
        _permit(corrupt_manifest, prepared), remaining_seconds=30
    )
    transaction.close()
    receipt_path = corrupt_root / committed["remote_receipt_id"]
    record = json.loads(receipt_path.read_text(encoding="utf-8"))
    record["action_id"] = "corrupt-action"
    receipt_path.write_bytes(helper.canonical(record))
    reconciled = helper.reconcile(corrupt_manifest, item_id="item")
    assert reconciled["state"] == "unknown"
    assert reconciled["items"][0]["error"]["code"] == "ACTION_UNKNOWN"
