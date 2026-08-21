from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from app.agent.fallback import resolve_fallback
from app.agent.orchestrator import AgentOrchestrator
from app.agent.tools import AgentState, doctor_tool_defs, doctor_tool_names
from app.core.config import Settings
from app.main import app
from app.services.diagnosis_service import DiagnosisService, detect_files
from app.services.run_store import RunStore

client = TestClient(app)
SAMPLE = Path(__file__).resolve().parents[1] / "examples" / "sample_run"


def _settings() -> Settings:
    s = Settings()
    s.feature_flags.llm_enabled = False
    return s


def _make_store() -> RunStore:
    store = RunStore(ttl_seconds=3600)
    store.create("diag_agent_test", detect_files(SAMPLE), SAMPLE)
    return store


def _sample_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(SAMPLE.iterdir()):
            if p.is_file():
                zf.writestr(p.name, p.read_text(encoding="utf-8", errors="replace"))
    return buf.getvalue()


# ---- tool schemas ----
def test_tool_schemas_inlined_and_matched_to_design():
    defs = doctor_tool_defs()
    assert [t["name"] for t in defs] == [
        "run_diagnosis", "generate_fix", "generate_report"]
    for t in defs:
        raw = json.dumps(t["input_schema"], ensure_ascii=False)
        assert "$ref" not in raw
        assert "$defs" not in raw
        assert t["description"]
    fix = defs[1]["input_schema"]
    assert set(fix["required"]) == {"diagnosis_id", "issue_ids", "user_confirmed"}
    rep = defs[2]["input_schema"]
    assert rep["required"] == ["diagnosis_id", "language"]
    assert rep["properties"]["language"]["enum"] == ["zh-CN", "en"]
    assert doctor_tool_names() == {"run_diagnosis", "generate_fix",
                                   "generate_report"}


# ---- fallback ----
def test_fallback_missing_diagnosis_id():
    fb = resolve_fallback("帮我诊断一下", AgentState())
    assert fb.confirmations and fb.confirmations[0]["field"] == "diagnosis_id"


def test_fallback_run_with_id():
    st = AgentState(diagnosis_id="diag_agent_test", upload_ok=True)
    fb = resolve_fallback("run diagnosis", st)
    assert fb.intent == "run_diagnosis"
    assert fb.args["diagnosis_id"] == "diag_agent_test"


def test_fallback_fix_requires_selection_and_confirm():
    st = AgentState(diagnosis_id="diag_agent_test", has_result=True,
                    available_issue_ids=["ISS-1"])
    fb = resolve_fallback("帮我修复这个问题", st)
    assert fb.intent == "generate_fix"
    assert fb.confirmations and fb.confirmations[0]["field"] == "issue_ids"


# ---- orchestrator: rule_based ----
def test_handle_rule_based_flow():
    store = _make_store()
    orch = AgentOrchestrator(_settings(), store, DiagnosisService())

    r1 = orch.handle("诊断一下", diagnosis_id="diag_agent_test")
    assert r1.status == "ok"
    assert r1.mode == "rule_based"
    assert r1.calls[0].name == "run_diagnosis"
    assert r1.calls[0].ok
    assert r1.calls[0].data["diagnosis_status"] == "succeeded"

    r2 = orch.handle("生成报告", diagnosis_id="diag_agent_test")
    assert r2.status == "ok"
    assert r2.calls[0].name == "generate_report"
    assert r2.calls[0].data["report_ready"] is True

    r3 = orch.handle("修复问题", diagnosis_id="diag_agent_test")
    assert r3.status == "confirmation_needed"
    assert r3.confirmations


def test_structured_call_generate_report():
    store = _make_store()
    orch = AgentOrchestrator(_settings(), store, DiagnosisService())
    orch.handle("诊断一下", diagnosis_id="diag_agent_test")
    resp = orch.handle(
        "出报告", diagnosis_id="diag_agent_test",
        structured_call={"name": "generate_report",
                         "arguments": {"diagnosis_id": "diag_agent_test",
                                       "language": "zh-CN"}})
    assert resp.status == "ok"
    assert resp.calls[0].name == "generate_report"
    assert resp.calls[0].data["report_id"]


