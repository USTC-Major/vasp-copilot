"""A legacy upload is authorized only on its original live SSH session."""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from backend.toolbox.contracts import ToolboxError
from backend.toolbox.ssh.connection import SSHManager
from backend.toolbox.ssh.errors import SSHConnectError


def _propose(api, path="job/result.txt"):
    owner = api.app.state.toolbox.files
    identity, session = owner.prepare_legacy_upload(
        "/review/root", path, hpc=api.state.hpc, cfg=None)
    binding = {
        "action_id": "new-upload-" + path.replace("/", "-"),
        "remote_root": "/review/root",
        "remote_relative_path": path,
        "file_identity": identity,
        "upload_session": session,
    }
    owner.register_legacy_upload(
        session, binding["action_id"], api.state.hpc,
        (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat())
    return owner, binding


def test_same_bound_session_observes_and_releases_once(file_api):
    owner, binding = _propose(file_api)
    with owner.legacy_upload(file_api.project_id, file_api.task_id,
                             binding, cfg=None) as (_before, hpc, _verify_after):
        assert hpc is file_api.state.hpc
        assert len(owner.legacy_sessions) == 1
    assert owner.legacy_sessions == {}
    with pytest.raises(ToolboxError, match="重新提案"):
        with owner.legacy_upload(file_api.project_id, file_api.task_id,
                                 binding, cfg=None):
            pass


def test_old_card_and_restart_epoch_never_rebind(file_api):
    owner, binding = _propose(file_api)
    owner.release_legacy_upload(binding)
    old = copy.deepcopy(binding)
    old.pop("upload_session")
    for stale in (old, binding):
        with pytest.raises(ToolboxError) as caught:
            with owner.legacy_upload(file_api.project_id, file_api.task_id,
                                     stale, cfg=None):
                pass
        assert caught.value.code == "ROOT_CHANGED"
    assert not owner.leases


def test_second_proposal_cannot_take_over_the_same_transport(file_api):
    class FakeHpc:
        closes = 0

        def close(self):
            self.closes += 1

    file_api.state.hpc = FakeHpc()
    owner, binding = _propose(file_api)
    with pytest.raises(ToolboxError) as caught:
        owner.prepare_legacy_upload(
            "/review/root", "job/second.txt", hpc=file_api.state.hpc, cfg=None)
    assert caught.value.code == "FILE_ACTION_BUSY"
    owner.discard_legacy_candidate(file_api.state.hpc)
    assert owner.legacy_session_active(binding)
    assert file_api.state.hpc.closes == 0
    owner.release_legacy_upload(binding)
    assert file_api.state.hpc.closes == 1


@pytest.mark.parametrize("change", ["device", "inode", "symlink"])
def test_root_or_link_change_is_rejected_without_global_weakening(file_api, monkeypatch, change):
    from .conftest import FakeRemoteFiles
    owner, binding = _propose(file_api)
    original = FakeRemoteFiles.inspect_root

    def moved(self, *args, **kwargs):
        root = original(self, *args, **kwargs)
        if change == "symlink":
            root["resolution_chain"] = [{"path": "/review/root",
                                          "target": "/elsewhere/root",
                                          "device": 11, "inode": 999,
                                          "type": "symlink"}]
        else:
            root["identity"][change] = 12 if change == "device" else 999
            for ancestor in root["ancestors"]:
                ancestor[change] = root["identity"][change]
        return root

    monkeypatch.setattr(FakeRemoteFiles, "inspect_root", moved)
    with pytest.raises(ToolboxError) as caught:
        with owner.legacy_upload(file_api.project_id, file_api.task_id,
                                 binding, cfg=None):
            pass
    assert caught.value.code == "ROOT_CHANGED"
    assert not owner.leases and not owner.legacy_sessions


def test_postcheck_accepts_only_missing_parent_created_by_this_upload(file_api, monkeypatch):
    from .conftest import FakeRemoteFiles
    created = False
    original = FakeRemoteFiles._read

    def observed(self, request, *, expected_endpoint=None):
        result = original(self, request, expected_endpoint=expected_endpoint)
        if created:
            result["parent_chain"].append({
                "path": "/review/root/job", "device": 11,
                "inode": 3000, "type": "directory"})
            result["target_exists"] = {"device": 11, "inode": 3001,
                                       "type": "file", "size": 7}
        else:
            result["missing_components"] = ["job"]
        return result

    monkeypatch.setattr(FakeRemoteFiles, "_read", observed)
    owner, binding = _propose(file_api)
    binding["source_size"] = 7
    with owner.legacy_upload(file_api.project_id, file_api.task_id,
                             binding, cfg=None) as (_before, _hpc, verify_after):
        created = True
        verify_after()
    assert not owner.legacy_sessions


def test_expiry_does_not_close_an_executing_session(file_api):
    class FakeHpc:
        closes = 0

        def close(self):
            self.closes += 1

    file_api.state.hpc = FakeHpc()
    owner, binding = _propose(file_api)
    ticket = binding["upload_session"]["session_id"]
    with owner.legacy_upload(file_api.project_id, file_api.task_id,
                             binding, cfg=None):
        owner._expire_legacy_upload(ticket)
        assert file_api.state.hpc.closes == 0
    assert file_api.state.hpc.closes == 1
    assert owner.legacy_sessions == {}


def test_unknown_target_in_another_device_namespace_blocks_upload(file_api):
    owner, binding = _propose(file_api)
    other = copy.deepcopy(binding["file_identity"])
    other["targets"][0]["path"] = "/review/root/job/another.txt"
    other["targets"][0]["anchors"] = [(99, 2000, "job/another.txt")]

    def put(flow):
        flow.setdefault("consent", {}).setdefault("actions", {})["historical-unknown"] = {
            "action_id": "historical-unknown", "kind": "hpc_upload", "state": "unknown",
            "binding": {"file_identity": other},
        }

    owner._save(file_api.project_id, file_api.task_id, put)
    with pytest.raises(ToolboxError) as caught:
        with owner.legacy_upload(file_api.project_id, file_api.task_id,
                                 binding, cfg=None):
            pass
    assert caught.value.code == "DESTINATION_CONFLICT"
    assert not owner.leases


def test_pinned_ssh_manager_cannot_reconnect_after_transport_loss():
    transport = Mock()
    transport.is_active.return_value = True
    client = Mock()
    client.get_transport.return_value = transport
    factory = Mock(return_value=client)
    manager = SSHManager(client_factory=factory)
    manager.switch(host="test.invalid", username="tester")
    manager.pin_current_transport()
    assert manager.connect() is client
    transport.is_active.return_value = False
    with pytest.raises(SSHConnectError, match="重新提案"):
        manager.connect()
    assert factory.call_count == 1
    manager.close()
    with pytest.raises(SSHConnectError):
        manager.connect()
    assert factory.call_count == 1
