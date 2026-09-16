"""M10 报告与收尾：从 VASP 输出文本解析关键结果（纯本地、纯文本、只读）。

对齐 WORKFLOW.md v14 §2 步8、MODULE_INTERFACES v1.2 §1.8：
- 提取只读输出文本（OUTCAR / OSZICAR 等），产出结构化摘要供 LLM 提炼。
- 数据边界：本地只留报告；数据/图表不默认下载，需用户要求再提取。
- 本层不含 LLM；提炼交给渲染层（可离线替代默认摘要）。
"""
from __future__ import annotations

import re
import math
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Optional

from backend.app.parsers.outcar import parse_outcar as parse_full_outcar
from backend.app.parsers.oszicar import parse_oszicar as parse_full_oszicar
from backend.app.parsers.incar import parse_incar
from backend.app.parsers.kpoints import parse_kpoints
from backend.app.schemas.parsed import ParsedRunData
from backend.app.diagnostics.rules.kpoints import KpointsLineModeWithoutStaticRule
from backend.app.diagnostics.rules.core_errors import (
    ZhegvLapackFailureRule, TooFewBandsRule, DavOrEdddavErrorRule)

# ---------------------------------------------------------------------------
# OUTCAR
# ---------------------------------------------------------------------------

_KEYS = {
    "ENCUT": re.compile(r"ENCUT\s*=\s*(\d+)"),
    "EDIFF": re.compile(r"EDIFF\s*=\s*([-+]?\d+(?:\.\d+)?(?:[Ee][-+]?\d+)?)"),
    "IBRION": re.compile(r"IBRION\s*=\s*(\d+)"),
    "ISIF": re.compile(r"ISIF\s*=\s*(\d+)"),
    "NSW": re.compile(r"NSW\s*=\s*(\d+)"),
    "ISMEAR": re.compile(r"ISMEAR\s*=\s*(-?\d+)"),
    "SIGMA": re.compile(r"SIGMA\s*=\s*([-+]?\d+(?:\.\d+)?(?:[Ee][-+]?\d+)?)"),
}
_FLOAT = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[EeDd][-+]?\d+)?"
_REF_ENERGY = re.compile(rf"free\s+energy\s+TOTEN\s*=\s*({_FLOAT})")
_EFERMI = re.compile(rf"E-fermi\s*:\s*({_FLOAT})")


def _number(text: str) -> float:
    return float(text.replace("D", "E").replace("d", "e"))


@dataclass
class OutcarSummary:
    """OUTCAR 的结构化摘要。unavailable 字段为 None。"""

    settings: dict[str, Optional[float]]         # ENCUT/EDIFF/IBRION/ISIF/NSW/ISMEAR/SIGMA
    energies_free: list[float] = field(default_factory=list)   # 各离子步 TOTEN
    efermi: Optional[float] = None
    converged: bool = False
    unrecoverable_error: bool = False

    @property
    def n_ionic_steps(self) -> int:
        return len(self.energies_free)

    @property
    def final_energy(self) -> Optional[float]:
        return self.energies_free[-1] if self.energies_free else None


def parse_outcar(text: str) -> OutcarSummary:
    """解析 OUTCAR 文本，返回结构化摘要（从缺失字段容忍）。"""
    settings: dict[str, Optional[float]] = {}
    for name, pat in [("ENCUT", _KEYS["ENCUT"]), ("EDIFF", _KEYS["EDIFF"]),
                      ("IBRION", _KEYS["IBRION"]), ("ISIF", _KEYS["ISIF"]),
                      ("NSW", _KEYS["NSW"]), ("ISMEAR", _KEYS["ISMEAR"]),
                      ("SIGMA", _KEYS["SIGMA"])]:
        m = pat.search(text)
        settings[name] = float(m.group(1)) if m else None
    energies = [_number(m.group(1)) for m in _REF_ENERGY.finditer(text)]
    efermi_m = _EFERMI.search(text)
    return OutcarSummary(
        settings=settings,
        energies_free=energies,
        efermi=_number(efermi_m.group(1)) if efermi_m else None,
        converged=parse_full_outcar(text).ionic_convergence_reached is True,
        unrecoverable_error="Unrecoverable error" in text
                        or "vasp has stopped" in text,
    )


