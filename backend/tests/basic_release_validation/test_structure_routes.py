"""Independent API checks for entry-point bypasses and durable side effects."""

from __future__ import annotations

import uuid
import hashlib

import pytest
from fastapi.testclient import TestClient


BAD = "Si\n1\n1 0 0\n0 1 0\n0 0 1\nSi\n2\n"
GOOD = (
    "Si pair\n-8\n1 0 0\n0 1 0\n0 0 1\nSi\n2\n"
    "Selective dynamics\nCartesian\n0D0 0D0 0D0 T F T\n"
    "1D0 1D0 1D0 F T F\n"
)


@pytest.fixture
def app_api(tmp_path, monkeypatch):
    from app.api.v1 import deps, files, structure, workflows
    from app.main import app
    from app.services.file_store import FileStore

    isolated = FileStore(tmp_path / "data" / "files")
    for module in (deps, files, structure, workflows):
        monkeypatch.setattr(module, "file_store", isolated)
    monkeypatch.setattr(workflows.workflow_service, "_file_store", isolated)
    with TestClient(app) as client:
        yield client, isolated, workflows.workflow_service


def _workflow_payload(text, workflow_id=None):
    workflow_id = workflow_id or "wf_" + uuid.uuid4().hex[:12]
    return {"workflow_id": workflow_id, "workflow": {
        "workflow_id": workflow_id,
        "structure": {
            "formula": "Si2", "elements": ["Si"], "counts": [2],
            "atom_count": 2,
            "lattice": {"matrix": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
                        "a": 1, "b": 1, "c": 1,
                        "alpha": 90, "beta": 90, "gamma": 90, "volume": 1},
            "source_sha256": "0" * 64,
            "poscar_text": text,
        },
        "requested_tasks": ["static"], "confirm": True,
    }}


def test_analyze_missing_coordinates_rejects_without_structure_record(app_api):
    client, isolated, _service = app_api
    upload = client.post("/api/v1/files/upload", files={"file": ("POSCAR", BAD, "text/plain")})
    assert upload.status_code == 200, upload.text
    before = len(isolated._structures)
    file_id = upload.json()["data"]["file"]["file_id"]
    response = client.post("/api/v1/structure/analyze", json={"file_id": file_id})
    assert 400 <= response.status_code < 500, response.text
    assert len(isolated._structures) == before


def test_inline_workflow_cannot_generate_from_fake_summary_and_bad_poscar(app_api):
    client, _store, _service = app_api
    payload = _workflow_payload(BAD)
    workflow_id = payload["workflow_id"]
    planned = client.post("/api/v1/workflows/plan", json=payload)
    generated = client.post("/api/v1/workflows/generate", json=payload)
    assert 400 <= planned.status_code < 500, planned.text
    assert 400 <= generated.status_code < 500, generated.text
    download = client.get(f"/api/v1/workflows/{workflow_id}/download")
    assert download.status_code == 404


def test_generate_rechecks_replayed_plan_rather_than_trusting_cached_summary(app_api):
    client, _store, service = app_api
    payload = _workflow_payload(GOOD)
    workflow_id = payload["workflow_id"]
    planned = client.post("/api/v1/workflows/plan", json=payload)
    assert planned.status_code == 200, planned.text
    assert service._plans[workflow_id].request.structure.source_sha256 == hashlib.sha256(GOOD.encode()).hexdigest()
    # Emulate a stale pre-fix cached request carrying an invalid raw POSCAR.
    service._plans[workflow_id].request.structure.poscar_text = BAD
    generated = client.post("/api/v1/workflows/generate", json={"workflow_id": workflow_id})
    assert 400 <= generated.status_code < 500, generated.text
    assert client.get(f"/api/v1/workflows/{workflow_id}/download").status_code == 404


def test_old_structure_id_and_diagnosis_source_cannot_bypass_validation(app_api, tmp_path):
    from app.api.v1 import deps
    from app.schemas.detected import DetectedFile, DetectedRun
    from app.schemas.structure import StructureSummary

    client, isolated, _service = app_api
    raw = isolated.store_file("POSCAR", "poscar", BAD.encode())
    stale = isolated.store_structure(raw.file_id, StructureSummary(
        formula="Si2", elements=["Si"], counts=[2], atom_count=2,
        poscar_text=BAD, source_sha256="0" * 64,
    ))
    from_old_id = client.post("/api/v1/workflows/generate", json={
        "structure_id": stale.structure_id,
        "workflow": {"requested_tasks": ["static"], "confirm": True},
    })
    assert 400 <= from_old_id.status_code < 500, from_old_id.text

    diagnosis_id = "diag_" + uuid.uuid4().hex[:12]
    run_dir = tmp_path / "diagnosis"
    run_dir.mkdir()
    (run_dir / "POSCAR").write_text(BAD, encoding="utf-8")
    deps.store.create(diagnosis_id, DetectedRun(
        root=diagnosis_id,
        files=[DetectedFile(name="POSCAR", kind="poscar", path="POSCAR")],
    ), run_dir)
    from_diagnosis = client.post("/api/v1/workflows/plan", json={
        "diagnosis_id": diagnosis_id,
        "workflow": {"requested_tasks": ["static"], "confirm": True},
    })
    assert 400 <= from_diagnosis.status_code < 500, from_diagnosis.text


def test_valid_scaled_structure_preserves_summary_and_raw_coordinates(app_api):
    client, _store, _service = app_api
    upload = client.post("/api/v1/files/upload", files={"file": ("POSCAR", GOOD, "text/plain")})
    assert upload.status_code == 200, upload.text
    file_id = upload.json()["data"]["file"]["file_id"]
    analyzed = client.post("/api/v1/structure/analyze", json={"file_id": file_id})
    assert analyzed.status_code == 200, analyzed.text
    summary = analyzed.json()["data"]["summary"]
    assert summary["atom_count"] == 2
    assert summary["lattice"]["volume"] == pytest.approx(8)
    assert summary["coordinate_mode"] == "cartesian"
    assert summary["selective_dynamics"] is True
    generated = client.post("/api/v1/workflows/generate", json={
        "structure_id": analyzed.json()["data"]["structure_id"],
        "workflow": {"workflow_id": "wf_" + uuid.uuid4().hex[:12],
                     "requested_tasks": ["static"], "confirm": True},
    })
    assert generated.status_code == 200, generated.text
