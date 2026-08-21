from __future__ import annotations

from ..issue_builder import build_issue
from ..engine import Rule
from ...schemas.issue import Issue
from ...schemas.parsed import ParsedRunData
from ...schemas.status import Severity


class KpointsLineModeWithoutStaticRule(Rule):
    rule_id = "KPOINTS_LINE_MODE_WITHOUT_STATIC"
    category = "kpoints"

    def run(self, parsed: ParsedRunData) -> list[Issue]:
        if not parsed.kpoints.line_mode:
            return []
        have = set(parsed.source_files)
        chg = parsed.incar.effective.get("ICHARG")
        ok = chg == 11 and "CHGCAR" in have
        if ok:
            return []
        return [build_issue(
            rule_id=self.rule_id, severity=Severity.HIGH, category=self.category,
            title="LINE-MODE 能带但缺少 static/CHGCAR",
            summary="KPOINTS 为 line-mode（能带），但未满足 static/CHGCAR 依赖。",
            evidence=[{"file": "KPOINTS", "message": parsed.kpoints.mode}],
            recommendations=[
                {"action": "review", "target": "manifest", "rationale": "先完成 static 并验证 CHGCAR"}],
            confidence=0.9, blocking=True,
            possible_causes=["dependency 未满足", "缺少 CHGCAR"],
        )]