# ---------------------------------------------------------------------------
# OSZICAR
# ---------------------------------------------------------------------------
_OSZI_LINE = re.compile(rf"^\s*(\d+)\s+F=\s*({_FLOAT})")
_DAV_LINE = re.compile(rf"DAV:?\s+\d+\s+({_FLOAT})")


@dataclass
class OszicarSummary:
    """OSZICAR 的结构化摘要。"""

    ionic_energies: list[float] = field(default_factory=list)  # 每次离子步的自由能
    dav_iterations: int = 0                                   # DAV 迭代计数（最近一次）
    dav_energies: list[float] = field(default_factory=list)    # 最近一次离子步的 eDAV

    @property
    def final_energy(self) -> Optional[float]:
        return self.ionic_energies[-1] if self.ionic_energies else None


def parse_osziacar(text: str) -> OszicarSummary:
    """解析 OSZICAR 文本，返回离子步自由能与最近一步 DAV 迭代。"""
    summary = OszicarSummary()
    davs: list[list[float]] = []
    current: list[float] = []
    for line in (text or "").splitlines():
        if _DAV_LINE.match(line):
            m = _DAV_LINE.search(line)
            current.append(_number(m.group(1)))
            summary.dav_iterations += 1
            continue
        m = _OSZI_LINE.match(line)
        if m:
            summary.ionic_energies.append(_number(m.group(2)))
            if current:
                davs.append(current)
                current = []
    if current:
        davs.append(current)
    if davs:
        summary.dav_energies = davs[-1]
    return summary


# ---------------------------------------------------------------------------
# 组合摘要（可直接进报告/提炼回调）
# ---------------------------------------------------------------------------
def summarize_run(outcar_text: str, osziacar_text: str = "") -> dict:
    """合并 OUTCAR + OSZICAR 为字典摘要，供 refine 回调与报告使用。"""
    o = parse_outcar(outcar_text)
    z = parse_osziacar(osziacar_text)
    return {
        "outcar": {
            # OUTCAR repeats TOTEN during electronic iterations; its count
            # is not an ionic-step count. Use explicit OSZICAR summaries,
            # preserving repeated numbering across restart blocks.
            "n_ionic_steps": len(z.ionic_energies) if z.ionic_energies else None,
            "final_energy": o.final_energy,
            "efermi": o.efermi,
            "converged": o.converged,
            "unrecoverable_error": o.unrecoverable_error,
            "settings": {k: v for k, v in o.settings.items() if v is not None},
        },
        "osziacar": {
            "final_energy": z.final_energy,
            "n_ionic_steps": len(z.ionic_energies),
            "recent_energies": z.ionic_energies[-3:],
            "n_dav_last": len(z.dav_energies) if z.dav_energies else None,
        },
    }


