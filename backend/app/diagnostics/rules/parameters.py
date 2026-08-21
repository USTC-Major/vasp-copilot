from __future__ import annotations

from ..issue_builder import build_issue
from ..engine import Rule
from ...schemas.issue import Issue
from ...schemas.mode import MagnetizationAnalysisMode
from ...schemas.parsed import ParsedRunData
from ...schemas.status import Severity

ATOMIC_MAGMOM_TOL = 0.05


def _natoms(parsed: ParsedRunData) -> int:
    return sum(parsed.poscar.counts)


def _as_list(v):
    if isinstance(v, list):
        return v
    if isinstance(v, (int, float)):
        return [v]
    return None


class LdauArrayLengthRule(Rule):
    rule_id = "LDAU_ARRAY_LENGTH_MISMATCH"
    category = "parameters"

    def run(self, parsed: ParsedRunData) -> list[Issue]:
        eff = parsed.incar.effective
        elems = parsed.poscar.elements
        n_elems = len(elems)
        if n_elems == 0:
            return []
        bad = []
        for key in ("LDAUL", "LDAUU", "LDAUJ"):
            val = _as_list(eff.get(key))
            if val and len(val) != n_elems:
                bad.append(key)
        if not bad:
            return []
        return [build_issue(
            rule_id=self.rule_id, severity=Severity.HIGH, category=self.category,
            title="LDAU 数组长度不匹配",
            summary=f"数组 {', '.join(bad)} 长度与 POSCAR 元素种类数 {n_elems} 不匹配。",
            evidence=[{"file": "INCAR", "message": f"LDAU 数组长度不匹配元素数 {n_elems}"}],
            recommendations=[
                {"action": "review", "target": "user", "rationale": "补全每个元素映射，缺 U 时要求用户输入，禁止猜值"}
            ],
            confidence=0.9, blocking=True,
            possible_causes=["元素顺序/数量不一致", "数组漏项"],
        )]


class MagmomCountMismatchRule(Rule):
    rule_id = "MAGMOM_COUNT_MISMATCH"
    category = "parameters"

    def run(self, parsed: ParsedRunData) -> list[Issue]:
        eff = parsed.incar.effective
        n = _natoms(parsed)
        mag = eff.get("MAGMOM")
        arr = _as_list(mag)
        if arr is None or n == 0:
            return []
        if len(arr) != n:
            return [build_issue(
                rule_id=self.rule_id, severity=Severity.HIGH, category=self.category,
                title="MAGMOM 展开数与原子数不匹配",
                summary=f"MAGMOM 展开数 {len(arr)} 不等于原子数 {n}。",
                evidence=[{"file": "INCAR", "message": f"MAGMOM 数 {len(arr)} vs atom {n}"}],
                recommendations=[
                    {"action": "review", "target": "user", "rationale": "按元素分组重新确认初始磁矩"}
                ],
                confidence=0.95, blocking=True,
                possible_causes=["MAGMOM 数组长度错误", "POSCAR 原子数变化"],
            )]
        return []


class IspinMagmomConflictRule(Rule):
    rule_id = "ISPIN_MAGMOM_CONFLICT"
    category = "parameters"

    def run(self, parsed: ParsedRunData) -> list[Issue]:
        eff = parsed.incar.effective
        ispin = eff.get("ISPIN")
        has_mag = _as_list(eff.get("MAGMOM")) is not None
        if ispin == 1 and has_mag:
            return [build_issue(
                rule_id=self.rule_id, severity=Severity.MEDIUM, category=self.category,
                title="ISPIN=1 却设置了 MAGMOM",
                summary="ISPIN=1（非磁性）但 INCAR 设置了 MAGMOM，二者冲突。",
                evidence=[{"file": "INCAR", "message": "ISPIN=1 与 MAGMOM 冲突"}],
                recommendations=[
                    {"action": "set_parameter", "target": "INCAR", "parameter": "ISPIN",
                     "new_value": "2", "rationale": "若为磁性计算请设置 ISPIN=2；否则移除 MAGMOM",
                     "requires_user_confirmation": True}
                ],
                auto_fixable=True, confidence=0.9, blocking=False,
                possible_causes=["磁性/非磁性意图不一致"],
            )]
        return []


class IonicControlConflictRule(Rule):
    rule_id = "IONIC_CONTROL_CONFLICT"
    category = "parameters"

    def run(self, parsed: ParsedRunData) -> list[Issue]:
        eff = parsed.incar.effective
        nsw = eff.get("NSW")
        ibrion = eff.get("IBRION")
        if isinstance(nsw, int) and isinstance(ibrion, int):
            if nsw > 0 and ibrion < 0:
                return [build_issue(
                    rule_id=self.rule_id, severity=Severity.HIGH, category=self.category,
                    title="离子控制冲突",
                    summary=f"NSW={nsw} 但 IBRION={ibrion}<0，离子步无法推进。",
                    evidence=[{"file": "INCAR", "message": "NSW/IBRION 冲突"}],
                    recommendations=[
                        {"action": "set_parameter", "target": "INCAR", "parameter": "IBRION",
                         "rationale": "使任务类型与离子控制一致"}],
                    auto_fixable=True, confidence=0.9, blocking=True,
                    possible_causes=["任务类型设置错误"],
                )]
        return []


