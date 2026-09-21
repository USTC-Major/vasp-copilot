from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from backend.tests.toolbox.legacy_bridge import ProjectStore
from ai_mode.server import create_ai_mode_app
from app.api.v1 import diagnosis as diagnosis_api
from app.api.v1 import workflows as workflows_api
from app.main import app
from app.schemas.detected import DetectedRun
from app.services.run_store import RunStore
from backend.app.services.workflow_service import (
    WorkflowArtifact,
    WorkflowPlanRecord,
    WorkflowService,
)


def test_diagnosis_recent_filters_expired_without_touching_or_deleting(
    monkeypatch, tmp_path: Path,
):
    history_store = RunStore(ttl_seconds=60)
    live_dir = tmp_path / "live"
    expired_dir = tmp_path / "expired"
    live_dir.mkdir()
    expired_dir.mkdir()
    detected = DetectedRun(root="run", files=[], missing_recommended=[],
                           candidate_job_logs=[])
    live = history_store.create("diag_live", detected, live_dir)
    expired = history_store.create("diag_expired", detected, expired_dir)
    now = time.time()
    live.touched_at = now - 5
    expired.touched_at = now - 61
    before_touch = live.touched_at
    monkeypatch.setattr(diagnosis_api, "store", history_store)

    response = TestClient(app).get("/api/v1/diagnosis/recent?limit=10")

    assert response.status_code == 200
    body = response.json()["data"]
    assert body["source"] == "diagnoses"
    assert [row["id"] for row in body["records"]] == ["diag_live"]
    assert live.touched_at == before_touch
    assert "diag_expired" in history_store._records
    assert expired_dir.is_dir()
    assert "base_dir" not in response.text and str(tmp_path) not in response.text


def test_workflow_recent_deduplicates_and_does_not_refresh_ttl(monkeypatch):
    service = WorkflowService(ttl_seconds=60)
    now = time.time()
    plan = WorkflowPlanRecord(
        workflow_id="wf_live", workflow_status="planned", plan={})
    plan.created_at = now - 20
    plan.touched_at = now - 10
    artifact = WorkflowArtifact(
        workflow_id="wf_live", zip_bytes=b"zip",
        body={"workflow_status": "generated"})
    artifact.created_at = now - 5
    artifact.touched_at = now - 2
    expired = WorkflowPlanRecord(
        workflow_id="wf_expired", workflow_status="planned", plan={})
    expired.touched_at = now - 61
    service._plans = {"wf_live": plan, "wf_expired": expired}
    service._artifacts = {"wf_live": artifact}
    before_plan_touch = plan.touched_at
    before_artifact_touch = artifact.touched_at
    monkeypatch.setattr(workflows_api, "workflow_service", service)

    response = TestClient(app).get("/api/v1/workflows/recent?limit=10")

    assert response.status_code == 200
    records = response.json()["data"]["records"]
    assert len(records) == 1
    assert records[0]["id"] == "wf_live"
    assert records[0]["status"] == "generated"
    assert plan.touched_at == before_plan_touch
    assert artifact.touched_at == before_artifact_touch
    assert "wf_expired" in service._plans


def test_ai_recent_history_is_safe_read_only_and_preserves_task_modes(
    monkeypatch, tmp_path: Path,
):
    import ai_mode.server as server_module

    monkeypatch.setenv("ENABLE_AI_MODE", "true")
    history_store = ProjectStore(tmp_path / "ai")
    project = history_store.create_project("真实项目", "不应进入摘要的描述")
    modes = ["Real", "Fake", "None"]
    task_ids = []
    for mode in modes:
        task = history_store.create_task(
            project["id"], title=f"{mode} 任务", goal="敏感输入内容",
            local_workspace=str(tmp_path / "private-workspace"),
            hpc_workspace="/secret/remote/path")
        history_store.update_task(
            project["id"], task["id"],
            flow={"execution_mode": mode, "goal": "不要泄露"})
        task_ids.append(task["id"])
    empty_project = history_store.create_project("空项目")
    before = history_store._path.read_bytes()
    monkeypatch.setattr(server_module, "_get_project_store", lambda: history_store)

    response = TestClient(create_ai_mode_app()).get("/ai/v1/history/recent?limit=20")

    assert response.status_code == 200
    records = response.json()["records"]
    task_rows = {row["task_id"]: row for row in records if row["kind"] == "ai_task"}
    assert {task_rows[task_id]["execution_mode"] for task_id in task_ids} == set(modes)
    assert any(row["kind"] == "ai_project" and row["project_id"] == empty_project["id"]
               for row in records)
    assert history_store._path.read_bytes() == before
    for forbidden in ("private-workspace", "/secret/remote/path", "敏感输入内容", "不要泄露"):
        assert forbidden not in response.text


def test_ai_recent_history_disabled_is_explicit(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("VASP_AI_HOME", str(tmp_path))
    monkeypatch.setenv("ENABLE_AI_MODE", "false")
    response = TestClient(create_ai_mode_app()).get("/ai/v1/history/recent")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "AI_MODE_DISABLED"
