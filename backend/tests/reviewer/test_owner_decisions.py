from __future__ import annotations

import hashlib
import threading
from datetime import datetime, timedelta, timezone

import pytest

from backend.toolbox import consent
from backend.toolbox.file_actions import FileActions
from backend.toolbox.reviewer import Reviewer
from backend.toolbox.review_transport import PROTOCOL, signature
from backend.toolbox.storage import recover_actions
from backend.ai_mode.reviewer import ReviewError, review_request


def test_human_can_reject_pending_proposed_reviewer_without_activating_scope():
    binding = {"scope_id": "s", "scope_version": 1}
    action = {"kind": "remote_file", "state": "pending", "binding": binding,
              "binding_hash": consent._binding_hash(binding),
              "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}
    flow = {"consent": {"actions": {"a": action},
                        "computation_scopes": {"s": {"approval_mode": "reviewer", "state": "proposed"}}}}
    files = object.__new__(FileActions)
    decided = files._decide_in_flow(flow, "a", False, "用户拒绝", None, reviewer=False)
    assert decided["state"] == "rejected"
    assert flow["consent"]["computation_scopes"]["s"]["state"] == "proposed"


def test_duplicate_signed_approval_cannot_reenqueue_old_approved_card():
    nonce = "n" * 64
    challenge = {"challenge_id": "c", "nonce": nonce, "owner_run_id": "old",
                 "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()}
    action = {"kind": "remote_file", "state": "approved",
              "_review_private": {"status": "consumed", "owner_run_id": "old",
                                  "challenge_id": "c", "nonce_hash": hashlib.sha256(nonce.encode()).hexdigest()}}
    flow = {"consent": {"actions": {"a": action}}}

    class Files:
        guard = threading.RLock()
        def __init__(self):
            self.enqueues = 0
        def _save(self, project, task, change):
            return change(flow)
        def _enqueue(self, project, task, action_id):
            self.enqueues += 1

    files = Files()
    reviewer = Reviewer(files)
    reviewer.run_id = "old"
    decision = {"decision": "approve", "reason": "ok",
                "checks": {key: True for key in ("scope_match", "manifest_match",
                    "ordinary_file_only", "no_scientific_claim", "no_execution")}}
    response = {"protocol_version": PROTOCOL, "challenge": challenge, "decision": decision,
                "signature": signature("s" * 32, PROTOCOL, challenge, decision)}
    reviewer._finish("p", "t", "a", challenge, response, "s" * 32)
    assert files.enqueues == 0
    assert action["_review_private"]["status"] == "consumed"


def test_owner_restart_invalidates_issued_review_without_reapproving():
    action = {"kind": "remote_file", "state": "pending",
              "review": {"state": "reviewing", "requested_at": "earlier"},
              "_review_private": {"status": "issued", "owner_run_id": "old"}}
    data = {"tasks": [{"flow": {"consent": {"actions": {"a": action}}}}]}
    recover_actions(data)
    assert action["state"] == "pending"
    assert action["review"]["state"] == "needs_human"
    assert action["_review_private"]["status"] == "invalidated"
    assert action["_review_private"]["invalidated_reason"] == "OWNER_RESTART"


def test_8500_nested_identity_file_rejected_before_model():
    challenge = {"challenge_id": "c", "nonce": "n" * 64, "owner_run_id": "r",
                 "action_id": "a", "binding_hash": "b", "manifest_digest": "m",
                 "scope_id": "s", "scope_version": 1, "project_id": "p", "task_id": "t",
                 "job_key": "j", "attempt_id": "i", "endpoint_digest": "e",
                 "policy_version": "v", "expires_at":
                 (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()}
    endpoint = {"host": "h", "port": 22, "username": "u", "endpoint_digest": "e",
                "local_config_digest": "d", "host_key": {"algorithm": "a", "sha256": "d", "verification": "known_hosts"},
                "scheduler_target": {"scheduler": "slurm", "identity_file": "/private/canary"}}
    manifest = {"protocol_version": "x", "policy_version": "v", "action_id": "a",
                "project_id": "p", "task_id": "t", "job_key": "j", "attempt_id": "i",
                "scope_id": "s", "scope_version": 1, "endpoint": endpoint, "roots": [],
                "expires_at": challenge["expires_at"], "max_operations": 1,
                "max_total_bytes": 1, "manifest_digest": "m", "items": []}
    view = {"manifest": manifest, "binding_hash": "b", "purpose": "file preparation",
            "scope": {"scope_id": "s", "version": 1, "job_key": "j", "attempt_id": "i",
                      "root_bindings": [], "source_bindings": [], "allowed_operations": ["mkdir"],
                      "max_operations": 1, "max_total_bytes": 1, "expires_at": challenge["expires_at"]}}
    calls = []
    with pytest.raises(ReviewError) as exc:
        review_request({"protocol_version": PROTOCOL, "challenge": challenge,
                        "review_input": view}, secret="s" * 32,
                       settings_loader=lambda: None, client_factory=lambda: calls.append(1))
    assert exc.value.code == "REVIEWER_BAD_REQUEST"
    assert calls == []
