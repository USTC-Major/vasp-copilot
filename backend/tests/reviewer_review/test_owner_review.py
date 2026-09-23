"""Independent HTTP/owner acceptance with the existing C memory remote."""

from __future__ import annotations

import datetime as dt
import json
import threading
import time

import pytest
from backend.toolbox.ssh import file_helper as protocol

from .conftest import plan, setup_scope


def _card(api, card_id: str) -> dict:
    response = api.client.get(api.path(f"/consents/{card_id}"))
    assert response.status_code == 200, response.text
    return response.json()["card"]


def _wait_card(api, card_id: str, predicate, *, timeout: float = 4.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        card = _card(api, card_id)
        if predicate(card):
            return card
        time.sleep(0.02)
    raise AssertionError(f"review action did not reach expected state: {_card(api, card_id)}")


def _activate(api, scope):
    response = api.client.post(
        api.path(f"/computation-scopes/{scope['scope_id']}/activate"),
        json={"expected_version": scope["version"], "approval_mode": "reviewer"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["state"] == "active"


def test_explicit_activation_then_signed_review_uses_original_c_worker(review_api):
    api = review_api
    root, scope = setup_scope(api)
    assert scope["state"] == "proposed"
    _activate(api, scope)

    planned = plan(api, root, scope)
    final = _wait_card(api, planned["card_id"], lambda card: card["state"] == "executed")
    assert len(api.transport.requests) == 1
    assert api.state.begins == 1
    assert "/review/root/notes.txt" in api.state.files
    assert final["receipt"]["spent"]["operations"] == 1
    assert final["review"]["decided_by"] == "reviewer"
    assert final["review"]["decision"] == "approve"

    replay = api.client.post(
        api.path(f"/file-actions/{planned['action_id']}/review"),
        json={"binding_hash": planned["binding_hash"]},
    )
    assert replay.status_code in {200, 202}, replay.text
    assert len(api.transport.requests) == 1
    assert api.state.begins == 1
    assert api.state.commits == 1


def test_activation_does_not_review_old_card_until_explicit_request(review_api):
    api = review_api
    root, scope = setup_scope(api)
    planned = plan(api, root, scope)
    _activate(api, scope)
    assert _card(api, planned["card_id"])["state"] == "pending"
    assert api.transport.requests == []
    assert api.state.begins == 0

    requested = api.client.post(
        api.path(f"/file-actions/{planned['action_id']}/review"),
        json={"binding_hash": planned["binding_hash"]},
    )
    assert requested.status_code in {200, 202}, requested.text
    _wait_card(api, planned["card_id"], lambda card: card["state"] == "executed")
    assert len(api.transport.requests) == 1
    assert api.state.commits == 1


def test_wrong_service_signature_falls_back_to_human_without_write(review_api):
    api = review_api
    api.transport.bad_signature = True
    root, scope = setup_scope(api)
    _activate(api, scope)
    planned = plan(api, root, scope)
    fallback = _wait_card(
        api, planned["card_id"],
        lambda card: card.get("review", {}).get("state") == "needs_human",
    )
    assert fallback["state"] == "pending"
    assert api.state.begins == 0
    assert api.state.commits == 0

    manual = api.client.post(api.path(f"/consents/{planned['card_id']}"), json={"approved": True})
    assert manual.status_code == 202, manual.text
    _wait_card(api, planned["card_id"], lambda card: card["state"] == "executed")
    assert api.state.commits == 1


def test_correctly_signed_reply_for_changed_challenge_cannot_approve(review_api):
    api = review_api
    api.transport.change_nonce = True
    root, scope = setup_scope(api)
    _activate(api, scope)
    planned = plan(api, root, scope)
    fallback = _wait_card(api, planned["card_id"], lambda card: card.get("review", {}).get("state") == "needs_human")
    assert fallback["state"] == "pending"
    assert len(api.transport.requests) == 1
    assert api.state.begins == api.state.commits == 0


def test_reply_after_owner_challenge_expiry_cannot_approve(review_api, monkeypatch):
    from backend.toolbox import reviewer

    api = review_api
    api.transport.release = threading.Event()
    monkeypatch.setattr(reviewer, "timedelta", lambda **_kwargs: dt.timedelta(milliseconds=80))
    root, scope = setup_scope(api)
    _activate(api, scope)
    planned = plan(api, root, scope)
    assert api.transport.entered.wait(2)
    time.sleep(0.15)
    api.transport.release.set()
    fallback = _wait_card(api, planned["card_id"], lambda card: card.get("review", {}).get("state") == "needs_human")
    assert fallback["state"] == "pending"
    assert api.state.begins == api.state.commits == 0


@pytest.mark.parametrize("verdict,expected_state,expected_review", [
    ("reject", "rejected", "rejected"),
    ("needs_human", "pending", "needs_human"),
])
def test_valid_nonapproval_decisions_do_not_write(review_api, verdict, expected_state, expected_review):
    api = review_api
    api.transport.verdict = verdict
    root, scope = setup_scope(api)
    _activate(api, scope)
    planned = plan(api, root, scope)
    final = _wait_card(api, planned["card_id"], lambda card: card.get("review", {}).get("state") == expected_review)
    assert final["state"] == expected_state
    assert final["review"]["decision"] == verdict
    assert api.state.begins == api.state.commits == 0


@pytest.mark.parametrize("op,destination", [("symlink", "notes.txt"), ("write_text", "run.sh")])
def test_known_link_or_script_never_calls_model(review_api, op, destination):
    api = review_api
    root, scope = setup_scope(api, operations=[op], source_paths=[] if op == "write_text" else None)
    _activate(api, scope)
    planned = plan(api, root, scope, op=op, destination=destination)
    fallback = _wait_card(api, planned["card_id"], lambda card: card.get("review", {}).get("state") == "needs_human")
    assert fallback["state"] == "pending"
    assert api.transport.requests == []
    assert api.state.begins == api.state.commits == 0


@pytest.mark.parametrize("review_api", [False], indirect=True)
def test_unconfigured_reviewer_never_uses_fake_and_human_can_finish(review_api):
    api = review_api
    status = api.client.get("/api/v1/toolbox/reviewer/status")
    assert status.status_code == 200
    assert status.json().get("data", status.json())["configured"] is False
    root, scope = setup_scope(api)
    _activate(api, scope)
    planned = plan(api, root, scope)
    fallback = _wait_card(api, planned["card_id"], lambda card: card.get("review", {}).get("state") == "needs_human")
    assert fallback["state"] == "pending"
    assert api.transport.requests == []
    assert api.state.begins == 0
    approved = api.client.post(api.path(f"/consents/{planned['card_id']}"), json={"approved": True})
    assert approved.status_code == 202, approved.text
    _wait_card(api, planned["card_id"], lambda card: card["state"] == "executed")
    assert api.state.commits == 1


@pytest.mark.parametrize("intervention", ["human_reject", "scope_revoke"])
def test_human_intervention_during_slow_review_blocks_late_write(review_api, intervention):
    api = review_api
    api.transport.release = threading.Event()
    root, scope = setup_scope(api)
    _activate(api, scope)
    planned = plan(api, root, scope)
    assert api.transport.entered.wait(2), "review transport was not entered"

    started = time.monotonic()
    detail = api.client.get(api.path("/detail"))
    if intervention == "human_reject":
        changed = api.client.post(api.path(f"/consents/{planned['card_id']}"), json={"approved": False})
    else:
        changed = api.client.post(
            api.path(f"/computation-scopes/{scope['scope_id']}/revoke"),
            json={"expected_version": scope["version"]},
        )
    elapsed = time.monotonic() - started
    assert detail.status_code == 200 and changed.status_code == 200
    assert elapsed < 1.0, "owner held a task lock across model network wait"
    if intervention == "human_reject":
        assert _card(api, planned["card_id"])["state"] == "rejected"
    else:
        assert _card(api, planned["card_id"])["state"] == "expired"
    api.transport.release.set()
    if intervention == "human_reject":
        final = _wait_card(api, planned["card_id"], lambda card: card["state"] == "rejected")
    else:
        time.sleep(0.05)
        final = _card(api, planned["card_id"])
        assert final["state"] == "expired"
    assert final["state"] != "executed"
    assert api.state.begins == 0
    assert api.state.commits == 0


def test_queued_card_rejected_before_dequeue_never_calls_model(review_api):
    api = review_api
    api.transport.release = threading.Event()
    root, scope = setup_scope(api)
    _activate(api, scope)
    first = plan(api, root, scope, key="first", destination="first.txt")
    assert api.transport.entered.wait(2)
    second = plan(api, root, scope, key="second", destination="second.txt")
    _wait_card(api, second["card_id"], lambda card: card.get("review", {}).get("state") == "queued")
    rejected = api.client.post(api.path(f"/consents/{second['card_id']}"), json={"approved": False})
    assert rejected.status_code == 200, rejected.text
    api.transport.release.set()
    _wait_card(api, first["card_id"], lambda card: card["state"] == "executed")
    assert _card(api, second["card_id"])["state"] == "rejected"
    assert len(api.transport.requests) == 1
    assert api.state.commits == 1


def test_write_text_body_and_model_reason_never_reach_reviewer_projection(review_api):
    api = review_api
    body_canary = "CANARY_PRIVATE_KEY_BODY_7f09b3"
    reason_canary = "CANARY_MODEL_REASON_351ac9"
    api.transport.reason = reason_canary
    root, scope = setup_scope(api, operations=["write_text"], source_paths=[])
    _activate(api, scope)
    planned = plan(api, root, scope, op="write_text", text=body_canary)
    final = _wait_card(api, planned["card_id"], lambda card: card["state"] == "executed")
    assert len(api.transport.requests) == 1
    model_input = json.dumps(api.transport.requests[0]["review_input"], ensure_ascii=False)
    assert body_canary not in model_input
    assert reason_canary not in json.dumps(final.get("review", {}), ensure_ascii=False)
    assert reason_canary not in api.client.get(api.path("/detail")).text
    assert reason_canary not in api.client.get(api.path("/events")).text
    # The original D full manual card can still show its exact user-entered text.
    assert body_canary in json.dumps(final["binding"]["manifest"], ensure_ascii=False)


def test_nested_endpoint_config_paths_do_not_enter_model_payload(review_api):
    api = review_api
    path_canary = "/private/CANARY_SSH_IDENTITY_FILE_71d8"
    api.state.endpoint["scheduler_target"]["identity_file"] = path_canary
    api.state.endpoint["scheduler_target"]["known_hosts_path"] = "/private/CANARY_KNOWN_HOSTS_e9a2"
    api.state.endpoint["endpoint_digest"] = protocol.digest(
        {key: value for key, value in api.state.endpoint.items() if key != "endpoint_digest"}
    )
    root, scope = setup_scope(api)
    _activate(api, scope)
    planned = plan(api, root, scope)
    _wait_card(api, planned["card_id"], lambda card: card["state"] == "executed")
    model_input = json.dumps(api.transport.requests[0]["review_input"], ensure_ascii=False)
    assert path_canary not in model_input
    assert "CANARY_KNOWN_HOSTS_e9a2" not in model_input
