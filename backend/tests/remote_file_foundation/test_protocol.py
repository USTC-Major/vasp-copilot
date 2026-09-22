"""Fixed-wire/identity checks are executable without a real SSH account."""
import ast
import copy
import datetime
import json
import shlex
from unittest.mock import Mock

import pytest

from backend.toolbox.ssh import file_helper as h
from backend.toolbox.ssh.connection import SSHManager
from backend.toolbox.ssh.remote_files import RemoteFileError, RemoteFiles, _Wire, helper_command


def test_command_is_only_fixed_python_source_and_python39_syntax():
    argv = shlex.split(helper_command())
    assert argv[:4] == ["python3", "-I", "-u", "-c"]
    assert len(argv) == 5
    ast.parse(argv[4], feature_version=(3, 9))
    assert argv[4].endswith("    main()\n")


@pytest.mark.parametrize("path", ["/root/link/../x", "/root/./x", "/../x"])
def test_absolute_dot_components_are_not_silently_reinterpreted(path):
    with pytest.raises(h.FileError, match="dot traversal"):
        h.absolute(path)


@pytest.mark.parametrize("path", ["../out", ".vasp-doctor-action-x", "a/.vasp-doctor-x", "a//b", "a/./b", "/absolute", "$(touch bad)"])
def test_destination_reserved_names_and_expansion_denied(path):
    with pytest.raises(h.FileError):
        h.relative(path)


def test_names_rederived_and_bounded():
    names = h.names("a" * 32, "valid_item")
    assert all("/" not in value for key, value in names.items() if isinstance(value, str))
    for action, item in [("../root", "good"), ("a" * 32, "../../evil"), ("a" * 32, "x" * 65)]:
        with pytest.raises(h.FileError):
            h.names(action, item)


def test_provenance_cannot_downgrade_potcar_or_change_identity():
    info = {"canonical_path": "/work/renamed.txt", "device": 1, "inode": 2, "size": 3,
            "mtime_ns": 4, "ctime_ns": 5, "type": "file", "content_class": "unclassified_external"}
    trusted = {**info, "origin": "managed_output", "content_class": "potcar",
               "receipt_id": "receipt", "manifest_digest": "digest", "endpoint_digest": "endpoint"}
    assert h.provenance_class(info, trusted, "endpoint") == "potcar"
    for bad in [None, {"origin": "external_source", "content_class": "normal_text"},
                {**trusted, "inode": 99}, {**trusted, "endpoint_digest": "other"}]:
        with pytest.raises(h.FileError):
            h.provenance_class(info, bad, "endpoint")
    assert h.provenance_class(info, {"origin": "external_source"}, "endpoint") == "unclassified_external"


def manager_fixture():
    client = Mock()
    transport = client.get_transport.return_value
    transport.is_active.return_value = True
    key = Mock()
    key.get_name.return_value = "ssh-test"
    key.asbytes.return_value = b"PUBLIC TEST KEY"
    transport.get_remote_server_key.return_value = key
    client.get_host_keys.return_value.lookup.return_value = {"ssh-test": key}
    manager = SSHManager(client_factory=lambda: client)
    manager.switch(host="fixture.invalid", port=2222, username="fixture")
    return manager, client, transport


def test_endpoint_contains_verified_public_evidence_and_no_password():
    manager, client, transport = manager_fixture()
    endpoint = manager.file_endpoint({"scheduler": "slurm", "host": "fixture.invalid", "port": 2222})
    assert endpoint["host_key"]["verification"] == "known_hosts"
    assert "password" not in json.dumps(endpoint)
    assert endpoint["endpoint_digest"] == h.digest({k: v for k, v in endpoint.items() if k != "endpoint_digest"})
    client.get_host_keys.return_value.lookup.return_value = {}
    assert manager.file_endpoint({})["host_key"]["verification"] == "unknown"
    transport.is_active.return_value = False
    with pytest.raises(Exception, match="disconnected"):
        manager.file_endpoint({}, connect=False)
    assert client.connect.call_count == 1


def test_local_configuration_paths_only_contribute_a_digest():
    manager, _, _ = manager_fixture()
    manager.connect()
    manager.identity_file = "C:/private-user/config/ssh-key"
    manager.known_hosts_path = "C:/private-user/config/hosts"
    endpoint = manager.file_endpoint({"identity_file": manager.identity_file, "known_hosts_path": manager.known_hosts_path}, connect=False)
    assert "private-user" not in json.dumps(endpoint)
    assert len(endpoint["local_config_digest"]) == 64


class ReplyChannel:
    def __init__(self, response):
        self.response, self.sent, self.commands, self.closed = response, [], [], False

    def settimeout(self, value): pass
    def exec_command(self, command): self.commands.append(command)
    def sendall(self, data): self.sent.append(data)
    def recv_stderr_ready(self): return False
    def recv_ready(self): return bool(self.response)
    def recv(self, size):
        result, self.response = self.response[:size], self.response[size:]
        return result
    def exit_status_ready(self): return not self.response
    def close(self): self.closed = True


