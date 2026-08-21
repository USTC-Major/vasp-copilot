"""CIF -> POSCAR 转换器单元测试（真实坐标、fail closed）。

fixture 均为内联小型科学 CIF；坐标断言以 CIF 真值为准，比较采用
周期边界等价（相差整数平移视为相同），含对称性结构使用
pymatgen StructureMatcher 做周期等价判定。
"""
from __future__ import annotations

import pytest
from pymatgen.analysis.structure_matcher import StructureMatcher
from pymatgen.core import Lattice, Structure
from pymatgen.io.cif import CifParser
from pymatgen.io.vasp import Poscar

from app.core.errors import ValidationError
from app.services.cif_converter import (
    _group_sites_by_first_occurrence,
    convert_cif_to_poscar,
)

# P1 无对称小胞：坐标全部显式给出，真值可直接断言。
P1_SMALL = """data_p1_small
_cell_length_a 4.0
_cell_length_b 5.0
_cell_length_c 6.0
_cell_angle_alpha 90.0
_cell_angle_beta 90.0
_cell_angle_gamma 90.0
_symmetry_space_group_name_H-M 'P 1'
loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
Na1 Na 0.10 0.20 0.30
Cl1 Cl 0.60 0.70 0.80
O1  O  0.45 0.55 0.05
"""

EXPECTED_P1 = [
    ("Na", (0.10, 0.20, 0.30)),
    ("Cl", (0.60, 0.70, 0.80)),
    ("O", (0.45, 0.55, 0.05)),
]

# 故意交错元素顺序的 P1 CIF：Fe/O/Fe/O。
INTERLEAVED = """data_interleaved
_cell_length_a 4.0
_cell_length_b 5.0
_cell_length_c 6.0
_cell_angle_alpha 90.0
_cell_angle_beta 90.0
_cell_angle_gamma 90.0
_symmetry_space_group_name_H-M 'P 1'
loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
Fe1 Fe 0.10 0.10 0.10
O1  O  0.20 0.20 0.20
Fe2 Fe 0.30 0.30 0.30
O2  O  0.40 0.40 0.40
"""

# 六方 R-3c Fe2O3，含完整对称操作（36 个 symops）：解析后应对称性
# 展开为 30 原子（Fe12O18）conventional 胞；由 pymatgen CifWriter 对
# 真实 hematite 结构生成，坐标/对称性均为科学真值。
FE2O3 = """data_Fe2O3
_symmetry_space_group_name_H-M   R-3c
_cell_length_a   5.03800000
_cell_length_b   5.03800000
_cell_length_c   13.77200000
_cell_angle_alpha   90.00000000
_cell_angle_beta   90.00000000
_cell_angle_gamma   120.00000000
_symmetry_Int_Tables_number   167
_chemical_formula_structural   Fe2O3
_chemical_formula_sum   'Fe12 O18'
_cell_volume   302.72199168
_cell_formula_units_Z   6
loop_
 _symmetry_equiv_pos_site_id
 _symmetry_equiv_pos_as_xyz
  1  'x, y, z'
  2  '-x, -y, -z'
  3  '-y, x-y, z'
  4  'y, -x+y, -z'
  5  '-x+y, -x, z'
  6  'x-y, x, -z'
  7  'y, x, -z+1/2'
  8  '-y, -x, z+1/2'
  9  'x-y, -y, -z+1/2'
  10  '-x+y, y, z+1/2'
  11  '-x, -x+y, -z+1/2'
  12  'x, x-y, z+1/2'
  13  'x+2/3, y+1/3, z+1/3'
  14  '-x+2/3, -y+1/3, -z+1/3'
  15  '-y+2/3, x-y+1/3, z+1/3'
  16  'y+2/3, -x+y+1/3, -z+1/3'
  17  '-x+y+2/3, -x+1/3, z+1/3'
  18  'x-y+2/3, x+1/3, -z+1/3'
  19  'y+2/3, x+1/3, -z+5/6'
  20  '-y+2/3, -x+1/3, z+5/6'
  21  'x-y+2/3, -y+1/3, -z+5/6'
  22  '-x+y+2/3, y+1/3, z+5/6'
  23  '-x+2/3, -x+y+1/3, -z+5/6'
  24  'x+2/3, x-y+1/3, z+5/6'
  25  'x+1/3, y+2/3, z+2/3'
  26  '-x+1/3, -y+2/3, -z+2/3'
  27  '-y+1/3, x-y+2/3, z+2/3'
  28  'y+1/3, -x+y+2/3, -z+2/3'
  29  '-x+y+1/3, -x+2/3, z+2/3'
  30  'x-y+1/3, x+2/3, -z+2/3'
  31  'y+1/3, x+2/3, -z+1/6'
  32  '-y+1/3, -x+2/3, z+1/6'
  33  'x-y+1/3, -y+2/3, -z+1/6'
  34  '-x+y+1/3, y+2/3, z+1/6'
  35  '-x+1/3, -x+y+2/3, -z+1/6'
  36  'x+1/3, x-y+2/3, z+1/6'
loop_
 _atom_site_type_symbol
 _atom_site_label
 _atom_site_symmetry_multiplicity
 _atom_site_fract_x
 _atom_site_fract_y
 _atom_site_fract_z
 _atom_site_occupancy
  Fe  Fe0  12  0.00000000  0.00000000  0.14470000  1
  O  O1  18  0.00000000  0.30540000  0.75000000  1
"""

