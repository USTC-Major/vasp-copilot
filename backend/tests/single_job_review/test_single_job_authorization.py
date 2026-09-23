from __future__ import annotations

import copy
import hashlib
import json
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.tests.toolbox_review.conftest import FakeHPC
from backend.toolbox.commands import ToolExecutor, _CONSENT_PENDING
from backend.toolbox.api import create_toolbox_app
from backend.toolbox.config import ExecutionConfig
from backend.toolbox.consent import claim_action, get_card, resolve_card, spawn_submit_card
from backend.toolbox.contracts import ToolboxError
from backend.toolbox.orchestrator import Orchestrator
from backend.toolbox.projects import ProjectStore
from backend.toolbox.service import ExecutionService
from backend.toolbox.submission import perform_submit
from backend.tests.valid_vasp_inputs import FILES as VALID_INPUTS


def _stack(tmp_path: Path, jobs: list[dict], *, hpc: FakeHPC | None = None):
    root = tmp_path / "store"
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    store = ProjectStore(root)
    project = store.create_project("S1-I-A independent review")
    task = store.create_task(
        project["id"], goal="single computation authorization",
        local_workspace=str(workspace), hpc_workspace="/review/calc",
    )
    fake = hpc or FakeHPC()
    cfg = ExecutionConfig(data_dir=root, max_jobs=4, ssh_username="review-user")
    orch = Orchestrator(cfg, hpc=fake, llm_factory=lambda _cfg: None)

    attestations: dict[str, dict] = {}
    global_drafts: list[dict] = []
    for job in jobs:
        job.setdefault("attempt_id", uuid.uuid4().hex)
        calc = f"/review/calc/{job['key']}"
        for name in ("INCAR", "POSCAR", "KPOINTS", "POTCAR"):
            fake.files[f"{calc}/{name}"] = VALID_INPUTS[name]
        script = b"#!/bin/bash\nsrun vasp_std\n"
        script_path = f"{calc}/run.sh"
        fake.files[script_path] = script
        script_hash = hashlib.sha256(script).hexdigest()
        attestation = {
            "job_key": job["key"], "attempt_id": job["attempt_id"],
            "source": "remote", "directory": calc, "script_name": "run.sh",
            "normalized_path": script_path, "sha256": script_hash,
            "size": len(script), "action_id": f"attest-{job['key']}",
            "binding_hash": f"binding-{job['key']}",
        }
        draft = {
            "job_key": job["key"], "attempt_id": job["attempt_id"],
            "dir": calc, "script_name": "run.sh", "script_source": "remote",
            "script_path": script_path, "script_sha256": script_hash,
            "script_size": len(script),
            "attestation_action_id": attestation["action_id"],
            "attestation_binding_hash": attestation["binding_hash"],
            "submit_cmd": "sbatch run.sh",
        }
        job["draft"] = draft
        attestations[job["key"]] = attestation
        global_drafts.append(copy.deepcopy(draft))

    flow = {
        "phase": "await_submit", "goal": "review", "local_dir": str(workspace),
        "hpc_dir": "/review/calc", "execution_mode": "Fake",
        "script_attestations": attestations, "draft": global_drafts,
        "precheck": {"ok": False, "issues": []}, "waiting": [],
        "extractions": {}, "report": "", "logs": [],
        "plan": {"strategy": "independent", "jobs": jobs},
    }
    for job in jobs:
        if job.get("status", "draft") in {"draft", "waiting"}:
            orch._precheck(flow, workspace, True, flow["hpc_dir"], [],
                           job_key=job["key"])
    store.update_task(project["id"], task["id"], flow=flow)
    return store, project["id"], task["id"], cfg, fake, orch


def _jobs(store, project_id, task_id):
    return {
        job["key"]: job for job in
        store.get_task(project_id, task_id)["flow"]["plan"]["jobs"]
    }


def _submit(store, project_id, task_id, orch, key):
    job = _jobs(store, project_id, task_id)[key]
    card = spawn_submit_card(store, project_id, task_id,
                             job["key"], job["attempt_id"])
    result = perform_submit(store, project_id, task_id, card["card_id"],
                            True, orch=orch)
    return card, result