def test_generate_fix_requires_confirmation_then_generates():
    store = _make_store()
    orch = AgentOrchestrator(_settings(), store, DiagnosisService())
    run = orch.handle("诊断一下", diagnosis_id="diag_agent_test")
    issue_ids = [i["issue_id"] for i in run.calls[0].data["issues"]]
    assert issue_ids
    iid = issue_ids[0]

    blocked = orch.handle(
        "修复", diagnosis_id="diag_agent_test",
        structured_call={"name": "generate_fix",
                         "arguments": {"diagnosis_id": "diag_agent_test",
                                       "issue_ids": [iid],
                                       "user_confirmed": False}})
    assert blocked.calls[0].error_code == "CONFIRMATION_REQUIRED"
    assert blocked.status == "confirmation_needed"

    confirmed = orch.handle(
        "修复", diagnosis_id="diag_agent_test",
        structured_call={"name": "generate_fix",
                         "arguments": {"diagnosis_id": "diag_agent_test",
                                       "issue_ids": [iid],
                                       "user_confirmed": True}})
    assert confirmed.calls[0].ok
    assert "fix_available" in confirmed.calls[0].data


# ---- orchestrator: LLM mode + downgrade ----
class FakeCompleter:
    def __init__(self, payload):
        self._payload = payload
        self.calls = 0

    def complete(self, messages):
        self.calls += 1
        return json.dumps(self._payload, ensure_ascii=False)


def test_handle_llm_resolves_calls():
    store = _make_store()
    orch = AgentOrchestrator(_settings(), store, DiagnosisService())
    fake = FakeCompleter({"calls": [{"name": "run_diagnosis",
                                     "arguments": {"diagnosis_id": "diag_agent_test"}}],
                          "explanation": "将运行结构化诊断"})
    resp = orch.handle("跑一下诊断", diagnosis_id="diag_agent_test",
                       use_llm=True, llm_client=fake)
    assert fake.calls == 1
    assert resp.mode == "rule_plus_llm"
    assert resp.llm_generated
    assert resp.calls[0].name == "run_diagnosis"
    assert resp.calls[0].ok


def test_handle_llm_unknown_tool_falls_back():
    store = _make_store()
    orch = AgentOrchestrator(_settings(), store, DiagnosisService())
    fake = FakeCompleter({"calls": [{"name": "rm_rf_everything", "arguments": {}}],
                          "explanation": "..."})
    resp = orch.handle("诊断一下", diagnosis_id="diag_agent_test",
                       use_llm=True, llm_client=fake)
    assert resp.degraded
    assert resp.mode == "rule_based"
    assert resp.calls and resp.calls[0].name == "run_diagnosis"


def test_handle_llm_exception_falls_back():
    class Boom:
        def complete(self, messages):
            raise RuntimeError("network down")

    store = _make_store()
    orch = AgentOrchestrator(_settings(), store, DiagnosisService())
    resp = orch.handle("诊断一下", diagnosis_id="diag_agent_test",
                       use_llm=True, llm_client=Boom())
    assert resp.degraded
    assert resp.mode == "rule_based"


# ---- HTTP endpoint ----
def test_agent_http_endpoint_run_then_report():
    up = client.post("/api/v1/diagnosis/upload",
                     files={"file": ("sample.zip", _sample_zip(),
                                     "application/zip")})
    diag = up.json()["data"]["diagnosis_id"]

    r1 = client.post("/api/v1/agent/handle",
                     json={"command": "诊断一下", "diagnosis_id": diag})
    body = r1.json()["data"]
    assert body["status"] == "ok"
    assert body["calls"][0]["name"] == "run_diagnosis"
    assert body["calls"][0]["data"]["diagnosis_status"] == "succeeded"

    r2 = client.post("/api/v1/agent/handle",
                     json={"command": "生成报告", "diagnosis_id": diag})
    body2 = r2.json()["data"]
    assert body2["calls"][0]["name"] == "generate_report"
    assert body2["calls"][0]["data"]["report_ready"] is True