def verify_run(outcar_text: str, oszicar_text: str = "", *,
               incar_text: str = "", kpoints_text: str = "",
               source_files: Iterable[str] = (), job_kind: str = "") -> dict:
    """Conservative result gate. Energy alone never proves completion.

    EDIFF requires both dE and d epsilon (https://vasp.at/wiki/EDIFF).
    Missing/truncated evidence remains unknown and is safe to query again.
    Input diagnostics are advice, never permission to change parameters.
    """
    oc = parse_full_outcar(outcar_text)
    oz = parse_full_oszicar(oszicar_text)
    incar = parse_incar(incar_text)
    settings = {}
    # Only executed OUTCAR settings can prove convergence. Editable INCAR
    # remains useful for advice, but cannot retrospectively relax EDIFF.
    for tag in ("EDIFF", "NELM", "NSW", "IBRION", "ICHARG", "ISMEAR"):
        matches = list(re.finditer(rf"\b{tag}\s*=\s*({_FLOAT})", outcar_text))
        if matches:
            settings[tag] = _number(matches[-1].group(1))
    parsed = ParsedRunData(outcar=oc, oszicar=oz, incar=incar,
                           kpoints=parse_kpoints(kpoints_text),
                           source_files=list(source_files))
    issues = [issue.model_dump(mode="json")
              for rule in (KpointsLineModeWithoutStaticRule(),
                           ZhegvLapackFailureRule(), TooFewBandsRule(),
                           DavOrEdddavErrorRule()) for issue in rule.run(parsed)]
    evidence = [{"file": "OUTCAR", **row} for row in oc.error_lines]
    fatal = list(re.finditer(
        r"(?im)^.*(?:Unrecoverable error|vasp has stopped|"
        r"charge density could not be read|error.*CHGCAR).*$", outcar_text))
    evidence.extend({"file": "OUTCAR", "line": outcar_text[:m.start()].count("\n") + 1,
                     "text": m.group(0).strip()} for m in fatal)
    recommendations = ["核对当前作业的 OUTCAR、OSZICAR 和调度日志；证据不足时继续查询，禁止重提。"]
    ismear = settings.get("ISMEAR", incar.effective.get("ISMEAR"))
    if parsed.kpoints.line_mode and ismear in (-4, -5):
        issues.append({"rule_id": "BAND_LINE_MODE_TETRAHEDRON",
                       "title": "line-mode 与四面体积分设置需要核对",
                       "evidence": [{"file": "KPOINTS", "message": "line-mode"},
                                    {"file": "INCAR/OUTCAR", "message": f"ISMEAR={ismear}"}],
                       "recommendations": [{"rationale": "核对能带路径的积分/展宽设置；由用户决定参数修改。"}]})
    for issue in issues:
        recommendations.extend(r.get("rationale", "") for r in issue.get("recommendations", []))

    electronic = None
    last = oz.electronic_steps[-1] if oz.electronic_steps else None
    ediff = settings.get("EDIFF")
    complete_block = bool(oz.ionic_steps and oz.last_electronic_step and not any(
        "trailing electronic block" in w or "no ionic summary" in w
        for w in oz.parser_warnings))
    if (last is not None and complete_block and isinstance(ediff, (float, int))
            and not isinstance(ediff, bool) and math.isfinite(ediff) and ediff > 0
            and last.delta_energy is not None and last.delta_epsilon is not None):
        electronic = (math.isfinite(last.delta_energy) and math.isfinite(last.delta_epsilon)
                      and abs(last.delta_energy) < ediff and abs(last.delta_epsilon) < ediff)
        evidence.append({"file": "OSZICAR", "line": last.source_line,
                         "text": f"dE={last.delta_energy}, d_eps={last.delta_epsilon}, EDIFF={ediff}"})
    relax = (settings.get("IBRION") in (1, 2, 3) and settings.get("NSW", 0) > 0
             or any(w in job_kind.lower() for w in ("relax", "优化", "弛豫")))
    if oc.normal_termination:
        evidence.append({"file": "OUTCAR", "text": "General timing and accounting informations"})
    if evidence and (oc.error_lines or fatal):
        status, reason = "failed", "OUTCAR 含明确错误证据"
    elif not oc.normal_termination:
        status, reason = "unknown", "缺少正常结束证据，可能尚未写完或异常终止"
    elif electronic is False:
        status, reason = "not_converged", "最终电子步未满足 EDIFF 的两个能量差条件"
    elif relax and not oc.ionic_convergence_reached and settings.get("NSW", 0) > 0 and oz.last_ionic_step >= settings["NSW"]:
        status, reason = "not_converged", "达到 NSW，未检测到结构优化收敛停止证据"
    elif electronic is not True:
        status, reason = "unknown", "缺少最终电子收敛证据（需要完整 OSZICAR 与 OUTCAR 中的 EDIFF）"
    elif relax and not oc.ionic_convergence_reached:
        status, reason = "unknown", "缺少结构优化收敛停止证据"
    elif oc.final_energy is None or not math.isfinite(oc.final_energy):
        status, reason = "unknown", "缺少有效最终能量"
    else:
        status, reason = "completed", "正常结束且所需收敛证据齐全"
    if status in {"failed", "not_converged"}:
        recommendations.append("保留输出并核对诊断；用户要求重试时调用 retry_job，重新准备、硬预检和一次性提交确认。")
    return {"status": status, "reason": reason, "normal_termination": oc.normal_termination,
            "electronic_converged": electronic, "ionic_converged": oc.ionic_convergence_reached,
            "evidence": evidence, "issues": issues, "recommendations": recommendations}
