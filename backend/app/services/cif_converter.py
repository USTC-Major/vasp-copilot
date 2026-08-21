"""CIF -> 真实 Structure -> POSCAR 转换（fail closed）。

职责单一：把 CIF 文本经 pymatgen ``CifParser`` 对称性展开为有序 Structure，
再写出保留真实分数坐标的 POSCAR。禁止生成任何占位/猜测坐标；任何无法可靠
转换的输入（解析失败、缺原子坐标、多 data block、部分占据/无序）均抛出
带稳定错误码的 ``ValidationError``，调用方不得在失败后继续生成 POSCAR。

诊断侧的 CIF 元数据提取仍由 ``app.parsers.cif.parse_cif`` 负责，两者互不依赖。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from pymatgen.core import Structure
from pymatgen.io.cif import CifParser
from pymatgen.io.vasp import Poscar

from ..core.errors import ValidationError

# symmetry_tolerance 合法范围：必须为正，且不超过该上限（单位 Å）。
_SYMMETRY_TOLERANCE_MAX = 1.0
# 位点总占据数与 1.0 的允许偏差（数值噪声级别）。
_OCCUPANCY_TOLERANCE = 1e-4

# CIF 文档特征检查（仅用于错误分类，不解析、不生成任何坐标）。
_DATA_BLOCK_RE = re.compile(r"^\s*data_\S+", re.IGNORECASE | re.MULTILINE)
# 完整坐标字段三元组：分数坐标或笛卡尔坐标（CIF 标签大小写不敏感）。
_COORD_FIELD_SETS = (
    ("_atom_site_fract_x", "_atom_site_fract_y", "_atom_site_fract_z"),
    ("_atom_site_cartn_x", "_atom_site_cartn_y", "_atom_site_cartn_z"),
)


def _has_data_block(text: str) -> bool:
    return _DATA_BLOCK_RE.search(text) is not None


def _has_full_coordinate_fields(text: str) -> bool:
    lowered = text.lower()
    return any(all(field in lowered for field in fields)
               for fields in _COORD_FIELD_SETS)


@dataclass(frozen=True)
class CifConversion:
    """一次成功 CIF 转换的产物。"""

    poscar_text: str
    formula: str
    atom_count: int
    standardized: bool


def _ensure_ordered(structure: Structure) -> None:
    """拒绝 partial occupancy / 混合占据 / 空位，MVP 不替用户决定占据方式。"""
    for site in structure:
        species = site.species
        if (
            not site.is_ordered
            or len(species) != 1
            or abs(species.num_atoms - 1.0) > _OCCUPANCY_TOLERANCE
        ):
            raise ValidationError(
                "CIF_DISORDERED_NOT_SUPPORTED",
                "CIF contains partial-occupancy or disordered sites; "
                "resolve the disorder and re-upload an ordered structure",
            )


def _group_sites_by_first_occurrence(structure: Structure) -> Structure:
    """按元素首次出现顺序稳定分组 sites，保证 POSCAR 元素行唯一且连续。

    只重排 sites 顺序：lattice、species、fractional coordinates、site
    properties 均原样保留，同一元素内部保持原始相对顺序；分组前后结构
    周期等价。仅允许在已通过 ``_ensure_ordered`` 的有序结构上调用。
    """
    order: dict[str, int] = {}
    for site in structure:
        symbol = site.specie.symbol
        order.setdefault(symbol, len(order))
    indexed = sorted(
        enumerate(structure), key=lambda pair: order[pair[1].specie.symbol]
    )
    return Structure.from_sites([site for _, site in indexed])


def convert_cif_to_poscar(
    text: str,
    *,
    source_file: str = "",
    standardize: bool = False,
    symmetry_tolerance: float = 0.01,
) -> CifConversion:
    """将 CIF 文本转换为保留真实坐标的 POSCAR（fail closed）。

    ``standardize=False`` 时保持 ``parse_structures(primitive=False)`` 的
    原样输出，不做任何 primitive 化或 conventional standardize；
    ``standardize=True`` 时用 SpacegroupAnalyzer(symprec=symmetry_tolerance)
    取 conventional standard 结构，并在标准化后再次执行无序检查。
    """
    if not 0.0 < symmetry_tolerance <= _SYMMETRY_TOLERANCE_MAX:
        raise ValidationError(
            "CIF_INVALID_SYMMETRY_TOLERANCE",
            f"symmetry_tolerance must be in (0, {_SYMMETRY_TOLERANCE_MAX}]",
        )

    # 文档特征检查：仅用于错误分类，不解析、不生成坐标。
    if not _has_data_block(text):
        raise ValidationError(
            "CIF_PARSE_FAILED",
            "CIF could not be parsed; check the file and re-upload",
        )
    if not _has_full_coordinate_fields(text):
        raise ValidationError(
            "CIF_MISSING_COORDINATES",
            "CIF contains no atom-site coordinate fields; cannot generate "
            "a POSCAR",
        )

    try:
        parser = CifParser.from_str(text)
        structures = parser.parse_structures(primitive=False)
    except Exception as exc:
        # 坐标字段齐全但 pymatgen 仍失败：不依据第三方异常文案分类。
        raise ValidationError(
            "CIF_PARSE_FAILED",
            "CIF could not be parsed; check the file and re-upload",
        ) from exc

    if len(structures) == 0:
        raise ValidationError(
            "CIF_MISSING_COORDINATES",
            "CIF contains no parseable atom sites with fractional "
            "coordinates; cannot generate a POSCAR",
        )
    if len(structures) > 1:
        raise ValidationError(
            "CIF_MULTIPLE_STRUCTURES_NOT_SUPPORTED",
            "CIF contains multiple structure blocks; split the CIF into one "
            "structure per file and re-upload",
        )

    structure = structures[0]
    if len(structure) == 0:
        raise ValidationError(
            "CIF_MISSING_COORDINATES",
            "CIF contains no parseable atom sites with fractional "
            "coordinates; cannot generate a POSCAR",
        )

    _ensure_ordered(structure)

    standardized = False
    if standardize:
        from pymatgen.symmetry.analyzer import SpacegroupAnalyzer

        try:
            analyzer = SpacegroupAnalyzer(structure, symprec=symmetry_tolerance)
            structure = analyzer.get_conventional_standard_structure()
        except Exception as exc:
            raise ValidationError(
                "CIF_STANDARDIZE_FAILED",
                "structure could not be standardized with the given "
                "symmetry_tolerance",
            ) from exc
        _ensure_ordered(structure)
        standardized = True

    structure = _group_sites_by_first_occurrence(structure)
    poscar = Poscar(structure)
    return CifConversion(
        poscar_text=poscar.get_str(),
        formula=structure.composition.reduced_formula,
        atom_count=len(structure),
        standardized=standardized,
    )
