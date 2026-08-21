from __future__ import annotations

import io
import zipfile

from fastapi.testclient import TestClient

from app.llm import reset_explainer, set_explainer
from app.llm.base import StubExplainer
from app.main import app

client = TestClient(app)


def _zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("INCAR", "SYSTEM = demo\nNELM = 60\nISMEAR = 0\n")
        zf.writestr("OSZICAR", "  1 F=-100.0    E0=-100.0  d E=-0.1\n")
    return buf.getvalue()


def _upload():
    r = client.post("/api/v1/diagnosis/upload",
                    files={"file": ("run.zip", _zip(), "application/zip")})
    return r.json()["data"]["diagnosis_id"]


def _run(diag_id, llm=False):
    return client.post("/api/v1/diagnosis/run",
                       json={"diagnosis_id": diag_id, "llm_explanation": llm})


def _get(diag_id):
    return client.get(f"/api/v1/diagnosis/{diag_id}").json()["data"]


def test_run_default_stays_rule_based():
    reset_explainer()
    diag = _upload()
    assert _run(diag, False).json()["data"]["mode"] == "rule_based"
    res = _get(diag)
    assert res["provenance"]["mode"] == "rule_based"
    assert res["provenance"]["llm_used"] is False
    assert res.get("llm_explanation") is None


def test_run_with_llm_enabled_via_stub():
    reset_explainer()
    set_explainer(StubExplainer("explain-text"))
    try:
        diag = _upload()
        assert _run(diag, True).json()["data"]["mode"] == "rule_plus_llm"
        res = _get(diag)
        assert res["provenance"]["mode"] == "rule_plus_llm"
        assert res["provenance"]["llm_used"] is True
        assert res["llm_explanation"] == "explain-text"
        report = client.get(f"/api/v1/diagnosis/{diag}/report").text
        assert "LLM 解释" in report
        assert "explain-text" in report
    finally:
        reset_explainer()


def test_run_llm_requested_but_disabled_degrades():
    reset_explainer()
    diag = _upload()
    assert _run(diag, True).json()["data"]["mode"] == "rule_based"


def test_explain_before_run():
    reset_explainer()
    diag = _upload()
    r = client.post(f"/api/v1/diagnosis/{diag}/explain", json={"question": "q"})
    assert r.status_code == 200
    assert "先运行诊断" in r.json()["data"]["answer"]


def test_explain_disabled():
    reset_explainer()
    diag = _upload()
    _run(diag, False)
    r = client.post(f"/api/v1/diagnosis/{diag}/explain", json={"question": "q"})
    assert r.status_code == 200
    assert "未启用" in r.json()["data"]["answer"]


def test_explain_with_stub_answers():
    reset_explainer()
    set_explainer(StubExplainer("x"))
    try:
        diag = _upload()
        _run(diag, False)
        r = client.post(f"/api/v1/diagnosis/{diag}/explain", json={"question": "q"})
        assert r.status_code == 200
        assert r.json()["data"]["answer"] == "zero"
    finally:
        reset_explainer()