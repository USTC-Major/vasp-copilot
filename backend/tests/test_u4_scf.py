"""Controlled SCF evidence fixtures: no LLM, remote materials or HPC calls."""
from __future__ import annotations

import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.api.v1.diagnosis import _plots_compat
from app.main import app
from app.parsers.incar import parse_incar
from app.parsers.oszicar import parse_oszicar
from app.parsers.outcar import parse_outcar
from app.schemas.parsed import ElectronicStep, OszicarData, ParsedRunData
from app.services.diagnosis_service import _build_plots


def _scf(oszicar: str, incar: str = "", outcar: str = "") -> dict:
    parsed = ParsedRunData(oszicar=parse_oszicar(oszicar), incar=parse_incar(incar), outcar=parse_outcar(outcar))
    return _plots_compat(_build_plots(parsed))["scf"]


def test_original_signed_changes_and_epsilon_survive_api_even_without_energy():
    text = "DAV: 1 -100.00000000 -8.00D+1 3.00e-2\nDAV: 2 ******** 0.000e+0 -2e-9\nDAV: 3 -100.00000000 -2.55e-11 5.1e-12\n1 F=-100 E0=-100\n"
    scf = _scf(text)
    rows = scf["series"]
    assert len(rows) == 3
    assert [(r["energy"], r["delta_energy_ev"], r["delta_epsilon_ev"]) for r in rows] == [(-100, -80, .03), (None, 0, -2e-9), (-100, -2.55e-11, 5.1e-12)]
    assert [r["source_line"] for r in rows] == [1, 2, 3]
    assert rows[1]["energy_status"] == "unparseable"
    assert rows[1]["delta_energy_status"] == "zero"
    assert rows[2]["delta_energy_raw"] == "-2.55e-11"
    assert all(r["delta_energy_source"] == "oszicar" for r in rows)


@pytest.mark.parametrize("token,state", [("NaN", "non_finite"), ("Infinity", "non_finite"), ("1e999", "non_finite"), ("*****", "unparseable")])
def test_invalid_original_never_replaced_by_energy_difference(token, state):
    rows = _scf(f"DAV: 1 -10.00000000 1 1\nDAV: 2 -11.00000000 {token} {token}\n")["series"]
    assert rows[1]["delta_energy_ev"] is None
    assert rows[1]["delta_energy_source"] == "oszicar"
    assert rows[1]["delta_energy_status"] == state
    assert rows[1]["delta_energy_raw"] == token
    json.dumps(rows, allow_nan=False)


@pytest.mark.parametrize("delta", [float("inf"), float("-inf"), float("nan")])
def test_legacy_numeric_nonfinite_delta_without_raw_is_not_treated_as_missing(delta):
    parsed = ParsedRunData(oszicar=OszicarData(electronic_steps=[
        ElectronicStep(ionic_step=1, electronic_step=1, algorithm="DAV", energy=-10),
        ElectronicStep(ionic_step=1, electronic_step=2, algorithm="DAV", energy=-11, delta_energy=delta),
    ]))
    row = _plots_compat(_build_plots(parsed))["scf"]["series"][1]
    assert row["energy"] == -11
    assert row["delta_energy_ev"] is None
    assert row["delta_energy_raw"] is None
    assert row["delta_energy_status"] == "non_finite"
    assert row["delta_energy_source"] == "oszicar"
    assert row["delta_energy_derivation"] is None
    json.dumps(row, allow_nan=False)


def test_energy_only_derivation_has_precision_and_independent_epsilon():
    rows = _scf("DAV: 1 -10.00000000\nDAV: 2 -11.00000000\nDAV: 3 -11.00000000\n")["series"]
    assert rows[0]["delta_energy_ev"] is None
    assert rows[1]["delta_energy_ev"] == -1
    assert rows[1]["delta_energy_source"] == "derived"
    assert rows[1]["delta_energy_derivation"]["previous_source_line"] == 1
    assert rows[1]["delta_energy_derivation"]["resolution_ev"] == 1e-8
    assert rows[2]["delta_energy_ev"] == 0
    assert rows[2]["delta_energy_status"] == "precision_limited"
    assert all(r["delta_epsilon_ev"] is None for r in rows)