class EdiffgSignSemanticsRule(Rule):
    rule_id = "EDIFFG_SIGN_SEMANTICS"
    category = "parameters"

    def run(self, parsed: ParsedRunData) -> list[Issue]:
        eff = parsed.incar.effective
        ediffg = eff.get("EDIFFG")
        nsw = eff.get("NSW")
        if isinstance(ediffg, (int, float)) and isinstance(nsw, int) and nsw > 0:
            if ediffg > 0 and nsw > 0:
                return [build_issue(
                    rule_id=self.rule_id, severity=Severity.MEDIUM, category=self.category,
                    title="EDIFFG 正负语义需确认",
                    summary=f"relax（NSW={nsw}>0）采用正值 EDIFFG={ediffg}；正值表示总能收敛，负值表示力收敛。",
                    evidence=[{"file": "INCAR", "message": "EDIFFG 正值用于 relax"}],
                    recommendations=[
                        {"action": "set_parameter", "target": "INCAR", "parameter": "EDIFFG",
                         "rationale": "若目标是力收敛建议用负值", "requires_user_confirmation": True}],
                    auto_fixable=True, confidence=0.7, blocking=False,
                    possible_causes=["能量/力收敛目标混淆"],
                )]
        return []


class LmaxmixTooLowForDftuRule(Rule):
    rule_id = "LMAXMIX_TOO_LOW_FOR_DFTU"
    category = "parameters"

    def run(self, parsed: ParsedRunData) -> list[Issue]:
        eff = parsed.incar.effective
        lmaxmix = eff.get("LMAXMIX")
        has_dftu = (eff.get("LDAU") is True) or (eff.get("LDAUU") is not None)
        if not has_dftu:
            return []
        need = 4
        if isinstance(lmaxmix, int) and lmaxmix < need:
            return [build_issue(
                rule_id=self.rule_id, severity=Severity.MEDIUM, category=self.category,
                title="DFT+U 下 LMAXMIX 可能偏低",
                summary=f"DFT+U 已启用，LMAXMIX={lmaxmix} 低于常见 d 体系下限 {need}，可能影响电荷密度混合。",
                evidence=[{"file": "INCAR", "message": f"LMAXMIX={lmaxmix} < {need}"}],
                recommendations=[
                    {"action": "set_parameter", "target": "INCAR", "parameter": "LMAXMIX",
                     "new_value": str(need), "rationale": "建议至少 {need}，需结合元素与 VASP 版本确认",
                     "requires_user_confirmation": True}],
                auto_fixable=True, confidence=0.7, blocking=False,
                possible_causes=["混合表示角动量不足"],
            )]
        return []


class IsmearTetraForMetalRiskRule(Rule):
    rule_id = "ISMEAR_TETRA_FOR_METAL_RISK"
    category = "parameters"

    def run(self, parsed: ParsedRunData) -> list[Issue]:
        eff = parsed.incar.effective
        ismear = eff.get("ISMEAR")
        if ismear == -5:
            return [build_issue(
                rule_id=self.rule_id, severity=Severity.LOW, category=self.category,
                title="tetrahedron 展宽适用性提示",
                summary="ISMEAR=-5（tetrahedron）对金属/马鞍点可能不适用，需确认目标与 K 网格。",
                evidence=[{"file": "INCAR", "message": "ISMEAR=-5"}],
                recommendations=[
                    {"action": "review", "target": "user", "rationale": "确认电子类型与展宽策略"}],
                confidence=0.5, blocking=False,
            )]
        return []


class Icharg11ChgcarMissingRule(Rule):
    rule_id = "ICHARG11_CHGCAR_MISSING"
    category = "parameters"

    def run(self, parsed: ParsedRunData) -> list[Issue]:
        eff = parsed.incar.effective
        have = set(parsed.source_files)
        if eff.get("ICHARG") == 11 and "CHGCAR" not in have:
            return [build_issue(
                rule_id=self.rule_id, severity=Severity.HIGH, category=self.category,
                title="ICHARG=11 但缺少 CHGCAR",
                summary="ICHARG=11 需要 CHGCAR，当前目录未提供，无法继承电荷密度。",
                evidence=[{"file": "INCAR", "message": "ICHARG=11"}],
                recommendations=[
                    {"action": "review", "target": "manifest", "rationale": "先完成 static 并继承其 CHGCAR"}],
                confidence=0.9, blocking=True,
                possible_causes=["上游 static 未完成", "CHGCAR 缺失"],
            )]
        return []