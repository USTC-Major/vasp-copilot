from __future__ import annotations

import hashlib
import copy
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from backend.tests.valid_vasp_inputs import FILES as VALID_INPUTS


@dataclass
class ApiHarness:
    client: TestClient
    app: object
    root: Path
    workspace: Path
    hpc: "FakeHPC"


class FakeHPC:
    """Deterministic in-memory execution adapter; never opens a network socket."""

    execution_mode = "Fake"

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.run_calls: list[tuple[str, str | None]] = []
        self.write_calls: list[str] = []
        self.queue_rows: list[str] = []
        self.job_id = 7300
        self.query_error: Exception | None = None
        self.submit_error: Exception | None = None

    def run(self, command, *, cwd=None, timeout=None):
        del timeout
        self.run_calls.append((command, cwd))
        if command.startswith("squeue"):
            if self.query_error is not None:
                raise self.query_error
            return 0, "\n".join(self.queue_rows), ""
        if command.startswith("sbatch"):
            if self.submit_error is not None:
                raise self.submit_error
            self.job_id += 1
            return 0, f"Submitted batch job {self.job_id}\n", ""
        if command.startswith("mkdir -p"):
            return 0, "", ""
        return 0, "", ""

    def stat(self, remote):
        if remote in self.files:
            return {"size": len(self.files[remote]), "mtime": 1}
        prefix = remote.rstrip("/") + "/"
        return {} if any(path.startswith(prefix) for path in self.files) else None

    def list_dir_info(self, remote):
        prefix = remote.rstrip("/") + "/"
        entries = {}
        for path, payload in self.files.items():
            if not path.startswith(prefix):
                continue
            tail = path[len(prefix):]
            name, separator, _rest = tail.partition("/")
            if name:
                entries[name] = {
                    "name": name,
                    "is_dir": bool(separator),
                    "size": 0 if separator else len(payload),
                }
        return list(entries.values())

    def read_file(self, remote, *, max_bytes=None):
        if remote not in self.files:
            raise FileNotFoundError(remote)
        payload = self.files[remote]
        return payload[:max_bytes] if max_bytes else payload

    def write_file(self, remote, data):
        payload = bytes(data)
        self.write_calls.append(remote)
        self.files[remote] = payload
        return len(payload)

    def atomic_write_file(self, remote, data, *, expected_sha256):
        payload = bytes(data)
        if hashlib.sha256(payload).hexdigest() != expected_sha256:
            raise RuntimeError("hash mismatch")
        return self.write_file(remote, payload)

    def mkdir(self, remote):
        del remote

    def sha256_file(self, remote):
        return hashlib.sha256(self.files[remote]).hexdigest()

    @property
    def submit_count(self) -> int:
        return sum(command.startswith("sbatch") for command, _cwd in self.run_calls)

    @property
    def query_count(self) -> int:
        return sum(command.startswith("squeue") for command, _cwd in self.run_calls)


class FakeRemoteFiles:
    """Identity-only adapter for the legacy upload bridge in this offline suite."""

    def __init__(self, hpc: FakeHPC) -> None:
        from backend.toolbox.ssh import file_helper

        self.hpc = hpc
        self._endpoint = {
            "schema_version": 1,
            "host": "offline-review.invalid",
            "port": 22,
            "username": "offline-review",
            "scheduler_target": {"scheduler": "fixture"},
            "host_key": {
                "algorithm": "ssh-ed25519",
                "sha256": "31" * 32,
                "verification": "known_hosts",
            },
            "local_config_digest": "13" * 32,
        }
        self._endpoint["endpoint_digest"] = file_helper.digest(self._endpoint)

    def endpoint(self):
        return copy.deepcopy(self._endpoint)

    def inspect_root(self, path, *, root_id=None, version=1):
        identity = {"device": 7, "inode": 700, "type": "directory"}
        return {
            "requested_path": path,
            "canonical_path": path,
            "identity": identity,
            "resolution_chain": [],
            "ancestors": [{"path": path, **identity}],
            "root_id": root_id,
            "version": version,
            "endpoint_digest": self._endpoint["endpoint_digest"],
        }

    def _read(self, request, *, expected_endpoint=None):
        assert expected_endpoint == self._endpoint
        assert request["op"] == "destination"
        root, relative = request["root"], request["path"]
        target = root["canonical_path"].rstrip("/") + "/" + relative
        target_exists = None
        if target in self.hpc.files:
            target_exists = {
                "device": 7,
                "inode": 1000 + sorted(self.hpc.files).index(target),
                "type": "file",
                "size": len(self.hpc.files[target]),
                "mtime_ns": 1,
                "ctime_ns": 1,
                "mode": 0o600,
            }
        return {
            "root_id": root["root_id"],
            "root_version": root["version"],
            "relative_path": relative,
            "parent_chain": copy.deepcopy(root["ancestors"]),
            "missing_components": [],
            "parent_item_id": None,
            "target_exists": target_exists,
        }


