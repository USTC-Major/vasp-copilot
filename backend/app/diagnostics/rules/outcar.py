from __future__ import annotations

from ..issue_builder import build_issue
from ..engine import Rule
from ...schemas.issue import Issue
from ...schemas.parsed import ParsedRunData
from ...schemas.status import Severity


class OutcarTruncatedRule(Rule):
    rule_id = "OUTCAR_TRUNCATED"
    category = "outcar"

    def run(self, parsed: ParsedRunData) -> list[Issue]:
        if parsed.outcar.normal_termination is False and parsed.outcar.truncated is True:
            sev = Severity.MEDIUM
            if any(j.get("category") in ("oom", "time_limit", "signal")
                   for jl in parsed.job_logs for j in jl.keywords):
                sev = Severity.HIGH
            return [build_issue(
                rule_id=self.rule_id, severity=sev, category=self.category,
                title="OUTCAR 截断/未正常结束",
                summary="OUTCAR 缺少正常结束标志或解析区块不完整，输出可能被截断。",
                evidence=[{"file": "OUTCAR", "message": "missing normal termination flag",
                          "data_ref": "outcar.normal_termination"}],
                recommendations=[
                    {"action": "review", "target": "manifest",
                     "rationale": "补充完整 OUTCAR/job log，检查磁盘、超时、节点异常"}],
                confidence=0.85, blocking=False,
                possible_causes=["磁盘满", "超时", "节点异常"],
            )]
        return []