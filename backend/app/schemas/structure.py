"""Doctor → BE-A 结构输入映射（IR-05）。

将 doctor 结构摘要（来自 POSCAR/CONTCAR 的元素/计数）桥接到
workflow 管线消费的 BE-A ``StructureContext``。"""

from __future__ import annotations

import hashlib
import math
from typing import List, Optional

from pydantic import BaseModel, ConfigDict

from backend.app.schemas.generation import LatticeInfo, StructureContext


class StructureSummary(BaseModel):
    """Doctor 侧结构摘要（BE-A StructureContext 的子集）。"""

    model_config = ConfigDict(extra="ignore")

    structure_id: Optional[str] = None
    source_file: str = ""
    formula: str = ""
    elements: List[str] = []
    counts: List[int] = []
    atom_count: int = 0
    lattice: Optional[LatticeInfo] = None
    poscar_text: str = ""
    source_sha256: Optional[str] = None
    transition_metals: List[str] = []


# d-block metals commonly checked for magnetism/DFT+U hints.
_TRANSITION_METALS = {
    "Sc", "Ti", "V", "Cr", "Mn", "Fe", "Co", "Ni", "Cu", "Zn",
    "Y", "Zr", "Nb", "Mo", "Tc", "Ru", "Rh", "Pd", "Ag", "Cd",
    "Hf", "Ta", "W", "Re", "Os", "Ir", "Pt", "Au", "Hg",
}


def _derive_formula(elements: List[str], counts: List[int]) -> str:
    parts = []
    for element, count in zip(elements, counts):
        parts.append(element + (str(count) if count > 1 else ""))
    return "".join(parts)


def _detect_transition_metals(elements: List[str]) -> List[str]:
    return [element for element in elements if element in _TRANSITION_METALS]


def _parse_lattice(text: str) -> Optional[LatticeInfo]:
    """解析 POSCAR scale factor + 3 个晶格矢量得到 LatticeInfo。"""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 5:
        return None
    try:
        scale = float(lines[1])
    except ValueError:
        scale = 1.0
    rows: List[List[float]] = []
    for index in range(2, 5):
        try:
            rows.append([float(v) for v in lines[index].split()[:3]])
        except ValueError:
            return None
    if len(rows) != 3 or any(len(row) != 3 for row in rows):
        return None
    matrix = [[scale * value for value in row] for row in rows]

    def _dot(u: List[float], v: List[float]) -> float:
        return sum(x * y for x, y in zip(u, v))

    def _norm(u: List[float]) -> float:
        return math.sqrt(sum(x * x for x in u))

    (a, b, c) = (_norm(matrix[i]) for i in range(3))
    acos_v = lambda v: math.degrees(math.acos(max(-1.0, min(1.0, v))))
    alpha = acos_v(_dot(matrix[1], matrix[2]) / (_norm(matrix[1]) * _norm(matrix[2])))
    beta = acos_v(_dot(matrix[0], matrix[2]) / (_norm(matrix[0]) * _norm(matrix[2])))
    gamma = acos_v(_dot(matrix[0], matrix[1]) / (_norm(matrix[0]) * _norm(matrix[1])))
    cross = [
        matrix[1][1] * matrix[2][2] - matrix[1][2] * matrix[2][1],
        matrix[1][2] * matrix[2][0] - matrix[1][0] * matrix[2][2],
        matrix[1][0] * matrix[2][1] - matrix[1][1] * matrix[2][0],
    ]
    volume = abs(_dot(matrix[0], cross))
    return LatticeInfo(
        matrix=matrix,
        a=a,
        b=b,
        c=c,
        alpha=alpha,
        beta=beta,
        gamma=gamma,
        volume=volume,
    )


def build_structure_summary(
    *,
    poscar_text: str,
    elements: List[str],
    counts: List[int],
    source_file: str = "POSCAR",
    structure_id: Optional[str] = None,
) -> StructureSummary:
    """由 POSCAR 派生数据构建 doctor 侧 StructureSummary。"""
    sha = hashlib.sha256(poscar_text.encode("utf-8")).hexdigest()
    return StructureSummary(
        structure_id=structure_id,
        source_file=source_file,
        formula=_derive_formula(elements, counts),
        elements=list(elements),
        counts=list(counts),
        atom_count=int(sum(counts)),
        lattice=_parse_lattice(poscar_text),
        poscar_text=poscar_text,
        source_sha256=sha,
        transition_metals=_detect_transition_metals(elements),
    )


def to_structure_context(summary: StructureSummary) -> StructureContext:
    """IR-05：将 doctor StructureSummary 映射为 BE-A StructureContext。"""
    return StructureContext(
        structure_id=summary.structure_id,
        formula=summary.formula,
        elements=list(summary.elements),
        counts=list(summary.counts),
        atom_count=summary.atom_count,
        lattice=summary.lattice,
        poscar_text=summary.poscar_text,
        source_sha256=summary.source_sha256,
        transition_metals=list(summary.transition_metals),
    )
