"""Independent platform-neutral checks of the owner/helper trust boundary."""

from __future__ import annotations

import datetime
import json

import pytest

from backend.toolbox.ssh import file_helper as protocol
from backend.toolbox.ssh.remote_files import (
    FileSession,
    RemoteFileError,
    RemoteFiles,
    helper_command,
)


def _endpoint() -> dict:
    endpoint = {
        "host": "fixture.invalid",
        "port": 22,
        "username": "review",
        "scheduler_target": {"scheduler": "fixture"},
        "host_key": {
            "algorithm": "fixture",
            "sha256": "22" * 32,
            "verification": "known_hosts",
        },
    }
    endpoint["endpoint_digest"] = protocol.digest(endpoint)
    return endpoint


class _Channel:
    def __init__(self, owner):
        self.owner = owner
        self.reply = b""
        self.closed = False

    def settimeout(self, _value):
        pass

    def exec_command(self, command):
        self.owner.commands.append(command)
        if self.owner.flip_endpoint_on_exec:
            changed = self.owner.endpoint.copy()
            changed["host"] = "switched.invalid"
            changed["endpoint_digest"] = protocol.digest(
                {key: value for key, value in changed.items() if key != "endpoint_digest"}
            )
            self.owner.endpoint = changed

    def sendall(self, data):
        self.owner.frames.append(bytes(data))
        request = json.loads(data)
        operation = request["op"]
        if operation == "probe":
            result = {"supported": True, "read_only": True}
        elif operation == "inspect":
            result = {
                "view": request["view"],
                "canonical_path": request["path"],
                "metadata": {"type": "file", "size": 1},
            }
        elif operation == "destination":
            result = {
                "root_id": request["root"]["root_id"],
                "root_version": request["root"]["version"],
                "relative_path": request["path"],
                "parent_chain": [],
                "missing_components": [],
                "parent_item_id": None,
                "target_exists": None,
            }
        else:
            raise AssertionError(f"unexpected operation {operation}")
        self.reply = protocol.canonical({"ok": True, "data": result}) + b"\n"

    def recv_stderr_ready(self):
        return False

    def recv_ready(self):
        return bool(self.reply)

    def recv(self, _size):
        result, self.reply = self.reply, b""
        return result

    def exit_status_ready(self):
        return False

    def close(self):
        self.closed = True


class _Transport:
    def __init__(self, owner):
        self.owner = owner

    def open_session(self, timeout=None):
        assert timeout == self.owner.connect_timeout
        channel = _Channel(self.owner)
        self.owner.channels.append(channel)
        return channel


class _Client:
    def __init__(self, owner):
        self.transport = _Transport(owner)

    def get_transport(self):
        return self.transport


class _Manager:
    def __init__(self):
        self.connect_timeout = 3
        self.commands: list[str] = []
        self.frames: list[bytes] = []
        self.channels: list[_Channel] = []
        self.sftp_calls = 0
        self.flip_endpoint_on_exec = False
        self.endpoint = _endpoint()
        self._client = _Client(self)
        self.connected = True

    def connect(self):
        return self._client

    def file_endpoint(self, _scheduler_target, *, connect=True):
        assert connect in {True, False}
        return self.endpoint.copy()

    def _get_sftp(self):
        self.sftp_calls += 1
        raise AssertionError("new remote file operations must not use SFTP content paths")


def _root(endpoint: dict) -> dict:
    return {
        "root_id": "root",
        "version": 1,
        "requested_path": "/research",
        "canonical_path": "/research",
        "endpoint_digest": endpoint["endpoint_digest"],
        "identity": {"device": 1, "inode": 2, "type": "directory"},
        "ancestors": [],
        "resolution_chain": [],
    }


def _context() -> dict:
    expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)
    return {
        "action_id": "8" * 32,
        "project_id": "project",
        "task_id": "task",
        "job_key": "job",
        "attempt_id": "attempt",
        "scope_id": "scope",
        "scope_version": 1,
        "expires_at": expires_at.isoformat(),
        "source_provenance": {},
    }


def test_fixed_helper_command_and_fake_transport_never_use_sftp_for_content():
    manager = _Manager()
    files = RemoteFiles(manager, scheduler_target={"scheduler": "fixture"})
    assert files.probe()["read_only"] is True
    path = "/outside root/普通.txt"
    assert files.inspect(path, "stat")["canonical_path"] == path

    assert len(manager.commands) == 2
    assert all(command == helper_command() for command in manager.commands)
    assert all(path not in command for command in manager.commands)
    assert any(path.encode("utf-8") in frame for frame in manager.frames)
    assert manager.sftp_calls == 0


@pytest.mark.parametrize("operation", ["read", "begin"])
def test_transport_switch_before_dispatch_sends_no_user_frame(operation):
    manager = _Manager()
    files = RemoteFiles(manager, scheduler_target={"scheduler": "fixture"})
    observed_endpoint = manager.endpoint.copy()
    manager.flip_endpoint_on_exec = True

    with pytest.raises(RemoteFileError) as changed:
        if operation == "read":
            files.inspect("/user/source.txt", "stat")
        else:
            FileSession(
                files,
                {
                    "endpoint": observed_endpoint,
                    "expires_at": (
                        datetime.datetime.now(datetime.timezone.utc)
                        + datetime.timedelta(minutes=1)
                    ).isoformat(),
                },
            )
    assert changed.value.code == "ENDPOINT_CHANGED"
    assert manager.channels and all(channel.closed for channel in manager.channels)
    assert manager.frames == []


def test_plan_rejects_planner_labels_reserved_targets_and_utf8_budget():
    manager = _Manager()
    files = RemoteFiles(manager, scheduler_target={"scheduler": "fixture"})
    root = _root(manager.endpoint)
    base = {
        "item_id": "write",
        "op": "write_text",
        "destination": {"root_id": "root", "relative_path": "note.txt"},
        "text": "汉",
        "encoding": "utf-8",
        "on_conflict": "fail",
    }

    with pytest.raises(RemoteFileError) as injected:
        files.plan(
            _context(),
            [root],
            [{**base, "content_class": "normal_text"}],
            budgets={"max_operations": 1, "max_total_bytes": 3},
        )
    assert injected.value.code == "INVALID_FILE_REQUEST"

    with pytest.raises(RemoteFileError) as reserved:
        files.plan(
            _context(),
            [root],
            [{**base, "destination": {"root_id": "root", "relative_path": ".vasp-doctor-action-evil/x"}}],
            budgets={"max_operations": 1, "max_total_bytes": 3},
        )
    assert reserved.value.code == "PATH_OUTSIDE_ROOT"

    with pytest.raises(RemoteFileError) as bytes_not_characters:
        files.plan(
            _context(),
            [root],
            [base],
            budgets={"max_operations": 1, "max_total_bytes": 2},
        )
    assert bytes_not_characters.value.code == "BUDGET_EXCEEDED"

    manifest = files.plan(
        _context(),
        [root],
        [base],
        budgets={"max_operations": 1, "max_total_bytes": 3},
    )
    assert manifest["items"][0]["bytes"] == 3
    assert manifest["items"][0]["content_class"] == "unclassified_external"
    assert manager.sftp_calls == 0
