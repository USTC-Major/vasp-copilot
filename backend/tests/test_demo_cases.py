from __future__ import annotations

import json
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

REPO = Path(__file__).resolve().parents[2]
DEMO = REPO / "demo_cases"


def _upload_run(case_dir: Path):
    zpath = case_dir / "input.zip"
    assert zpath.is_file(), zpath
    data = zpath.read_bytes()
    r = client.post("/api/v1/diagnosis/upload",
                    files={"file": (zpath.name, data, "application/zip")})
    assert r.status_code == 200, r.text
    diag_id = r.json()["data"]["diagnosis_id"]
    r = client.post("/api/v1/diagnosis/run", json={"diagnosis_id": diag_id})
    assert r.status_code == 200, r.text
    run_data = r.json()["data"]
    g = client.get(f"/api/v1/diagnosis/{diag_id}")
    assert g.status_code == 200
    return run_data, g.json()["data"]


def _load_case(case_dir: Path):
    meta = yaml.safe_load(
        (case_dir / "case.yaml").read_text(encoding="utf-8"))
    rules_path = (DEMO / "expected_outputs" / meta["case_id"]
                  / "expected_rules.json")
    expected_rules = json.loads(rules_path.read_text(encoding="utf-8"))
    return meta, expected_rules


def test_every_failed_run_case_matches_expected_outputs():
    cases = sorted(p for p in (DEMO / "failed_runs").iterdir() if p.is_dir())
    assert len(cases) >= 3, "need at least 3 failed_run demo cases (13.3)"
    for case_dir in cases:
        meta, expected_rules = _load_case(case_dir)
        run_data, result = _upload_run(case_dir)
        issues = result["issues"]

        assert {i["rule_id"] for i in issues} == set(meta["expected_rule_ids"]), case_dir.name

        counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
        for i in issues:
            if i["severity"] in counts:
                counts[i["severity"]] += 1
        assert counts == meta["expected_issue_count_by_severity"], case_dir.name

        assert bool(run_data["fix_available"]) == bool(meta["recommended_fix_expected"]), case_dir.name

        for exp in expected_rules:
            act = next(i for i in issues if i["rule_id"] == exp["rule_id"])
            assert act["severity"] == exp["severity"], (case_dir.name, exp["rule_id"])
            assert act["blocking"] == exp["blocking"], (case_dir.name, exp["rule_id"])
            assert act["auto_fixable"] == exp["auto_fixable"], (case_dir.name, exp["rule_id"])
            files = {e["file"] for e in act["evidence"]}
            assert files == set(exp["evidence_files"]), (case_dir.name, exp["rule_id"])


def test_demo_input_has_no_potcar():
    for case_dir in (DEMO / "failed_runs").iterdir():
        if not case_dir.is_dir():
            continue
        zpath = case_dir / "input.zip"
        names = [n.filename for n in __import__("zipfile").ZipFile(str(zpath)).infolist()]
        assert all("POTCAR" not in n.upper() for n in names), case_dir.name

def test_demo_case_count_meets_design_target():
    # design 13.4 P0: 8-12 demo cases in total (failed runs + structure samples)
    failed = [p for p in (DEMO / "failed_runs").iterdir()
              if p.is_dir() and (p / "case.yaml").is_file()]
    structures = [p for p in (DEMO / "structures").iterdir()
                  if p.is_dir() and (p / "case.yaml").is_file()]
    total = len(failed) + len(structures)
    assert len(failed) >= 8, "design 13.4 acceptance: at least 8 failed_run fixtures"
    assert len(structures) >= 2, "expected structure samples incl. CIF (13.3)"
    assert 8 <= total <= 12, f"demo case total {total} outside design target 8-12"


def test_sample_cif_is_parsable_structure_sample():
    # structures/sample_cif is a CIF asset per design 13.3 asset tree
    cif_dir = DEMO / "structures" / "sample_cif"
    assert (cif_dir / "case.yaml").is_file(), cif_dir
    cifs = list(cif_dir.glob("*.cif")) + list(cif_dir.glob("*.CIF"))
    assert cifs, "sample_cif must contain a .cif file"
    assert (cif_dir / "case.yaml").read_text(encoding="utf-8").startswith("case_id: sample_cif")