def test_two_jobs_require_identity_and_each_approval_submits_only_one(tmp_path):
    jobs = [
        {"key": "a", "label": "A", "kind": "vasp", "requires": [], "status": "draft"},
        {"key": "b", "label": "B", "kind": "vasp", "requires": [], "status": "draft"},
    ]
    store, pid, tid, cfg, hpc, orch = _stack(tmp_path, jobs)
    before = _jobs(store, pid, tid)
    assert before["a"]["precheck"]["snapshot"]["job_key"] == "a"
    assert before["b"]["precheck"]["snapshot"]["job_key"] == "b"
    assert before["a"]["precheck"]["digest"] != before["b"]["precheck"]["digest"]

    with pytest.raises(ToolboxError) as missing:
        spawn_submit_card(store, pid, tid)
    assert missing.value.code == "JOB_REQUIRED"
    service = ExecutionService(tmp_path / "unused", settings_loader=lambda: cfg,
                               orch_factory=lambda: orch, monitor_enabled=False)
    service.store = store
    for tool in ("precheck", "draft", "submit"):
        response = service.execute(pid, tid, tool, {})
        assert response["ok"] is False
        assert response["error"]["code"] == "JOB_REQUIRED"
    assert hpc.submit_count == 0

    b_draft = copy.deepcopy(before["b"]["draft"])
    b_precheck = copy.deepcopy(before["b"]["precheck"])
    card_a, result_a = _submit(store, pid, tid, orch, "a")
    assert "a 已提交" in result_a
    current = _jobs(store, pid, tid)
    assert hpc.submit_count == 1
    assert current["a"]["submission_action_id"] == card_a["action_id"]
    assert current["b"].get("slurm_id") is None
    assert current["b"]["draft"] == b_draft
    assert current["b"]["precheck"] == b_precheck
    flow = store.get_task(pid, tid)["flow"]
    assert flow["phase"] == "monitoring"
    scope_a = flow["consent"]["computation_scopes"][card_a["binding"]["scope_id"]]
    assert scope_a["submit_limit"] == 0

    card_b, result_b = _submit(store, pid, tid, orch, "b")
    assert "b 已提交" in result_b
    current = _jobs(store, pid, tid)
    assert hpc.submit_count == 2
    assert current["a"]["submission_action_id"] == card_a["action_id"]
    assert current["b"]["submission_action_id"] == card_b["action_id"]
    submit_cwds = [cwd for command, cwd in hpc.run_calls
                   if command.startswith("sbatch")]
    assert submit_cwds == ["/review/calc/a", "/review/calc/b"]

    perform_submit(store, pid, tid, card_a["card_id"], True, orch=orch)
    assert hpc.submit_count == 2


@pytest.mark.parametrize("mutation", ["version", "expiry", "quota"])
def test_stale_expired_or_empty_scope_never_dispatches(tmp_path, mutation):
    store, pid, tid, _cfg, hpc, orch = _stack(tmp_path, [
        {"key": "only", "label": "only", "kind": "vasp", "requires": [], "status": "draft"},
    ])
    job = _jobs(store, pid, tid)["only"]
    card = spawn_submit_card(store, pid, tid, "only", job["attempt_id"])
    flow = store.get_task(pid, tid)["flow"]
    scope = flow["consent"]["computation_scopes"][card["binding"]["scope_id"]]
    if mutation == "version":
        scope["version"] = 2
    elif mutation == "expiry":
        scope["expires_at"] = "2000-01-01T00:00:00+00:00"
    else:
        scope["submit_limit"] = 0
    store.update_task(pid, tid, flow=flow)

    result = perform_submit(store, pid, tid, card["card_id"], True, orch=orch)
    assert hpc.submit_count == 0, result
    assert get_card(store, pid, tid, card["card_id"])["state"] == "failed"
    assert _jobs(store, pid, tid)["only"].get("submission_state") is None


def test_unknown_receipt_consumes_scope_and_restart_never_replays(tmp_path):
    hpc = FakeHPC()
    hpc.submit_error = TimeoutError("lost after scheduler dispatch")
    store, pid, tid, cfg, hpc, orch = _stack(tmp_path, [
        {"key": "only", "label": "only", "kind": "vasp", "requires": [], "status": "draft"},
    ], hpc=hpc)
    job = _jobs(store, pid, tid)["only"]
    card = spawn_submit_card(store, pid, tid, "only", job["attempt_id"])
    result = perform_submit(store, pid, tid, card["card_id"], True, orch=orch)
    assert "结果不确定" in result
    assert hpc.submit_count == 1
    flow = store.get_task(pid, tid)["flow"]
    assert _jobs(store, pid, tid)["only"]["submission_state"] == "unknown"
    assert flow["consent"]["computation_scopes"][card["binding"]["scope_id"]]["submit_limit"] == 0

    restarted = ProjectStore(store.root)
    restarted_orch = Orchestrator(cfg, hpc=hpc, llm_factory=lambda _cfg: None)
    perform_submit(restarted, pid, tid, card["card_id"], True, orch=restarted_orch)
    restarted_orch._submit(restarted, pid, tid,
                           restarted.get_task(pid, tid)["flow"])
    assert hpc.submit_count == 1
    assert _jobs(restarted, pid, tid)["only"]["submission_state"] == "unknown"


