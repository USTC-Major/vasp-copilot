"""Offline result endpoint contract; no SSH, scheduler, model or user workspace."""
from __future__ import annotations

import hashlib
import base64
import copy
import json
import os
import select
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from backend.toolbox.api import create_toolbox_app
from backend.toolbox.computation import digest
from backend.toolbox.config import ExecutionConfig
from backend.toolbox.scheduler_profile import target_binding
from backend.toolbox.ssh.remote_files import RemoteFileError
from backend.toolbox.ssh.errors import SSHError


class FakeResults:
    def __init__(self, target):
        self.scheduler_target = {**target, "identity_file": target["identity_file"] or None,
                                 "known_hosts_path": target["known_hosts_path"] or None}
        self.payload = b"raw\x00OUTCAR\xff\n"
        self.calls = []
        self.failure = None
        self.on_read = None

    def read_result(self, directory, name):
        self.calls.append((directory, name))
        if self.on_read:
            self.on_read()
        if self.failure:
            raise self.failure
        return self.payload, hashlib.sha256(self.payload).hexdigest()


@pytest.fixture
def results_api(tmp_path):
    cfg = ExecutionConfig(data_dir=tmp_path, ssh_host="fixture.invalid", ssh_username="tester")
    files = FakeResults(target_binding(cfg))
    app = create_toolbox_app(root=tmp_path, settings_loader=lambda: cfg,
                             monitor_enabled=False, file_factory=lambda: files)
    with TestClient(app) as client:
        project = client.post("/api/v1/toolbox/projects", json={"name": "results"}).json()["project"]
        task = client.post(f"/api/v1/toolbox/projects/{project['id']}/tasks", json={
            "title": "job", "local_workspace": str(tmp_path), "hpc_workspace": "/remote/calc"}).json()["task"]
        svc = app.state.toolbox
        pid, tid = project["id"], task["id"]
        root = "/remote/calc"
        draft = {"job_key": "static", "attempt_id": "attempt-one", "dir": root + "/static"}
        binding = {"operation": "submit", "project_id": pid, "task_id": tid,
                   "job_key": "static", "attempt_id": "attempt-one", "draft": draft,
                   "remote_root": root,
                   "endpoint_digest": digest({"scheduler_target": target_binding(cfg), "host_key_evidence": "unknown"})}
        action = {"action_id": "a" * 32, "kind": "submit", "state": "executed",
                  "binding": binding, "binding_hash": digest(binding)}
        job = {"key": "static", "attempt_id": "attempt-one", "status": "completed",
               "submission_state": "submitted", "slurm_id": 103,
               "scheduler_target": target_binding(cfg), "submission_action_id": action["action_id"],
               "precheck": {"snapshot": {"scheduler_target": target_binding(cfg)}}, "draft": draft}
        flow = {"plan": {"jobs": [job]}, "consent": {"actions": {action["action_id"]: action}}}
        svc.store.update_task(pid, tid, flow=flow)
        path = f"/api/v1/toolbox/projects/{pid}/tasks/{tid}/results/OUTCAR"
        yield client, svc, cfg, files, pid, tid, path, flow


def fetch(client, path, *, job="static", attempt="attempt-one"):
    return client.get(path, params={"job_key": job, "attempt_id": attempt})


def test_exact_result_bytes_and_headers(results_api):
    client, _, _, files, _, _, path, _ = results_api
    response = fetch(client, path)
    assert response.status_code == 200
    assert response.content == files.payload
    assert response.headers["content-length"] == str(len(files.payload))
    assert response.headers["x-content-sha256"] == hashlib.sha256(files.payload).hexdigest()
    assert response.headers["content-disposition"] == 'attachment; filename="OUTCAR"'
    assert files.calls == [("/remote/calc/static", "OUTCAR")]


@pytest.mark.parametrize("mutate, status", [
    (lambda flow: flow["plan"]["jobs"][0].update(status="running"), 409),
    (lambda flow: flow["plan"]["jobs"][0].pop("slurm_id"), 409),
    (lambda flow: flow["plan"]["jobs"][0].update(draft={"dir": "/other"}), 409),
    (lambda flow: flow["consent"]["actions"].clear(), 409),
    (lambda flow: flow["plan"]["jobs"][0].update(scheduler_target={}), 409),
])
def test_origin_fail_closed(results_api, mutate, status):
    client, svc, _, files, pid, tid, path, flow = results_api
    mutate(flow)
    svc.store.update_task(pid, tid, flow=flow)
    response = fetch(client, path)
    assert response.status_code == status
    assert files.calls == []
    assert response.headers["content-type"].startswith("application/json")


def test_identity_and_fixed_names(results_api):
    client, _, _, files, _, _, path, _ = results_api
    assert fetch(client, path, job="wrong").status_code == 404
    assert fetch(client, path, attempt="older").status_code == 404
    assert fetch(client, path.replace("OUTCAR", "WAVECAR")).status_code == 400
    assert files.calls == []


