from __future__ import annotations

from .conftest import call_tool, create_project_task, resolve_card
from .test_monitor_events_report import OUTCAR_OK, OSZICAR_OK


def _failed_flow(api, old_job_id=6101):
    return {
        "phase": "blocked",
        "goal": "recover one failed attempt",
        "strategy": "single",
        "local_dir": str(api.workspace),
        "hpc_dir": "/review/calc",
        "execution_mode": "Fake",
        "waiting": [],
        "precheck": {"ok": False, "issues": []},
        "draft": [],
        "artifacts": {},
        "extractions": {"relax": {
            "outcar": {"converged": False, "energy": -10.0},
            "output_sha256": {"OUTCAR": "a" * 64},
        }},
        "report": "",
        "logs": [],
        "plan": {"strategy": "single", "jobs": [{
            "key": "relax",
            "label": "结构优化",
            "kind": "relax",
            "requires": [],
            "status": "failed",
            "slurm_id": old_job_id,
            "submission_state": "submitted",
            "diagnosis": {
                "status": "failed",
                "reason": "offline injected scheduler failure",
                "evidence": [{"file": "squeue", "text": "FAILED"}],
                "recommendations": ["review before retry"],
            },
        }]},
        "monitor": {"state": "error", "interval_seconds": 60,
                    "remote_cancelled": False},
    }


def _remote_ready(api, key):
    base = f"/review/calc/{key}"
    api.hpc.files.update({
        f"{base}/INCAR": b"SYSTEM = retry\nEDIFF = 1e-4\n",
        f"{base}/POSCAR": b"retry POSCAR\n",
        f"{base}/KPOINTS": b"Automatic mesh\n0\nGamma\n1 1 1\n0 0 0\n",
        f"{base}/POTCAR": b"TITEL = PAW_PBE offline\n",
        f"{base}/run.sh": b"#!/bin/bash\nsrun vasp_std\n",
    })
    return base


def _attest_draft_and_submit(api, project_id, task_id):
    attestation = call_tool(api, project_id, task_id, "draft")
    assert attestation["pending"]["kind"] == "script_attestation"
    resolve_card(api, project_id, task_id,
                 attestation["pending"]["card_id"], True)
    draft = call_tool(api, project_id, task_id, "draft")
    assert draft["flow"]["precheck"]["ok"] is True
    assert draft["pending"]["kind"] == "submit"
    return resolve_card(api, project_id, task_id,
                        draft["pending"]["card_id"], True)


def test_retry_preserves_attempt_and_new_job_id_continues_monitoring(api):
    project, task = create_project_task(api)
    project_id, task_id = project["id"], task["id"]
    store = api.app.state.toolbox.store
    store.update_task(project_id, task_id, flow=_failed_flow(api))
    remote = _remote_ready(api, "relax")

    diagnosed = call_tool(api, project_id, task_id, "diagnose_job",
                          {"job_key": "relax"})
    assert diagnosed["pending"] is None
    retry = call_tool(api, project_id, task_id, "retry_job",
                      {"job_key": "relax"})
    assert retry["pending"]["kind"] == "retry_job"
    reset = resolve_card(api, project_id, task_id,
                         retry["pending"]["card_id"], True)
    reset_job = reset["flow"]["jobs"][0]
    assert reset_job["status"] == "draft"
    assert reset_job.get("slurm_id") is None
    assert len(reset_job["attempts"]) == 1
    assert reset_job["attempts"][0]["job"]["slurm_id"] == 6101
    assert reset_job["attempts"][0]["job"]["diagnosis"]["status"] == "failed"
    assert api.hpc.submit_count == 0

    submitted = _attest_draft_and_submit(api, project_id, task_id)
    current = submitted["flow"]["jobs"][0]
    assert current["slurm_id"] == 7301
    assert current["slurm_id"] != 6101
    assert current["attempts"][0]["job"]["slurm_id"] == 6101
    assert api.hpc.submit_count == 1

    api.hpc.files.update({
        f"{remote}/OUTCAR": OUTCAR_OK,
        f"{remote}/OSZICAR": OSZICAR_OK,
    })
    api.hpc.queue_rows = []
    api.app.state.toolbox.monitor.tick(store)
    detail = api.client.get(
        f"/api/v1/toolbox/projects/{project_id}/tasks/{task_id}/detail").json()
    assert detail["flow"]["jobs"][0]["slurm_id"] == 7301
    assert detail["flow"]["jobs"][0]["status"] == "completed"
    assert detail["flow"]["jobs"][0]["attempts"][0]["job"]["slurm_id"] == 6101
    assert api.hpc.submit_count == 1


