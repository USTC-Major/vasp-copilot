from __future__ import annotations

import pytest

from app.diagnostics.fixes import ALLOWED_FIX_WHITELIST, FixGenerator
from app.parsers.incar import parse_incar
from app.schemas.issue import Issue, Recommendation
from app.schemas.parsed import ParsedRunData
from app.schemas.status import FixStatus, Severity


def _parsed(incar_text: str) -> ParsedRunData:
    return ParsedRunData(incar=parse_incar(incar_text), source_files=["INCAR"])


def _issue(issue_id: str, rule_id: str, parameter: str, action: str,
           new_value=None, auto_fixable: bool = True) -> Issue:
    return Issue(
        issue_id=issue_id, rule_id=rule_id, severity=Severity.MEDIUM,
        category="parameters", title="t", auto_fixable=auto_fixable,
        recommendations=[Recommendation(
            action=action, target="INCAR", parameter=parameter,
            new_value=new_value, rationale="test rationale")],
    )


def test_whitelist_contains_core_params():
    for p in ("NBANDS", "ALGO", "AMIX", "BMIX", "MAXMIX", "NSW", "IBRION",
              "EDIFF", "EDIFFG", "SIGMA", "ISMEAR", "LMAXMIX", "ISPIN",
              "NELM", "NELMDL", "LREAL", "PREC", "ENCUT"):
        assert p in ALLOWED_FIX_WHITELIST


def test_generate_replace_whitelist_produces_files():
    text = "SYSTEM = test\nNELM = 60\nISMEAR = 0\n"
    parsed = _parsed(text)
    issue = _issue("I-001", "R-NELM", "NELM", "set_parameter", "200")
    gen = FixGenerator()
    fix, files = gen.generate(parsed=parsed, issues=[issue], incar_text=text)

    assert fix.safe_to_generate is True
    assert fix.fix_status == FixStatus.GENERATED
    assert fix.generated_file_id == "INCAR.fixed"
    assert fix.target_file == "INCAR"
    assert set(files.keys()) == {"INCAR.fixed", "parameter_diff.json", "APPLY_MANUALLY.md"}
    assert "NELM = 200" in files["INCAR.fixed"]
    # original text is not mutated
    assert "NELM = 60" in text
    assert "ISMEAR = 0" in files["INCAR.fixed"]


def test_generate_empty_incar_is_not_safe():
    parsed = _parsed("")
    issue = _issue("I-002", "R-NELM", "NELM", "set_parameter", "200")
    gen = FixGenerator()
    fix, files = gen.generate(parsed=parsed, issues=[issue], incar_text="   ")
    assert fix.safe_to_generate is False
    assert files == {}


def test_whitelist_rejection_returns_unavailable():
    text = "SYSTEM = test\nISIF = 2\n"
    parsed = _parsed(text)
    issue = _issue("I-003", "R-ISIF", "ISIF", "set_parameter", "3")
    gen = FixGenerator()
    fix, files = gen.generate(parsed=parsed, issues=[issue], incar_text=text)
    assert fix.safe_to_generate is False
    assert fix.fix_status == FixStatus.UNAVAILABLE
    assert files == {}


def test_add_parameter_when_missing():
    text = "SYSTEM = test\nISMEAR = 0\n"
    parsed = _parsed(text)
    issue = _issue("I-004", "R-NELM", "NELM", "add_parameter", "120")
    gen = FixGenerator()
    fix, files = gen.generate(parsed=parsed, issues=[issue], incar_text=text)
    assert fix.safe_to_generate is True
    assert "NELM = 120" in files["INCAR.fixed"]


def test_remove_parameter():
    text = "SYSTEM = test\nISMEAR = 0\n"
    parsed = _parsed(text)
    issue = _issue("I-005", "R-ISMEAR", "ISMEAR", "remove_parameter", None)
    gen = FixGenerator()
    fix, files = gen.generate(parsed=parsed, issues=[issue], incar_text=text)
    assert fix.safe_to_generate is True
    assert "ISMEAR = 0" not in files["INCAR.fixed"]


def test_roundtrip_preserves_unknown_params():
    text = "SYSTEM = test\nMYCUSTOM = 3\nNELM = 60\n"
    parsed = _parsed(text)
    issue = _issue("I-006", "R-NELM", "NELM", "set_parameter", "200")
    gen = FixGenerator()
    fix, files = gen.generate(parsed=parsed, issues=[issue], incar_text=text)
    assert fix.safe_to_generate is True
    assert "MYCUSTOM = 3" in files["INCAR.fixed"]
    re = parse_incar(files["INCAR.fixed"])
    assert "MYCUSTOM" in re.unknown


def test_missing_new_value_warns_and_not_safe():
    text = "SYSTEM = test\nNELM = 60\n"
    parsed = _parsed(text)
    issue = _issue("I-007", "R-NELM", "NELM", "set_parameter", None)
    gen = FixGenerator()
    fix, files = gen.generate(parsed=parsed, issues=[issue], incar_text=text)
    assert fix.safe_to_generate is False
    assert fix.fix_status == FixStatus.UNAVAILABLE
    assert any("R-NELM" in w for w in fix.warnings)


def test_number_formatting_uses_plain_text():
    text = "SYSTEM = test\nAMIX = 0.2\n"
    parsed = _parsed(text)
    issue = _issue("I-008", "R-AMIX", "AMIX", "set_parameter", "0.4")
    gen = FixGenerator()
    fix, files = gen.generate(parsed=parsed, issues=[issue], incar_text=text)
    assert fix.safe_to_generate is True
    assert "AMIX = 0.4" in files["INCAR.fixed"]
