"""Require AC-K1 Gamma default cases to execute on Linux without skip/failure."""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path


CRITICAL_CASES = {
    ("backend.tests.be_a.test_kpoints.TestGridFormula",
     "test_automatic_grid_defaults_gamma_without_changing_subdivisions[1000.0-1-grid0]"),
    ("backend.tests.be_a.test_kpoints.TestGridFormula",
     "test_automatic_grid_defaults_gamma_without_changing_subdivisions[1000.0-2-grid1]"),
    ("backend.tests.be_a.test_kpoints.TestGridFormula",
     "test_automatic_grid_defaults_gamma_without_changing_subdivisions[729.0-1-grid2]"),
    ("backend.tests.be_a.test_pipeline_contract",
     "test_si2_static_default_generates_gamma_8_with_unchanged_other_inputs"),
    ("backend.tests.be_a.test_kpoints.TestUniformRendering", "test_monkhorst_text"),
    ("backend.tests.be_a.test_pipeline_contract.TestRequestValidation",
     "test_band_kpoints_line_mode_end_to_end"),
    ("backend.tests.be_a.test_golden", "test_golden_bundle[nacl-nacl_request]"),
    ("backend.tests.be_a.test_golden", "test_golden_bundle[fe2o3-fe2o3_request]"),
    ("backend.tests.be_a.test_golden", "test_golden_zip_is_byte_identical"),
    ("backend.tests.be_a.test_bundle_zip.TestPipelineDeterminism",
     "test_zip_roundtrip_matches_manifest"),
    ("backend.tests.be_a.test_reports.TestFileTreeConsistency",
     "test_file_tree_matches_bundle"),
}


def main(path: str) -> None:
    observed: dict[tuple[str, str], list[ET.Element]] = {}
    for case in ET.parse(Path(path)).getroot().iter("testcase"):
        key = (case.attrib.get("classname", ""), case.attrib.get("name", ""))
        if key in CRITICAL_CASES:
            observed.setdefault(key, []).append(case)
    missing = sorted(CRITICAL_CASES - observed.keys())
    duplicates = sorted(key for key, matches in observed.items() if len(matches) != 1)
    nonpassing = []
    for key, matches in observed.items():
        for case in matches:
            tags = {child.tag.rsplit("}", 1)[-1] for child in case}
            bad = tags & {"skipped", "failure", "error"}
            if bad:
                nonpassing.append(f"{key[0]}::{key[1]}:{','.join(sorted(bad))}")
    if missing or duplicates or nonpassing:
        raise SystemExit(
            "critical AC-K1 Linux evidence incomplete; "
            f"missing={missing!r} duplicates={duplicates!r} nonpassing={nonpassing!r}"
        )
    print(f"{len(CRITICAL_CASES)} AC-K1 Linux tests executed without skip/failure")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: ci_assert.py JUNIT_XML")
    main(sys.argv[1])
