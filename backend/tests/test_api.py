from __future__ import annotations

import io
import zipfile

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _make_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("INCAR", "SYSTEM = demo\nNELM = 60\nISMEAR = 0\n")
        zf.writestr("OSZICAR", "  1 F=-100.0    E0=-100.0  d E=-0.1\n")
    return buf.getvalue()


def _upload():
    resp = client.post("/api/v1/diagnosis/upload",
                       files={"file": ("run.zip", _make_zip(), "application/zip")})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["request_id"]
    diag_id = body["data"]["diagnosis_id"]
    assert diag_id.startswith("diag_")
    assert body["data"]["diagnosis_status"] == "uploaded"
    return diag_id


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_upload_returns_detected():
    diag_id = _upload()
    r = client.get(f"/api/v1/diagnosis/{diag_id}")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["diagnosis_status"] == "uploaded"
    assert data["diagnosis_id"] == diag_id
def test_upload_detected_design_fields():
    """对齐设计 6.8：detected.files 同时暴露 relative_path/size_bytes/sha256 与存量 name/size/path。"""
    r = client.post("/api/v1/diagnosis/upload",
                    files={"file": ("run.zip", _make_zip(), "application/zip")})
    assert r.status_code == 200, r.text
    files = r.json()["data"]["detected"]["files"]
    assert files, "expected at least one detected file"
    for f in files:
        assert f["relative_path"] == f["path"]
        assert f["size_bytes"] == f["size"]
        assert isinstance(f["relative_path"], str) and f["relative_path"]
        assert isinstance(f["size_bytes"], int)
        assert isinstance(f.get("sha256"), str) and len(f["sha256"]) == 64


def test_run_and_get_full_flow():
    diag_id = _upload()
    r = client.post("/api/v1/diagnosis/run", json={"diagnosis_id": diag_id})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["diagnosis_status"] == "succeeded"
    assert data["report_ready"] is True
    assert isinstance(data["issue_count"], dict)
    assert data["mode"] == "rule_based"

    g = client.get(f"/api/v1/diagnosis/{diag_id}")
    assert g.status_code == 200
    result = g.json()["data"]
    assert result["diagnosis_id"] == diag_id
    assert "issues" in result
    assert "next_step" in result
    assert "report" in result


def test_report_download():
    diag_id = _upload()
    client.post("/api/v1/diagnosis/run", json={"diagnosis_id": diag_id})
    r = client.get(f"/api/v1/diagnosis/{diag_id}/report")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    assert "VASP-Doctor" in r.text


def test_download_fix_available_after_run():
    diag_id = _upload()
    r = client.post("/api/v1/diagnosis/run", json={"diagnosis_id": diag_id})
    fix_available = r.json()["data"]["fix_available"]
    resp = client.get(f"/api/v1/diagnosis/{diag_id}/download-fix")
    if fix_available:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/zip")
    else:
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "FIX_NOT_AVAILABLE"


def test_download_fix_before_run_is_409():
    diag_id = _upload()
    r = client.get(f"/api/v1/diagnosis/{diag_id}/download-fix")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "FIX_NOT_AVAILABLE"


def test_unknown_diagnosis_404():
    r = client.get("/api/v1/diagnosis/diag_nope")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "DIAGNOSIS_NOT_FOUND"


def test_upload_rejects_non_zip():
    r = client.post("/api/v1/diagnosis/upload",
                    files={"file": ("run.zip", b"GIF89a......", "application/x-gif")})
    assert r.status_code == 415
    assert r.json()["error"]["code"] == "NOT_A_ZIP"


def _upload_preview():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("INCAR", "SYSTEM = x\n")
        zf.writestr("OUTCAR", "\n".join(f"line {i}" for i in range(600)))
        zf.writestr("POTCAR", "C ARE=0\n")
        zf.writestr("bin.dat", b"abc\x00def")
        zf.writestr("big.txt", "\n".join(f"row {i} content" for i in range(2500)))
    resp = client.post("/api/v1/diagnosis/upload",
                       files={"file": ("run.zip", buf.getvalue(), "application/zip")})
    assert resp.status_code == 200
    return resp.json()["data"]["diagnosis_id"]


