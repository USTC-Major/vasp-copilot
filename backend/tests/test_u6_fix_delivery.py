"""Offline U-6 evidence: recommendations are not downloadable artifacts."""
from __future__ import annotations

import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.api.v1 import diagnosis as api
from app.agent.tools import AgentState, DoctorTools, GenerateFixArgs, RunDiagnosisArgs
from app.diagnostics.fixes import FixGenerator
from app.main import app
from app.parsers.incar import parse_incar
from app.schemas.fix import RecommendedFix
from app.schemas.issue import Issue, Recommendation
from app.schemas.parsed import ParsedRunData
from app.schemas.status import FixStatus, Severity
from app.services.diagnosis_service import DiagnosisService, detect_files
from app.services.run_store import RunStore

TEXT = "SYSTEM = test\nNELM = 60\nISMEAR = 0\n"


def issue(parameter="NELM", new_value="200", action="set_parameter", auto_fixable=True):
    return Issue(issue_id="I-U6", rule_id="R-U6", severity=Severity.MEDIUM,
                 title="受控建议", auto_fixable=auto_fixable,
                 recommendations=[Recommendation(action=action, target="INCAR",
                                                 parameter=parameter, new_value=new_value,
                                                 rationale="受控问题依据")])


@pytest.fixture
def flow(tmp_path, monkeypatch):
    (tmp_path / "INCAR").write_text(TEXT, encoding="utf-8")
    service = DiagnosisService()
    monkeypatch.setattr(service._engine, "run", lambda _: [issue()])
    store = RunStore()
    record = store.create("diag_u6", detect_files(tmp_path), tmp_path)
    monkeypatch.setattr(api, "store", store)
    monkeypatch.setattr(api, "diagnosis_service", service)
    return TestClient(app), service, record


def run(client):
    response = client.post("/api/v1/diagnosis/run", json={"diagnosis_id": "diag_u6"})
    assert response.status_code == 200, response.text
    return response.json()["data"]


def get(client):
    return client.get("/api/v1/diagnosis/diag_u6").json()["data"]


def download(client):
    return client.get("/api/v1/diagnosis/diag_u6/download-fix")


def test_valid_zip_matches_get_diff_json_manual_and_original(flow):
    client, _, record = flow
    assert run(client)["fix_available"] is True
    data = get(client)
    fix = data["recommended_fixes"][0]
    assert data["fix_reason_code"] == fix["reason_code"] == "candidate_ready"
    assert data["fix_available"] is True
    before = record.base_dir.joinpath("INCAR").read_bytes()
    response = download(client)
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        prefix = fix["fix_id"] + "/"
        assert set(archive.namelist()) == {prefix + n for n in ("INCAR.fixed", "parameter_diff.json", "APPLY_MANUALLY.md")}
        candidate = archive.read(prefix + "INCAR.fixed").decode()
        metadata = json.loads(archive.read(prefix + "parameter_diff.json"))
        manual = archive.read(prefix + "APPLY_MANUALLY.md").decode()
    assert parse_incar(candidate).effective["NELM"] == 200
    assert metadata["fix_id"] == fix["fix_id"]
    assert metadata["changes"] == fix["changes"]
    assert "- NELM = 60\n+ NELM = 200" == fix["diff"]
    assert "| `NELM` | replace | 60 | 200 |" in manual
    assert all(step in manual for step in fix["manual_steps"])
    assert record.base_dir.joinpath("INCAR").read_bytes() == before
    assert get(client)["recommended_fixes"][0]["fix_status"] == "generated"
    assert client.get("/api/v1/diagnosis/diag_u6/report").status_code == 200


