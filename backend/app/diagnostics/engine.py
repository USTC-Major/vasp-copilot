from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional, Sequence

from ..schemas.issue import Evidence, Issue, Recommendation
from ..schemas.parsed import ParsedRunData
from ..schemas.status import Severity

SAME_SOURCE_RULE_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"ZHEGV_LAPACK_FAILURE", "DAV_OR_EDDDAV_ERROR"}),
)


def _root_cause_candidate(issues: Sequence[Issue]) -> str:
    """一组相关 issue 共享来源的稳定标签。"""
    lines = [
        (ev.file, ev.line) for i in issues for ev in i.evidence
        if ev.line is not None
    ]
    if lines:
        file, line = min(lines, key=lambda fl: (fl[0].lower(), fl[1]))
        return f"{file}:{line}"
    refs = [ev.data_ref for i in issues for ev in i.evidence if ev.data_ref]
    if refs:
        return sorted(set(refs))[0]
    return "unknown_source"


class Rule(ABC):
    """确定性诊断规则基类（MVP 11.1）。"""

    rule_id: str = ""
    category: str = ""

    @abstractmethod
    def run(self, parsed: ParsedRunData) -> list[Issue]:
        """返回零个或多个 issue。"""


class IssueBuilder:
    """统一构造 Evidence/Recommendation/Issue 的辅助类。"""

    def __init__(self, rule_id: str, severity: Severity, category: str):
        self.rule_id = rule_id
        self.severity = severity
        self.category = category

    def ev(self, file: str, line: Optional[int] = None, message: str = "",
           data_ref: Optional[str] = None, excerpt: Optional[str] = None) -> Evidence:
        return Evidence(file=file, line=line, message=message,
                        data_ref=data_ref, excerpt=excerpt)

    def rec(self, action: str, target: str, parameter: Optional[str] = None,
            new_value: Optional[str] = None, rationale: str = "",
            requires_user_confirmation: bool = True) -> Recommendation:
        return Recommendation(action=action, target=target, parameter=parameter,
                              new_value=new_value, rationale=rationale,
                              requires_user_confirmation=requires_user_confirmation)

    def issue(self, title: str, summary: str, evidence: list[Evidence],
              recommendations: Optional[list[Recommendation]] = None,
              auto_fixable: bool = False, confidence: float = 0.8,
              blocking: bool = False, possible_causes: Optional[list[str]] = None,
              data_ref: Optional[str] = None) -> Issue:
        if not evidence:
            raise ValueError("every issue must carry at least one evidence")
        return Issue(
            issue_id=f"{self.rule_id}-0001",
            rule_id=self.rule_id,
            severity=self.severity,
            category=self.category,
            title=title,
            summary=summary,
            evidence=evidence,
            recommendations=recommendations or [],
            auto_fixable=auto_fixable,
            confidence=confidence,
            blocking=blocking,
            possible_causes=possible_causes or [],
            data_ref=data_ref,
        )


class DiagnosisEngine:
    """对 ParsedRunData 运行已注册规则（确定性）。"""

    def __init__(self) -> None:
        self._rules: list[Rule] = []

    def register(self, rule: Rule) -> None:
        self._rules.append(rule)

    def register_all(self, rules: Sequence[Rule]) -> None:
        for r in rules:
            self.register(r)

    @property
    def rules(self) -> list[Rule]:
        return list(self._rules)

    def run(self, parsed: ParsedRunData) -> list[Issue]:
        issues: list[Issue] = []
        for rule in self._rules:
            for iss in rule.run(parsed):
                n = sum(1 for i in issues if i.rule_id == iss.rule_id) + 1
                iss.issue_id = f"{iss.rule_id}-{n:04d}"
                issues.append(iss)
        issues = self._deduplicate(issues)
        order = {Severity.CRITICAL: 0, Severity.HIGH: 1, Severity.MEDIUM: 2,
                 Severity.LOW: 3, Severity.INFO: 4}
        issues.sort(key=lambda i: (order.get(i.severity, 5), i.rule_id))
        return issues

    @staticmethod
    def _deduplicate(issues: list[Issue]) -> list[Issue]:
        for group in SAME_SOURCE_RULE_GROUPS:
            members = [i for i in issues if i.rule_id in group]
            if len(members) < 2:
                continue
            ids = [m.issue_id for m in members]
            cause = _root_cause_candidate(members)
            for m in members:
                m.related_issue_ids = [i for i in ids if i != m.issue_id]
                m.root_cause_candidate = cause
        return issues