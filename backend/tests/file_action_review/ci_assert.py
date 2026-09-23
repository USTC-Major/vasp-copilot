"""Reject a green CI run that skipped or omitted critical C POSIX behaviors."""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path


CRITICAL_CASES = {
    "test_linux_owner_copy_source_changed_during_copy",
    "test_linux_owner_errno_and_fsync_failures[ENOSPC]",
    "test_linux_owner_errno_and_fsync_failures[EDQUOT]",
    "test_linux_owner_errno_and_fsync_failures[EACCES]",
    "test_linux_owner_errno_and_fsync_failures[prepared_receipt_fsync]",
    "test_linux_owner_errno_and_fsync_failures[pre_publish_target_race]",
    "test_linux_owner_errno_and_fsync_failures[post_publish_fsync]",
    "test_linux_owner_errno_and_fsync_failures[committed_receipt_fsync]",
    "test_linux_owner_cleanup_and_multi_root_failure",
    "test_linux_two_helpers_noreplace_owner_receipts",
    "test_linux_owner_reconcile_after_local_receipt_loss",
}


def main(path: str) -> None:
    cases = list(ET.parse(Path(path)).getroot().iter("testcase"))
    observed: dict[str, list[ET.Element]] = {}
    for case in cases:
        name = case.attrib.get("name", "")
        if name in CRITICAL_CASES:
            observed.setdefault(name, []).append(case)
    missing = sorted(CRITICAL_CASES - observed.keys())
    bad: list[str] = []
    duplicates = sorted(name for name, matches in observed.items() if len(matches) != 1)
    for name in sorted(CRITICAL_CASES & observed.keys()):
        for index, case in enumerate(observed[name]):
            tags = {child.tag.rsplit("}", 1)[-1] for child in case}
            nonpassing = tags & {"skipped", "failure", "error"}
            if nonpassing:
                bad.append(f"{name}[{index}]:{','.join(sorted(nonpassing))}")
    if missing or bad or duplicates:
        raise SystemExit(
            "critical file-action POSIX evidence incomplete; "
            f"missing={missing!r} nonpassing={bad!r} duplicates={duplicates!r}"
        )
    print("critical file-action POSIX tests executed without skip/failure:")
    for name in sorted(CRITICAL_CASES):
        print(f"- {name}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: ci_assert.py JUNIT_XML")
    main(sys.argv[1])
