from __future__ import annotations

import copy
import datetime as dt
import os
import sys
import threading
from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient

from backend.toolbox.config import ExecutionConfig
from backend.toolbox.ssh import file_helper as protocol


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "posix_critical: real Linux owner/helper/filesystem behavior; must execute in CI",
    )


@pytest.fixture
def required_linux_capabilities():
    """A missing declared CI capability is a failure, never a Linux skip."""
    assert sys.platform.startswith("linux") and os.name == "posix"
    assert hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY")
    assert os.open in os.supports_dir_fd
    assert os.stat in os.supports_dir_fd
    assert os.unlink in os.supports_dir_fd
    return {"o_nofollow": os.O_NOFOLLOW, "o_directory": os.O_DIRECTORY}


def endpoint() -> dict:
    value = {
        "schema_version": 1,
        "host": "review.invalid",
        "port": 22,
        "username": "review-user",
        "scheduler_target": {"scheduler": "fixture"},
        "host_key": {
            "algorithm": "ssh-ed25519",
            "sha256": "42" * 32,
            "verification": "known_hosts",
        },
        "local_config_digest": "24" * 32,
    }
    value["endpoint_digest"] = protocol.digest(value)
    return value


@dataclass
class FakeRemoteState:
    endpoint_value: dict = field(default_factory=endpoint)
    source_bytes: dict[str, bytes] = field(
        default_factory=lambda: {"/outside/source.txt": b"remote review payload\n"}
    )
    observed_overrides: dict[str, dict] = field(default_factory=dict)
    begin_count: int = 0
    prepare_count: int = 0
    commit_count: int = 0
    abort_count: int = 0
    reconcile_count: int = 0
    committed: dict[tuple[str, str], dict] = field(default_factory=dict)
    writes: list[str] = field(default_factory=list)
    block_prepare: threading.Event | None = None
    prepare_entered: threading.Event = field(default_factory=threading.Event)
    block_root: threading.Event | None = None
    root_entered: threading.Event = field(default_factory=threading.Event)
    flip_endpoint_on_inspect: bool = False
    plan_hook: object | None = None
    lose_commit_receipt: bool = False
    lose_commit_item_id: str | None = None
    corrupt_reconcile_field: str | None = None
    hpc: object = field(default_factory=object)

    def source_evidence(self, path: str) -> dict:
        payload = self.source_bytes[path]
        if path in self.observed_overrides:
            return {
                **copy.deepcopy(self.observed_overrides[path]),
                "requested_path": path,
                "canonical_path": path,
                "resolution_chain": [],
                "link_target": None,
                "content_class": protocol.content_class(path),
                "verification_level": "metadata",
                "sha256": None,
                "source_write_risk": "possible",
                "endpoint_digest": self.endpoint_value["endpoint_digest"],
            }
        return {
            "requested_path": path,
            "canonical_path": path,
            "device": 11,
            "inode": 1000 + sorted(self.source_bytes).index(path),
            "type": "file",
            "size": len(payload),
            "mtime_ns": 10,
            "ctime_ns": 10,
            "mode": 0o600,
            "resolution_chain": [],
            "link_target": None,
            "content_class": protocol.content_class(path),
            "verification_level": "metadata",
            "sha256": None,
            "source_write_risk": "possible",
            "endpoint_digest": self.endpoint_value["endpoint_digest"],
        }