NO_SITES = """data_no_sites
_cell_length_a 4.0
_cell_length_b 5.0
_cell_length_c 6.0
_cell_angle_alpha 90.0
_cell_angle_beta 90.0
_cell_angle_gamma 90.0
"""

MULTI_BLOCK = """data_block_a
_cell_length_a 4.0
_cell_length_b 4.0
_cell_length_c 4.0
_cell_angle_alpha 90.0
_cell_angle_beta 90.0
_cell_angle_gamma 90.0
_symmetry_space_group_name_H-M 'P 1'
loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
Na1 Na 0.10 0.20 0.30
data_block_b
_cell_length_a 5.0
_cell_length_b 5.0
_cell_length_c 5.0
_cell_angle_alpha 90.0
_cell_angle_beta 90.0
_cell_angle_gamma 90.0
_symmetry_space_group_name_H-M 'P 1'
loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
Cl1 Cl 0.10 0.10 0.10
"""

PARTIAL_OCCUPANCY = """data_partial
_cell_length_a 4.0
_cell_length_b 5.0
_cell_length_c 6.0
_cell_angle_alpha 90.0
_cell_angle_beta 90.0
_cell_angle_gamma 90.0
_symmetry_space_group_name_H-M 'P 1'
loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
_atom_site_occupancy
Na1 Na 0.10 0.20 0.30 0.5
Cl1 Cl 0.60 0.70 0.80 1.0
"""

# 可被 CIF 语法解析但数值损坏：坐标字段齐全，pymatgen 解析失败。
CORRUPT_NUMERIC = """data_corrupt
_cell_length_a 4.0
_cell_length_b 5.0
_cell_length_c 6.0
_cell_angle_alpha 90.0
_cell_angle_beta 90.0
_cell_angle_gamma 90.0
_symmetry_space_group_name_H-M 'P 1'
loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
Na1 Xx not_a_number zz
"""

# 仅含 Cartesian 坐标字段的 CIF：分类上属于"坐标字段齐全"，不得被
# 误判为 CIF_MISSING_COORDINATES；当前 pymatgen 版本不支持仅 Cartn
# 坐标的 CIF，应走 pymatgen 原生失败路径 CIF_PARSE_FAILED。
CARTN_ONLY = """data_cartn
_cell_length_a 4.0
_cell_length_b 5.0
_cell_length_c 6.0
_cell_angle_alpha 90.0
_cell_angle_beta 90.0
_cell_angle_gamma 90.0
_symmetry_space_group_name_H-M 'P 1'
loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_Cartn_x
_atom_site_Cartn_y
_atom_site_Cartn_z
Na1 Na 0.4 1.0 1.8
Cl1 Cl 2.4 3.5 4.8
"""


def _close_mod1(a: float, b: float, tol: float = 1e-6) -> bool:
    d = abs(a - b) % 1.0
    return min(d, 1.0 - d) <= tol


def _frac_equal_mod1(u, v, tol: float = 1e-6) -> bool:
    return all(_close_mod1(x, y, tol) for x, y in zip(u, v))


def _site_set(structure) -> set:
    """周期等价的 (species, frac_coords) 多重集合表示。"""
    out = []
    for site in structure:
        coords = tuple(round(c % 1.0, 6) for c in site.frac_coords)
        out.append((str(site.specie), coords))
    return set(out)


def _poscar_species_counts_lines(poscar_text: str) -> tuple:
    lines = [ln for ln in poscar_text.splitlines() if ln.strip()]
    # 0=注释 1=标度 2..4=晶格矢量 5=元素行 6=计数行
    return lines[5], lines[6]


