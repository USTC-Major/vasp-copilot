from __future__ import annotations

from typing import Any, Optional

from ..schemas.issue import Evidence, Issue, Recommendation
from ..schemas.status import Severity


def build_issue(*, rule_id: str, severity: Severity, category: str,
                title: str, summary: str,
                evidence: list[dict[str, Any]],
                recommendations: Optional[list[dict[str, Any]]] = None,
                auto_fixable: bool = False, confidence: float = 0.8,
                blocking: bool = False,
                possible_causes: Optional[list[str]] = None,
                data_ref: Optional[str] = None) -> Issue:
    if not evidence:
        raise ValueError("every issue must carry at least one evidence")
    evs = [Evidence(**e) for e in evidence]
    recs = [Recommendation(**r) for r in (recommendations or [])]
    return Issue(
        issue_id=f"{rule_id}-0001",
        rule_id=rule_id,
        severity=severity,
        category=category,
        title=title,
        summary=summary,
        evidence=evs,
        recommendations=recs,
        auto_fixable=auto_fixable,
        confidence=confidence,
        blocking=blocking,
        possible_causes=possible_causes or [],
        data_ref=data_ref,
    )