def test_restart_after_submit_claim_blocks_new_card_before_scheduler_receipt(tmp_path):
    store, pid, tid, cfg, hpc, _orch = _stack(tmp_path, [
        {"key": "only", "label": "only", "kind": "vasp", "requires": [], "status": "draft"},
        {"key": "other", "label": "other", "kind": "vasp", "requires": [], "status": "draft"},
    ])
    job = _jobs(store, pid, tid)["only"]
    card = spawn_submit_card(store, pid, tid, "only", job["attempt_id"])
    assert resolve_card(store, pid, tid, card["card_id"], approved=True)["approved"]
    assert claim_action(store, pid, tid, card["action_id"])["state"] == "executing"
    assert hpc.submit_count == 0

    restarted = ProjectStore(store.root)
    assert get_card(restarted, pid, tid, card["card_id"])["state"] == "unknown"
    with pytest.raises(ToolboxError) as blocked:
        current = _jobs(restarted, pid, tid)["only"]
        spawn_submit_card(restarted, pid, tid, "only", current["attempt_id"])
    assert blocked.value.code == "SUBMISSION_UNKNOWN"
    # The unresolved A attempt is local to A. Independent B still needs its
    # own card and may submit without replaying A.
    other = _jobs(restarted, pid, tid)["other"]
    other_card = spawn_submit_card(restarted, pid, tid,
                                   "other", other["attempt_id"])
    restarted_orch = Orchestrator(cfg, hpc=hpc, llm_factory=lambda _cfg: None)
    result = perform_submit(restarted, pid, tid, other_card["card_id"], True,
                            orch=restarted_orch)
    assert "other 已提交" in result
    assert hpc.submit_count == 1
    current = _jobs(restarted, pid, tid)
    assert current["only"]["submission_state"] == "unknown"
    assert current["other"]["submission_state"] == "submitted"


def test_recovery_keeps_known_scheduler_receipt_even_if_action_was_executing(tmp_path):
    store, pid, tid, _cfg, hpc, _orch = _stack(tmp_path, [
        {"key": "only", "label": "only", "kind": "vasp", "requires": [], "status": "draft"},
    ])
    job = _jobs(store, pid, tid)["only"]
    card = spawn_submit_card(store, pid, tid, "only", job["attempt_id"])
    resolve_card(store, pid, tid, card["card_id"], approved=True)
    claim_action(store, pid, tid, card["action_id"])
    flow = store.get_task(pid, tid)["flow"]
    current = flow["plan"]["jobs"][0]
    current.update(status="submitted", submission_state="submitted",
                   submission_action_id=card["action_id"], slurm_id=8123)
    store.update_task(pid, tid, flow=flow)

    restarted = ProjectStore(store.root)
    recovered = _jobs(restarted, pid, tid)["only"]
    assert recovered["status"] == "submitted"
    assert recovered["submission_state"] == "submitted"
    assert recovered["submission_action_id"] == card["action_id"]
    assert recovered["slurm_id"] == 8123
    assert get_card(restarted, pid, tid, card["card_id"])["state"] == "unknown"
    assert hpc.submit_count == 0


def test_dependency_completion_does_not_reuse_earlier_precheck(tmp_path):
    store, pid, tid, _cfg, hpc, orch = _stack(tmp_path, [
        {"key": "a", "label": "A", "kind": "vasp", "requires": [], "status": "draft"},
        {"key": "b", "label": "B", "kind": "vasp", "requires": ["a"], "status": "waiting"},
    ])
    b_before = copy.deepcopy(_jobs(store, pid, tid)["b"]["precheck"])
    with pytest.raises(ToolboxError) as blocked:
        spawn_submit_card(store, pid, tid, "b", _jobs(store, pid, tid)["b"]["attempt_id"])
    assert blocked.value.code == "DEPENDENCY_NOT_READY"

    _submit(store, pid, tid, orch, "a")
    flow = store.get_task(pid, tid)["flow"]
    a = next(job for job in flow["plan"]["jobs"] if job["key"] == "a")
    a["status"] = "completed"
    store.update_task(pid, tid, flow=flow)
    b = _jobs(store, pid, tid)["b"]
    stale_card = spawn_submit_card(store, pid, tid, "b", b["attempt_id"])
    stale_result = perform_submit(store, pid, tid, stale_card["card_id"], True,
                                  orch=orch)
    assert "AI_PRECHECK_STALE" in stale_result
    assert hpc.submit_count == 1
    assert _jobs(store, pid, tid)["b"]["precheck"] != b_before

    refreshed = store.get_task(pid, tid)["flow"]
    b = next(job for job in refreshed["plan"]["jobs"] if job["key"] == "b")
    # The submit-time hard check persisted current dependency evidence. A new
    # exact card is required; the old A approval is never inherited.
    fresh_card = spawn_submit_card(store, pid, tid, "b", b["attempt_id"])
    fresh_result = perform_submit(store, pid, tid, fresh_card["card_id"], True,
                                  orch=orch)
    assert "b 已提交" in fresh_result
    assert hpc.submit_count == 2


