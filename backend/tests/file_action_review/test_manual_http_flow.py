from __future__ import annotations

import time

from .conftest import future


def _url(api, suffix=""):
    return f"/api/v1/toolbox/projects/{api.project_id}/tasks/{api.task_id}{suffix}"


def _setup_scope(api, *, max_operations=1, max_total_bytes=None):
    roots = api.client.put(
        _url(api, "/file-roots"),
        json={"expected_version": 0, "roots": [{"path": "/review/root"}]},
    )
    assert roots.status_code == 200, roots.text
    root = roots.json()["data"]["roots"][0]
    scope = api.client.post(
        _url(api, "/computation-scopes"),
        json={
            "job_key": api.job_key,
            "attempt_id": api.attempt_id,
            "root_bindings": [
                {
                    "root_id": root["root_id"],
                    "version": root["version"],
                    "destination_prefixes": ["job"],
                }
            ],
            "allowed_operations": ["copy"],
            "source_paths": ["/outside/source.txt"],
            "max_operations": max_operations,
            "max_total_bytes": max_total_bytes or len(api.state.source_bytes["/outside/source.txt"]),
            "expires_at": future(),
            "approval_mode": "human",
        },
    )
    assert scope.status_code == 201, scope.text
    return root, scope.json()["data"]


def _plan(api, root, scope, *, key="review-key", item_id="copy-one", destination="job/source.txt"):
    return api.client.post(
        _url(api, "/tools"),
        json={
            "name": "remote_file_plan",
            "args": {
                "scope_id": scope["scope_id"],
                "scope_version": scope["version"],
                "job_key": api.job_key,
                "attempt_id": api.attempt_id,
                "idempotency_key": key,
                "items": [
                    {
                        "item_id": item_id,
                        "op": "copy",
                        "source": {"absolute_path": "/outside/source.txt"},
                        "destination": {
                            "root_id": root["root_id"],
                            "relative_path": destination,
                        },
                        "on_conflict": "fail",
                    }
                ],
            },
        },
    )


def test_human_http_root_scope_plan_approve_and_receipt(file_api):
    """The C backend is usable without an AI, planner schema, or web UI."""
    root, scope = _setup_scope(file_api)
    assert scope["state"] == "proposed"
    assert scope["submit_limit"] == 0

    planned = _plan(file_api, root, scope)
    assert planned.status_code == 200, planned.text
    body = planned.json()
    card = body["pending"]
    assert card["state"] == "pending"
    assert file_api.state.begin_count == 0
    listing = file_api.client.get(_url(file_api, "/file-actions"))
    assert listing.status_code == 200, listing.text
    assert [row["action_id"] for row in listing.json()["data"]["active"]] == [
        card["action_id"]
    ]
    assert listing.json()["data"]["actions"] == []

    missing_confirmation = file_api.client.post(
        _url(file_api, f"/consents/{card['card_id']}"),
        json={"approved": True, "note": "missing scope confirmation"},
    )
    assert missing_confirmation.status_code == 409
    assert missing_confirmation.json()["error"]["code"] == "SCOPE_STALE"
    assert file_api.state.begin_count == 0

    approved = file_api.client.post(
        _url(file_api, f"/consents/{card['card_id']}"),
        json={
            "approved": True,
            "note": "independent manual approval",
            "scope_confirmation": {
                "scope_id": scope["scope_id"],
                "version": scope["version"],
            },
        },
    )
    assert approved.status_code == 202, approved.text
    assert approved.json()["card"]["state"] in {"approved", "executing"}
    assert file_api.app.state.toolbox.files.wait_idle(3)

    final = file_api.client.get(_url(file_api, f"/consents/{card['card_id']}"))
    assert final.status_code == 200, final.text
    action = final.json()["card"]
    assert action["state"] == "executed"
    assert action["receipt"]["spent"] == {
        "operations": 1,
        "bytes": len(file_api.state.source_bytes["/outside/source.txt"]),
    }
    assert action["receipt"]["held_unknown"] == {"operations": 0, "bytes": 0}
    assert len(file_api.state.writes) == 1
    preview = file_api.client.post(
        _url(file_api, "/tools"),
        json={
            "name": "remote_inspect",
            "args": {"path": "/review/root/job/source.txt", "view": "text"},
        },
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["data"]["text"] == "remote review payload\n"
    listing = file_api.client.get(_url(file_api, "/file-actions"))
    assert listing.json()["data"]["active"] == []
    assert [row["action_id"] for row in listing.json()["data"]["actions"]] == [
        card["action_id"]
    ]
    serialized = final.text
    assert "prepare_token" not in serialized
    assert "dispatch_nonce" not in serialized
    assert '"nonce"' not in serialized

    replay = file_api.client.post(
        _url(file_api, f"/consents/{card['card_id']}"),
        json={"approved": True, "note": "replay"},
    )
    assert replay.status_code == 200
    assert replay.json()["replayed"] is True
    time.sleep(0.05)
    assert len(file_api.state.writes) == 1


def test_scope_revoke_is_idempotent_and_stale_version_is_rejected(file_api):
    root, scope = _setup_scope(file_api)
    revoke = file_api.client.post(
        _url(file_api, f"/computation-scopes/{scope['scope_id']}/revoke"),
        json={"expected_version": 1, "reason": "review revoke"},
    )
    assert revoke.status_code == 200, revoke.text
    assert revoke.json()["data"]["state"] == "revoked"

    repeated = file_api.client.post(
        _url(file_api, f"/computation-scopes/{scope['scope_id']}/revoke"),
        json={"expected_version": 1, "reason": "repeat"},
    )
    assert repeated.status_code == 200
    assert repeated.json()["data"]["state"] == "revoked"

    denied = _plan(file_api, root, scope, key="revoked")
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "SCOPE_REVOKED"
    assert file_api.state.begin_count == 0
