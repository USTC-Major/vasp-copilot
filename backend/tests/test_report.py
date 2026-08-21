from __future__ import annotations

from app.report.generator import ReportGenerator
from app.report.next_step import compute_next_step
from app.schemas.detected import DetectedFile, DetectedRun
from app.schemas.issue import Evidence, Issue
from app.schemas.result import DiagnosisResult, Provenance
from app.schemas.status import DiagnosisStatus, Severity


def _issue(sev, issue_id="I-1"):
    return Issue(
        issue_id=issue_id, rule_id="R-T", severity=sev, category="parameters",
        title="title", summary="summary",
        evidence=[Evidence(file="INCAR", line=3, message="evidence msg")],
    )


def _result(issues, extra=None):
    return DiagnosisResult(
        diagnosis_id="D-1",
        diagnosis_status=DiagnosisStatus.SUCCEEDED,
        summary="ok summary",
        detected_run=DetectedRun(
            root="/tmp/run", run_type="static",
            files=[DetectedFile(name="INCAR", kind="incar")],
            missing_recommended=["OUTCAR"],
        ),
        issues=issues,
        recommended_fixes=extra or [],
        next_step=compute_next_step(issues=issues),
        provenance=Provenance(parser_version="0.1.0", rule_set_version="1"),
    )


def test_next_step_blocks_on_high():
    ns = compute_next_step(issues=[_issue(Severity.HIGH)])
    assert ns.allowed is False
    assert ns.suggested_task is None


def test_next_step_blocks_on_critical():
    ns = compute_next_step(issues=[_issue(Severity.CRITICAL), _issue(Severity.INFO)])
    assert ns.allowed is False


def test_next_step_allows_when_no_high():
    ns = compute_next_step(issues=[_issue(Severity.MEDIUM), _issue(Severity.LOW)])
    assert ns.allowed is True
    assert ns.suggested_task == "static"


def test_next_step_allows_when_empty():
    ns = compute_next_step(issues=[])
    assert ns.allowed is True


def test_report_generate_produces_markdown_and_metadata():
    result = _result([_issue(Severity.MEDIUM, "I-1"), _issue(Severity.HIGH, "I-2")])
    gen = ReportGenerator()
    body, meta = gen.generate(result)

    assert meta.format == "markdown"
    assert meta.report_id.startswith("RPT-")
    assert meta.diagnosis_id == "D-1"
    assert meta.size_bytes == len(body.encode("utf-8"))
    assert len(meta.sha256) == 64
    assert meta.sections == ["summary", "input_overview", "issues", "fixes",
                             "missing_evidence", "disclaimer"]
    assert "VASP-Doctor" in body
    assert "summary" in body
    assert "input_overview" in body
    assert "disclaimer" in body
    assert "Critical" in body
    assert "High" in body