def test_one_printed_quantum_difference_is_precision_limited_without_float_noise():
    row = _scf("DAV: 1 -30.0\nDAV: 2 -30.1\n")["series"][1]
    assert row["delta_energy_ev"] == -0.1
    assert row["delta_energy_status"] == "precision_limited"
    assert row["delta_energy_derivation"]["resolution_ev"] == 0.1


@pytest.mark.parametrize("text,last_delta", [
    ("DAV: 1 -10.000\nDAV: 3 -11.000\n", None),
    ("DAV: 1 -10.000\nDAV: 2 ****\nDAV: 3 -11.000\n", None),
    ("DAV: 1 -10.000\nDAV: 2 -1e999\nDAV: 3 -11.000\n", None),
    ("DAV: 1 -10.000\n1 F=-10\nDAV: 2 -11.000\n", None),
    ("DAV: 1 -10.000\nDAV: 2 -11.000\nDAV: 1 -12.000\n", None),
    ("DAV: 1 -10.000\nN E dE d eps ncg rms\nDAV: 2 -11.000\n", None),
])
def test_derivation_does_not_bridge_unobserved_or_reset_steps(text, last_delta):
    assert _scf(text)["series"][-1]["delta_energy_ev"] is last_delta


def test_repeated_ionic_and_electronic_labels_have_distinct_block_identity():
    text = "DAV: 1 -10\nDAV: 2 -11\n1 F=-11\nDAV: 1 -20\nDAV: 2 -21\n1 F=-21\nDAV: 1 -30\nDAV: 1 -40\n"
    parsed = parse_oszicar(text)
    rows = _scf(text)["series"]
    assert [r["block_id"] for r in rows] == [1, 1, 2, 2, 3, 4]
    assert [r["ionic_step"] for r in rows] == [1, 1, 1, 1, 2, 2]
    assert [r["ionic_step_inferred"] for r in rows] == [False, False, False, False, True, True]
    # Existing rule evidence is intentionally unchanged, including tail buffer semantics.
    assert parsed.electronic_energy_series == [-30, -40]
    assert parsed.last_electronic_step == 1


def test_reset_block_without_own_summary_discloses_inferred_ionic_association():
    rows = _scf("DAV: 1 -10\nDAV: 2 -11\nDAV: 1 -20\n3 F=-20\n")["series"]
    assert [r["ionic_step"] for r in rows] == [3, 3, 3]
    assert [r["ionic_step_inferred"] for r in rows] == [True, True, False]


def test_header_reset_before_summary_only_attributes_latest_block_directly():
    text = "N E dE d eps\nDAV: 1 -10\nDAV: 2 -11\nN E dE d eps\nDAV: 1 -20\n1 F=-20\n"
    rows = _scf(text)["series"]
    assert [r["block_id"] for r in rows] == [1, 1, 2]
    assert [r["ionic_step_inferred"] for r in rows] == [True, True, False]
    assert parse_oszicar(text).electronic_energy_series == [-10, -11, -20]


def test_output_echo_parser_rejects_system_and_comment_examples_but_accepts_group():
    output = "SYSTEM = debugging EDIFF=1e-12 and NELM=2\n# EDIFF=1e-12\nexample EDIFF=1e-12\nNELM = 60; NELMIN = 2; EDIFF = 1e-5\n"
    params = _scf("", outcar=output)["parameters"]
    assert params["nelm"]["value"] == 60
    assert params["ediff"]["value"] == 1e-5
    assert params["ediff"]["source_line"] == 4
    assert len(params["ediff"]["candidates"]) == 1
    for text in ("SYSTEM = aborting loop because EDIFF is reached", "# aborting loop because EDIFF is reached", "example: aborting loop because EDIFF is reached"):
        assert parse_outcar(text).electronic_convergence_evidence == []


@pytest.mark.parametrize("text", ["", "1 F=-10 E0=-10\n", "DAV: 1 0\n", "DAV: 1 1e999\n"])
def test_empty_single_zero_and_nonfinite_energy_are_not_fabricated(text):
    scf = _scf(text)
    assert len(scf["series"]) == text.count("DAV:")
    json.dumps(scf, allow_nan=False)
    if scf["series"]:
        assert scf["series"][0]["delta_energy_ev"] is None


