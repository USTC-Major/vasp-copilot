"""Workflow API 集成测试（IR-01/IR-03/IR-05）。

覆盖 /api/v1/workflows/{plan,generate,download}：从已存 doctor run 解析结构
（POSCAR -> StructureContext）、带待确认项的 plan 预览、确定性状态、
zip 下载，以及 BE-A 错误到统一错误封装的映射。"""

from __future__ import annotations

import io
import tempfile
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.v1 import deps
from app.main import app
from app.schemas.detected import DetectedFile, DetectedRun

client = TestClient(app)

FE2O3_POSCAR = """Fe2O3
1.0
5.03 0.0 0.0
-2.515 4.356 0.0
0.0 0.0 13.75
Fe O
2 3
Direct
0.0 0.0 0.0
0.5 0.5 0.5
0.3 0.3 0.25
0.7 0.7 0.5
0.1 0.1 0.75
"""


def _seed_run(diag_id: str, poscar: str = FE2O3_POSCAR) -> str:
    dest = Path(tempfile.mkdtemp(prefix="wf_test_")) / diag_id
    dest.mkdir(parents=True)
    if poscar is not None:
        (dest / "POSCAR").write_text(poscar, encoding="utf-8")
    detected = DetectedRun(
        root=diag_id,
        files=[DetectedFile(name="POSCAR", kind="poscar",
                            size=0, path="POSCAR")],
        missing_recommended=[],
        candidate_job_logs=[],
    )
    deps.store.create(diag_id, detected, dest)
    return diag_id


def _relax_static(wf_id: str = "wf_local") -> dict:
    return {"workflow": {
        "workflow_id": wf_id,
        "requested_tasks": ["relax", "static"],
        "goal_text": "relax then static",
        "confirm": True,
    }}


def test_plan_from_diagnosis_returns_confirmations():
    diag_id = _seed_run("diag_plan_ok")
    r = client.post("/api/v1/workflows/plan",
                    json={"diagnosis_id": diag_id, **_relax_static("wf_plan_ok")})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["workflow_id"]
    assert [s["task"] for s in data["steps"]] == ["relax", "static"]
    assert "file_inheritance_plan" in data
    assert "warnings" in data
    if data["needs_confirmation"]:
        assert data["status"] == "needs_confirmation"
        assert data["confirmations"]


def test_plan_with_explicit_structure():
    payload = {
        "workflow": {
            "structure": {
                "formula": "Fe2O3",
                "elements": ["Fe", "O"],
                "counts": [2, 3],
                "poscar_text": FE2O3_POSCAR,
                "transition_metals": ["Fe"],
            },
            "requested_tasks": ["relax"],
        }
    }
    r = client.post("/api/v1/workflows/plan", json=payload)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["workflow_id"]


def test_generate_and_download_roundtrip():
    diag_id = _seed_run("diag_gen_ok")
    g = client.post("/api/v1/workflows/generate",
                    json={"diagnosis_id": diag_id, **_relax_static("wf_gen_ok")})
    assert g.status_code == 200, g.text
    data = g.json()["data"]
    assert data["workflow_status"] == "generated"
    url = data["download_url"]
    assert url.startswith("/api/v1/workflows/")

    d = client.get(url)
    assert d.status_code == 200
    assert d.headers["content-type"].startswith("application/zip")
    zf = zipfile.ZipFile(io.BytesIO(d.content))
    names = zf.namelist()
    for expected in ("workflow_plan.json", "workflow_manifest.json",
                     "README_run_order.md", "01_relax/POSCAR",
                     "01_relax/INCAR", "01_relax/KPOINTS", "01_relax/submit.sh"):
        assert expected in names, expected
    assert any("02_static/POSCAR" in n for n in names)


def test_generate_without_confirmation_is_409():
    diag_id = _seed_run("diag_conf_needed")
    payload = {"diagnosis_id": diag_id, "workflow": {
        "requested_tasks": ["relax", "static"],
        "confirm": False,
    }}
    r = client.post("/api/v1/workflows/generate", json=payload)
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "RECIPE_CONFIRMATION_REQUIRED"


def test_dftu_unconfirmed_is_409():
    diag_id = _seed_run("diag_dftu")
    payload = {"diagnosis_id": diag_id, "workflow": {
        "requested_tasks": ["relax"],
        "confirm": True,
        "dftu": {"enabled": True, "entries": []},
    }}
    r = client.post("/api/v1/workflows/generate", json=payload)
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "DFTU_CONFIRMATION_REQUIRED"


def test_missing_diagnosis_404():
    r = client.post("/api/v1/workflows/plan", json={"diagnosis_id": "diag_nope"})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "DIAGNOSIS_NOT_FOUND"


def test_no_structure_required_409():
    r = client.post("/api/v1/workflows/plan", json={})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "STRUCTURE_REQUIRED"


def test_download_unknown_workflow_404():
    r = client.get("/api/v1/workflows/wf_nope/download")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "WORKFLOW_NOT_FOUND"


def test_diagnosis_without_poscar_404():
    diag_id = _seed_run("diag_no_poscar", poscar=None)
    r = client.post("/api/v1/workflows/plan", json={"diagnosis_id": diag_id})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "STRUCTURE_NOT_FOUND"

def test_plan_returns_workflow_status_alias():
    diag_id = _seed_run("diag_plan_alias")
    r = client.post("/api/v1/workflows/plan",
                    json={"diagnosis_id": diag_id, **_relax_static("wf_plan_alias")})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["workflow_status"] == data["status"]


def test_get_planned_workflow_metadata():
    diag_id = _seed_run("diag_plan_get")
    r = client.post("/api/v1/workflows/plan",
                    json={"diagnosis_id": diag_id, **_relax_static("wf_plan_get")})
    assert r.status_code == 200, r.text
    wf_id = r.json()["data"]["workflow_id"]
    g = client.get(f"/api/v1/workflows/{wf_id}")
    assert g.status_code == 200, g.text
    data = g.json()["data"]
    assert data["workflow_id"] == wf_id
    assert data["workflow_status"] in ("planned", "needs_confirmation")
    assert data["plan"]["schema_version"] == "1.0"
    assert [s["task"] for s in data["plan"]["steps"]] == ["relax", "static"]
    assert "confirmations" in data and "warnings" in data


def test_get_generated_workflow_metadata():
    diag_id = _seed_run("diag_gen_get")
    g = client.post("/api/v1/workflows/generate",
                    json={"diagnosis_id": diag_id, **_relax_static("wf_gen_get")})
    assert g.status_code == 200, g.text
    wf_id = g.json()["data"]["workflow_id"]
    meta = client.get(f"/api/v1/workflows/{wf_id}")
    assert meta.status_code == 200, meta.text
    data = meta.json()["data"]
    assert data["workflow_status"] == "generated"
    assert isinstance(data["revision"], int)
    assert "file_tree" in data
    assert data["validation"]["valid"] is True
    assert data["download_url"].endswith(f"{wf_id}/download")


def test_get_unknown_workflow_404():
    r = client.get("/api/v1/workflows/wf_nope")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "WORKFLOW_NOT_FOUND"