@pytest.mark.parametrize("code, status", [("RESULT_TOO_LARGE", 413), ("RESULT_TIMEOUT", 503)])
def test_remote_failure_never_returns_partial_file(results_api, code, status):
    client, _, _, files, _, _, path, _ = results_api
    files.failure = RemoteFileError(code, "do not leak remote path")
    response = fetch(client, path)
    assert response.status_code == status
    assert files.payload not in response.content
    assert b"remote path" not in response.content


def test_connection_failure_is_safe_503(results_api):
    client, _, _, files, _, _, path, _ = results_api
    files.failure = SSHError("private endpoint detail")
    response = fetch(client, path)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "RESULT_TRANSPORT_UNAVAILABLE"
    assert b"private endpoint detail" not in response.content


def test_changed_attempt_during_read_discards_bytes(results_api):
    client, svc, _, files, pid, tid, path, flow = results_api
    def change():
        flow["plan"]["jobs"][0]["attempt_id"] = "attempt-two"
        svc.store.update_task(pid, tid, flow=flow)
    files.on_read = change
    response = fetch(client, path)
    assert response.status_code in {404, 409}
    assert files.payload not in response.content


def test_fixed_helper_frames_accept_short_reads_and_reject_wrong_order(monkeypatch):
    from backend.toolbox.ssh import remote_files
    payload = b"firstshort"
    checksum = hashlib.sha256(payload).hexdigest()
    endpoint = {"host_key": {"verification": "known_hosts"}, "endpoint_digest": "same"}
    class Manager:
        def file_endpoint(self, target):
            return copy.deepcopy(endpoint)
    class Wire:
        frames = []
        def __init__(self, manager, **kwargs):
            assert kwargs["expected_endpoint"] == endpoint
        def call(self, request, **kwargs):
            assert kwargs["timeout"] <= 60
            return self.frames.pop(0)
        def check_endpoint(self):
            pass
        def close(self):
            pass
    monkeypatch.setattr(remote_files, "_Wire", Wire)
    files = remote_files.RemoteFiles(Manager(), scheduler_target={})
    root = {"requested_path": "/remote", "canonical_path": "/remote", "resolution_chain": []}
    Wire.frames = [root, {"state": "ready", "size": len(payload)},
                   {"state": "chunk", "index": 0, "data": base64.b64encode(b"first").decode()},
                   {"state": "chunk", "index": 1, "data": base64.b64encode(b"short").decode()},
                   {"state": "complete", "length": len(payload), "sha256": checksum}]
    assert files.read_result("/remote", "OUTCAR") == (payload, checksum)
    Wire.frames = [root, {"state": "ready", "size": len(payload)},
                   {"state": "chunk", "index": 2, "data": base64.b64encode(payload).decode()}]
    with pytest.raises(RemoteFileError, match="Out-of-order"):
        files.read_result("/remote", "OUTCAR")


def test_helper_overall_deadline(monkeypatch):
    from backend.toolbox.ssh import remote_files
    class Manager:
        def file_endpoint(self, target):
            return {"host_key": {"verification": "known_hosts"}, "endpoint_digest": "same"}
    class Wire:
        def __init__(self, manager, **kwargs):
            pass
        def call(self, request, **kwargs):
            return {"requested_path": "/remote", "canonical_path": "/remote", "resolution_chain": []}
        def close(self):
            pass
    ticks = iter([0, 181])
    monkeypatch.setattr(remote_files, "_Wire", Wire)
    monkeypatch.setattr(remote_files.time, "monotonic", lambda: next(ticks))
    with pytest.raises(RemoteFileError, match="deadline"):
        remote_files.RemoteFiles(Manager(), scheduler_target={}).read_result("/remote", "OUTCAR")


def test_helper_result_session_rejects_mixed_operation(monkeypatch):
    from backend.toolbox.ssh import file_helper
    requests = iter([{"op": "result_begin", "root": {}, "name": "OUTCAR",
                      "max_bytes": file_helper.RESULT_LIMIT}, {"op": "probe"}])
    replies = []
    closed = []
    class Reader:
        def __init__(self, root, name):
            self.info = {"size": 0}
        def close(self):
            closed.append(True)
    monkeypatch.setattr(file_helper, "ResultReader", Reader)
    monkeypatch.setattr(file_helper, "_read_request", lambda timeout=None: next(requests))
    monkeypatch.setattr(file_helper, "_reply", lambda data=None, error=None: replies.append((data, error)))
    file_helper.main()
    assert replies[0][0] == {"state": "ready", "size": 0}
    assert replies[1][1].code == "PROTOCOL_ERROR"
    assert closed == [True]