@pytest.mark.parametrize("incar,outcar,value,source,state,conflict", [
    ("", "", None, None, "missing", False),
    ("EDIFF=0\n", "", 0, "INCAR", "available", False),
    ("EDIFF=1e-5\n", "EDIFF = 1D-6\n", 1e-6, "OUTCAR", "available", True),
    ("EDIFF=1e-5\n", "EDIFF = 1e-5\nEDIFF = 1e-5\n", 1e-5, "OUTCAR", "available", False),
    ("EDIFF=1e-5\n", "EDIFF = 1e-5\nEDIFF = 1e-6\n", None, None, "conflict", False),
    ("EDIFF=1e-5\n", "EDIFF = NaN\n", None, "OUTCAR", "invalid", True),
    ("EDIFF=-1\n", "", None, "INCAR", "invalid", False),
    ("EDIFF=.TRUE.\n", "", None, "INCAR", "invalid", False),
])
def test_ediff_provenance_and_output_conflicts(incar, outcar, value, source, state, conflict):
    parameter = _scf("", incar, outcar)["parameters"]["ediff"]
    assert (parameter["value"], parameter["source"], parameter["status"], parameter["input_conflict"]) == (value, source, state, conflict)
    if source:
        assert parameter["source_line"] == (2 if outcar.count("EDIFF") == 2 else 1)


@pytest.mark.parametrize("setting,expected", [("NELM=60", 60), ("NELM=0", None), ("NELM=2.5", None), ("NELM=.TRUE.", None), ("NELM=1e999", None), ("", None)])
def test_nelm_never_guessed_or_coerced_from_invalid_input(setting, expected):
    parameter = _scf("DAV: 1 -1\n", setting)["parameters"]["nelm"]
    assert parameter["value"] == expected


@pytest.mark.parametrize("name", ["EDIFF", "NELM"])
def test_unrepresentable_integer_parameter_fails_soft(name):
    scf = _scf("DAV: 1 -1\n", f"{name}={'1' + '0' * 400}\n")
    parameter = scf["parameters"][name.lower()]
    assert parameter["value"] is None
    assert parameter["status"] == "invalid"
    assert len(scf["series"]) == 1
    json.dumps(scf, allow_nan=False)


def test_output_ediff_stop_is_evidence_without_ionic_or_block_convergence_inference():
    output = "reached required accuracy - stopping structural energy minimisation\naborting loop because EDIFF is reached\n"
    scf = _scf("DAV: 1 -10\n1 F=-10\nDAV: 1 -20\n2 F=-20\n", outcar=output)
    assert scf["convergence_evidence"] == [{"kind": "electronic_ediff_stop", "source": "OUTCAR", "source_line": 2, "scope": "unassigned", "block_id": None}]
    assert "converged" not in scf and all("converged" not in r for r in scf["series"])
    assert _scf("", outcar=output.splitlines()[0])["convergence_evidence"] == []


def test_upload_run_get_preserves_null_rows_raw_changes_and_thresholds():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("OSZICAR", "DAV: 1 -1.0000000 0.1 0.2\nDAV: 2 **** -2.1e-11 0\n1 F=-1\n")
        archive.writestr("INCAR", "NELM=40\nEDIFF=0\n")
        archive.writestr("OUTCAR", "NELM = 40\nEDIFF = 0\n")
    client = TestClient(app)
    response = client.post("/api/v1/diagnosis/upload", files={"file": ("run.zip", buffer.getvalue(), "application/zip")})
    assert response.status_code == 200
    identifier = response.json()["data"]["diagnosis_id"]
    assert client.post("/api/v1/diagnosis/run", json={"diagnosis_id": identifier, "llm_explanation": False}).status_code == 200
    response = client.get(f"/api/v1/diagnosis/{identifier}")
    assert response.status_code == 200
    scf = response.json()["data"]["plots"]["scf"]
    assert len(scf["series"]) == 2
    assert scf["series"][1]["energy"] is None
    assert scf["series"][1]["delta_energy_raw"] == "-2.1e-11"
    assert scf["parameters"]["nelm"]["value"] == 40
    assert scf["parameters"]["ediff"]["value"] == 0
    assert scf["parameters"]["ediff"]["source"] == "OUTCAR"
