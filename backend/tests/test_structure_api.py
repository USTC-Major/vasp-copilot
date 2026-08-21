"""POST /api/v1/structure/analyze 回归测试（CIF 真实坐标 + fail closed）。

覆盖：真实坐标保持、response schema 不变、POSCAR 上传回归、
失败路径的 FileStore 零副作用（以"上传原始 CIF 后的记录数"为基线）。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pymatgen.analysis.structure_matcher import StructureMatcher
from pymatgen.io.cif import CifParser
from pymatgen.io.vasp import Poscar

from app.api.v1 import deps
from app.main import app

client = TestClient(app)

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
"""

# 含完整对称操作的 R-3c Fe2O3：解析后对称性展开为 30 原子（Fe12O18）。
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

POSCAR_TEXT = """NaCl demo
1.0
4.0 0.0 0.0
0.0 5.0 0.0
0.0 0.0 6.0
Na Cl
1 1
Direct
0.10 0.20 0.30
0.60 0.70 0.80
"""


def _upload(name: str, text: str) -> str:
    r = client.post(
        "/api/v1/files/upload",
        files={"file": (name, text, "text/plain")},
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["file"]["file_id"]


def _analyze(file_id: str, **extra):
    return client.post(
        "/api/v1/structure/analyze", json={"file_id": file_id, **extra}
    )


def _frac_equal_mod1(u, v, tol: float = 1e-6) -> bool:
    def close(a, b):
        d = abs(a - b) % 1.0
        return min(d, 1.0 - d) <= tol

    return all(close(x, y) for x, y in zip(u, v))


def _store_counts() -> tuple:
    """FileStore 当前文件/结构记录数（用于零副作用基线比较）。"""
    return len(deps.file_store._files), len(deps.file_store._structures)


# --------------------------------------------------------------- 成功路径
def test_analyze_cif_preserves_real_coordinates_and_schema():
    file_id = _upload("demo.cif", P1_SMALL)
    r = _analyze(file_id)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    # response schema 保持不变
    assert set(data.keys()) == {
        "structure_id", "summary", "normalized_poscar_file_id", "file_id",
    }
    assert data["file_id"] == file_id
    assert data["structure_id"].startswith("str_")
    summary = data["summary"]
    assert summary["elements"] == ["Na", "Cl"]
    assert summary["counts"] == [1, 1]
    assert summary["atom_count"] == 2
    assert summary["standardized"] is False
    assert summary["lattice"]["a"] == pytest.approx(4.0, abs=1e-6)

    # normalized POSCAR 的坐标必须来自 CIF 真值（周期边界等价）
    normalized_id = data["normalized_poscar_file_id"]
    assert normalized_id
    poscar_text = deps.file_store.get_file(normalized_id).path.read_text(
        encoding="utf-8")
    struct = Poscar.from_str(poscar_text).structure
    coords = {str(s.specie): s.frac_coords for s in struct}
    assert _frac_equal_mod1(coords["Na"], (0.10, 0.20, 0.30))
    assert _frac_equal_mod1(coords["Cl"], (0.60, 0.70, 0.80))


def test_analyze_fe2o3_cif_periodically_equivalent():
    file_id = _upload("Fe2O3.cif", FE2O3)
    r = _analyze(file_id)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    poscar_text = deps.file_store.get_file(
        data["normalized_poscar_file_id"]).path.read_text(encoding="utf-8")
    struct = Poscar.from_str(poscar_text).structure
    raw = CifParser.from_str(FE2O3).parse_structures(primitive=False)[0]
    assert StructureMatcher().fit(raw, struct)
    # 对称性展开（2 个不等价位点，多重度 12+18 -> 30 原子）+ 元素行唯一
    assert data["summary"]["atom_count"] == 30
    assert len(set(data["summary"]["elements"])) == len(
        data["summary"]["elements"])


def test_analyze_poscar_upload_still_works():
    file_id = _upload("POSCAR", POSCAR_TEXT)
    r = _analyze(file_id)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["normalized_poscar_file_id"] is None
    assert data["summary"]["elements"] == ["Na", "Cl"]
    assert data["summary"]["counts"] == [1, 1]
    assert data["summary"]["standardized"] is False


def test_analyze_cif_standardize_true():
    file_id = _upload("Fe2O3_std.cif", FE2O3)
    r = _analyze(file_id, standardize=True, symmetry_tolerance=0.01)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["summary"]["standardized"] is True


# ---------------------------------------------------- fail closed + 零副作用
@pytest.mark.parametrize("text,code", [
    ("this is not a cif at all", "CIF_PARSE_FAILED"),
    (NO_SITES, "CIF_MISSING_COORDINATES"),
    (MULTI_BLOCK, "CIF_MULTIPLE_STRUCTURES_NOT_SUPPORTED"),
    (PARTIAL_OCCUPANCY, "CIF_DISORDERED_NOT_SUPPORTED"),
])
def test_failed_conversion_has_no_file_store_side_effects(text, code):
    file_id = _upload("bad.cif", text)
    # 基线：原始上传文件本身允许存在
    files_before, structures_before = _store_counts()
    r = _analyze(file_id)
    assert r.status_code == 422, r.text
    error = r.json()["error"]
    assert error["code"] == code
    # 未新增 normalized POSCAR，也未新增 StructureRecord
    assert _store_counts() == (files_before, structures_before)
    # 错误信息不得泄漏内部细节
    message = error["message"]
    assert "Traceback" not in message
    assert "pymatgen" not in message.lower()
    assert ":\\" not in message


def test_multi_block_error_suggests_split():
    file_id = _upload("multi.cif", MULTI_BLOCK)
    r = _analyze(file_id)
    assert r.status_code == 422
    assert "split" in r.json()["error"]["message"].lower()


@pytest.mark.parametrize("tol", [0.0, -0.5, 2.0])
def test_invalid_symmetry_tolerance_422(tol):
    file_id = _upload("tol.cif", P1_SMALL)
    files_before, structures_before = _store_counts()
    r = _analyze(file_id, symmetry_tolerance=tol)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "CIF_INVALID_SYMMETRY_TOLERANCE"
    assert _store_counts() == (files_before, structures_before)