def test_preview_outcar_limited_to_500_lines():
    diag_id = _upload_preview()
    r = client.get(f"/api/v1/diagnosis/{diag_id}/preview", params={"path": "OUTCAR"})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["name"] == "OUTCAR"
    assert data["kind"] == "outcar"
    assert data["policy"]["max_preview_lines"] == 500
    p = data["preview"]
    assert p["total_lines"] == 600
    assert p["end_line"] - p["start_line"] + 1 <= 500
    assert p["truncated"] is False
    assert p["next_cursor"] is None
    assert "line 0" in p["content"]


def test_preview_outcar_tail():
    diag_id = _upload_preview()
    r = client.get(f"/api/v1/diagnosis/{diag_id}/preview",
                   params={"path": "OUTCAR", "mode": "tail", "max_lines": 20})
    p = r.json()["data"]["preview"]
    assert p["end_line"] == p["total_lines"] == 600
    assert p["start_line"] == 600 - 20 + 1
    assert p["content"].endswith("line 599")
    assert p["next_cursor"] is None


def test_preview_normal_text_truncated_with_cursor():
    diag_id = _upload_preview()
    r = client.get(f"/api/v1/diagnosis/{diag_id}/preview",
                   params={"path": "big.txt", "max_lines": 100})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["kind"] == "file"
    p = data["preview"]
    assert p["total_lines"] == 2500
    assert p["end_line"] - p["start_line"] + 1 == 100
    assert p["truncated"] is True
    assert p["next_cursor"] is not None
    assert data["policy"]["max_preview_lines"] == 1000

    r2 = client.get(f"/api/v1/diagnosis/{diag_id}/preview",
                    params={"path": "big.txt", "cursor": p["next_cursor"]})
    p2 = r2.json()["data"]["preview"]
    assert p2["start_line"] == 101
    assert p2["truncated"] is True


def test_preview_potcar_403():
    diag_id = _upload_preview()
    r = client.get(f"/api/v1/diagnosis/{diag_id}/preview", params={"path": "POTCAR"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "FILE_PREVIEW_POLICY_DENIED"


def test_preview_binary_415():
    diag_id = _upload_preview()
    r = client.get(f"/api/v1/diagnosis/{diag_id}/preview", params={"path": "bin.dat"})
    assert r.status_code == 415
    assert r.json()["error"]["code"] == "FILE_PREVIEW_UNSUPPORTED_BINARY"


def test_preview_traversal_rejected():
    diag_id = _upload_preview()
    r = client.get(f"/api/v1/diagnosis/{diag_id}/preview", params={"path": "../secret"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "PATH_TRAVERSAL"


def test_preview_missing_file_404():
    diag_id = _upload_preview()
    r = client.get(f"/api/v1/diagnosis/{diag_id}/preview", params={"path": "NOPE"})
    assert r.status_code == 404


def test_preview_invalid_cursor_422():
    diag_id = _upload_preview()
    r = client.get(f"/api/v1/diagnosis/{diag_id}/preview",
                   params={"path": "big.txt", "cursor": "!!!not-a-cursor!!!"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "INVALID_CURSOR"

# ---------- CORS (MVP 13.2) ----------
def test_cors_preflight_allows_configured_origin():
    r = client.options(
        "/api/v1/diagnosis/upload",
        headers={"Origin": "http://localhost:5173",
                 "Access-Control-Request-Method": "POST"},
    )
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_cors_preflight_denies_unlisted_origin():
    r = client.options(
        "/api/v1/diagnosis/upload",
        headers={"Origin": "http://evil.example",
                 "Access-Control-Request-Method": "POST"},
    )
    assert r.headers.get("access-control-allow-origin") != "http://evil.example"