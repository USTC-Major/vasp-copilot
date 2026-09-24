from __future__ import annotations

from pathlib import Path

from .conftest import call_tool, create_project_task, resolve_card
from backend.tests.valid_vasp_inputs import POTCAR as SYNTHETIC_POTCAR


def _plan_one_job(api, project_id, task_id):
    planned = call_tool(api, project_id, task_id, "plan", {
        "strategy": "single offline relaxation",
        "jobs": [{
            "key": "relax",
            "label": "结构优化",
            "kind": "relax",
            "requires": [],
        }],
    })
    assert planned["pending"] is None
    assert planned["flow"]["phase"] == "running"
    jobs = planned["flow"]["jobs"]
    assert [(job["key"], job["status"], job["slurm_id"])
            for job in jobs] == [("relax", "draft", None)]


def test_ai_disabled_manual_registration_copy_consent_and_precheck(api):
    """The 8000 contract remains useful with no AI/model or external services."""
    project, task = create_project_task(api)
    project_id, task_id = project["id"], task["id"]
    _plan_one_job(api, project_id, task_id)

    state = call_tool(api, project_id, task_id, "get_state")
    artifacts = state["flow"]["artifacts"]
    by_name = {artifact["name"]: artifact_id
               for artifact_id, artifact in artifacts.items()}
    assert {"INCAR", "POSCAR", "KPOINTS"} <= set(by_name)
    assert all(len(artifact["sha256"]) == 64 for artifact in artifacts.values())

    first = call_tool(api, project_id, task_id, "copy_inputs", {
        "artifact_ids": [by_name["INCAR"], by_name["POSCAR"]],
        "job_key": "relax",
    })
    card = first["pending"]
    assert card["kind"] == "copy_inputs"
    assert card["state"] == "pending"
    assert not (api.workspace / "relax" / "INCAR").exists()

    rejected = resolve_card(api, project_id, task_id, card["card_id"], False)
    assert rejected["card"]["state"] == "rejected"
    assert not (api.workspace / "relax" / "INCAR").exists()

    replay_rejected = resolve_card(api, project_id, task_id,
                                   card["card_id"], True)
    assert replay_rejected["card"]["state"] == "rejected"
    assert replay_rejected["card"]["action_id"] == card["action_id"]
    assert not (api.workspace / "relax" / "INCAR").exists()

    second = call_tool(api, project_id, task_id, "copy_inputs", {
        "artifact_ids": [by_name["INCAR"], by_name["POSCAR"]],
        "job_key": "relax",
    })
    approved = resolve_card(api, project_id, task_id,
                            second["pending"]["card_id"], True)
    assert approved["card"]["state"] == "executed"
    assert (api.workspace / "relax" / "INCAR").read_bytes() == \
        (api.workspace / "INCAR").read_bytes()
    assert (api.workspace / "relax" / "POSCAR").read_bytes() == \
        (api.workspace / "POSCAR").read_bytes()

    before = {
        path.name: (path.stat().st_mtime_ns, path.read_bytes())
        for path in (api.workspace / "relax").iterdir()
    }
    replay_approved = resolve_card(
        api, project_id, task_id, second["pending"]["card_id"], True)
    after = {
        path.name: (path.stat().st_mtime_ns, path.read_bytes())
        for path in (api.workspace / "relax").iterdir()
    }
    assert replay_approved["card"]["state"] == "executed"
    assert replay_approved["card"]["action_id"] == approved["card"]["action_id"]
    assert after == before

    precheck = call_tool(api, project_id, task_id, "precheck")
    assert precheck["pending"] is None
    assert precheck["flow"]["precheck"]["ok"] is False
    assert precheck["flow"]["precheck"]["issues"]
    assert api.hpc.submit_count == 0


def test_patch_cannot_replace_authoritative_flow(api):
    project, task = create_project_task(api)
    response = api.client.patch(
        f"/api/v1/toolbox/projects/{project['id']}/tasks/{task['id']}",
        json={"title": "allowed", "flow": {"phase": "done"}},
    )
    assert response.status_code in {400, 409, 422}, response.text
    detail = api.client.get(
        f"/api/v1/toolbox/projects/{project['id']}/tasks/{task['id']}/detail")
    assert detail.status_code == 200
    assert detail.json()["flow"].get("phase") != "done"


def test_detail_and_events_reads_do_not_query_scheduler(api):
    project, task = create_project_task(api)
    _plan_one_job(api, project["id"], task["id"])
    before = api.hpc.query_count
    detail = api.client.get(
        f"/api/v1/toolbox/projects/{project['id']}/tasks/{task['id']}/detail")
    events = api.client.get(
        f"/api/v1/toolbox/projects/{project['id']}/tasks/{task['id']}/events",
        params={"after": 0},
    )
    assert detail.status_code == events.status_code == 200
    assert api.hpc.query_count == before
    assert events.json()["cursor"] >= 0


def test_workspace_is_confined_to_fixture(api):
    """Guard the review itself against accidental writes outside tmp_path."""
    project, task = create_project_task(api)
    assert Path(task["local_workspace"]).resolve() == api.workspace.resolve()
    assert Path(api.root).resolve() != Path.home().resolve()


