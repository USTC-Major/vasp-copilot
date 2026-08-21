from __future__ import annotations

import io
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.core.errors import NotFoundError
from app.main import app
from app.schemas.detected import DetectedRun
from app.services.run_store import RunStore

client = TestClient(app)


def _zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("OSZICAR", (
            "  1 F=-20.0 E0=-20.0 d E=-0.5 mag= 0.6\n"
            "  2 F=-20.1 E0=-20.1 d E=-0.1 mag= 0.58\n"
        ))
        zf.writestr("OUTCAR", (
            " vasp.6.3.0 21-Apr-2023\n"
            " ISPIN = 2\n"
            " magnetization (x)\n"
            " # of ion       s       p       d       tot\n"
            " ----------------------------------------------------\n"
            "    1    0.100    0.200    0.300    0.600\n"
            "    2    0.050    0.100    0.150    0.300\n"
            " ----------------------------------------------------\n"
            " tot    0.150    0.300    0.450    0.900\n"
        ))
    return buf.getvalue()


def _upload(session_id: str | None = None) -> str:
    params = {"session_id": session_id} if session_id else None
    r = client.post("/api/v1/diagnosis/upload",
                    params=params,
                    files={"file": ("run.zip", _zip(), "application/zip")})
    assert r.status_code == 200, r.text
    return r.json()["data"]["diagnosis_id"]


def test_run_returns_scf_and_magnetization_series():
    diag = _upload()
    r = client.post("/api/v1/diagnosis/run", json={"diagnosis_id": diag})
    assert r.status_code == 200, r.text
    g = client.get(f"/api/v1/diagnosis/{diag}").json()["data"]
    plots = g["plots"]
    assert plots["scf"]["x_label"] == "电子步"
    assert "series" in plots["scf"]
    assert plots["scf"]["series"]
    assert plots["scf"]["series"][0]["ionic_step"] == 1
    assert plots["scf"]["series"][0]["electronic_step"] == 1
    assert plots["scf"]["series"][1]["electronic_step"] == 2
    assert all("energy_ev" in p for p in plots["scf"]["series"])
    assert plots["magnetization"]["series"]
    atom = plots["magnetization"]["series"][0]
    assert atom["atom_index"] == 1 and atom["tot"] == 0.6


def test_upload_echoes_session_id():
    r = client.post("/api/v1/diagnosis/upload",
                    params={"session_id": "sess_demo_1"},
                    files={"file": ("run.zip", _zip(), "application/zip")})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["session_id"] == "sess_demo_1"


def test_upload_without_session_id_still_ok():
    diag = _upload()
    assert diag.startswith("diag_")


def test_run_store_expiry_removes_disk(tmp_path):
    store = RunStore(ttl_seconds=10)
    base = tmp_path / "runs" / "diag_zzz"
    base.mkdir(parents=True, exist_ok=True)
    (base / "INCAR").write_text("SYSTEM = x\n", encoding="utf-8")
    record = store.create("diag_zzz", DetectedRun(root="diag_zzz"), base)
    record.touched_at = time.time() - 100
    with pytest.raises(NotFoundError):
        store.get("diag_zzz")
    assert not base.exists()