def create_isolated_app(root: Path, hpc: FakeHPC | None = None, *,
                        monitor_enabled: bool = False):
    """Use the architecture-owned public app factory with an isolated root."""
    from backend.toolbox.api import create_toolbox_app
    from backend.toolbox.config import ExecutionConfig
    from backend.toolbox.orchestrator import Orchestrator

    fake = hpc or FakeHPC()
    config = ExecutionConfig(
        data_dir=root,
        max_jobs=2,
        poll_interval_seconds=60,
        ssh_username="offline-review",
    )
    app = create_toolbox_app(
        root=root,
        settings_loader=lambda: config,
        orch_factory=lambda: Orchestrator(config, hpc=fake),
        monitor_enabled=monitor_enabled,
        file_factory=lambda: FakeRemoteFiles(fake),
    )
    return app, fake


@pytest.fixture
def api(tmp_path, monkeypatch):
    root = tmp_path / "isolated-home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for name in ("POSCAR", "INCAR", "KPOINTS"):
        (workspace / name).write_bytes(VALID_INPUTS[name])

    monkeypatch.setenv("VASP_AI_HOME", str(root))
    monkeypatch.setenv("ENABLE_AI_MODE", "false")
    for key in (
        "OPENAI_API_KEY",
        "AI_MODE_LLM_API_KEY",
        "TOOLBOX_SSH_HOST",
        "AI_MODE_SSH_HOST",
    ):
        monkeypatch.delenv(key, raising=False)

    app, hpc = create_isolated_app(root)
    with TestClient(app) as client:
        yield ApiHarness(client=client, app=app, root=root,
                         workspace=workspace, hpc=hpc)


def create_project_task(api: ApiHarness, *, hpc_workspace="/review/calc"):
    project_response = api.client.post(
        "/api/v1/toolbox/projects",
        json={"name": "offline contract project", "description": "D0-Q2"},
    )
    assert project_response.status_code == 200, project_response.text
    project = project_response.json()["project"]
    task_response = api.client.post(
        f"/api/v1/toolbox/projects/{project['id']}/tasks",
        json={
            "title": "manual path",
            "goal": "offline deterministic validation",
            "local_workspace": str(api.workspace),
            "hpc_workspace": hpc_workspace,
        },
    )
    assert task_response.status_code == 200, task_response.text
    task = task_response.json()["task"]
    return project, task


def call_tool(api: ApiHarness, project_id: str, task_id: str,
              name: str, args: dict | None = None):
    response = api.client.post(
        f"/api/v1/toolbox/projects/{project_id}/tasks/{task_id}/tools",
        json={"name": name, "args": args or {}},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mode"] == "toolbox"
    assert body["task_id"] == task_id
    return body


def resolve_card(api: ApiHarness, project_id: str, task_id: str,
                 card_id: str, approved: bool):
    response = api.client.post(
        f"/api/v1/toolbox/projects/{project_id}/tasks/{task_id}/consents/{card_id}",
        json={"approved": approved, "note": "D0-Q2 offline review"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mode"] == "toolbox"
    assert body["task_id"] == task_id
    return body