class FakeSession:
    def __init__(self, state: FakeRemoteState, manifest: dict):
        self.state = state
        self.manifest = copy.deepcopy(manifest)
        self.index = 0
        self.closed = False
        self.prepared = None

    def prepare_next(self, *, should_cancel=None):
        self.state.prepare_count += 1
        self.state.prepare_entered.set()
        if self.state.block_prepare is not None:
            while not self.state.block_prepare.wait(0.01):
                if should_cancel and should_cancel():
                    self.state.abort_count += 1
                    from backend.toolbox.ssh.remote_files import RemoteFileError

                    raise RemoteFileError(
                        "ACTION_ABORTED", "cancelled by review", stage="prepare"
                    )
        item = self.manifest["items"][self.index]
        self.prepared = {
            "action_id": self.manifest["action_id"],
            "manifest_digest": self.manifest["manifest_digest"],
            "item_id": item["item_id"],
            "state": "prepared",
            "source_evidence": copy.deepcopy(item.get("source")),
            "temporary_evidence": {
                "device": 11,
                "inode": 4000 + self.index,
                "type": "file" if item["op"] != "mkdir" else "directory",
                "size": item["bytes"],
                "mtime_ns": 20,
                "ctime_ns": 20,
                "mode": item["mode"] or 0o777,
            },
            "target_evidence": None,
            "content_class": item["content_class"],
            "bytes_processed": item["bytes"],
            "sha256": "ab" * 32 if item["op"] in {"copy", "write_text"} else None,
            "verification_level": "review-fixture",
            "remote_receipt_id": (
                item["names"]["action_directory"]
                + "/"
                + item["names"]["prepared_name"]
            ),
            "published": False,
            "prepare_token": "cd" * 16,
            "prepared_digest": "ef" * 32,
            "commit_ttl_seconds": 30,
        }
        return copy.deepcopy(self.prepared)

    def commit(self, permit):
        assert self.prepared is not None
        self.state.commit_count += 1
        item = self.manifest["items"][self.index]
        root = next(
            root
            for root in self.manifest["roots"]
            if root["root_id"] == item["destination"]["root_id"]
        )
        target_path = (
            root["canonical_path"].rstrip("/")
            + "/"
            + item["destination"]["relative_path"]
        )
        receipt = {
            key: copy.deepcopy(value)
            for key, value in self.prepared.items()
            if key not in {"prepare_token", "prepared_digest", "commit_ttl_seconds"}
        }
        receipt.update(
            state="committed",
            target_evidence={
                "canonical_path": target_path,
                "endpoint_digest": self.manifest["endpoint"]["endpoint_digest"],
                "device": 11,
                "inode": 5000 + self.index,
                "type": "file" if item["op"] != "mkdir" else "directory",
                "size": item["bytes"],
                "mtime_ns": 30,
                "ctime_ns": 30,
                "mode": item["mode"] or 0o777,
            },
            remote_receipt_id=(
                item["names"]["action_directory"]
                + "/"
                + item["names"]["committed_name"]
            ),
            published=True,
        )
        self.state.committed[(self.manifest["action_id"], item["item_id"])] = copy.deepcopy(receipt)
        self.state.writes.append(target_path)
        if item["op"] == "copy":
            self.state.source_bytes[target_path] = self.state.source_bytes[
                item["source"]["requested_path"]
            ]
        elif item["op"] == "write_text":
            self.state.source_bytes[target_path] = item["text"].encode("utf-8")
        if item["op"] in {"copy", "write_text"}:
            self.state.observed_overrides[target_path] = copy.deepcopy(
                receipt["target_evidence"]
            )
        if self.state.lose_commit_receipt or self.state.lose_commit_item_id == item["item_id"]:
            from backend.toolbox.ssh.remote_files import RemoteFileError

            raise RemoteFileError(
                "ACTION_UNKNOWN",
                "review fixture lost the committed reply",
                stage="commit",
                dispatched=True,
                published="unknown",
            )
        self.index += 1
        self.prepared = None
        return receipt

    def abort(self):
        self.state.abort_count += 1
        return {"state": "aborted", "published": False, "items": [], "leftovers": []}

    def close(self):
        self.closed = True