def test_complete_manual_fake_submission_monitor_and_report(api):
    """Frozen no-model path through attestation, consent, receipt and report."""
    (api.workspace / "POTCAR").write_bytes(SYNTHETIC_POTCAR)
    project, task = create_project_task(api)
    project_id, task_id = project["id"], task["id"]
    _plan_one_job(api, project_id, task_id)
    state = call_tool(api, project_id, task_id, "get_state")
    artifacts = state["flow"]["artifacts"]
    by_name = {artifact["name"]: artifact_id
               for artifact_id, artifact in artifacts.items()}
    assert set(by_name) == {"INCAR", "POSCAR", "KPOINTS", "POTCAR"}

    for name in ("INCAR", "POSCAR", "KPOINTS", "POTCAR"):
        upload = call_tool(api, project_id, task_id, "hpc_upload", {
            "artifact_id": by_name[name], "job_key": "relax",
        })
        assert upload["pending"]["kind"] == "hpc_upload"
        confirmed = resolve_card(
            api, project_id, task_id, upload["pending"]["card_id"], True)
        assert confirmed["card"]["state"] == "executed"

    remote = "/review/calc/relax"
    api.hpc.files[f"{remote}/run.sh"] = b"#!/bin/bash\nsrun vasp_std\n"
    first_draft = call_tool(api, project_id, task_id, "draft")
    assert first_draft["pending"]["kind"] == "script_attestation"
    attested = resolve_card(
        api, project_id, task_id, first_draft["pending"]["card_id"], True)
    assert attested["card"]["state"] == "executed"
    assert api.hpc.submit_count == 0

    second_draft = call_tool(api, project_id, task_id, "draft")
    assert second_draft["flow"]["precheck"]["ok"] is True
    assert second_draft["flow"]["draft"][0]["job_key"] == "relax"
    submit_card = second_draft["pending"]
    assert submit_card["kind"] == "submit"
    assert api.hpc.submit_count == 0

    submitted = resolve_card(
        api, project_id, task_id, submit_card["card_id"], True)
    assert submitted["card"]["state"] == "executed"
    job = submitted["flow"]["jobs"][0]
    assert job["slurm_id"] == 7301
    assert job["submission_state"] == "submitted"
    assert api.hpc.submit_count == 1

    api.hpc.queue_rows = ["7301 review relax offline-review R node01"]
    api.app.state.toolbox.monitor.tick(api.app.state.toolbox.store)
    running = api.client.get(
        f"/api/v1/toolbox/projects/{project_id}/tasks/{task_id}/detail").json()
    assert running["flow"]["jobs"][0]["status"] == "running"

    from .test_monitor_events_report import OUTCAR_OK, OSZICAR_OK
    api.hpc.files.update({
        f"{remote}/OUTCAR": OUTCAR_OK,
        f"{remote}/OSZICAR": OSZICAR_OK,
    })
    api.hpc.queue_rows = []
    api.app.state.toolbox.monitor.tick(api.app.state.toolbox.store)
    completed = api.client.get(
        f"/api/v1/toolbox/projects/{project_id}/tasks/{task_id}/detail").json()
    assert completed["flow"]["phase"] == "done"
    assert completed["flow"]["jobs"][0]["status"] == "completed"
    assert "## 概览" in completed["flow"]["report"]
    assert completed["flow"]["jobs"][0]["slurm_id"] == 7301
    assert api.hpc.submit_count == 1


def test_upload_rejects_remote_workspace_change_before_any_write(api):
    project, task = create_project_task(api)
    project_id, task_id = project["id"], task["id"]
    _plan_one_job(api, project_id, task_id)
    state = call_tool(api, project_id, task_id, "get_state")
    artifact_id = next(key for key, value in state["flow"]["artifacts"].items()
                       if value["name"] == "INCAR")
    card = call_tool(api, project_id, task_id, "hpc_upload", {
        "artifact_id": artifact_id, "job_key": "relax",
    })["pending"]
    assert "当前SSH连接" in card["reason"]
    flow = api.app.state.toolbox.store.get_task(project_id, task_id)["flow"]
    flow["hpc_dir"] = "/review/changed"
    api.app.state.toolbox.store.update_task(project_id, task_id, flow=flow,
                                            hpc_workspace="/review/changed")
    result = resolve_card(api, project_id, task_id, card["card_id"], True)
    assert result["card"]["state"] == "failed"
    assert api.hpc.write_calls == []
    assert not api.app.state.toolbox.files.legacy_sessions


def test_upload_postwrite_identity_failure_is_unknown_and_never_replayed(api, monkeypatch):
    from .conftest import FakeRemoteFiles
    original = FakeRemoteFiles._read
    observations = 0

    def changed_after_write(self, request, *, expected_endpoint=None):
        nonlocal observations
        result = original(self, request, expected_endpoint=expected_endpoint)
        observations += 1
        if observations >= 3:
            result["target_exists"] = None
        return result

    monkeypatch.setattr(FakeRemoteFiles, "_read", changed_after_write)
    project, task = create_project_task(api)
    project_id, task_id = project["id"], task["id"]
    _plan_one_job(api, project_id, task_id)
    state = call_tool(api, project_id, task_id, "get_state")
    artifact_id = next(key for key, value in state["flow"]["artifacts"].items()
                       if value["name"] == "INCAR")
    card = call_tool(api, project_id, task_id, "hpc_upload", {
        "artifact_id": artifact_id, "job_key": "relax",
    })["pending"]
    first = resolve_card(api, project_id, task_id, card["card_id"], True)
    assert first["card"]["state"] == "unknown"
    assert first["card"].get("file_dispatch_at")
    assert len(api.hpc.write_calls) == 1
    replay = resolve_card(api, project_id, task_id, card["card_id"], True)
    assert replay["card"]["state"] == "unknown"
    assert len(api.hpc.write_calls) == 1
