from __future__ import annotations

import pytest

from app.core.errors import AppError, ConflictError, ValidationError
from app.hpc.fake import FakeHpcBridge
from app.hpc.schemas import collection_decision

BUNDLE = {
    "01_relax/INCAR": b"SYSTEM = demo\nNELM = 60\n",
    "01_relax/KPOINTS": b"k-points\n0\nGamma\n1 1 1\n0 0 0\n",
    "01_relax/submit.sh": b"#!/bin/bash\n",
}


def _deploy_and_submit(bridge, scenario="completed"):
    plan = bridge.plan_deployment(BUNDLE)
    bridge.preflight(plan.deployment_id)
    gd = bridge.authorize_deploy(plan.deployment_id)
    manifest = bridge.execute_deploy(plan.deployment_id, gd.grant_id, "k-deploy-1")
    draft = bridge.create_submission_draft(plan.deployment_id, "01_relax")
    gs = bridge.authorize_submit(draft.submission_draft_id)
    receipt, job = bridge.submit(draft.submission_draft_id, gs.grant_id,
                                 draft.idempotency_key, scenario=scenario)
    return bridge, plan, manifest, draft, job


def test_list_clusters_exposes_no_secrets():
    b = FakeHpcBridge(enabled=True)
    clusters = b.list_clusters()
    assert len(clusters) == 1
    c = clusters[0]
    dumped = c.model_dump(mode="json")
    for secret in ("host", "hostname", "user", "username", "private_key",
                   "token", "password"):
        assert secret not in dumped, secret
    assert c.capabilities == ["deploy", "submit", "status", "collect"]


def test_deploy_draft_submit_lifecycle():
    bridge, plan, manifest, draft, job = _deploy_and_submit(
        FakeHpcBridge(enabled=True))
    assert plan.deployment_status == "deployed"
    assert manifest.verified is True
    assert manifest.next_action == "create_submission_draft"
    assert draft.hpc_job_status == "authorized"
    assert job.status == "pending"


def test_status_timeline_pending_running_completed():
    bridge, _, _, _, job = _deploy_and_submit(FakeHpcBridge(enabled=True))
    s1 = bridge.advance(job.remote_job_id, steps=1)
    assert s1.status in ("pending", "running")
    s2 = bridge.advance(job.remote_job_id, steps=1)
    assert s2.status in ("running", "completed")
    s3 = bridge.advance(job.remote_job_id, steps=1)
    assert s3.status == "completed"
    assert s3.collectable is True


def test_full_terminal_collect_flow():
    bridge = FakeHpcBridge(enabled=True)
    _, _, _, _, job = _deploy_and_submit(bridge)
    terminal = bridge.advance(job.remote_job_id)
    assert terminal.status == "completed"
    gc = bridge.authorize_collection(job.remote_job_id)
    remote = {"OSZICAR": b"1 F= -100", "OUTCAR": b"ok",
              "POTCAR": b"secret", "WAVECAR": b"big", "CHGCAR": b"big2",
              ".secret": b"x", "run.log": b"job: done"}
    col = bridge.collect(job.remote_job_id, gc.grant_id, remote,
                         create_diagnosis=True)
    names = {e.relative_path for e in col.manifest_files}
    assert {"OSZICAR", "OUTCAR", "run.log"} <= names
    assert "POTCAR" not in names and "WAVECAR" not in names
    assert "CHGCAR" not in names and ".secret" not in names
    reasons = {e["relative_path"]: e["reason"] for e in col.excluded}
    assert reasons.get("POTCAR") == "policy_denied"
    assert reasons.get("WAVECAR") == "policy_denied"
    assert reasons.get(".secret") == "hidden_file"
    assert col.diagnosis_id and col.next_url


def test_grant_single_use_deploy():
    bridge = FakeHpcBridge(enabled=True)
    plan = bridge.plan_deployment(BUNDLE)
    gd = bridge.authorize_deploy(plan.deployment_id)
    bridge.execute_deploy(plan.deployment_id, gd.grant_id, "k1")
    with pytest.raises(ConflictError):
        bridge.execute_deploy(plan.deployment_id, gd.grant_id, "k2")


def test_idempotent_submit_no_duplicate():
    bridge, _, _, draft, first = _deploy_and_submit(FakeHpcBridge(enabled=True))
    gs = bridge.authorize_submit(draft.submission_draft_id)
    receipt2, job2 = bridge.submit(draft.submission_draft_id, gs.grant_id,
                                   draft.idempotency_key)
    assert job2.remote_job_id == first.remote_job_id
    assert len(bridge._jobs) == 1


def test_submit_rejects_arbitrary_argv():
    bridge = FakeHpcBridge(enabled=True)
    plan = bridge.plan_deployment(BUNDLE)
    bridge.preflight(plan.deployment_id)
    gd = bridge.authorize_deploy(plan.deployment_id)
    bridge.execute_deploy(plan.deployment_id, gd.grant_id, "k1")
    draft = bridge.create_submission_draft(plan.deployment_id)
    gs = bridge.authorize_submit(draft.submission_draft_id)
    with pytest.raises(ValidationError) as ei:
        bridge.submit(draft.submission_draft_id, gs.grant_id,
                      draft.idempotency_key,
                      argv=["sbatch", "--partition=cpu; rm -rf /", "x"])
    assert ei.value.code == "HPC_ARGV_REJECTED"


def test_collect_requires_terminal():
    bridge = FakeHpcBridge(enabled=True)
    _, _, _, _, job = _deploy_and_submit(bridge)
    with pytest.raises(ConflictError):
        bridge.authorize_collection(job.remote_job_id)


def test_failed_scenario_terminal_failed():
    bridge, _, _, _, job = _deploy_and_submit(
        FakeHpcBridge(enabled=True), scenario="failed")
    terminal = bridge.advance(job.remote_job_id)
    assert terminal.status == "failed"
    assert terminal.state.exit_code == 1
    assert terminal.collectable is True


def test_disabled_bridge_raises():
    bridge = FakeHpcBridge(enabled=False)
    with pytest.raises(AppError) as ei:
        bridge.list_clusters()
    assert ei.value.code == "HPC_BRIDGE_DISABLED"


def test_collection_decision_policy():
    assert collection_decision("POTCAR") == (False, "policy_denied")
    assert collection_decision("WAVECAR") == (False, "policy_denied")
    assert collection_decision(".secret") == (False, "hidden_file")
    assert collection_decision("vasprun.xml") == (True, "")
    assert collection_decision("OUTCAR") == (True, "")
    assert collection_decision("run.log") == (True, "")
