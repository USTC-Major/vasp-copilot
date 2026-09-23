from __future__ import annotations

import copy
import threading
import time

from .conftest import future
from .test_manual_http_flow import _plan, _setup_scope, _url


def _managed_potcar_action(api) -> dict:
    observed = api.state.source_evidence("/outside/source.txt")
    return {
        "action_id": "restricted-audit",
        "kind": "remote_file",
        "state": "executed",
        "binding": {
            "manifest": {
                "endpoint": copy.deepcopy(api.state.endpoint_value),
                "manifest_digest": "77" * 32,
                "items": [],
                "roots": [],
            }
        },
        "receipt": {
            "items": [
                {
                    "item_id": "managed",
                    "state": "committed",
                    "target_evidence": copy.deepcopy(observed),
                    "content_class": "potcar",
                    "remote_receipt_id": "audit/committed.json",
                }
            ]
        },
    }


def _insert_managed_audit(api) -> None:
    action = _managed_potcar_action(api)

    def update(flow):
        flow.setdefault("consent", {}).setdefault("actions", {})[
            action["action_id"]
        ] = action

    api.app.state.toolbox.files._save(api.project_id, api.task_id, update)


def test_endpoint_switch_during_source_observation_creates_no_scope(file_api):
    roots = file_api.client.put(
        _url(file_api, "/file-roots"),
        json={"expected_version": 0, "roots": [{"path": "/review/root"}]},
    )
    assert roots.status_code == 200, roots.text
    root = roots.json()["data"]["roots"][0]
    file_api.state.flip_endpoint_on_inspect = True
    response = file_api.client.post(
        _url(file_api, "/computation-scopes"),
        json={
            "job_key": file_api.job_key,
            "attempt_id": file_api.attempt_id,
            "root_bindings": [
                {
                    "root_id": root["root_id"],
                    "version": root["version"],
                    "destination_prefixes": ["job"],
                }
            ],
            "allowed_operations": ["copy"],
            "source_paths": ["/outside/source.txt"],
            "max_operations": 1,
            "max_total_bytes": len(file_api.state.source_bytes["/outside/source.txt"]),
            "expires_at": future(),
            "approval_mode": "human",
        },
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "ENDPOINT_CHANGED"
    flow = file_api.app.state.toolbox.require_task(
        file_api.project_id, file_api.task_id
    )["flow"]
    assert not (flow.get("consent") or {}).get("computation_scopes")


def test_scope_generation_cas_rejects_new_restricted_audit(file_api, monkeypatch):
    roots = file_api.client.put(
        _url(file_api, "/file-roots"),
        json={"expected_version": 0, "roots": [{"path": "/review/root"}]},
    )
    root = roots.json()["data"]["roots"][0]
    owner = file_api.app.state.toolbox.files
    original = owner._observe
    inserted = False

    def interleaved(files, path):
        nonlocal inserted
        result = original(files, path)
        if not inserted:
            inserted = True
            _insert_managed_audit(file_api)
        return result

    monkeypatch.setattr(owner, "_observe", interleaved)
    response = file_api.client.post(
        _url(file_api, "/computation-scopes"),
        json={
            "job_key": file_api.job_key,
            "attempt_id": file_api.attempt_id,
            "root_bindings": [
                {
                    "root_id": root["root_id"],
                    "version": root["version"],
                    "destination_prefixes": ["job"],
                }
            ],
            "allowed_operations": ["copy"],
            "source_paths": ["/outside/source.txt"],
            "max_operations": 1,
            "max_total_bytes": len(file_api.state.source_bytes["/outside/source.txt"]),
            "expires_at": future(),
            "approval_mode": "human",
        },
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "SOURCE_CHANGED"
    flow = file_api.app.state.toolbox.require_task(
        file_api.project_id, file_api.task_id
    )["flow"]
    scopes = (flow.get("consent") or {}).get("computation_scopes", {})
    assert scopes == {}


def test_plan_generation_cas_rejects_audit_inserted_after_observation(file_api):
    root, scope = _setup_scope(file_api)
    file_api.state.plan_hook = lambda: _insert_managed_audit(file_api)
    response = _plan(file_api, root, scope, key="generation-race")
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "SOURCE_CHANGED"
    flow = file_api.app.state.toolbox.require_task(
        file_api.project_id, file_api.task_id
    )["flow"]
    remote = [
        action
        for action in (flow.get("consent") or {}).get("actions", {}).values()
        if action.get("kind") == "remote_file"
    ]
    assert [action["action_id"] for action in remote] == ["restricted-audit"]


def test_two_workers_do_not_drop_second_same_task_claim(file_api, monkeypatch):
    payload_bytes = len(file_api.state.source_bytes["/outside/source.txt"])
    root, scope = _setup_scope(
        file_api, max_operations=2, max_total_bytes=payload_bytes * 2
    )
    first = _plan(
        file_api,
        root,
        scope,
        key="claim-one",
        item_id="one",
        destination="job/one.txt",
    ).json()["pending"]
    second = _plan(
        file_api,
        root,
        scope,
        key="claim-two",
        item_id="two",
        destination="job/two.txt",
    ).json()["pending"]

    owner = file_api.app.state.toolbox.files
    original = owner._claim
    barrier = threading.Barrier(2)
    collision_lock = threading.Lock()
    collision_calls = 0

    def colliding_claim(*args):
        nonlocal collision_calls
        with collision_lock:
            collision_calls += 1
            collide = collision_calls <= 2
        if collide:
            barrier.wait(timeout=3)
        return original(*args)

    monkeypatch.setattr(owner, "_claim", colliding_claim)
    file_api.state.block_prepare = threading.Event()
    for card in (first, second):
        response = file_api.client.post(
            _url(file_api, f"/consents/{card['card_id']}"),
            json={
                "approved": True,
                "scope_confirmation": {
                    "scope_id": scope["scope_id"],
                    "version": scope["version"],
                },
            },
        )
        assert response.status_code == 202, response.text

    assert file_api.state.prepare_entered.wait(3)
    file_api.state.block_prepare.set()
    deadline = time.monotonic() + 5
    states = []
    while time.monotonic() < deadline:
        states = [
            file_api.client.get(_url(file_api, f"/consents/{card['card_id']}"))
            .json()["card"]["state"]
            for card in (first, second)
        ]
        if states == ["executed", "executed"]:
            break
        time.sleep(0.01)
    assert states == ["executed", "executed"]
    assert sorted(file_api.state.writes) == [
        "/review/root/job/one.txt",
        "/review/root/job/two.txt",
    ]


def test_corrupt_remote_receipt_cannot_resolve_unknown(file_api):
    root, scope = _setup_scope(file_api)
    action = _plan(file_api, root, scope, key="unknown-corrupt").json()["pending"]
    file_api.state.lose_commit_receipt = True
    approved = file_api.client.post(
        _url(file_api, f"/consents/{action['card_id']}"),
        json={
            "approved": True,
            "scope_confirmation": {
                "scope_id": scope["scope_id"],
                "version": scope["version"],
            },
        },
    )
    assert approved.status_code == 202, approved.text
    assert file_api.app.state.toolbox.files.wait_idle(3)
    saved = file_api.client.get(
        _url(file_api, f"/consents/{action['card_id']}")
    ).json()["card"]
    assert saved["state"] == "unknown"

    file_api.state.lose_commit_receipt = False
    file_api.state.corrupt_reconcile_field = "action_id"
    reconciled = file_api.client.post(
        _url(file_api, f"/file-actions/{action['action_id']}/reconcile"), json={}
    )
    assert reconciled.status_code == 409, reconciled.text
    again = file_api.client.get(
        _url(file_api, f"/consents/{action['card_id']}")
    ).json()["card"]
    assert again["state"] == "unknown"
    assert again["receipt"]["held_unknown"] == {
        "operations": 1,
        "bytes": len(file_api.state.source_bytes["/outside/source.txt"]),
    }
