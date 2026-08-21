from __future__ import annotations

import tempfile
from pathlib import Path

import app.services.diagnosis_service as svc_mod
from app.parsers.vasprun import parse_vasprun
from app.services.diagnosis_service import (
    DiagnosisService, _kind_for, _load_parsed,
)

SAMPLE = """\
<?xml version="1.0" encoding="ISO-8859-1"?>
<modeling>
  <calculation>
    <energy>
      <i name="e_fr_energy">-100.50000000</i>
    </energy>
    <c name="reached_required_accuracy">F</c>
  </calculation>
  <calculation>
    <energy>
      <i name="e_fr_energy">-101.20000000</i>
    </energy>
    <c name="reached_required_accuracy">T</c>
  </calculation>
</modeling>
"""


def test_parse_vasprun_minimal_summary():
    info = parse_vasprun(SAMPLE)
    assert info.present is True
    assert info.truncated is False
    assert info.final_energy == -101.2
    assert info.converged is True
    assert info.n_ionic_steps == 2
    assert info.source_file == "vasprun.xml"


def test_kind_detection_vasprun():
    assert _kind_for("vasprun.xml") == "vasprun"
    assert _kind_for("VASPRUN.XML") == "vasprun"
    assert _kind_for("CONTCAR") == "concar"


def test_load_parsed_vasprun_optional_when_present():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "vasprun.xml").write_text(SAMPLE, encoding="utf-8")
        parsed = _load_parsed(root, None)
        assert parsed.vasprun is not None
        assert parsed.vasprun.final_energy == -101.2
        assert "vasprun.xml" in parsed.source_files


def test_load_parsed_vasprun_absent_is_none():
    with tempfile.TemporaryDirectory() as td:
        parsed = _load_parsed(Path(td), None)
        assert parsed.vasprun is None
        assert "vasprun.xml" not in parsed.source_files


def test_oversize_vasprun_marked_truncated(monkeypatch):
    monkeypatch.setattr(svc_mod, "VASPRUN_MAX_PARSE_BYTES", 8)
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "vasprun.xml").write_text(SAMPLE, encoding="utf-8")
        parsed = _load_parsed(root, None)
        assert parsed.vasprun is not None
        assert parsed.vasprun.truncated is True
        assert parsed.vasprun.final_energy is None
        assert "vasprun.xml" in parsed.source_files


def test_diagnosis_output_unchanged_by_optional_vasprun():
    svc = DiagnosisService()
    demo = (Path(__file__).resolve().parents[2] / "demo_cases" / "failed_runs"
            / "scf_reached_nelm")
    files = {}
    for child in sorted(demo.iterdir()):
        if child.is_file() and child.name not in ("input.zip", "case.yaml"):
            files[child.name] = child.read_text(encoding="utf-8")

    def run(with_vasprun: bool):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for name, text in files.items():
                (root / name).write_text(text, encoding="utf-8")
            if with_vasprun:
                (root / "vasprun.xml").write_text(SAMPLE, encoding="utf-8")
            parsed = _load_parsed(root, None)
            result, _body, _fix = svc.run_diagnosis(
                parsed, root, llm_explanation=False)
            return {i.rule_id for i in result.issues}

    assert run(True) == run(False), "optional vasprun must not change diagnosis"
