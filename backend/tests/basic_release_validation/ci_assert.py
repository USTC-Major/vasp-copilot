"""Require critical S1-R input/result and real POSIX SSH cases in Linux JUnit."""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path


CRITICAL_CASES = {
    ("backend.tests.test_input_validation_unit", "test_negative_volume_and_three_axis_scales_are_reflected_in_lattice"),
    ("backend.tests.test_input_validation_unit", "test_synthetic_potcar_variant_matches_element_but_wrong_order_blocks"),
    ("backend.tests.basic_release_validation.test_structure_routes", "test_analyze_missing_coordinates_rejects_without_structure_record"),
    ("backend.tests.basic_release_validation.test_structure_routes", "test_valid_scaled_structure_preserves_summary_and_raw_coordinates"),
    ("backend.tests.basic_release_validation.test_precheck_paths", "test_invalid_but_nonempty_input_blocks_both_prechecks_and_submit_card[garbage_poscar-False]"),
    ("backend.tests.basic_release_validation.test_precheck_paths", "test_invalid_but_nonempty_input_blocks_both_prechecks_and_submit_card[garbage_poscar-True]"),
    ("backend.tests.basic_release_validation.test_precheck_paths", "test_invalid_but_nonempty_input_blocks_both_prechecks_and_submit_card[wrong_potcar_species-False]"),
    ("backend.tests.basic_release_validation.test_precheck_paths", "test_invalid_but_nonempty_input_blocks_both_prechecks_and_submit_card[wrong_potcar_species-True]"),
    ("backend.tests.test_nl_plan_api", "test_invalid_legacy_structure_never_calls_nl_model"),
    ("backend.tests.test_toolbox_results", "test_posix_fixed_helper_subprocess_streams_exact_bytes"),
    ("backend.tests.test_toolbox_results", "test_posix_result_fd_denies_links_and_detects_rename_truncate_growth"),
    ("backend.tests.test_toolbox_results", "test_posix_result_32mib_boundary"),
    ("backend.tests.basic_release_validation.test_loopback_ssh_result", "test_loopback_known_hosts_reads_exact_result"),
    ("backend.tests.basic_release_validation.test_loopback_ssh_result", "test_loopback_bad_or_unknown_host_key_rejected_before_auth"),
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
            "critical S1-R Linux evidence incomplete; "
            f"missing={missing!r} duplicates={duplicates!r} nonpassing={nonpassing!r}"
        )
    print(f"{len(CRITICAL_CASES)} S1-R Linux tests executed without skip/failure")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: ci_assert.py JUNIT_XML")
    main(sys.argv[1])