class FakeRemoteFiles:
    def __init__(self, state: FakeRemoteState):
        self.state = state
        self.hpc = state.hpc

    def endpoint(self):
        return copy.deepcopy(self.state.endpoint_value)

    def inspect_root(self, path, *, root_id=None, version=1):
        self.state.root_entered.set()
        if self.state.block_root is not None:
            assert self.state.block_root.wait(5), "review root observation was not released"
        return {
            "requested_path": path,
            "canonical_path": path,
            "identity": {"device": 11, "inode": 2000, "type": "directory"},
            "resolution_chain": [],
            "ancestors": [{"path": path, "device": 11, "inode": 2000, "type": "directory"}],
            "mode": 0o700,
            "capabilities": {"write": True},
            "root_id": root_id,
            "version": version,
            "endpoint_digest": self.state.endpoint_value["endpoint_digest"],
        }

    def inspect(
        self,
        path,
        view="stat",
        *,
        provenance=None,
        limit=200,
        cursor=None,
        expected_evidence=None,
        expected_endpoint=None,
    ):
        del limit, cursor
        if self.state.flip_endpoint_on_inspect:
            from backend.toolbox.ssh.remote_files import RemoteFileError

            changed = copy.deepcopy(self.state.endpoint_value)
            changed["host_key"]["sha256"] = "99" * 32
            changed["endpoint_digest"] = protocol.digest(
                {key: value for key, value in changed.items() if key != "endpoint_digest"}
            )
            self.state.endpoint_value = changed
            raise RemoteFileError("ENDPOINT_CHANGED", "endpoint switched during observation")
        if expected_endpoint is not None:
            assert expected_endpoint == self.state.endpoint_value
        observed = self.state.source_evidence(path)
        if view == "stat":
            return copy.deepcopy(observed)
        assert view == "text"
        assert expected_evidence == observed
        assert provenance and provenance["expected_evidence"] == observed
        return {
            **copy.deepcopy(observed),
            "content_class": provenance.get("content_class", observed["content_class"]),
            "text": self.state.source_bytes[path].decode("utf-8"),
            "truncated": False,
        }

    def plan(self, context, roots, items, *, budgets):
        result_items = []
        for index, raw in enumerate(items):
            root = next(r for r in roots if r["root_id"] == raw["destination"]["root_id"])
            source = None
            text = raw.get("text")
            if raw["op"] in {"copy", "symlink"}:
                source = self.state.source_evidence(raw["source"]["absolute_path"])
                provenance = context["source_provenance"][raw["item_id"]]
                content_class = protocol.strict_class(
                    source["content_class"], provenance.get("content_class", "unclassified_external")
                )
                byte_count = (
                    source["size"]
                    if raw["op"] == "copy"
                    else len(source["canonical_path"].encode())
                )
            else:
                provenance = None
                content_class = protocol.content_class(raw["destination"]["relative_path"])
                byte_count = len((text or "").encode()) if raw["op"] == "write_text" else 0
            result_items.append(
                {
                    "item_id": raw["item_id"],
                    "op": raw["op"],
                    "source": source,
                    "destination": {
                        "root_id": root["root_id"],
                        "root_version": root["version"],
                        "relative_path": raw["destination"]["relative_path"],
                        "parent_chain": [
                            {
                                "path": root["canonical_path"],
                                "device": root["identity"]["device"],
                                "inode": root["identity"]["inode"],
                                "type": "directory",
                            }
                        ],
                        "missing_components": [],
                        "parent_item_id": None,
                        "target_exists": None,
                    },
                    "text": text,
                    "mode": None if raw["op"] == "symlink" else 0o700 if raw["op"] == "mkdir" else 0o600,
                    "on_conflict": "fail",
                    "content_class": content_class,
                    "names": protocol.names(context["action_id"], raw["item_id"]),
                    "bytes": byte_count,
                    "source_provenance": provenance,
                }
            )
        manifest = {
            "protocol_version": protocol.PROTOCOL,
            "policy_version": protocol.POLICY,
            **{key: copy.deepcopy(value) for key, value in context.items() if key != "source_provenance"},
            "endpoint": self.endpoint(),
            "roots": copy.deepcopy(roots),
            **copy.deepcopy(budgets),
            "items": result_items,
        }
        manifest["manifest_digest"] = protocol.digest(manifest)
        if self.state.plan_hook is not None:
            self.state.plan_hook()
        return manifest

    def _read(self, request, *, expected_endpoint=None):
        assert expected_endpoint == self.state.endpoint_value
        assert request["op"] == "destination"
        root, relative = request["root"], request["path"]
        return {
            "root_id": root["root_id"],
            "root_version": root["version"],
            "relative_path": relative,
            "parent_chain": copy.deepcopy(root["ancestors"]),
            "missing_components": [],
            "parent_item_id": None,
            "target_exists": None,
        }

    def begin(self, manifest, dispatch_context, *, dispatch_guard=None):
        del dispatch_context
        if dispatch_guard:
            with dispatch_guard("begin", None):
                self.state.begin_count += 1
        else:
            self.state.begin_count += 1
        return FakeSession(self.state, manifest)

    def reconcile(self, manifest, receipt_locator=None, *, item_id=None):
        del receipt_locator
        self.state.reconcile_count += 1
        receipt = self.state.committed.get((manifest["action_id"], item_id))
        if receipt is None:
            return {
                "state": "unknown",
                "items": [{"item_id": item_id, "state": "unknown"}],
                "read_only": True,
            }
        receipt = copy.deepcopy(receipt)
        if self.state.corrupt_reconcile_field:
            receipt[self.state.corrupt_reconcile_field] = "corrupted-review-value"
        return {
            "state": "confirmed",
            "items": [
                {
                    "item_id": item_id,
                    "state": "confirmed",
                    "remote_receipt_id": receipt["remote_receipt_id"],
                    "content_class": receipt["content_class"],
                    "sha256": receipt["sha256"],
                    "receipt": receipt,
                }
            ],
            "read_only": True,
        }


@dataclass
class FileApi:
    client: TestClient
    app: object
    state: FakeRemoteState
    project_id: str
    task_id: str
    job_key: str = "job-a"
    attempt_id: str = "attempt-a"


@pytest.fixture
def file_api(tmp_path):
    from backend.toolbox.api import create_toolbox_app

    state = FakeRemoteState()
    config = ExecutionConfig(data_dir=tmp_path, max_jobs=2, poll_interval_seconds=60)
    app = create_toolbox_app(
        root=tmp_path,
        settings_loader=lambda: config,
        monitor_enabled=False,
        file_factory=lambda: FakeRemoteFiles(state),
    )
    with TestClient(app) as client:
        project = client.post(
            "/api/v1/toolbox/projects", json={"name": "file review", "description": ""}
        ).json()["project"]
        task = client.post(
            f"/api/v1/toolbox/projects/{project['id']}/tasks",
            json={"title": "file review", "goal": "", "local_workspace": "", "hpc_workspace": ""},
        ).json()["task"]
        service = app.state.toolbox
        service.store.update_task(
            project["id"],
            task["id"],
            flow={
                "phase": "running",
                "plan": {
                    "jobs": [
                        {
                            "key": "job-a",
                            "label": "A",
                            "kind": "static",
                            "requires": [],
                            "status": "draft",
                            "attempt_id": "attempt-a",
                        }
                    ]
                },
                "consent": {"actions": {}, "computation_scopes": {}},
            },
        )
        yield FileApi(client, app, state, project["id"], task["id"])


def future(minutes=30):
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=minutes)).isoformat()
