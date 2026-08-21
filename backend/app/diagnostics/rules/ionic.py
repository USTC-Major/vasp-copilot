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
        # NSW 必须为有效正整数：NSW=0 是静态计算配置，本规则永不触发；
        # bool 是 int 子类，True/False 不是合法 NSW，须显式排除。
        if isinstance(nsw, bool) or not isinstance(nsw, int) or nsw <= 0:
            return []
        last = parsed.oszicar.last_ionic_step
        if last <= 0 or last < nsw:
            return []
        # 收敛证据：OSZICAR 标志或 OUTCAR 明确的结构优化收敛文本；
        # 证据不足（None）不视为已收敛，也不声称确定未收敛。
        converged = (parsed.oszicar.converged
                     or parsed.outcar.ionic_convergence_reached is True)
        if converged:
            return []
        return [build_issue(
            rule_id=self.rule_id, severity=Severity.HIGH, category=self.category,
            title="离子步数达到 NSW",
            summary=f"离子步数已达 NSW={nsw} 且无收敛标志，几何优化疑似未收敛。",
            evidence=[{"file": "OSZICAR", "message": f"ionic steps >= NSW={nsw}",
                      "data_ref": "oszicar.last_ionic_step"}],
            recommendations=[
                {"action": "set_parameter", "target": "INCAR", "parameter": "NSW",
                 "rationale": "延长 NSW 前先检查力趋势与 SCF 收敛"}
            ],
            auto_fixable=True, confidence=0.8, blocking=True,
            possible_causes=["几何未收敛", "SCF 不稳定"],
        )]