@pytest.mark.parametrize("response", [b"not json\n", b'{"ok":true', b"x" * (h.MAX_REPLY + 2)], ids=["malformed", "truncated", "oversize"])
def test_dispatched_write_loss_is_unknown_without_retry(response):
    manager, _, transport = manager_fixture()
    channel = ReplyChannel(response)
    transport.open_session.return_value = channel
    wire = _Wire(manager)
    with pytest.raises(RemoteFileError) as caught:
        wire.call({"op": "commit", "permit": {}}, writing=True)
    assert caught.value.code == "ACTION_UNKNOWN" and caught.value.retryable is False
    assert len(channel.sent) == 1 and len(channel.commands) == 1
    wire.close()


def test_read_payload_stays_on_stdin():
    manager, _, transport = manager_fixture()
    channel = ReplyChannel(b'{"ok":true,"data":{"read_only":true}}\n')
    transport.open_session.return_value = channel
    path = "/read/$(not_executed); 'data'.txt"
    RemoteFiles(manager, scheduler_target={}).inspect(path)
    assert path not in channel.commands[0]
    assert json.loads(channel.sent[0])["path"] == path


def test_planner_cannot_supply_provenance_or_content_label():
    manager, _, _ = manager_fixture()
    files = RemoteFiles(manager, scheduler_target={})
    endpoint = files.endpoint()
    context = {"action_id": "a" * 32, "project_id": "p", "task_id": "t", "job_key": "j", "attempt_id": "a",
               "scope_id": "s", "scope_version": 1,
               "expires_at": (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=10)).isoformat()}
    for field in ("content_class", "provenance", "source_provenance", "names"):
        with pytest.raises(RemoteFileError):
            files.plan(context, [{"root_id": "r", "endpoint_digest": endpoint["endpoint_digest"]}],
                       [{"item_id": "i", "op": "write_text", field: "normal_text"}],
                       budgets={"max_operations": 1, "max_total_bytes": 100})


def test_untrusted_endpoint_refuses_write_before_remote_dispatch():
    manager, client, transport = manager_fixture()
    client.get_host_keys.return_value.lookup.return_value = {}
    files = RemoteFiles(manager, scheduler_target={})
    with pytest.raises(RemoteFileError) as caught:
        files.plan({"expires_at": (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=1)).isoformat()},
                   [], [{}], budgets={"max_operations": 1, "max_total_bytes": 1})
    assert caught.value.code == "ENDPOINT_CHANGED"
    transport.open_session.assert_not_called()


@pytest.mark.parametrize("operation", ["read", "begin"])
def test_transport_switch_between_observation_and_channel_sends_no_user_request(monkeypatch, operation):
    manager, _, transport = manager_fixture()
    files = RemoteFiles(manager, scheduler_target={})
    endpoint = files.endpoint()
    channel = ReplyChannel(b'{"ok":true,"data":{}}\n')
    transport.open_session.return_value = channel
    original_connect = manager.connect
    calls = []
    def switch_during_channel_open():
        client = original_connect()
        calls.append(True)
        if operation == "begin" or len(calls) == 2:
            manager._active = {**manager.active, "host": "other.invalid"}
        return client
    monkeypatch.setattr(manager, "connect", switch_during_channel_open)
    with pytest.raises(RemoteFileError) as caught:
        if operation == "read":
            files._read({"op": "root", "path": "/user/path"}, expected_endpoint=endpoint)
        else:
            from backend.toolbox.ssh.remote_files import FileSession
            FileSession(files, {"endpoint": endpoint, "expires_at": "2099-01-01T00:00:00+00:00"})
    assert caught.value.code == "ENDPOINT_CHANGED"
    assert channel.sent == []
    assert channel.closed


def test_commit_rejects_permission_beyond_scope_without_sending():
    from backend.toolbox.ssh.remote_files import FileSession
    import time
    session = FileSession.__new__(FileSession)
    session._check = lambda: None
    session.prepared, session.prepared_at = {}, time.monotonic()
    session.manifest = {"expires_at": (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=5)).isoformat()}
    session.wire = Mock()
    with pytest.raises(RemoteFileError, match="scope expiry"):
        session.commit({"valid_until": (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=20)).isoformat()})
    session.wire.call.assert_not_called()


def test_commit_ttl_computed_by_adapter_not_caller():
    from backend.toolbox.ssh.remote_files import FileSession
    import time
    session = FileSession.__new__(FileSession)
    session._check = lambda: None
    session.prepared, session.prepared_at = {}, time.monotonic()
    session.manifest = {"expires_at": (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=60)).isoformat()}
    session.wire = Mock()
    permit = {"valid_until": (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=1)).isoformat()}
    session.commit(permit)
    frame = session.wire.call.call_args.args[0]
    assert 0 < frame["remaining_seconds"] <= 1
    assert frame["permit"] == permit