def test_selection_invalidates_only_changed_unsubmitted_job(tmp_path):
    store, pid, tid, cfg, hpc, orch = _stack(tmp_path, [
        {"key": "a", "label": "A", "kind": "vasp", "requires": [], "status": "draft"},
        {"key": "b", "label": "B", "kind": "vasp", "requires": [], "status": "draft"},
    ])
    before = _jobs(store, pid, tid)
    b_precheck = copy.deepcopy(before["b"]["precheck"])
    b_draft = copy.deepcopy(before["b"]["draft"])
    card_a = spawn_submit_card(store, pid, tid, "a", before["a"]["attempt_id"])
    card_b = spawn_submit_card(store, pid, tid, "b", before["b"]["attempt_id"])
    executor = ToolExecutor(store=store, project_id=pid, task_id=tid,
                            cfg=cfg, orch=orch)

    result = executor.handle("select_jobs", {"skip": ["a"]})
    assert "仅规划" in result
    after = _jobs(store, pid, tid)
    assert after["a"]["status"] == "skipped"
    assert after["a"].get("precheck") is None
    assert after["a"].get("draft") is None
    assert after["b"]["precheck"] == b_precheck
    assert after["b"]["draft"] == b_draft
    assert get_card(store, pid, tid, card_a["card_id"])["state"] == "expired"
    assert get_card(store, pid, tid, card_b["card_id"])["state"] == "pending"
    assert hpc.submit_count == 0


def test_workspace_patch_invalidates_authority_and_refuses_live_job(tmp_path):
    store, pid, tid, cfg, hpc, orch = _stack(tmp_path, [
        {"key": "a", "label": "A", "kind": "vasp", "requires": [], "status": "draft"},
        {"key": "b", "label": "B", "kind": "vasp", "requires": [], "status": "draft"},
    ])
    jobs = _jobs(store, pid, tid)
    card_a = spawn_submit_card(store, pid, tid, "a", jobs["a"]["attempt_id"])
    card_b = spawn_submit_card(store, pid, tid, "b", jobs["b"]["attempt_id"])
    app = create_toolbox_app(
        root=store.root, settings_loader=lambda: cfg,
        orch_factory=lambda: orch, monitor_enabled=False,
    )
    with TestClient(app) as client:
        response = client.patch(
            f"/api/v1/toolbox/projects/{pid}/tasks/{tid}",
            json={"hpc_workspace": "/review/new-calc"},
        )
        assert response.status_code == 200, response.text
        authoritative = app.state.toolbox.store
        after = _jobs(authoritative, pid, tid)
        assert all(job.get("precheck") is None and job.get("draft") is None
                   for job in after.values())
        assert get_card(authoritative, pid, tid, card_a["card_id"])["state"] == "expired"
        assert get_card(authoritative, pid, tid, card_b["card_id"])["state"] == "expired"
        scopes = authoritative.get_task(pid, tid)["flow"]["consent"]["computation_scopes"]
        assert scopes and all(scope["state"] == "revoked" for scope in scopes.values())

    live_store, live_pid, live_tid, live_cfg, live_hpc, live_orch = _stack(
        tmp_path / "live", [
            {"key": "a", "label": "A", "kind": "vasp", "requires": [], "status": "draft"},
            {"key": "b", "label": "B", "kind": "vasp", "requires": [], "status": "draft"},
        ])
    _submit(live_store, live_pid, live_tid, live_orch, "a")
    before_live = copy.deepcopy(live_store.get_task(live_pid, live_tid)["flow"])
    live_app = create_toolbox_app(
        root=live_store.root, settings_loader=lambda: live_cfg,
        orch_factory=lambda: live_orch, monitor_enabled=False,
    )
    with TestClient(live_app) as client:
        response = client.patch(
            f"/api/v1/toolbox/projects/{live_pid}/tasks/{live_tid}",
            json={"hpc_workspace": "/must-not-change"},
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] in {"TASK_ACTIVE", "TASK_UNRESOLVED"}
        after_live = live_app.state.toolbox.store.get_task(live_pid, live_tid)["flow"]
        assert after_live["hpc_dir"] == before_live["hpc_dir"]
        assert after_live["plan"] == before_live["plan"]
        assert after_live["consent"] == before_live["consent"]
    assert live_hpc.submit_count == 1


