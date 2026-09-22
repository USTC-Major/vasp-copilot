"""Real fixed helper subprocess behind an in-memory SSH channel; no network."""
import datetime
import os
import select
import shlex
import subprocess
import sys
import uuid

import pytest

from backend.toolbox.ssh.connection import SSHManager
from backend.toolbox.ssh.remote_files import RemoteFileError, RemoteFiles

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="actual Linux helper execution required")


class Key:
    def get_name(self): return "fixture-key"
    def asbytes(self): return b"PUBLIC FIXTURE KEY"


class Channel:
    def __init__(self, transport):
        self.transport, self.process = transport, None
    def settimeout(self, value): pass
    def exec_command(self, command):
        argv = shlex.split(command)
        assert argv[:4] == ["python3", "-I", "-u", "-c"] and len(argv) == 5
        self.transport.commands.append(command)
        self.process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
    def sendall(self, data):
        self.transport.requests.append(data)
        self.process.stdin.write(data)
        self.process.stdin.flush()
    def recv_ready(self): return bool(select.select([self.process.stdout], [], [], 0)[0])
    def recv_stderr_ready(self): return bool(select.select([self.process.stderr], [], [], 0)[0]) and self.process.poll() is None
    def recv(self, size): return os.read(self.process.stdout.fileno(), size)
    def recv_stderr(self, size): return os.read(self.process.stderr.fileno(), size)
    def exit_status_ready(self): return self.process.poll() is not None
    def close(self):
        if self.process:
            self.process.stdin.close()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                self.process.wait(timeout=3)
            self.process.stdout.close()
            self.process.stderr.close()


class Client:
    def __init__(self): self.commands, self.requests, self.active = [], [], True
    def connect(self, **kwargs): pass
    def close(self): self.active = False
    def get_transport(self): return self
    def get_host_keys(self): return self
    def lookup(self, hostname): return {"fixture-key": Key()}
    def get_remote_server_key(self): return Key()
    def is_active(self): return self.active
    def set_keepalive(self, value): pass
    def open_session(self, **kwargs): return Channel(self)


def future(seconds=300):
    return (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=seconds)).isoformat()


def test_adapter_plans_readonly_then_commits_four_operations_without_local_copy(tmp_path):
    root, source_root = tmp_path / "write", tmp_path / "readonly"
    root.mkdir()
    source_root.mkdir()
    source = source_root / "WAVECAR"
    data = b"SOURCE_BYTES_NEVER_ON_LOCAL_WIRE\0" * 90000
    source.write_bytes(data)
    source.chmod(0o440)
    client = Client()
    manager = SSHManager(client_factory=lambda: client)
    manager.switch(host="fixture.invalid", username="tester")
    files = RemoteFiles(manager, scheduler_target={"scheduler": "fixture"})
    assert files.probe()["supported"]
    root_info = files.inspect_root(str(root), root_id="r")
    assert list(root.iterdir()) == []
    context = {"project_id": "p", "task_id": "t", "job_key": "j", "attempt_id": "attempt",
               "scope_id": "file-scope", "scope_version": 1, "expires_at": future(),
               "source_provenance": {"copy": {"origin": "external_source"}, "link": {"origin": "external_source"}}}
    def destination(path): return {"root_id": "r", "relative_path": path}
    items = [
        {"item_id": "dir", "op": "mkdir", "destination": destination("job")},
        {"item_id": "text", "op": "write_text", "destination": destination("job/run.sh"), "text": "#!/bin/sh\necho fixture\n"},
        {"item_id": "copy", "op": "copy", "source": {"absolute_path": str(source)}, "destination": destination("job/copied.txt")},
        {"item_id": "link", "op": "symlink", "source": {"absolute_path": str(source)}, "destination": destination("job/linked.txt")},
    ]
    manifest = files.plan(context, [root_info], items, budgets={"max_operations": 4, "max_total_bytes": len(data) + 10000})
    assert list(root.iterdir()) == []
    fields = ("action_id", "manifest_digest", "project_id", "task_id", "job_key", "attempt_id", "scope_id", "scope_version")
    dispatch = {**{k: manifest[k] for k in fields}, "binding_hash": "a" * 64, "dispatch_nonce": uuid.uuid4().hex}
    receipts = []
    with files.begin(manifest, dispatch) as session:
        for item in manifest["items"]:
            prepared = session.prepare_next()
            permit = {**{k: manifest[k] for k in ("action_id", "manifest_digest", "job_key", "attempt_id", "scope_id", "scope_version")},
                      "item_id": item["item_id"], "endpoint_digest": manifest["endpoint"]["endpoint_digest"],
                      "root_id": "r", "root_version": 1, "prepared_digest": prepared["prepared_digest"],
                      "prepare_token": prepared["prepare_token"], "valid_until": future(20)}
            receipts.append(session.commit(permit))
        assert session.abort()["published"] is True
    assert (root / "job/copied.txt").read_bytes() == data
    assert (root / "job/linked.txt").is_symlink()
    assert (root / "job/run.sh").stat().st_mode & 0o777 == 0o600
    assert (root / "job").stat().st_mode & 0o777 == 0o700
    assert source.stat().st_mode & 0o777 == 0o440
    assert all(b"SOURCE_BYTES_NEVER_ON_LOCAL_WIRE" not in request for request in client.requests)
    assert files.reconcile(manifest)["state"] == "confirmed"
    assert receipts[2]["content_class"] == "large_vasp"
    with pytest.raises(RemoteFileError) as caught:
        files.begin(manifest, dispatch)
    assert caught.value.code == "ACTION_ALREADY_EXISTS"
    # Renaming sensitive content does not grant a preview when owner restores provenance.
    evidence = receipts[2]["target_evidence"]
    provenance = {**evidence, "origin": "managed_output", "content_class": receipts[2]["content_class"],
                  "receipt_id": receipts[2]["remote_receipt_id"], "manifest_digest": manifest["manifest_digest"]}
    with pytest.raises(RemoteFileError) as caught:
        files.inspect(str(root / "job/copied.txt"), "text", provenance=provenance)
    assert caught.value.code == "CONTENT_READ_DENIED"
