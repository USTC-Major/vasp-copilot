"""Require every offline reviewer case to execute and pass in the Linux job."""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path


EXPECTED = {
    "backend.tests.reviewer.test_owner_decisions": {
        "test_human_can_reject_pending_proposed_reviewer_without_activating_scope",
        "test_duplicate_signed_approval_cannot_reenqueue_old_approved_card",
        "test_owner_restart_invalidates_issued_review_without_reapproving",
        "test_8500_nested_identity_file_rejected_before_model",
    },
    "backend.tests.reviewer_review.test_model_adapter": {
        "test_real_adapter_uses_one_isolated_completion_and_signs_exact_decision",
        "test_missing_key_never_falls_to_fake_or_calls_provider",
        "test_truncated_tool_call_or_duplicate_key_cannot_produce_signed_approval[response0]",
        "test_truncated_tool_call_or_duplicate_key_cannot_produce_signed_approval[response1]",
        "test_truncated_tool_call_or_duplicate_key_cannot_produce_signed_approval[response2]",
        "test_internal_route_rejects_missing_service_credential_before_model",
    },
    "backend.tests.reviewer_review.test_owner_review": {
        "test_explicit_activation_then_signed_review_uses_original_c_worker",
        "test_activation_does_not_review_old_card_until_explicit_request",
        "test_wrong_service_signature_falls_back_to_human_without_write",
        "test_correctly_signed_reply_for_changed_challenge_cannot_approve",
        "test_reply_after_owner_challenge_expiry_cannot_approve",
        "test_valid_nonapproval_decisions_do_not_write[reject-rejected-rejected]",
        "test_valid_nonapproval_decisions_do_not_write[needs_human-pending-needs_human]",
        "test_known_link_or_script_never_calls_model[symlink-notes.txt]",
        "test_known_link_or_script_never_calls_model[write_text-run.sh]",
        "test_unconfigured_reviewer_never_uses_fake_and_human_can_finish[False]",
        "test_human_intervention_during_slow_review_blocks_late_write[human_reject]",
        "test_human_intervention_during_slow_review_blocks_late_write[scope_revoke]",
        "test_queued_card_rejected_before_dequeue_never_calls_model",
        "test_write_text_body_and_model_reason_never_reach_reviewer_projection",
        "test_nested_endpoint_config_paths_do_not_enter_model_payload",
    },
}
EXPECTED_CASES = {(module, name) for module, names in EXPECTED.items() for name in names}


def main(path: str) -> None:
    cases = list(ET.parse(Path(path)).getroot().iter("testcase"))
    observed: dict[tuple[str, str], list[ET.Element]] = {}
    for case in cases:
        module = case.attrib.get("classname", "")
        if module in EXPECTED:
            key = (module, case.attrib.get("name", ""))
            observed.setdefault(key, []).append(case)

    missing = sorted(EXPECTED_CASES - observed.keys())
    unexpected = sorted(observed.keys() - EXPECTED_CASES)
    duplicates = sorted(key for key, matches in observed.items() if len(matches) != 1)
    nonpassing = []
    for key, matches in observed.items():
        for case in matches:
            tags = {child.tag.rsplit("}", 1)[-1] for child in case}
            bad = tags & {"skipped", "failure", "error"}
            if bad:
                nonpassing.append(f"{key[0]}::{key[1]}:{','.join(sorted(bad))}")
    if missing or unexpected or duplicates or nonpassing:
        raise SystemExit(
            "reviewer offline evidence incomplete; "
            f"missing={missing!r} unexpected={unexpected!r} "
            f"duplicates={duplicates!r} nonpassing={nonpassing!r}"
        )
    print(f"{len(EXPECTED_CASES)} reviewer offline tests executed without skip/failure")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: ci_assert.py JUNIT_XML")
    main(sys.argv[1])
