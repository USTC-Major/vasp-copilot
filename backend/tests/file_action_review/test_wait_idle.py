from __future__ import annotations

import threading

import pytest

from .test_manual_http_flow import _plan, _setup_scope, _url


def _approve(api, card, scope):
    response = api.client.post(
        _url(api, f"/consents/{card['card_id']}"),
        json={"approved": True, "scope_confirmation": {
            "scope_id": scope["scope_id"], "version": scope["version"],
        }},
    )
    assert response.status_code == 202, response.text


@pytest.mark.parametrize("outcome", ["execute", "declined", "claim_error", "run_error"])
def test_wait_idle_covers_dequeued_claim_until_worker_finally(file_api, monkeypatch, outcome):
    root, scope = _setup_scope(file_api)
    planned = _plan(file_api, root, scope, key=f"wait-idle-{outcome}")
    assert planned.status_code == 200, planned.text
    card = planned.json()["pending"]
    owner = file_api.app.state.toolbox.files
    original_claim = owner._claim
    entered, release = threading.Event(), threading.Event()

    def gated_claim(*key):
        entered.set()
        assert release.wait(3), "test did not release the claim gate"
        if outcome == "claim_error":
            raise RuntimeError("injected pre-claim failure")
        return original_claim(*key)

    def fail_run(*_args):
        raise RuntimeError("injected pre-dispatch worker failure")

    monkeypatch.setattr(owner, "_claim", gated_claim)
    if outcome == "run_error":
        monkeypatch.setattr(owner, "_run", fail_run)
    try:
        _approve(file_api, card, scope)
        assert entered.wait(3), "worker did not reach the dequeued claim gate"
        # The missing phase is proven, not inferred from thread scheduling.
        with owner.guard:
            assert not owner.queue and not owner.active
        assert not owner.wait_idle(timeout=0.05), "dequeued work was reported idle before claim"
        if outcome == "declined":
            # A durable terminal decision makes the real _claim return None.
            owner._update_action(file_api.project_id, file_api.task_id, card["action_id"],
                                 lambda action: action.update(state="rejected"))
    finally:
        release.set()
    assert owner.wait_idle(3), "claim outcome left the owner permanently busy"
    saved = file_api.client.get(_url(file_api, f"/consents/{card['card_id']}")).json()["card"]
    expected = "executed" if outcome == "execute" else "rejected" if outcome == "declined" else "failed"
    assert saved["state"] == expected
    receipt = saved["receipt"]
    zero = {"operations": 0, "bytes": 0}
    amount = {"operations": 1, "bytes": len(file_api.state.source_bytes["/outside/source.txt"])}
    assert receipt.get("reservation", {}).get("state") != "reserved"
    assert receipt.get("held_unknown", zero) == zero
    assert receipt.get("spent", zero) == (amount if outcome == "execute" else zero)
    assert receipt.get("released", zero) == (amount if outcome.endswith("error") else zero)
    if outcome == "execute":
        assert file_api.state.begin_count == file_api.state.commit_count == 1
        assert file_api.state.writes == ["/review/root/job/source.txt"]
    else:
        assert file_api.state.begin_count == file_api.state.commit_count == 0
        assert file_api.state.writes == []


def test_wait_idle_covers_claim_none_requeue_until_the_next_claim_finishes(file_api, monkeypatch):
    root, scope = _setup_scope(file_api)
    planned = _plan(file_api, root, scope, key="wait-idle-requeued")
    assert planned.status_code == 200, planned.text
    card = planned.json()["pending"]
    owner = file_api.app.state.toolbox.files
    original_claim = owner._claim
    first_entered, release_first = threading.Event(), threading.Event()
    second_entered, release_second = threading.Event(), threading.Event()
    claim_calls = 0

    def claim_after_retry(*key):
        nonlocal claim_calls
        claim_calls += 1
        if claim_calls == 1:
            first_entered.set()
            assert release_first.wait(3)
            return None  # Still approved: production _loop must requeue.
        second_entered.set()
        assert release_second.wait(3)
        return original_claim(*key)

    monkeypatch.setattr(owner, "_claim", claim_after_retry)
    try:
        _approve(file_api, card, scope)
        assert first_entered.wait(3)
        assert not owner.wait_idle(timeout=0.05)
        release_first.set()
        assert second_entered.wait(3), "approved claim rejection was not requeued"
        with owner.guard:
            assert not owner.queue and not owner.active
        assert not owner.wait_idle(timeout=0.05), "retry claim created another idle gap"
    finally:
        release_first.set()
        release_second.set()
    assert owner.wait_idle(3)
    saved = file_api.client.get(_url(file_api, f"/consents/{card['card_id']}")).json()["card"]
    assert saved["state"] == "executed"
    assert saved["receipt"]["spent"] == {"operations": 1, "bytes": len(file_api.state.source_bytes["/outside/source.txt"])}
    assert saved["receipt"]["held_unknown"] == {"operations": 0, "bytes": 0}
    assert file_api.state.begin_count == file_api.state.commit_count == 1
    assert file_api.state.writes == ["/review/root/job/source.txt"]
