from __future__ import annotations

import json

from .conftest import create_project_task


OUTCAR_OK = (
    "ENCUT = 400  EDIFF = 1e-4  IBRION = 2  ISIF = 3  NSW = 99\n"
    "  free  energy   TOTEN = -123.456789 eV\n"
    "  reached required accuracy - stopping structural energy minimisation\n"
    "  General timing and accounting informations for this job:\n"
    "Elapsed time (sec): 15.0\n"
).encode()
OSZICAR_OK = (
    " DAV:  2    -0.12345679E+03   -1e-7  -2e-7  12  1e-6\n"
    "   1 F= -.12345679E+03 E0= -.12345679E+03 d E =-.45678E+00\n"
).encode()


def _monitoring_flow(api, job_id=4815):
    return {
        "phase": "monitoring",
        "goal": "offline monitor evidence",
        "strategy": "single",
        "local_dir": str(api.workspace),
        "hpc_dir": "/review/calc",
        "execution_mode": "Fake",
        "waiting": [],
        "precheck": {"ok": True, "hard": True, "issues": [],
                     "digest": "d" * 64},
        "draft": [],
        "artifacts": {},
        "extractions": {},
        "report": "",
        "logs": [],
        "plan": {"strategy": "single", "jobs": [{
            "key": "relax",
            "label": "结构优化",
            "kind": "relax",
            "requires": [],
            "status": "submitted",
            "slurm_id": job_id,
            "submission_state": "submitted",
        }]},
        "monitor": {"state": "monitoring", "interval_seconds": 60,
                    "remote_cancelled": False},
    }


def test_owner_monitor_writes_execution_events_and_report_not_chat(api):
    project, task = create_project_task(api)
    project_id, task_id = project["id"], task["id"]
    store = api.app.state.toolbox.store
    monitor = api.app.state.toolbox.monitor
    store.update_task(project_id, task_id, flow=_monitoring_flow(api))

    chat_path = api.root / "chat_store.json"
    chat_path.write_text(json.dumps({
        "schema_version": 1,
        "messages": {f"{project_id}:{task_id}": [
            {"role": "user", "content": "must remain untouched"},
        ]},
    }, ensure_ascii=False), encoding="utf-8")
    chat_before = chat_path.read_bytes()

    api.hpc.queue_rows = ["4815 review relax offline-review R node01"]
    assert monitor.tick(store) == 1
    running = api.client.get(
        f"/api/v1/toolbox/projects/{project_id}/tasks/{task_id}/detail")
    assert running.status_code == 200, running.text
    assert running.json()["flow"]["jobs"][0]["status"] == "running"
    assert running.json()["monitor"]["state"] == "monitoring"
    assert api.hpc.submit_count == 0
    assert chat_path.read_bytes() == chat_before

    base = "/review/calc/relax"
    api.hpc.files.update({
        f"{base}/OUTCAR": OUTCAR_OK,
        f"{base}/OSZICAR": OSZICAR_OK,
        f"{base}/INCAR": b"SYSTEM = offline\nEDIFF = 1e-4\n",
        f"{base}/KPOINTS": b"Automatic mesh\n0\nGamma\n1 1 1\n0 0 0\n",
    })
    api.hpc.queue_rows = []
    assert monitor.tick(store) == 1

    completed = api.client.get(
        f"/api/v1/toolbox/projects/{project_id}/tasks/{task_id}/detail")
    assert completed.status_code == 200, completed.text
    detail = completed.json()
    assert detail["flow"]["phase"] == "done"
    assert detail["flow"]["jobs"][0]["status"] == "completed"
    assert "## 概览" in detail["flow"]["report"]
    assert "-123.456789" in detail["flow"]["report"]
    assert api.hpc.submit_count == 0
    assert chat_path.read_bytes() == chat_before

    events_response = api.client.get(
        f"/api/v1/toolbox/projects/{project_id}/tasks/{task_id}/events",
        params={"after": 0},
    )
    assert events_response.status_code == 200, events_response.text
    events = events_response.json()["events"]
    assert len(events) >= 2
    assert [event["id"] for event in events] == sorted(
        event["id"] for event in events)
    assert all(event["project_id"] == project_id for event in events)
    assert all(event["task_id"] == task_id for event in events)
    assert all(event["message"] for event in events)


def test_monitor_query_failure_stays_traceable_and_does_not_resubmit(api):
    project, task = create_project_task(api)
    project_id, task_id = project["id"], task["id"]
    store = api.app.state.toolbox.store
    monitor = api.app.state.toolbox.monitor
    store.update_task(project_id, task_id, flow=_monitoring_flow(api, job_id=9001))
    api.hpc.query_error = TimeoutError("offline injected disconnect")

    assert monitor.tick(store) == 1
    detail = api.client.get(
        f"/api/v1/toolbox/projects/{project_id}/tasks/{task_id}/detail").json()
    job = detail["flow"]["jobs"][0]
    assert job["slurm_id"] == 9001
    assert job["status"] == "submitted"
    assert detail["monitor"]["state"] == "error"
    assert detail["monitor"]["last_attempt_at"]
    assert "offline injected disconnect" in detail["monitor"]["last_error"]
    assert api.hpc.submit_count == 0

    events = api.client.get(
        f"/api/v1/toolbox/projects/{project_id}/tasks/{task_id}/events",
        params={"after": 0},
    ).json()["events"]
    assert any(event["kind"] == "monitor.error" for event in events)
