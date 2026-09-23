from __future__ import annotations

import copy
import datetime as dt
import hashlib
import hmac
import json
import threading
from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient

from backend.tests.file_action_integration.test_owner import MemoryRemote, World, meta
from backend.toolbox.config import ExecutionConfig


SECRET = "qa-reviewer-shared-secret-32-bytes-minimum"


def canonical(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


@dataclass
class ReviewStub:
    verdict: str = "approve"
    reason: str = "ordinary file operation"
    bad_signature: bool = False
    change_nonce: bool = False
    entered: threading.Event = field(default_factory=threading.Event)
    release: threading.Event | None = None
    requests: list[dict] = field(default_factory=list)

    def __call__(self, request: dict) -> dict:
        self.requests.append(copy.deepcopy(request))
        self.entered.set()
        if self.release is not None:
            assert self.release.wait(5), "review stub was not released"
        decision = {
            "decision": self.verdict,
            "reason": self.reason,
            "checks": {
                "scope_match": True,
                "manifest_match": True,
                "ordinary_file_only": True,
                "no_scientific_claim": True,
                "no_execution": True,
            },
        }
        signed = {
            "protocol_version": request["protocol_version"],
            "challenge": copy.deepcopy(request["challenge"]),
            "decision": decision,
        }
        if self.change_nonce:
            signed["challenge"]["nonce"] = "f" * 64
        signed["signature"] = hmac.new(SECRET.encode(), canonical(signed), hashlib.sha256).hexdigest()
        if self.bad_signature:
            signed["signature"] = "0" * 64
        return signed


@dataclass
class ReviewApi:
    client: TestClient
    app: object
    state: World
    transport: ReviewStub
    project_id: str
    task_id: str
    job_key: str = "job-a"
    attempt_id: str = "attempt-a"

    def path(self, suffix: str = "") -> str:
        return f"/api/v1/toolbox/projects/{self.project_id}/tasks/{self.task_id}{suffix}"


@pytest.fixture
def review_api(tmp_path, monkeypatch, request):
    from backend.toolbox.api import create_toolbox_app

    enabled = getattr(request, "param", True)
    for key in ("VASP_REVIEWER_ENABLED", "VASP_REVIEWER_SHARED_SECRET", "VASP_REVIEWER_URL"):
        monkeypatch.delenv(key, raising=False)
    if enabled:
        monkeypatch.setenv("VASP_REVIEWER_ENABLED", "true")
        monkeypatch.setenv("VASP_REVIEWER_SHARED_SECRET", SECRET)
        monkeypatch.setenv("VASP_REVIEWER_URL", "http://127.0.0.1:8500")
    state = World()
    state.files["/outside/source.txt"] = {
        "metadata": meta(51, size=len(b"remote review payload\n")),
        "text": "remote review payload\n",
    }
    transport = ReviewStub()
    config = ExecutionConfig(data_dir=tmp_path, max_jobs=2, poll_interval_seconds=60)
    app = create_toolbox_app(
        root=tmp_path,
        settings_loader=lambda: config,
        monitor_enabled=False,
        file_factory=lambda: MemoryRemote(state),
        reviewer_transport=transport,
    )
    with TestClient(app) as client:
        project = client.post("/api/v1/toolbox/projects", json={"name": "reviewer qa"}).json()["project"]
        task = client.post(
            f"/api/v1/toolbox/projects/{project['id']}/tasks",
            json={"title": "reviewer qa", "goal": "", "local_workspace": "", "hpc_workspace": ""},
        ).json()["task"]
        app.state.toolbox.store.update_task(
            project["id"],
            task["id"],
            flow={
                "phase": "running",
                "plan": {
                    "jobs": [
                        {"key": "job-a", "label": "A", "kind": "static", "requires": [], "status": "draft", "attempt_id": "attempt-a"}
                    ]
                },
                "consent": {"actions": {}, "computation_scopes": {}},
            },
        )
        yield ReviewApi(client, app, state, transport, project["id"], task["id"])


def future(minutes: int = 30) -> str:
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=minutes)).isoformat()


def setup_scope(api: ReviewApi, *, mode: str = "reviewer", operations: list[str] | None = None, source_paths: list[str] | None = None):
    roots = api.client.put(api.path("/file-roots"), json={"expected_version": 0, "roots": [{"path": "/review/root"}]})
    assert roots.status_code == 200, roots.text
    root = roots.json()["data"]["roots"][0]
    scope = api.client.post(
        api.path("/computation-scopes"),
        json={
            "job_key": api.job_key,
            "attempt_id": api.attempt_id,
            "root_bindings": [{"root_id": root["root_id"], "version": root["version"], "destination_prefixes": [""]}],
            "allowed_operations": operations or ["copy"],
            "source_paths": ["/outside/source.txt"] if source_paths is None else source_paths,
            "max_operations": 2,
            "max_total_bytes": 4096,
            "expires_at": future(),
            "approval_mode": mode,
        },
    )
    assert scope.status_code == 201, scope.text
    return root, scope.json()["data"]


def plan(api: ReviewApi, root: dict, scope: dict, *, op: str = "copy", text: str | None = None, key: str = "qa-plan", destination: str = "notes.txt"):
    item = {
        "item_id": "one",
        "op": op,
        "destination": {"root_id": root["root_id"], "relative_path": destination},
        "on_conflict": "fail",
    }
    if op in {"copy", "symlink"}:
        item["source"] = {"absolute_path": "/outside/source.txt"}
    if op == "write_text":
        item["text"] = text or "review fixture"
    result = api.client.post(
        api.path("/tools"),
        json={
            "name": "remote_file_plan",
            "args": {
                "scope_id": scope["scope_id"],
                "scope_version": scope["version"],
                "job_key": api.job_key,
                "attempt_id": api.attempt_id,
                "idempotency_key": key,
                "items": [item],
            },
        },
    )
    assert result.status_code == 200, result.text
    return result.json()["pending"]