@pytest.mark.parametrize("scenario,code", [
    ("no_issues", "no_issues"), ("no_rule", "no_rule"),
    ("missing_incar", "missing_incar"), ("missing_value", "missing_value"),
    ("no_changes", "no_changes"), ("manual", "manual_review"),
    ("whitelist", "safety_rejected"), ("safety", "safety_rejected"),
    ("generation_failed", "generation_failed"),
])
def test_unavailable_reason_and_report_survive(flow, monkeypatch, scenario, code):
    client, service, record = flow
    if scenario == "missing_incar":
        record.base_dir.joinpath("INCAR").unlink()
    elif scenario == "no_issues":
        monkeypatch.setattr(service._engine, "run", lambda _: [])
    elif scenario == "no_rule":
        monkeypatch.setattr(service._engine, "run", lambda _: [Issue(issue_id="I-X", rule_id="R-X", severity=Severity.LOW)])
    elif scenario == "missing_value":
        monkeypatch.setattr(service._engine, "run", lambda _: [issue(new_value=None)])
    elif scenario == "no_changes":
        monkeypatch.setattr(service._engine, "run", lambda _: [issue(new_value="60")])
    elif scenario == "manual":
        monkeypatch.setattr(service._engine, "run", lambda _: [issue(auto_fixable=False)])
    elif scenario == "whitelist":
        monkeypatch.setattr(service._engine, "run", lambda _: [issue(parameter="ISIF", new_value="3")])
    elif scenario == "safety":
        monkeypatch.setattr(service._fixer, "_static_gate", lambda *args: ["受控静态复核拒绝"])
    elif scenario == "generation_failed":
        def fail(**kwargs):
            raise RuntimeError("private generator detail")
        monkeypatch.setattr(service._fixer, "generate", fail)
    data = run(client)
    assert data["fix_available"] is False
    assert data["fix_reason_code"] == code
    result = get(client)
    assert result["fix_reason_code"] == code
    assert result["fix_manual_steps"]
    assert result["recommended_fixes"][0]["reason_code"] == code
    assert record.fix_files == {}
    response = download(client)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "FIX_NOT_AVAILABLE"
    assert "private generator detail" not in response.text
    assert client.get("/api/v1/diagnosis/diag_u6/report").status_code == 200


@pytest.mark.parametrize("fault,code", [
    ("missing_file", "candidate_missing"), ("empty_file", "candidate_missing"),
    ("empty_diff", "candidate_invalid"), ("empty_changes", "candidate_invalid"),
    ("json_mismatch", "candidate_invalid"), ("candidate_mismatch", "candidate_invalid"),
    ("manual_mismatch", "candidate_invalid"), ("source_changed", "candidate_invalid"),
    ("unsafe_path", "candidate_invalid"),
])
def test_tampered_or_missing_candidates_fail_closed(flow, fault, code):
    client, _, record = flow
    run(client)
    fix = record.result.recommended_fixes[0]
    files = record.fix_files[fix.fix_id]
    if fault == "missing_file":
        del files["INCAR.fixed"]
    elif fault == "empty_file":
        files["APPLY_MANUALLY.md"] = " "
    elif fault == "empty_diff":
        fix.diff = "  "
    elif fault == "empty_changes":
        fix.changes = []
    elif fault == "json_mismatch":
        metadata = json.loads(files["parameter_diff.json"])
        metadata["fix_id"] = "FIX-OTHER"
        files["parameter_diff.json"] = json.dumps(metadata)
    elif fault == "candidate_mismatch":
        files["INCAR.fixed"] = files["INCAR.fixed"].replace("200", "100")
    elif fault == "manual_mismatch":
        files["APPLY_MANUALLY.md"] = files["APPLY_MANUALLY.md"].replace("| 60 | 200 |", "| 60 | 100 |")
    elif fault == "source_changed":
        record.base_dir.joinpath("INCAR").write_text(TEXT.replace("60", "80"), encoding="utf-8")
    elif fault == "unsafe_path":
        del record.fix_files[fix.fix_id]
        fix.fix_id = "../escape"
        record.fix_files[fix.fix_id] = files
    data = get(client)
    assert data["fix_available"] is False
    assert data["fix_reason_code"] == data["recommended_fixes"][0]["reason_code"] == code
    assert download(client).status_code == 409
    assert client.get("/api/v1/diagnosis/diag_u6/report").status_code == 200


def test_rerun_without_candidate_removes_old_package_and_recovers(flow, monkeypatch):
    client, service, record = flow
    assert run(client)["fix_available"]
    monkeypatch.setattr(service._engine, "run", lambda _: [])
    assert not run(client)["fix_available"]
    assert record.fix_files == {}
    assert download(client).status_code == 409
    monkeypatch.setattr(service._engine, "run", lambda _: [issue()])
    assert run(client)["fix_available"]
    assert download(client).status_code == 200


