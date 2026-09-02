"""M10 报告与收尾：从 VASP 输出文本解析关键结果（纯本地、纯文本、只读）。

对齐 WORKFLOW.md v14 §2 步8、MODULE_INTERFACES v1.2 §1.8：
- 提取只读输出文本（OUTCAR / OSZICAR 等），产出结构化摘要供 LLM 提炼。
- 数据边界：本地只留报告；数据/图表不默认下载，需用户要求再提取。
- 本层不含 LLM；提炼交给渲染层（可离线替代默认摘要）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Optional

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
_FLOAT = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[Ee][-+]?\d+)?"
_REF_ENERGY = re.compile(rf"free\s+energy\s+TOTEN\s*=\s*({_FLOAT})")
_EFERMI = re.compile(rf"E-fermi\s*:\s*({_FLOAT})")


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
    energies = [float(m.group(1)) for m in _REF_ENERGY.finditer(text)]
    efermi_m = _EFERMI.search(text)
    return OutcarSummary(
        settings=settings,
        energies_free=energies,
        efermi=float(efermi_m.group(1)) if efermi_m else None,
        converged="achieved" in text and "convergence" in text,
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
            current.append(float(m.group(1)))
            summary.dav_iterations += 1
            continue
        m = _OSZI_LINE.match(line)
        if m:
            summary.ionic_energies.append(float(m.group(2)))
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
            "n_ionic_steps": o.n_ionic_steps,
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
