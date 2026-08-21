from __future__ import annotations

from ..issue_builder import build_issue
from ..engine import Rule
from ...schemas.issue import Issue
from ...schemas.parsed import ParsedRunData
from ...schemas.status import Severity


class IonicReachedNswRule(Rule):
    rule_id = "IONIC_REACHED_NSW"
    category = "ionic"

    def run(self, parsed: ParsedRunData) -> list[Issue]:
        incar = parsed.incar.effective
        nsw = incar.get("NSW")
        last = parsed.oszicar.last_step
        converged = parsed.oszicar.converged
        if nsw is None or not isinstance(nsw, int):
            return []
        if last >= nsw and not converged:
            return [build_issue(
                rule_id=self.rule_id, severity=Severity.HIGH, category=self.category,
                title="离子步数达到 NSW",
                summary=f"离子步数已达 NSW={nsw} 且无收敛标志，几何优化疑似未收敛。",
                evidence=[{"file": "OSZICAR", "message": f"ionic steps >= NSW={nsw}",
                          "data_ref": "oszicar.last_step"}],
                recommendations=[
                    {"action": "set_parameter", "target": "INCAR", "parameter": "NSW",
                     "rationale": "延长 NSW 前先检查力趋势与 SCF 收敛"}
                ],
                auto_fixable=True, confidence=0.8, blocking=True,
                possible_causes=["几何未收敛", "SCF 不稳定"],
            )]
        return []