@pytest.mark.skipif(os.name != "posix", reason="actual O_DIRECTORY/dir_fd/O_NOFOLLOW requires POSIX")
def test_posix_fixed_helper_subprocess_streams_exact_bytes(tmp_path):
    from pathlib import Path
    from backend.toolbox.ssh import file_helper as helper
    directory = tmp_path / "results"
    directory.mkdir()
    payload = b"\x00OUTCAR\xff" * 9000
    (directory / "OUTCAR").write_bytes(payload)
    source = Path(helper.__file__).read_text(encoding="utf-8")
    process = subprocess.Popen([sys.executable, "-I", "-u", "-c", source],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    def call(request):
        process.stdin.write(json.dumps(request).encode() + b"\n")
        process.stdin.flush()
        if not select.select([process.stdout], [], [], 5)[0]:
            raise TimeoutError("Fixed helper response exceeded 5 seconds")
        return json.loads(process.stdout.readline())
    try:
        root = call({"op": "root", "path": str(directory)})["data"]
        assert root["resolution_chain"] == []
        ready = call({"op": "result_begin", "root": root, "name": "OUTCAR",
                      "max_bytes": helper.RESULT_LIMIT})
        assert ready["data"] == {"state": "ready", "size": len(payload)}
        chunks = []
        while True:
            frame = call({"op": "result_next"})["data"]
            if frame["state"] == "complete":
                assert frame["length"] == len(payload)
                assert frame["sha256"] == hashlib.sha256(payload).hexdigest()
                break
            assert frame["index"] == len(chunks)
            chunks.append(base64.b64decode(frame["data"], validate=True))
        assert b"".join(chunks) == payload
        assert max(map(len, chunks)) <= helper.RESULT_CHUNK
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        assert process.returncode == 0, process.stderr.read().decode(errors="replace")


@pytest.mark.skipif(os.name != "posix", reason="actual O_DIRECTORY/dir_fd/O_NOFOLLOW requires POSIX")
def test_posix_result_fd_denies_links_and_detects_rename_truncate_growth(tmp_path):
    from backend.toolbox.ssh import file_helper as helper
    directory = tmp_path / "results"
    directory.mkdir()
    target = directory / "OUTCAR"
    target.write_bytes(b"initial")
    root = helper.root_evidence(str(directory))
    reader = helper.ResultReader(root, "OUTCAR")
    assert base64.b64decode(reader.next()["data"]) == b"initial"
    target.write_bytes(b"cut")
    with pytest.raises(helper.FileError, match="changed"):
        reader.next()
    reader.close()
    target.write_bytes(b"again")
    root = helper.root_evidence(str(directory))
    reader = helper.ResultReader(root, "OUTCAR")
    target.rename(directory / "old")
    target.write_bytes(b"again")
    with pytest.raises(helper.FileError, match="changed"):
        reader.next()
    reader.close()
    target.write_bytes(b"grow")
    root = helper.root_evidence(str(directory))
    reader = helper.ResultReader(root, "OUTCAR")
    target.write_bytes(b"grown file")
    with pytest.raises(helper.FileError, match="changed"):
        reader.next()
    reader.close()
    target.unlink()
    target.symlink_to(directory / "old")
    with pytest.raises((OSError, helper.FileError)):
        helper.ResultReader(helper.root_evidence(str(directory)), "OUTCAR")
    link_dir = tmp_path / "linked"
    link_dir.symlink_to(directory, target_is_directory=True)
    with pytest.raises(helper.FileError, match="link"):
        helper.ResultReader(helper.root_evidence(str(link_dir)), "OUTCAR")


@pytest.mark.skipif(os.name != "posix", reason="actual O_DIRECTORY/dir_fd/O_NOFOLLOW requires POSIX")
def test_posix_result_32mib_boundary(tmp_path):
    from backend.toolbox.ssh import file_helper as helper
    directory = tmp_path / "results"
    directory.mkdir()
    target = directory / "OUTCAR"
    with target.open("wb") as stream:
        stream.truncate(helper.RESULT_LIMIT)
    reader = helper.ResultReader(helper.root_evidence(str(directory)), "OUTCAR")
    assert reader.info["size"] == helper.RESULT_LIMIT
    expected = hashlib.sha256()
    frame_count = 0
    while True:
        frame = reader.next()
        if frame["state"] == "complete":
            assert frame["length"] == helper.RESULT_LIMIT
            assert frame["sha256"] == expected.hexdigest()
            break
        assert frame["index"] == frame_count
        chunk = base64.b64decode(frame["data"], validate=True)
        assert 0 < len(chunk) <= helper.RESULT_CHUNK
        expected.update(chunk)
        frame_count += 1
    assert frame_count == helper.RESULT_LIMIT // helper.RESULT_CHUNK
    reader.close()
    with target.open("wb") as stream:
        stream.truncate(helper.RESULT_LIMIT + 1)
    with pytest.raises(helper.FileError) as error:
        helper.ResultReader(helper.root_evidence(str(directory)), "OUTCAR")
    assert error.value.code == "RESULT_TOO_LARGE"
