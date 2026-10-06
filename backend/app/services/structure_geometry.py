"""Bounded read-only geometry from the exact POSCAR used by workflow generation.

All syntax and scale semantics belong to validate_poscar; this module only
converts validated coordinates. It never wraps positions or standardizes cells.
"""
from __future__ import annotations

import hashlib
import json
import math

from backend.input_validation import InputValidationError, validate_poscar
from ..core.errors import ValidationError
from ..schemas.structure import StructureSummary, _lattice_from_validated

VIEW_MAX_ATOMS = 2048
VIEW_MAX_BYTES = 1024 * 1024
VIEW_MAX_NUMBER = 1e6


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _bounded(values):
    if any(not math.isfinite(v) or abs(v) > VIEW_MAX_NUMBER for v in values):
        raise ValidationError("STRUCTURE_VIEW_NUMBER_LIMIT", "结构坐标超出只读查看的有限数值范围；原摘要和生成流程仍可使用")


def build_structure_geometry(structure_id: str, summary: StructureSummary):
    try:
        info = validate_poscar(summary.poscar_text, include_coordinates=True)
    except InputValidationError as exc:
        raise ValidationError("STRUCTURE_VIEW_INVALID", f"结构查看数据无效：{exc}") from exc
    if info.vasp4 or tuple(summary.elements) != info.elements or tuple(summary.counts) != info.counts:
        raise ValidationError("STRUCTURE_VIEW_INVALID", "结构查看物种与保存的 POSCAR 不一致")
    if info.atom_count > VIEW_MAX_ATOMS:
        raise ValidationError("STRUCTURE_VIEW_ATOM_LIMIT", f"只读结构查看最多支持 {VIEW_MAX_ATOMS} 个原子；原摘要和生成流程不受影响")
    basis = info.matrix
    _bounded(v for row in basis for v in row)
    if min(math.hypot(*v) for v in basis) < 1e-8:
        raise ValidationError("STRUCTURE_VIEW_NUMBER_LIMIT", "晶胞长度过小，无法可靠显示；原摘要和生成流程不受影响")
    # Row-vector basis: dual_i dot r gives fractional component i.
    determinant = _dot(basis[0], _cross(basis[1], basis[2]))
    if not math.isfinite(determinant) or determinant == 0:
        raise ValidationError("STRUCTURE_VIEW_NUMBER_LIMIT", "缩放后晶格数值无法可靠转换坐标；原摘要和生成流程不受影响")
    dual = [tuple(v / determinant for v in _cross(basis[j], basis[k]))
            for j, k in ((1, 2), (2, 0), (0, 1))]
    species = [element for element, count in zip(info.elements, info.counts) for _ in range(count)]
    sites = []
    for index, (element, coordinate) in enumerate(zip(species, info.coordinates)):
        if info.coordinate_mode == "direct":
            fractional = coordinate
            cartesian = tuple(sum(coordinate[i] * basis[i][j] for i in range(3)) for j in range(3))
        else:
            cartesian = tuple(coordinate[j] * info.cartesian_scale[j] for j in range(3))
            fractional = tuple(_dot(row, cartesian) for row in dual)
        _bounded((*fractional, *cartesian))
        site = {"id": index + 1, "element": element, "fractional": fractional,
                "cartesian_angstrom": cartesian}
        if info.selective_dynamics:
            site["selective_flags"] = info.selective_flags[index]
        sites.append(site)
    physical = {"basis_cartesian_angstrom": basis, "sites": sites}
    physical_json = json.dumps(physical, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    result = {
        "structure_id": structure_id, "formula": summary.formula, "atom_count": info.atom_count,
        **physical, "lattice": _lattice_from_validated(info).model_dump(mode="json"),
        "coordinate_mode": info.coordinate_mode, "selective_dynamics": info.selective_dynamics,
        "selective_flags_basis": "direct_lattice_vectors",
        "source": {"format": "materials_project" if summary.source_material_id else "cif" if summary.source_file.lower().endswith(".cif") else "poscar",
                   "file_name": summary.source_file, "material_id": summary.source_material_id,
                   "coordinate_source": "StructureSummary.poscar_text",
                   "poscar_sha256": hashlib.sha256(summary.poscar_text.encode("utf-8")).hexdigest()},
        "geometry_sha256": hashlib.sha256(physical_json.encode("utf-8")).hexdigest(),
    }
    if len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")) > VIEW_MAX_BYTES - 1024:
        raise ValidationError("STRUCTURE_VIEW_SIZE_LIMIT", "结构查看响应超过 1 MiB 上限；原摘要和生成流程不受影响")
    return result
