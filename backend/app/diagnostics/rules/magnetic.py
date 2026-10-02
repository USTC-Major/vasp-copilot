from __future__ import annotations

from ..issue_builder import build_issue
from ..engine import Rule
from ...schemas.issue import Issue
from ...schemas.mode import MagnetizationAnalysisMode
from ...schemas.parsed import ParsedRunData
from ...schemas.status import Severity
from ..magnetic_analysis import build_magnetic_analysis


class MagmomSignFlipRule(Rule):
    rule_id = "MAGMOM_SIGN_FLIP"
    category = "magnetic"

    def run(self, parsed: ParsedRunData) -> list[Issue]:
        mode = parsed.calculation_mode.magnetization_analysis_mode
        if mode != MagnetizationAnalysisMode.COLLINEAR:
            return []
        analysis = build_magnetic_analysis(parsed)
        if analysis["pattern"] == "global_reversed":
            return []
        flips = [(a["atom_index"], a["input_reference"], a["output_moment"])
                 for a in analysis["atoms"] if a["comparison_available"] and a["orientation"] == "reversed"]
        if not flips:
            return []
        detail = ", ".join(f"atom{i}: {a}->{b:.3f}" for i, a, b in flips[:5])
        return [build_issue(
            rule_id=self.rule_id, severity=Severity.MEDIUM, category=self.category,
            title="局域投影相对输入参考反向（提示）",
            summary=f"部分原子最近局域投影与输入 MAGMOM 参考符号相反（{detail}）；使用显示近零阈值 0.05 μB，不判断磁性基态或收敛。",
            evidence=[{"file": "OUTCAR", "message": "最近局域投影与输入 MAGMOM 参考反向（非实测初始态）",
                      "data_ref": "outcar.final_magnetization"}],
            recommendations=[
                {"action": "review", "target": "user", "rationale": "结合原子对应、投影方法及计算阶段核实预期排列"}
            ],
            confidence=0.6, blocking=False,
            possible_causes=["输入参考与输出投影不同", "计算阶段或投影方法影响"],
        )]


class LocalMomentCollapseRule(Rule):
    rule_id = "LOCAL_MOMENT_COLLAPSE"
    category = "magnetic"

    def run(self, parsed: ParsedRunData) -> list[Issue]:
        mode = parsed.calculation_mode.magnetization_analysis_mode
        if mode != MagnetizationAnalysisMode.COLLINEAR:
            return []
        analysis = build_magnetic_analysis(parsed)
        collapsed = sum(a["comparison_available"] and abs(a["input_reference"]) > 0.5
                        and a["output_group"] == "near_zero" for a in analysis["atoms"])
        if collapsed == 0:
            return []
        return [build_issue(
            rule_id=self.rule_id, severity=Severity.MEDIUM, category=self.category,
            title="较大输入参考对应近零局域投影（提示）",
            summary=f"有 {collapsed} 个原子输入 MAGMOM 参考幅值 >0.5 μB，而最近输出投影在显示近零区间（≤0.05 μB）。这与幅值衰减标签不同，不证明实际磁矩随时间塌缩。",
            evidence=[{"file": "OUTCAR", "message": "输入参考较大，最近输出局域投影接近零（显示启发式）",
                      "data_ref": "outcar.final_magnetization"}],
            recommendations=[
                {"action": "review", "target": "user", "rationale": "结合原子对应、投影方法及计算阶段核实预期局域磁矩"}
            ],
            confidence=0.6, blocking=False,
            possible_causes=["输入参考与输出投影不同", "计算阶段或投影方法影响"],
        )]
