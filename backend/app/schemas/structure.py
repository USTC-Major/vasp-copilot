"""Doctor → BE-A 结构输入映射（IR-05）。

将 doctor 结构摘要（来自 POSCAR/CONTCAR 的元素/计数）桥接到
workflow 管线消费的 BE-A ``StructureContext``。"""

from __future__ import annotations

import hashlib
import math
from typing import List, Optional

from pydantic import BaseModel, ConfigDict

from backend.input_validation import InputValidationError, PoscarInfo, validate_poscar
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
    coordinate_mode: str = "direct"
    selective_dynamics: bool = False


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


def _lattice_from_validated(info: PoscarInfo) -> LatticeInfo:
    """Derive all lattice fields from the strictly validated POSCAR matrix."""
    matrix = [list(row) for row in info.matrix]

    (a, b, c) = (math.hypot(*matrix[i]) for i in range(3))
    unit = [[v / length for v in row] for row, length in zip(matrix, (a, b, c))]
    def cosine(i: int, j: int) -> float:
        return sum(x * y for x, y in zip(unit[i], unit[j]))
    acos_v = lambda v: math.degrees(math.acos(max(-1.0, min(1.0, v))))
    alpha = acos_v(cosine(1, 2))
    beta = acos_v(cosine(0, 2))
    gamma = acos_v(cosine(0, 1))
    return LatticeInfo(
        matrix=matrix,
        a=a,
        b=b,
        c=c,
        alpha=alpha,
        beta=beta,
        gamma=gamma,
        volume=info.volume,
    )


def build_structure_summary(
    *,
    poscar_text: str,
    elements: List[str],
    counts: List[int],
    source_file: str = "POSCAR",
    structure_id: Optional[str] = None,
    validated: Optional[PoscarInfo] = None,
) -> StructureSummary:
    """由 POSCAR 派生数据构建 doctor 侧 StructureSummary。"""
    info = validated or validate_poscar(poscar_text)
    if info.vasp4:
        raise InputValidationError("POSCAR_SPECIES_REQUIRED", "该 POSCAR 未列物种；请提供含元素符号行的 POSCAR")
    if tuple(elements) != info.elements or tuple(counts) != info.counts:
        raise InputValidationError("POSCAR_METADATA_MISMATCH", "POSCAR 物种或数量与结构信息不一致")
    sha = hashlib.sha256(poscar_text.encode("utf-8")).hexdigest()
    return StructureSummary(
        structure_id=structure_id,
        source_file=source_file,
        formula=_derive_formula(elements, counts),
        elements=list(elements),
        counts=list(counts),
        atom_count=int(sum(counts)),
        lattice=_lattice_from_validated(info),
        poscar_text=poscar_text,
        source_sha256=sha,
        transition_metals=_detect_transition_metals(elements),
        coordinate_mode=info.coordinate_mode,
        selective_dynamics=info.selective_dynamics,
    )


def validated_structure_context(context: StructureContext) -> StructureContext:
    """Discard untrusted derived fields and check metadata against source text."""
    info = validate_poscar(context.poscar_text)
    if info.vasp4:
        raise InputValidationError("POSCAR_SPECIES_REQUIRED", "该 POSCAR 未列物种；请提供含元素符号行的 POSCAR")
    if (tuple(context.elements) != info.elements or tuple(context.counts) != info.counts
            or context.atom_count != info.atom_count):
        raise InputValidationError("POSCAR_METADATA_MISMATCH", "工作流物种、数量或原子总数与 POSCAR 不一致")
    summary = build_structure_summary(poscar_text=context.poscar_text,
                                      elements=list(info.elements), counts=list(info.counts),
                                      validated=info)
    return context.model_copy(update={
        "formula": summary.formula, "lattice": summary.lattice,
        "source_sha256": summary.source_sha256,
        "transition_metals": list(summary.transition_metals),
    })


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
