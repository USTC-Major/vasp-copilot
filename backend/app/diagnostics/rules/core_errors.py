from __future__ import annotations

from ..issue_builder import build_issue
from ..engine import Rule
from ...schemas.issue import Issue
from ...schemas.parsed import ParsedRunData
from ...schemas.status import Severity


def _match_outcar(parsed: ParsedRunData, *patterns: str) -> list:
    hits = []
    for err in parsed.outcar.error_lines:
        text = err.get("text", "")
        for p in patterns:
            if p.lower() in text.lower():
                hits.append(err)
                break
    return hits


class BrmixSeriousProblemRule(Rule):
    rule_id = "BRMIX_SERIOUS_PROBLEM"
    category = "core_errors"

    def run(self, parsed: ParsedRunData) -> list[Issue]:
        hits = _match_outcar(parsed, "BRMIX: very serious", "brmix")
        if not hits:
            return []
        err = hits[0]
        return [build_issue(
            rule_id=self.rule_id, severity=Severity.HIGH, category=self.category,
            title="BRMIX 严重问题",
            summary="OUTCAR 报告 BRMIX 严重问题，可能为电荷混合失败。",
            evidence=[{"file": "OUTCAR", "line": err.get("line"), "message": err.get("text", ""),
                       "data_ref": "outcar.error_lines"}],
            recommendations=[
                {"action": "review", "target": "user", "rationale": "检查不兼容 CHGCAR、结构/电子类型、混合与初始磁态"}],
            confidence=0.85, blocking=True,
            possible_causes=["不兼容 CHGCAR", "混合设置不当", "初始磁态异常"],
        )]


class ZhegvLapackFailureRule(Rule):
    rule_id = "ZHEGV_LAPACK_FAILURE"
    category = "core_errors"

    def run(self, parsed: ParsedRunData) -> list[Issue]:
        hits = _match_outcar(parsed, "ZHEGV", "ZHEGV_")
        if not hits:
            return []
        err = hits[0]
        return [build_issue(
            rule_id=self.rule_id, severity=Severity.HIGH, category=self.category,
            title="LAPACK 对角化失败",
            summary="OUTCAR/job log 出现 ZHEGV/LAPACK 对角化失败。",
            evidence=[{"file": "OUTCAR", "line": err.get("line"), "message": err.get("text", ""),
                       "data_ref": "outcar.error_lines"}],
            recommendations=[
                {"action": "review", "target": "user", "rationale": "检查结构是否畸变/重叠、数值精度与并行划分"}],
            confidence=0.85, blocking=True,
            possible_causes=["结构畸变/重叠", "数值精度", "并行划分"],
        )]


class TooFewBandsRule(Rule):
    rule_id = "TOO_FEW_BANDS"
    category = "core_errors"

    def run(self, parsed: ParsedRunData) -> list[Issue]:
        hits = _match_outcar(parsed, "TOO FEW BANDS", "to few bands", "too few bands")
        if not hits:
            return []
        err = hits[0]
        return [build_issue(
            rule_id=self.rule_id, severity=Severity.HIGH, category=self.category,
            title="能带数不足",
            summary="VASP 报告 too few bands，需增加 NBANDS。",
            evidence=[{"file": "OUTCAR", "line": err.get("line"), "message": err.get("text", ""),
                       "data_ref": "outcar.error_lines"}],
            recommendations=[
                {"action": "set_parameter", "target": "INCAR", "parameter": "NBANDS",
                 "new_value": "", "rationale": "增加 NBANDS 留安全余量", "requires_user_confirmation": True}],
            auto_fixable=True, confidence=0.85, blocking=True,
            possible_causes=["电子数增加", "NBANDS 设置过低"],
        )]


class DavOrEdddavErrorRule(Rule):
    rule_id = "DAV_OR_EDDDAV_ERROR"
    category = "core_errors"

    def run(self, parsed: ParsedRunData) -> list[Issue]:
        hits = _match_outcar(parsed, "EDDDAV", "DAV", "RMM-DIIS: failed")
        if not hits:
            return []
        err = hits[0]
        return [build_issue(
            rule_id=self.rule_id, severity=Severity.HIGH, category=self.category,
            title="电荷密度求解失败",
            summary="出现 EDDDAV/DAV 求解决败错误。",
            evidence=[{"file": "OUTCAR", "line": err.get("line"), "message": err.get("text", ""),
                       "data_ref": "outcar.error_lines"}],
            recommendations=[
                {"action": "review", "target": "user", "rationale": "检查 mixing、ALGO、结构"}],
            confidence=0.8, blocking=True,
            possible_causes=["电荷密度不一致", "结构/混合问题"],
        )]