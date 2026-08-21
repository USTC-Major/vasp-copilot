from __future__ import annotations

from ..schemas.fix import RecommendedFix
from ..schemas.issue import Issue
from ..schemas.result import NextStep
from ..schemas.status import Severity


def compute_next_step(*, issues: list[Issue], fixes: list[RecommendedFix] | None = None,
                      suggested_task: str = "static") -> NextStep:
    """MVP 5.6 next-step 门控。

    若仍有未解决或未经用户确认修复的 Critical/High issue，则阻断后续计算
    （next_step.allowed=false）。确定性 rule_based 模式不建模用户确认步骤，
    因此默认任何 Critical/High issue 都会阻断。"""
    fixes = fixes or []
    blocking = [i for i in issues if i.severity in (Severity.CRITICAL, Severity.HIGH)]
    if blocking:
        ids = sorted({i.issue_id for i in blocking})
        return NextStep(
            allowed=False,
            reason=f"存在 {len(blocking)} 个 Critical/High 问题未解决或未确认修复（{', '.join(ids)}）",
        )
    return NextStep(
        allowed=True,
        reason="无 Critical/High 问题，可进入后续 static/DOS/band 流程",
        suggested_task=suggested_task,
    )