def test_dependency_completion_requires_fresh_submission_approval(api):
    project, task = create_project_task(api)
    project_id, task_id = project["id"], task["id"]
    store = api.app.state.toolbox.store
    relax_dir = _remote_ready(api, "relax")
    _remote_ready(api, "static")
    flow = {
        **_failed_flow(api),
        "phase": "monitoring",
        "precheck": {"ok": True, "hard": True, "issues": []},
        "extractions": {},
        "plan": {"strategy": "chain", "jobs": [
            {
                "key": "relax", "label": "结构优化", "kind": "relax",
                "requires": [], "status": "running", "slurm_id": 6201,
                "submission_state": "submitted",
            },
            {
                "key": "static", "label": "静态", "kind": "static",
                "requires": ["relax"], "status": "draft", "slurm_id": None,
            },
        ]},
    }
    store.update_task(project_id, task_id, flow=flow)
    api.hpc.files.update({
        f"{relax_dir}/OUTCAR": OUTCAR_OK,
        f"{relax_dir}/OSZICAR": OSZICAR_OK,
    })
    api.hpc.queue_rows = []

    api.app.state.toolbox.monitor.tick(store)
    after_relax = api.client.get(
        f"/api/v1/toolbox/projects/{project_id}/tasks/{task_id}/detail").json()
    jobs = {job["key"]: job for job in after_relax["flow"]["jobs"]}
    assert jobs["relax"]["status"] == "completed"
    assert jobs["static"]["status"] == "draft"
    assert jobs["static"].get("slurm_id") is None
    assert api.hpc.submit_count == 0

    attestation = call_tool(api, project_id, task_id, "draft")
    assert attestation["pending"]["kind"] == "script_attestation"
    assert api.hpc.submit_count == 0
    resolve_card(api, project_id, task_id,
                 attestation["pending"]["card_id"], True)
    draft = call_tool(api, project_id, task_id, "draft")
    assert draft["pending"]["kind"] == "submit"
    assert api.hpc.submit_count == 0
    submitted = resolve_card(api, project_id, task_id,
                             draft["pending"]["card_id"], True)
    jobs = {job["key"]: job for job in submitted["flow"]["jobs"]}
    assert jobs["static"]["slurm_id"] == 7301
    assert api.hpc.submit_count == 1


def test_stop_monitor_reports_completed_results_without_remote_cancel(api):
    project, task = create_project_task(api)
    project_id, task_id = project["id"], task["id"]
    store = api.app.state.toolbox.store
    relax_dir = _remote_ready(api, "relax")
    flow = {
        **_failed_flow(api),
        "phase": "monitoring",
        "extractions": {},
        "plan": {"strategy": "parallel", "jobs": [
            {
                "key": "relax", "label": "结构优化", "kind": "relax",
                "requires": [], "status": "running", "slurm_id": 6301,
                "submission_state": "submitted",
            },
            {
                "key": "static", "label": "静态", "kind": "static",
                "requires": [], "status": "running", "slurm_id": 6302,
                "submission_state": "submitted",
            },
        ]},
    }
    store.update_task(project_id, task_id, flow=flow)
    api.hpc.files.update({
        f"{relax_dir}/OUTCAR": OUTCAR_OK,
        f"{relax_dir}/OSZICAR": OSZICAR_OK,
    })
    api.hpc.queue_rows = ["6302 review static offline-review R node02"]
    api.app.state.toolbox.monitor.tick(store)

    stopped = call_tool(api, project_id, task_id, "stop_monitor")
    jobs = {job["key"]: job for job in stopped["flow"]["jobs"]}
    assert jobs["relax"]["status"] == "completed"
    assert jobs["static"]["status"] in {"canceled", "running"}
    assert stopped["flow"]["report"]
    detail = api.client.get(
        f"/api/v1/toolbox/projects/{project_id}/tasks/{task_id}/detail").json()
    assert detail["monitor"]["state"] == "stopped"
    assert detail["monitor"]["remote_cancelled"] is False
    assert not any(command.startswith(("scancel", "ccancel"))
                   for command, _cwd in api.hpc.run_calls)


def test_uncertain_submit_receipt_is_persisted_and_never_replayed(api):
    project, task = create_project_task(api)
    project_id, task_id = project["id"], task["id"]
    call_tool(api, project_id, task_id, "plan", {
        "strategy": "uncertain receipt",
        "jobs": [{
            "key": "relax", "label": "结构优化", "kind": "relax",
            "requires": [],
        }],
    })
    _remote_ready(api, "relax")
    attestation = call_tool(api, project_id, task_id, "draft")
    resolve_card(api, project_id, task_id,
                 attestation["pending"]["card_id"], True)
    draft = call_tool(api, project_id, task_id, "draft")
    submit_card = draft["pending"]
    api.hpc.submit_error = TimeoutError("connection lost after dispatch")

    uncertain = resolve_card(api, project_id, task_id,
                             submit_card["card_id"], True)
    job = uncertain["flow"]["jobs"][0]
    assert uncertain["card"]["state"] == "unknown"
    assert job["submission_state"] == "unknown"
    assert job.get("slurm_id") is None
    assert api.hpc.submit_count == 1

    replay = resolve_card(api, project_id, task_id,
                          submit_card["card_id"], True)
    assert replay["card"]["state"] == "unknown"
    assert api.hpc.submit_count == 1

    blocked = call_tool(api, project_id, task_id, "submit")
    assert blocked["pending"] is None
    assert blocked["error"]["code"] == "SUBMISSION_UNKNOWN"
    assert api.hpc.submit_count == 1