def test_legacy_valid_files_and_fields_are_compatible(flow):
    client, _, record = flow
    run(client)
    current = record.result.recommended_fixes[0]
    payload = current.model_dump(exclude={"reason_code", "reason", "manual_steps"})
    record.result.recommended_fixes = [RecommendedFix.model_validate(payload)]
    files = record.fix_files[current.fix_id]
    files["APPLY_MANUALLY.md"] = files["APPLY_MANUALLY.md"].split("\n## 原因与风险")[0]
    files["../unrelated.txt"] = "must not enter archive"
    assert get(client)["fix_reason_code"] == "candidate_ready"
    response = download(client)
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert len(archive.namelist()) == 3
        assert not any("unrelated" in name for name in archive.namelist())


def test_legacy_orphan_package_never_authorizes_download(flow):
    client, _, record = flow
    run(client)
    record.result.recommended_fixes = []
    assert record.fix_files
    assert get(client)["fix_available"] is False
    assert download(client).status_code == 409


def test_legacy_missing_incar_and_empty_recommendations(flow):
    client, _, record = flow
    run(client)
    record.result.recommended_fixes = []
    record.base_dir.joinpath("INCAR").unlink()
    assert get(client)["fix_reason_code"] == "missing_incar"
    assert download(client).status_code == 409


def test_download_packaging_failure_is_retryable_and_recovers(flow, monkeypatch):
    client, _, _ = flow
    run(client)
    original = api.zipfile.ZipFile.writestr
    with monkeypatch.context() as scoped:
        def fail(*args, **kwargs):
            raise OSError("private package detail")
        scoped.setattr(api.zipfile.ZipFile, "writestr", fail)
        response = download(client)
        assert response.status_code == 409
        error = response.json()["error"]
        assert error["code"] == "FIX_PACKAGE_FAILED"
        assert error["retryable"] is True
        assert "private package detail" not in response.text
        assert get(client)["fix_available"] is True
        assert client.get("/api/v1/diagnosis/diag_u6/report").status_code == 200
    assert api.zipfile.ZipFile.writestr == original
    assert download(client).status_code == 200


@pytest.mark.parametrize("text,rec", [
    (TEXT, issue(new_value="60.0")),
    (TEXT, issue(parameter="AMIX", action="remove_parameter", new_value=None)),
])
def test_generator_filters_effective_noops(text, rec):
    parsed = ParsedRunData(incar=parse_incar(text), source_files=["INCAR"])
    fix, files = FixGenerator().generate(parsed=parsed, issues=[rec], incar_text=text)
    assert fix.reason_code == "no_changes"
    assert fix.fix_status == FixStatus.UNAVAILABLE
    assert not fix.changes and not fix.diff and files == {}


def test_remove_duplicate_parameter_fails_safety_gate():
    text = "SYSTEM = test\nNELM = 30\nNELM = 60\n"
    parsed = ParsedRunData(incar=parse_incar(text), source_files=["INCAR"])
    fix, files = FixGenerator().generate(parsed=parsed, issues=[issue(action="remove_parameter", new_value=None)], incar_text=text)
    assert fix.fix_status == FixStatus.PROPOSED
    assert fix.reason_code == "safety_rejected"
    assert fix.safe_to_generate is False and files == {}


@pytest.mark.parametrize("extra", ["ISMEAR", "MYCUSTOM"])
def test_inline_unplanned_parameter_loss_is_rejected(extra):
    text = f"SYSTEM = test\nNELM = 60; {extra} = 3\n"
    parsed = ParsedRunData(incar=parse_incar(text), source_files=["INCAR"])
    fix, files = FixGenerator().generate(parsed=parsed, issues=[issue()], incar_text=text)
    assert fix.safe_to_generate is False
    assert fix.reason_code == "safety_rejected"
    assert files == {}
    assert any("多个赋值" in warning for warning in fix.warnings)


@pytest.mark.parametrize("code", ["no_issues", "no_rule", "no_changes", "missing_incar", "missing_value", "manual_review", "safety_rejected"])
def test_no_candidate_steps_do_not_reference_nonexistent_fixed_input(code):
    from app.diagnostics.fixes import fix_reason_fields
    assert "INCAR.fixed" not in " ".join(fix_reason_fields(code)["manual_steps"])


