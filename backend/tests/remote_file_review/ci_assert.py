"""Reject a green CI run that skipped or omitted critical POSIX behaviors."""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path


CRITICAL_TESTS = {
    "test_linux_helper_copy_prepare_commit_keeps_bytes_remote",
    "test_linux_noreplace_target_race_and_same_inode",
    "test_linux_source_parent_and_root_changes_do_not_publish",
    "test_linux_content_policy_modes_and_capability_fail_closed",
    "test_linux_publish_receipt_failure_and_prepared_only_reconcile_unknown",
}


def main(path: str) -> None:
    root = ET.parse(Path(path)).getroot()
    cases = list(root.iter("testcase"))
    observed = {}
    for case in cases:
        base = case.attrib.get("name", "").split("[")[0]
        if base in CRITICAL_TESTS:
            observed.setdefault(base, []).append(case)
    missing = sorted(CRITICAL_TESTS - observed.keys())
    bad = []
    for name in sorted(CRITICAL_TESTS & observed.keys()):
        for index, case in enumerate(observed[name]):
            child_tags = {child.tag.rsplit("}", 1)[-1] for child in case}
            nonpassing = child_tags & {"skipped", "failure", "error"}
            if nonpassing:
                bad.append(f"{name}[{index}]:{','.join(sorted(nonpassing))}")
    if missing or bad:
        raise SystemExit(
            "critical POSIX evidence incomplete; "
            f"missing={missing!r} nonpassing={bad!r}"
        )
    print("critical POSIX tests executed without skip/failure:")
    for name in sorted(CRITICAL_TESTS):
        print(f"- {name}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: ci_assert.py JUNIT_XML")
    main(sys.argv[1])