def test_legacy_mixed_task_preserves_audit_and_retry_creates_fresh_attempt(tmp_path):
    root = tmp_path / "legacy-store"
    root.mkdir()
    original_failed = {
        "key": "old", "label": "old", "kind": "vasp", "requires": [],
        "status": "failed", "slurm_id": 91, "submission_state": "submitted",
        "submission_action_id": "historical-submit",
        "diagnosis": {"status": "failed", "reason": "historical evidence"},
        "result_read_error": "historical read note", "custom_fact": {"keep": True},
    }
    old_precheck = {"ok": True, "hard": True, "digest": "a" * 64,
                    "snapshot": {"legacy": True}}
    old_draft = [{"job_key": "old", "dir": "/legacy/old",
                  "submit_cmd": "sbatch old.sh"}]
    payload = {
        "schema_version": 1,
        "projects": [{"id": "p", "name": "legacy", "created_at": "x", "updated_at": "x"}],
        "tasks": [{
            "id": "t", "project_id": "p", "title": "mixed", "goal": "audit",
            "status": "failed", "updated_at": "x", "local_workspace": str(tmp_path / "ws"),
            "flow": {
                "phase": "blocked", "execution_mode": "Fake",
                "local_dir": str(tmp_path / "ws"), "hpc_dir": "/legacy",
                "precheck": old_precheck, "draft": old_draft,
                "plan": {"strategy": "legacy", "jobs": [
                    copy.deepcopy(original_failed),
                    {"key": "future", "label": "future", "kind": "vasp",
                     "requires": [], "status": "draft"},
                ]},
                "consent": {"actions": {"old-write": {
                    "action_id": "old-write", "card_id": "old-write",
                    "kind": "copy_inputs", "state": "pending",
                    "binding": {"operation": "copy_inputs", "job_key": "old"},
                    "binding_hash": "legacy", "expires_at": "2999-01-01T00:00:00+00:00",
                }}},
                "script_attestations": {"old": {"legacy": True}},
                "extractions": {"old": {"historical": True}},
            },
        }],
        "waiting": [], "events": [],
    }
    (root / "execution_store.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    store = ProjectStore(root)
    flow = store.get_task("p", "t")["flow"]
    jobs = {job["key"]: job for job in flow["plan"]["jobs"]}
    assert jobs["old"].get("attempt_id") is None
    assert jobs["future"].get("attempt_id")
    assert jobs["future"].get("precheck") is None
    assert jobs["future"].get("draft") is None
    assert flow["legacy_submission_evidence"] == {
        "authorizes_submission": False,
        "precheck": old_precheck,
        "draft": old_draft,
    }

    cfg = ExecutionConfig(data_dir=root, ssh_username="legacy-user")
    hpc = FakeHPC()
    orch = Orchestrator(cfg, hpc=hpc, llm_factory=lambda _cfg: None)
    executor = ToolExecutor(store=store, project_id="p", task_id="t",
                            cfg=cfg, orch=orch)
    pending = executor.handle("retry_job", {"job_key": "old"})
    assert pending.startswith(_CONSENT_PENDING)
    action_id = pending[len(_CONSENT_PENDING):]
    resolve_card(store, "p", "t", action_id, approved=True)
    result = executor.execute_action(action_id)
    assert "已恢复为待准备" in result
    assert hpc.submit_count == 0

    flow = store.get_task("p", "t")["flow"]
    jobs = {job["key"]: job for job in flow["plan"]["jobs"]}
    recovered = jobs["old"]
    assert recovered["attempt_id"]
    assert recovered["status"] == "draft"
    assert recovered.get("slurm_id") is None
    assert recovered.get("precheck") is None
    assert recovered.get("draft") is None
    history = recovered["attempt_history"]
    assert len(history) == 1
    assert history[0]["job"] == original_failed
    assert get_card(store, "p", "t", "old-write")["state"] == "expired"

    stable_attempt = recovered["attempt_id"]
    restarted = ProjectStore(root)
    assert _jobs(restarted, "p", "t")["old"]["attempt_id"] == stable_attempt
    executor.execute_action(action_id)
    assert len(_jobs(store, "p", "t")["old"]["attempt_history"]) == 1
    assert hpc.submit_count == 0