def test_agent_run_clears_old_candidates(flow, monkeypatch):
    client, service, record = flow
    assert run(client)["fix_available"]
    monkeypatch.setattr(service._engine, "run", lambda _: [])
    tools = DoctorTools(api.settings, api.store, service)
    response = tools.run_diagnosis(RunDiagnosisArgs(diagnosis_id="diag_u6"), AgentState())
    assert response.ok and response.data["fix_available"] is False
    assert record.fix_files == {}
    assert download(client).status_code == 409


def test_agent_selected_candidate_updates_recommendation_and_delivery(flow, monkeypatch):
    client, service, record = flow
    other = issue(parameter="AMIX", new_value="0.4").model_copy(update={"issue_id": "I-OTHER"})
    monkeypatch.setattr(service._engine, "run", lambda _: [issue(), other])
    run(client)
    tools = DoctorTools(api.settings, api.store, service)
    response = tools.generate_fix(GenerateFixArgs(diagnosis_id="diag_u6", issue_ids=["I-OTHER"], user_confirmed=True), AgentState())
    assert response.ok and response.data["fix_available"] is True
    assert record.result.recommended_fixes[0].issue_ids == ["I-OTHER"]
    assert get(client)["fix_available"] is True
    assert download(client).status_code == 200
    assert "AMIX" in record.report_text


def test_agent_failed_candidate_clears_package_and_preserves_report(flow, monkeypatch):
    client, service, record = flow
    run(client)
    tools = DoctorTools(api.settings, api.store, service)
    def fail(**kwargs):
        raise RuntimeError("private generator detail")
    monkeypatch.setattr(tools._fixer, "generate", fail)
    response = tools.generate_fix(GenerateFixArgs(diagnosis_id="diag_u6", issue_ids=["I-U6"], user_confirmed=True), AgentState())
    assert response.ok and response.data["fix_available"] is False
    assert response.data["fix_reason_code"] == "generation_failed"
    assert record.fix_files == {}
    assert download(client).status_code == 409
    assert client.get("/api/v1/diagnosis/diag_u6/report").status_code == 200


def test_legacy_empty_proposed_fix_is_not_a_confirmation_wait(flow):
    client, _, record = flow
    run(client)
    record.result.issues = []
    record.result.recommended_fixes = [RecommendedFix(fix_id="FIX-EMPTY", target_file="INCAR")]
    data = get(client)
    assert data["fix_reason_code"] == "no_issues"
    assert data["fix_available"] is False
    assert download(client).status_code == 409


@pytest.mark.parametrize("packages", [None, {"FIX-I-U6": None}, {"FIX-I-U6": {"INCAR.fixed": 123}}])
def test_legacy_malformed_packages_fail_closed(flow, packages):
    client, _, record = flow
    run(client)
    record.fix_files = packages
    assert get(client)["fix_reason_code"] == "candidate_missing"
    assert download(client).status_code == 409


def test_generator_rejects_unplanned_known_parameter_change(flow, monkeypatch):
    client, service, _ = flow
    apply = service._fixer._apply
    def apply_badly(parsed, plan):
        text, diff = apply(parsed, plan)
        return text.replace("ISMEAR = 0", "ISMEAR = 1"), diff
    monkeypatch.setattr(service._fixer, "_apply", apply_badly)
    data = run(client)
    assert data["fix_reason_code"] == "safety_rejected"
    assert data["fix_available"] is False
    assert any("ISMEAR" in w for w in get(client)["recommended_fixes"][0]["warnings"])
    assert download(client).status_code == 409


def test_touched_line_comment_assignment_does_not_block_valid_candidate(flow):
    client, _, record = flow
    record.base_dir.joinpath("INCAR").write_text(TEXT.replace("NELM = 60", "NELM = 60 # note: ISMEAR = 3"), encoding="utf-8")
    assert run(client)["fix_available"] is True
    assert download(client).status_code == 200


def test_missing_value_whitespace_is_explicit(flow, monkeypatch):
    client, service, _ = flow
    monkeypatch.setattr(service._engine, "run", lambda _: [issue(new_value="   ")])
    assert run(client)["fix_reason_code"] == "missing_value"
    assert download(client).status_code == 409
