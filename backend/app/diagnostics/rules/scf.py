from __future__ import annotations

from ..analyzer import analyze_scf
from ..issue_builder import build_issue
from ..engine import Rule
from ...schemas.issue import Issue
from ...schemas.parsed import ParsedRunData
from ...schemas.status import Severity

OSCILLATION_FLIP_THRESHOLD = 0.3
OSCILLATION_AMP_THRESHOLD = 1e-3


class ScfReachedNelmRule(Rule):
    rule_id = "SCF_REACHED_NELM"
    category = "scf"

    def run(self, parsed: ParsedRunData) -> list[Issue]:
        incar = parsed.incar.effective
        nelm = incar.get("NELM")
        if nelm is None:
            # mark default assumption, do not assert
            return []
        m = analyze_scf(parsed.oszicar.energy_series, nelm=nelm,
                        oszicar_last_step=_last_electronic_steps(parsed))
        if not m.reached_nelm:
            return []
        return [build_issue(
            rule_id=self.rule_id, severity=Severity.HIGH, category=self.category,
            title="SCF 达到 NELM 上限",
            summary=f"电子步数达到配置的 NELM={nelm} 且未出现收敛标志，SCF 疑似未收敛。",
            evidence=[{"file": "OSZICAR", "message": f"last electronic step reached NELM={nelm}",
                      "data_ref": "oszicar.last_step"}],
            recommendations=[
                {"action": "set_parameter", "target": "INCAR", "parameter": "ALGO",
                 "rationale": "检查 ALGO/混合/展宽，一次只改少量参数"}
            ],
            auto_fixable=True, confidence=0.85, blocking=True,
            possible_causes=["ALGO 不合适", "mixing 参数差", "初始磁矩问题", "结构差"],
        )]


class ScfEnergyOscillationRule(Rule):
    rule_id = "SCF_ENERGY_OSCILLATION"
    category = "scf"

    def run(self, parsed: ParsedRunData) -> list[Issue]:
        m = analyze_scf(parsed.oszicar.energy_series)
        if m.steps < 4:
            return []
        # flip rate and non-decaying amplitude in the recent window
        if m.sign_flip_rate >= OSCILLATION_FLIP_THRESHOLD and \
           m.final_amplitude >= OSCILLATION_AMP_THRESHOLD and \
           m.amplitude_slope >= 0:
            return [build_issue(
                rule_id=self.rule_id, severity=Severity.HIGH, category=self.category,
                title="SCF 能量震荡不收敛",
                summary=f"最近窗口内能量差符号翻转率 {m.sign_flip_rate:.2f} 且幅度未衰减（斜率 {m.amplitude_slope:.2e}），疑似震荡。",
                evidence=[{"file": "OSZICAR", "message": "SCF 能量震荡",
                          "data_ref": "oszicar.energy_series"}],
                recommendations=[
                    {"action": "set_parameter", "target": "INCAR", "parameter": "AMIX",
                     "rationale": "降低混合/更换 ALGO/调整 SIGMA"}
                ],
                auto_fixable=True, confidence=0.8, blocking=False,
                possible_causes=["mixing 参数", "ALGO", "SIGMA", "初始磁矩"],
            )]
        return []


def _last_electronic_steps(parsed: ParsedRunData) -> int:
    return parsed.oszicar.last_step