# ---------------------------------------------------------------- 真实坐标
def test_p1_coordinates_and_lattice_preserved():
    result = convert_cif_to_poscar(P1_SMALL, source_file="p1.cif")
    struct = Poscar.from_str(result.poscar_text).structure
    # 晶格与 CIF 一致
    assert struct.lattice.a == pytest.approx(4.0, abs=1e-6)
    assert struct.lattice.b == pytest.approx(5.0, abs=1e-6)
    assert struct.lattice.c == pytest.approx(6.0, abs=1e-6)
    assert struct.lattice.alpha == pytest.approx(90.0, abs=1e-6)
    assert struct.lattice.beta == pytest.approx(90.0, abs=1e-6)
    assert struct.lattice.gamma == pytest.approx(90.0, abs=1e-6)
    # 逐 site 比较 species 与分数坐标（周期边界等价）
    actual = {(str(s.specie), tuple(s.frac_coords)) for s in struct}
    assert len(actual) == len(EXPECTED_P1)
    for element, coords in EXPECTED_P1:
        assert any(
            sp == element and _frac_equal_mod1(c, coords) for sp, c in actual
        ), f"missing {element} at {coords}"
    assert result.atom_count == 3
    assert result.formula == "NaClO"
    assert result.standardized is False


def test_standardize_false_makes_no_transformation():
    raw = CifParser.from_str(P1_SMALL).parse_structures(primitive=False)[0]
    result = convert_cif_to_poscar(P1_SMALL, standardize=False)
    struct = Poscar.from_str(result.poscar_text).structure
    assert _site_set(struct) == _site_set(raw)
    assert struct.lattice.matrix == pytest.approx(raw.lattice.matrix)
    assert result.standardized is False


# ------------------------------------------------------------ 元素分组
def test_interleaved_sites_grouped_unique_and_ordered():
    result = convert_cif_to_poscar(INTERLEAVED)
    species_line, counts_line = _poscar_species_counts_lines(result.poscar_text)
    species = species_line.split()
    counts = [int(x) for x in counts_line.split()]
    # 元素行唯一且按首次出现顺序
    assert species == ["Fe", "O"]
    assert len(species) == len(set(species))
    assert counts == [2, 2]

    struct = Poscar.from_str(result.poscar_text).structure
    # counts 与每元素实际 site 数一致，坐标按分组一一对应（原相对顺序保留）
    assert [str(s.specie) for s in struct] == ["Fe", "Fe", "O", "O"]
    assert _frac_equal_mod1(struct[0].frac_coords, (0.10, 0.10, 0.10))
    assert _frac_equal_mod1(struct[1].frac_coords, (0.30, 0.30, 0.30))
    assert _frac_equal_mod1(struct[2].frac_coords, (0.20, 0.20, 0.20))
    assert _frac_equal_mod1(struct[3].frac_coords, (0.40, 0.40, 0.40))
    # MAGMOM/LDAU/POTCAR 数组按元素 counts 展开时与 site 顺序一一对应
    magmom_slots = [el for el, n in zip(species, counts) for _ in range(n)]
    assert magmom_slots == [str(s.specie) for s in struct]
    # 分组前后周期等价
    raw = CifParser.from_str(INTERLEAVED).parse_structures(primitive=False)[0]
    assert StructureMatcher().fit(raw, struct)


def test_grouping_preserves_lattice_species_and_site_properties():
    lattice = Lattice.orthorhombic(4.0, 5.0, 6.0)
    structure = Structure(
        lattice,
        species=["Fe", "O", "Fe", "O"],
        coords=[[0.1, 0.1, 0.1], [0.2, 0.2, 0.2],
                [0.3, 0.3, 0.3], [0.4, 0.4, 0.4]],
        site_properties={"magmom": [2.3, -0.6, 2.1, -0.7]},
    )
    grouped = _group_sites_by_first_occurrence(structure)
    assert grouped.lattice == structure.lattice
    assert [str(s.specie) for s in grouped] == ["Fe", "Fe", "O", "O"]
    assert grouped.site_properties["magmom"] == [2.3, 2.1, -0.6, -0.7]
    assert _site_set(grouped) == _site_set(structure)
    assert StructureMatcher().fit(structure, grouped)


# ---------------------------------------------------------- 对称性展开
def test_fe2o3_symmetry_expansion_periodically_equivalent():
    result = convert_cif_to_poscar(FE2O3, source_file="Fe2O3.cif")
    struct = Poscar.from_str(result.poscar_text).structure
    # 晶格与 CIF 一致（六方：a=b=5.038, c=13.772, gamma=120）
    assert struct.lattice.a == pytest.approx(5.038, abs=1e-3)
    assert struct.lattice.b == pytest.approx(5.038, abs=1e-3)
    assert struct.lattice.c == pytest.approx(13.772, abs=1e-3)
    assert struct.lattice.gamma == pytest.approx(120.0, abs=1e-3)
    # 对称性展开：2 个不等价位点（多重度 12+18）-> 30 原子（Fe12O18），且保持 Fe:O = 2:3
    assert result.atom_count == 30
    comp = struct.composition
    assert comp["Fe"] / comp["O"] == pytest.approx(2.0 / 3.0, rel=1e-6)
    # 元素行唯一
    species_line, _ = _poscar_species_counts_lines(result.poscar_text)
    species = species_line.split()
    assert len(species) == len(set(species))
    # 周期等价：CIF 解析结构 vs 输出 POSCAR 重读结构
    raw = CifParser.from_str(FE2O3).parse_structures(primitive=False)[0]
    assert StructureMatcher().fit(raw, struct)


