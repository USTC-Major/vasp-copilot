from __future__ import annotations

from backend.toolbox.projects import ProjectStore

from .conftest import future
from .test_manual_http_flow import _plan, _url


def test_unknown_second_item_releases_proven_unstarted_third_item(file_api):
    payload_bytes = len(file_api.state.source_bytes["/outside/source.txt"])
    roots = file_api.client.put(
        _url(file_api, "/file-roots"),
        json={"expected_version": 0, "roots": [{"path": "/review/root"}]},
    )
    assert roots.status_code == 200, roots.text
    root = roots.json()["data"]["roots"][0]
    scope_response = file_api.client.post(
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
            "max_operations": 3,
            "max_total_bytes": payload_bytes * 3,
            "expires_at": future(),
            "approval_mode": "human",
        },
    )
    assert scope_response.status_code == 201, scope_response.text
    scope = scope_response.json()["data"]
    items = [
        {
            "item_id": item_id,
            "op": "copy",
            "source": {"absolute_path": "/outside/source.txt"},
            "destination": {
                "root_id": root["root_id"],
                "relative_path": f"job/{item_id}.txt",
            },
            "on_conflict": "fail",
        }
        for item_id in ("first", "uncertain", "unstarted")
    ]
    planned = file_api.client.post(
        _url(file_api, "/tools"),
        json={
            "name": "remote_file_plan",
            "args": {
                "scope_id": scope["scope_id"],
                "scope_version": scope["version"],
                "job_key": file_api.job_key,
                "attempt_id": file_api.attempt_id,
                "idempotency_key": "three-item-unknown",
                "items": items,
            },
        },
    )
    assert planned.status_code == 200, planned.text
    action = planned.json()["pending"]
    file_api.state.lose_commit_item_id = "uncertain"
    approved = file_api.client.post(
        _url(file_api, f"/consents/{action['action_id']}"),
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
        _url(file_api, f"/consents/{action['action_id']}")
    ).json()["card"]
    assert saved["state"] == "unknown"
    assert saved["receipt"]["spent"] == {
        "operations": 1,
        "bytes": payload_bytes,
    }
    assert saved["receipt"]["held_unknown"] == {
        "operations": 1,
        "bytes": payload_bytes,
    }
    assert saved["receipt"]["released"] == {
        "operations": 1,
        "bytes": payload_bytes,
    }
    assert file_api.state.writes == [
        "/review/root/job/first.txt",
        "/review/root/job/uncertain.txt",
    ]
    assert saved["receipt"]["item_outcomes"]["unstarted"] == {
        "state": "not_executed",
        "evidence": "worker_stopped_before_prepare",
    }
    released_target = _plan(
        file_api,
        root,
        scope,
        key="reuse-released-third-while-unknown",
        item_id="replacement",
        destination="job/unstarted.txt",
    )
    assert released_target.status_code == 200, released_target.text
    held_target = _plan(
        file_api,
        root,
        scope,
        key="reject-held-second-while-unknown",
        item_id="held-replacement",
        destination="job/uncertain.txt",
    )
    assert held_target.status_code == 409, held_target.text
    assert held_target.json()["error"]["code"] == "DESTINATION_CONFLICT"

    # Startup recovery preserves the durable negative fact; it must not infer
    # all missing records are unknown and re-hold the third item's budget.
    restarted_store = ProjectStore(file_api.app.state.toolbox.root)
    restarted = restarted_store.get_task(file_api.project_id, file_api.task_id)["flow"]
    recovered = restarted["consent"]["actions"][action["action_id"]]
    assert recovered["state"] == "unknown"
    assert recovered["receipt"]["released"] == {
        "operations": 1,
        "bytes": payload_bytes,
    }
    assert recovered["receipt"]["item_outcomes"]["unstarted"]["state"] == "not_executed"

    begins, commits = file_api.state.begin_count, file_api.state.commit_count
    reconciled = file_api.client.post(
        _url(file_api, f"/file-actions/{action['action_id']}/reconcile"), json={}
    )
    assert reconciled.status_code == 200, reconciled.text
    final = reconciled.json()["data"]
    assert final["state"] == "failed"
    assert final["receipt"]["spent"] == {
        "operations": 2,
        "bytes": payload_bytes * 2,
    }
    assert final["receipt"]["held_unknown"] == {"operations": 0, "bytes": 0}
    assert final["receipt"]["released"] == {
        "operations": 1,
        "bytes": payload_bytes,
    }
    assert final["receipt"]["item_outcomes"]["unstarted"]["state"] == "not_executed"
    assert file_api.state.begin_count == begins
    assert file_api.state.commit_count == commits
    assert file_api.state.writes == [
        "/review/root/job/first.txt",
        "/review/root/job/uncertain.txt",
    ]
