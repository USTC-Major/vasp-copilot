from __future__ import annotations

from ..issue_builder import build_issue
from ..engine import Rule
from ...schemas.issue import Issue
from ...schemas.mode import MagnetizationAnalysisMode
from ...schemas.parsed import ParsedRunData
from ...schemas.status import Severity


def _initial_magmom(parsed: ParsedRunData):
    v = parsed.incar.effective.get("MAGMOM")
    if isinstance(v, list):
        return [float(x) for x in v]
    if isinstance(v, (int, float)):
        return [float(v)]
    return []


def _comparable_atoms(initial: list[float], final: list) -> list:
    """按索引配对原子；仅初始/最终磁矩均已知的原子参与。"""
    pairs = []
    for i, iv in enumerate(initial):
        if i < len(final):
            fv = final[i].get("tot")
            if fv is not None:
                pairs.append((iv, fv))
    return pairs


class MagmomSignFlipRule(Rule):
    rule_id = "MAGMOM_SIGN_FLIP"
    category = "magnetic"

    def run(self, parsed: ParsedRunData) -> list[Issue]:
        mode = parsed.calculation_mode.magnetization_analysis_mode
        if mode != MagnetizationAnalysisMode.COLLINEAR:
            return []
        final = parsed.outcar.final_magnetization or []
        initial = _initial_magmom(parsed)
        flips = []
        for i, (iv, fv) in enumerate(_comparable_atoms(initial, final)):
            if iv > 1e-3 and fv < -1e-3:
                flips.append((i + 1, iv, fv))
        if not flips:
            return []
        detail = ", ".join(f"atom{i}: {a}->{b:.3f}" for i, a, b in flips[:5])
        return [build_issue(
            rule_id=self.rule_id, severity=Severity.MEDIUM, category=self.category,
            title="磁矩符号翻转（提示）",
            summary=f"部分原子最终磁矩符号与初始相反（{detail}）。可能是物理允许的磁态变化。",
            evidence=[{"file": "OUTCAR", "message": "最终局域磁矩符号与初始相反",
                      "data_ref": "outcar.final_magnetization"}],
            recommendations=[
                {"action": "review", "target": "user", "rationale": "核实预期磁态；可考虑新的初始磁矩"}
            ],
            confidence=0.6, blocking=False,
            possible_causes=["不同的磁态", "初始磁矩不合适"],
        )]


class LocalMomentCollapseRule(Rule):
    rule_id = "LOCAL_MOMENT_COLLAPSE"
    category = "magnetic"

    def run(self, parsed: ParsedRunData) -> list[Issue]:
        mode = parsed.calculation_mode.magnetization_analysis_mode
        if mode != MagnetizationAnalysisMode.COLLINEAR:
            return []
        final = parsed.outcar.final_magnetization or []
        initial = _initial_magmom(parsed)
        collapsed = 0
        for iv, fv in _comparable_atoms(initial, final):
            if abs(iv) > 0.5 and abs(fv) < 0.05:
                collapsed += 1
        if collapsed == 0:
            return []
        return [build_issue(
            rule_id=self.rule_id, severity=Severity.MEDIUM, category=self.category,
            title="局域磁矩塌缩（提示）",
            summary=f"有 {collapsed} 个原子初始磁矩较大而最终接近零，疑似磁矩塌缩。需核实预期磁态。",
            evidence=[{"file": "OUTCAR", "message": "最终局域磁矩趋于零",
                      "data_ref": "outcar.final_magnetization"}],
            recommendations=[
                {"action": "review", "target": "user", "rationale": "核实磁态、结构、U/泛函与初始化"}
            ],
            confidence=0.6, blocking=False,
            possible_causes=["磁态变化", "结构/泛函/U 问题"],
        )]