def test_standardize_true_uses_symmetry_tolerance():
    result = convert_cif_to_poscar(FE2O3, standardize=True,
                                   symmetry_tolerance=0.01)
    assert result.standardized is True
    struct = Poscar.from_str(result.poscar_text).structure
    raw = CifParser.from_str(FE2O3).parse_structures(primitive=False)[0]
    # 标准化后仍与原 CIF 结构周期等价，组分守恒
    assert StructureMatcher().fit(raw, struct)
    assert struct.composition.reduced_formula == raw.composition.reduced_formula


# -------------------------------------------------------------- fail closed
def test_garbage_text_without_data_block_is_parse_failed():
    for text in ("this is not a cif at all", "", "<html>nope</html>"):
        with pytest.raises(ValidationError) as exc:
            convert_cif_to_poscar(text)
        assert exc.value.code == "CIF_PARSE_FAILED"


def test_missing_atom_sites_fail_closed():
    with pytest.raises(ValidationError) as exc:
        convert_cif_to_poscar(NO_SITES)
    assert exc.value.code == "CIF_MISSING_COORDINATES"


def test_cartesian_only_cif_not_misclassified_as_missing_coordinates():
    # 坐标字段（Cartn 三元组）齐全：不得归为 CIF_MISSING_COORDINATES；
    # 当前 pymatgen 不支持仅 Cartn 坐标，走原生失败路径。
    with pytest.raises(ValidationError) as exc:
        convert_cif_to_poscar(CARTN_ONLY)
    assert exc.value.code == "CIF_PARSE_FAILED"


def test_multiple_data_blocks_fail_closed():
    with pytest.raises(ValidationError) as exc:
        convert_cif_to_poscar(MULTI_BLOCK)
    assert exc.value.code == "CIF_MULTIPLE_STRUCTURES_NOT_SUPPORTED"
    assert "split" in exc.value.message.lower()


def test_partial_occupancy_fail_closed():
    with pytest.raises(ValidationError) as exc:
        convert_cif_to_poscar(PARTIAL_OCCUPANCY)
    assert exc.value.code == "CIF_DISORDERED_NOT_SUPPORTED"
    # standardize=True 也必须拒绝，且不得输出 POSCAR
    with pytest.raises(ValidationError) as exc2:
        convert_cif_to_poscar(PARTIAL_OCCUPANCY, standardize=True)
    assert exc2.value.code == "CIF_DISORDERED_NOT_SUPPORTED"


def test_disorder_introduced_by_standardization_fail_closed(monkeypatch):
    """覆盖标准化之后的第二次无序检查。"""
    import pymatgen.symmetry.analyzer as sga

    disordered = Structure(
        Lattice.orthorhombic(4.0, 5.0, 6.0),
        species=[{"Na": 0.5}, {"Cl": 1.0}],
        coords=[[0.1, 0.2, 0.3], [0.6, 0.7, 0.8]],
    )

    class FakeAnalyzer:
        def __init__(self, structure, symprec=0.01):
            self.symprec = symprec

        def get_conventional_standard_structure(self):
            return disordered

    monkeypatch.setattr(sga, "SpacegroupAnalyzer", FakeAnalyzer)
    with pytest.raises(ValidationError) as exc:
        convert_cif_to_poscar(P1_SMALL, standardize=True,
                              symmetry_tolerance=0.01)
    assert exc.value.code == "CIF_DISORDERED_NOT_SUPPORTED"


def test_invalid_symmetry_tolerance_fail_closed():
    for tol in (0.0, -0.1, 1.5):
        with pytest.raises(ValidationError) as exc:
            convert_cif_to_poscar(P1_SMALL, symmetry_tolerance=tol)
        assert exc.value.code == "CIF_INVALID_SYMMETRY_TOLERANCE"


def test_corrupt_cif_parse_failed():
    with pytest.raises(ValidationError) as exc:
        convert_cif_to_poscar(CORRUPT_NUMERIC)
    assert exc.value.code == "CIF_PARSE_FAILED"


def test_error_messages_are_sanitized():
    for text in (NO_SITES, MULTI_BLOCK, PARTIAL_OCCUPANCY, CORRUPT_NUMERIC,
                 "this is not a cif at all"):
        with pytest.raises(ValidationError) as exc:
            convert_cif_to_poscar(text)
        message = exc.value.message
        assert "Traceback" not in message
        assert "pymatgen" not in message.lower()
        assert ":\\" not in message and ":/" not in message
