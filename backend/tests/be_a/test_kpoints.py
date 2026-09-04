"""KPOINTS：KPPA/网格公式（n_i ∝ |b_i|）、最小值 1、Gamma/Monkhorst、band Line-mode 格式。"""

import math

import pytest

from backend.app.generators.kpoints import KpointsGenerator
from backend.app.parsers.kpoints import parse_kpoints
from backend.app.recipes.derived import KPPA_TABLE, generate_kpoint_grid
from backend.app.recipes.errors import DerivedParameterUnresolved, KpointsGenerationFailed
from backend.app.schemas.generation import KpointsSpec

NACL_POSCAR = (
    "NaCl\n1.0\n5.6 0.0 0.0\n0.0 5.6 0.0\n0.0 0.0 5.6\n"
    "Na Cl\n1 1\nDirect\n0.0 0.0 0.0\n0.5 0.5 0.5\n"
)

CUBIC_LATTICE = {"abc": [5.0, 5.0, 5.0], "angles": [90.0, 90.0, 90.0]}
HEXAGONAL_LATTICE = {"abc": [5.03, 5.03, 13.75], "angles": [90.0, 90.0, 120.0]}
# Fe2O3 六方晶胞的完整 lattice matrix（POSCAR 舍入值）。
FE2O3_MATRIX_ROUNDED = [[5.03, 0.0, 0.0], [-2.515, 4.356, 0.0], [0.0, 0.0, 13.75]]
FE2O3_MATRIX_EXACT = [[5.03, 0.0, 0.0], [-2.515, 4.356048, 0.0], [0.0, 0.0, 13.75]]


def _grid(inputs):
    return generate_kpoint_grid(inputs)["grid"]


def _unrounded(matrix, kppa, atom_count):
    """独立参考实现：由 lattice matrix 直接算出未取整的 n_i（交叉校验用）。"""

    def cross(u, v):
        return (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2],
                u[0] * v[1] - u[1] * v[0])

    def dot(u, v):
        return sum(x * y for x, y in zip(u, v))

    volume = abs(dot(matrix[0], cross(matrix[1], matrix[2])))
    b = [2.0 * math.pi * math.sqrt(dot(cross(matrix[(i + 1) % 3], matrix[(i + 2) % 3]),
                                   cross(matrix[(i + 1) % 3], matrix[(i + 2) % 3]))) / volume
         for i in range(3)]
    geom = (b[0] * b[1] * b[2]) ** (1.0 / 3.0)
    scale = (kppa / atom_count) ** (1.0 / 3.0)
    return [scale * value / geom for value in b]


class _StubKpath:
    """最小 HighSymmKpath 替身：仅提供 ``.kpath = {"path": ..., "kpoints": ...}``。"""

    def __init__(self, path, kpoints):
        self.kpath = {"path": path, "kpoints": kpoints}


class TestGridFormula:
    def test_grid_minimum_one(self):
        grid = _grid({"kppa": 100.0, "atom_count": 1,
                      "lattice": {"abc": [5.0, 5.0, 100.0], "angles": [90.0, 90.0, 90.0]}})
        assert all(n >= 1 for n in grid)
        assert grid == [13, 13, 1]

    def test_long_axis_gets_coarser_grid(self):
        """实空间长轴 → 倒格矢更短 → 该方向所需网格数更少（n_i ∝ |b_i| ∝ 1/L_i）。

        旧断言 ``grid[2] > grid[0]`` 要求长轴更密，与保持倒空间采样密度的要求相反。
        """

        info = generate_kpoint_grid({
            "kppa": 1000.0, "atom_count": 1,
            "lattice": {"abc": [5.0, 5.0, 20.0], "angles": [90.0, 90.0, 90.0]},
        })
        assert info["grid"] == [16, 16, 4]
        assert info["grid"][2] < info["grid"][0]

    def test_formula_reference_value(self):
        """N_total ≈ kppa / atom_count；立方各向同性 → 三方向相等。

        n_i = max(1, floor((kppa/natoms)^(1/3) · |b_i| / (|b1||b2||b3|)^(1/3) + 0.5))。
        立方 5 Å 时 |b_i| 全等，故 n_i = (kppa/natoms)^(1/3) 的 round-half-up。
        """

        assert _grid({"kppa": 1000.0, "atom_count": 1, "lattice": CUBIC_LATTICE}) == [10, 10, 10]
        assert _grid({"kppa": 1000.0, "atom_count": 2, "lattice": CUBIC_LATTICE}) == [8, 8, 8]

    def test_hexagonal_uses_gamma_centering(self):
        info = generate_kpoint_grid({
            "kppa": 1000.0, "atom_count": 1,
            "lattice": {"abc": [5.0, 5.0, 13.7], "angles": [90, 90, 120]},
        })
        assert info["centering"] == "Gamma"
        assert info["grid"] == [15, 15, 5]

    def test_kppa_table_versioned_by_precision(self):
        assert KPPA_TABLE["relax"]["quick"] < KPPA_TABLE["relax"]["standard"]
        assert KPPA_TABLE["relax"]["standard"] < KPPA_TABLE["relax"]["high"]
        assert KPPA_TABLE["dos"]["standard"] > KPPA_TABLE["relax"]["standard"]

    def test_invalid_lattice_rejected(self):
        with pytest.raises(DerivedParameterUnresolved):
            generate_kpoint_grid({"kppa": 1000.0, "atom_count": 1,
                                  "lattice": {"abc": [5.0, 5.0]}})


class TestGridDirectionality:
    """U1：实空间某方向变长时，该方向所需网格数不得错误增大。

    c 序列取 {5,10,20,30}：c=30 的未取整值为 3.0289（a 方向 18.1735），
    远离 .5 取整边界；弃用 c=40，其未取整值恰为 2.5，round-half-up 与二进制
    浮点组合存在歧义。
    """

    @pytest.mark.parametrize(
        "c_length,expected",
        [(5.0, [10, 10, 10]), (10.0, [13, 13, 6]), (20.0, [16, 16, 4]), (30.0, [18, 18, 3])],
    )
    def test_u1_exact_grid_per_c_length(self, c_length, expected):
        grid = _grid({"kppa": 1000.0, "atom_count": 1,
                      "lattice": {"abc": [5.0, 5.0, c_length], "angles": [90.0, 90.0, 90.0]}})
        assert grid == expected

    def test_u1_long_axis_count_strictly_decreases(self):
        grids = [
            _grid({"kppa": 1000.0, "atom_count": 1,
                   "lattice": {"abc": [5.0, 5.0, c], "angles": [90.0, 90.0, 90.0]}})
            for c in (5.0, 10.0, 20.0, 30.0)
        ]
        c_axis = [grid[2] for grid in grids]
        a_axis = [grid[0] for grid in grids]
        # 长轴（c）方向网格数依次 10→6→4→3，严格递减、绝不含错误增大。
        assert c_axis == [10, 6, 4, 3]
        assert all(later < earlier for earlier, later in zip(c_axis, c_axis[1:]))
        # 短轴（a）方向随 c 变长而单调不减（总点数守恒下的正确再分配）。
        assert all(later >= earlier for earlier, later in zip(a_axis, a_axis[1:]))

    def test_reference_values_avoid_rounding_boundary(self):
        """所有精确参考值的未取整结果距最近 .5 边界至少 0.05，取整无歧义。"""

        cases = [
            ([[5.0, 0, 0], [0, 5.0, 0], [0, 0, 30.0]], 1000.0, 1),
            ([[5.0, 0, 0], [0, 5.0, 0], [0, 0, 10.0]], 1000.0, 1),
            ([[5.0, 0, 0], [0, 5.0, 0], [0, 0, 5.0]], 1000.0, 1),
            (FE2O3_MATRIX_EXACT, 1000.0, 5),
            (FE2O3_MATRIX_EXACT, 1500.0, 5),
            ([[5.6, 0, 0], [0, 5.6, 0], [0, 0, 5.6]], 500.0, 2),
            ([[4, 0, 0], [1, 5, 0], [0.5, 1, 6]], 1000.0, 4),
            ([[5.0, 0, 0], [0, 5.0, 0], [0, 0, 100.0]], 100.0, 1),
        ]
        for matrix, kppa, atom_count in cases:
            for value in _unrounded(matrix, kppa, atom_count):
                assert abs((value - math.floor(value)) - 0.5) >= 0.05, (
                    f"unrounded {value} sits on a .5 boundary"
                )


class TestGridAnisotropyAndMatrix:
    def test_u2_hexagonal_exact_value(self):
        info = generate_kpoint_grid({"kppa": 1000.0, "atom_count": 5, "lattice": HEXAGONAL_LATTICE})
        assert info["grid"] == [9, 9, 3]
        assert info["centering"] == "Gamma"

    def test_u3_matrix_matches_abc_angles(self):
        """同一晶格的 matrix 输入与 abc+angles 输入必须给出完全一致的结果。"""

        from_matrix = generate_kpoint_grid(
            {"kppa": 1000.0, "atom_count": 5, "lattice": {"matrix": FE2O3_MATRIX_EXACT}}
        )
        from_abc = generate_kpoint_grid(
            {"kppa": 1000.0, "atom_count": 5, "lattice": HEXAGONAL_LATTICE}
        )
        assert from_matrix["grid"] == from_abc["grid"] == [9, 9, 3]
        assert from_matrix["centering"] == from_abc["centering"] == "Gamma"

    def test_u4_triclinic_matrix_grid(self):
        """强各向异性三斜晶胞：n_i 排序与 |b_i| 排序一致。"""

        info = generate_kpoint_grid({
            "kppa": 1000.0, "atom_count": 4,
            "lattice": {"matrix": [[4.0, 0.0, 0.0], [1.0, 5.0, 0.0], [0.5, 1.0, 6.0]]},
        })
        assert info["grid"] == [8, 6, 5]
        assert info["grid"] == sorted(info["grid"], reverse=True)

    def test_u5_minimum_grid_is_one(self):
        assert _grid({"kppa": 100.0, "atom_count": 1,
                      "lattice": {"abc": [5.0, 5.0, 100.0], "angles": [90.0, 90.0, 90.0]}}) == [13, 13, 1]

    def test_u7_deterministic(self):
        inputs = {"kppa": 1000.0, "atom_count": 5, "lattice": HEXAGONAL_LATTICE}
        first = generate_kpoint_grid(dict(inputs))
        second = generate_kpoint_grid(dict(inputs))
        assert first == second
        assert all(isinstance(n, int) for n in first["grid"])
        assert isinstance(first["centering"], str)
        assert isinstance(first["kppa"], float)

    def test_u8_matrix_contradicting_angles_rejected(self):
        """matrix 为唯一几何真值；与 angles 明显矛盾时 fail closed，不得分别取用。"""

        cubic = [[5.6, 0.0, 0.0], [0.0, 5.6, 0.0], [0.0, 0.0, 5.6]]
        with pytest.raises(DerivedParameterUnresolved):
            generate_kpoint_grid({"kppa": 1000.0, "atom_count": 2,
                                  "lattice": {"matrix": cubic, "angles": [90, 90, 120]}})

    def test_u8_matrix_contradicting_abc_rejected(self):
        cubic = [[5.6, 0.0, 0.0], [0.0, 5.6, 0.0], [0.0, 0.0, 5.6]]
        with pytest.raises(DerivedParameterUnresolved):
            generate_kpoint_grid({"kppa": 1000.0, "atom_count": 1,
                                  "lattice": {"matrix": cubic, "abc": [5.0, 5.0, 20.0]}})

    def test_u9_matrix_is_single_source_of_truth_within_tolerance(self):
        """POSCAR 文本舍入级不一致（相对 1.9e-5 / 0.0017°）在容差内放行，
        且结果等于"仅 matrix 单独输入"的结果 —— 证明 matrix 是唯一几何真值。"""

        combined = generate_kpoint_grid({
            "kppa": 1000.0, "atom_count": 5,
            "lattice": {"matrix": FE2O3_MATRIX_ROUNDED,
                        "abc": [5.03, 5.03, 13.75], "angles": [90, 90, 120]},
        })
        matrix_only = generate_kpoint_grid({
            "kppa": 1000.0, "atom_count": 5,
            "lattice": {"matrix": FE2O3_MATRIX_ROUNDED},
        })
        assert combined == matrix_only
        assert combined["grid"] == [9, 9, 3]
        assert combined["centering"] == "Gamma"


class TestGridFailClosed:
    """U6：kppa / atom_count / abc / angles / matrix 的数值全部 fail closed。

    NaN 能绕过普通大小比较，bool 是 int 子类，因此必须显式校验类型与有限性。
    """

    @pytest.mark.parametrize("kppa", [True, False, "1000", 0, 0.0, -1, -1.0,
                                      float("nan"), float("inf"), float("-inf")])
    def test_u6_invalid_kppa_rejected(self, kppa):
        with pytest.raises(DerivedParameterUnresolved):
            generate_kpoint_grid({"kppa": kppa, "atom_count": 1, "lattice": CUBIC_LATTICE})

    def test_u6_missing_kppa_rejected(self):
        with pytest.raises(DerivedParameterUnresolved):
            generate_kpoint_grid({"atom_count": 1, "lattice": CUBIC_LATTICE})

    @pytest.mark.parametrize("atom_count", [True, False, 0, -1, 2.5, "3", None])
    def test_u6_invalid_atom_count_rejected(self, atom_count):
        with pytest.raises(DerivedParameterUnresolved):
            generate_kpoint_grid({"kppa": 1000.0, "atom_count": atom_count,
                                  "lattice": CUBIC_LATTICE})

    def test_u6_missing_atom_count_rejected(self):
        with pytest.raises(DerivedParameterUnresolved):
            generate_kpoint_grid({"kppa": 1000.0, "lattice": CUBIC_LATTICE})

    @pytest.mark.parametrize("abc", [
        [5.0, float("nan"), 5.0], [5.0, float("inf"), 5.0], [5.0, float("-inf"), 5.0],
        [5.0, True, 5.0], [5.0, "5", 5.0], [5.0, -5.0, 5.0], [5.0, 0.0, 5.0],
        [5.0, 5.0], [5.0, 5.0, 5.0, 5.0],
    ])
    def test_u6_invalid_abc_rejected(self, abc):
        with pytest.raises(DerivedParameterUnresolved):
            generate_kpoint_grid({"kppa": 1000.0, "atom_count": 1,
                                  "lattice": {"abc": abc, "angles": [90.0, 90.0, 90.0]}})

    @pytest.mark.parametrize("angles", [
        [90.0, float("nan"), 90.0], [90.0, float("inf"), 90.0], [90.0, True, 90.0],
        [90.0, "90", 90.0], [0.0, 90.0, 90.0], [180.0, 90.0, 90.0],
        [-10.0, 90.0, 90.0], [179.0, 179.0, 179.0], [90.0, 90.0],
    ])
    def test_u6_invalid_angles_rejected(self, angles):
        with pytest.raises(DerivedParameterUnresolved):
            generate_kpoint_grid({"kppa": 1000.0, "atom_count": 1,
                                  "lattice": {"abc": [5.0, 5.0, 5.0], "angles": angles}})

    @pytest.mark.parametrize("matrix", [
        [[5.0, 0.0, 0.0], [0.0, 5.0, 0.0]],                       # 2x3
        [[5.0, 0.0, 0.0], [0.0, 5.0, 0.0], [0.0, 0.0]],           # 行长度不足
        [[5.0, 0.0, 0.0], [0.0, float("nan"), 0.0], [0.0, 0.0, 5.0]],
        [[5.0, 0.0, 0.0], [0.0, float("inf"), 0.0], [0.0, 0.0, 5.0]],
        [[5.0, 0.0, 0.0], [0.0, True, 0.0], [0.0, 0.0, 5.0]],
        [[5.0, 0.0, 0.0], [0.0, "5", 0.0], [0.0, 0.0, 5.0]],
        [[5.0, 0.0, 0.0], [10.0, 0.0, 0.0], [0.0, 0.0, 5.0]],     # 奇异（共线）
        [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]],      # 奇异（零体积）
    ])
    def test_u6_invalid_matrix_rejected(self, matrix):
        with pytest.raises(DerivedParameterUnresolved):
            generate_kpoint_grid({"kppa": 1000.0, "atom_count": 1, "lattice": {"matrix": matrix}})

    @pytest.mark.parametrize("lattice", [{}, {"abc": [5.0, 5.0, 5.0]}, {"angles": [90, 90, 90]}])
    def test_u6_incomplete_lattice_rejected(self, lattice):
        """abc 无 angles 不再默认 90 度，直接 fail closed。"""

        with pytest.raises(DerivedParameterUnresolved):
            generate_kpoint_grid({"kppa": 1000.0, "atom_count": 1, "lattice": lattice})

    def test_u6_missing_lattice_rejected(self):
        with pytest.raises(DerivedParameterUnresolved):
            generate_kpoint_grid({"kppa": 1000.0, "atom_count": 1})


class TestUniformRendering:
    def test_gamma_centering_text(self):
        text = KpointsGenerator().uniform([7, 7, 20], "Gamma", kppa=1000.0)
        lines = text.splitlines()
        assert lines[1] == "0"
        assert lines[2] == "Gamma"
        assert lines[3] == "7 7 20"
        assert lines[4] == "0 0 0"

    def test_monkhorst_text(self):
        text = KpointsGenerator().uniform([4, 4, 4], "Monkhorst")
        assert "Monkhorst-Pack" in text.splitlines()[2]

    def test_invalid_grid_rejected(self):
        with pytest.raises(KpointsGenerationFailed):
            KpointsGenerator().uniform([0, 4, 4])
        with pytest.raises(KpointsGenerationFailed):
            KpointsGenerator().uniform([4, 4])

    def test_spec_dispatch(self):
        spec = KpointsSpec(mode="automatic_density", grid=[2, 2, 2], centering="Gamma")
        text = KpointsGenerator().generate(spec)
        assert "2 2 2" in text


class TestBandLineMode:
    def test_l1_stub_renders_exact_endpoint_pairs(self):
        """逐字节断言：A-B-C 展开为 (A,B)、(B,C) 端点对，段间恰一个空行。"""

        stub = _StubKpath(
            path=[["\\Gamma", "X", "M", "\\Gamma"]],
            kpoints={"\\Gamma": (0.0, 0.0, 0.0), "X": (0.5, 0.0, 0.0), "M": (0.5, 0.5, 0.0)},
        )
        text = KpointsGenerator()._render_line_mode(stub, 20, "C")
        assert text == (
            "C\n"
            "20\n"
            "Line-mode\n"
            "Reciprocal\n"
            "0 0 0  ! GAMMA\n"
            "0.5 0 0  ! X\n"
            "\n"
            "0.5 0 0  ! X\n"
            "0.5 0.5 0  ! M\n"
            "\n"
            "0.5 0.5 0  ! M\n"
            "0 0 0  ! GAMMA\n"
        )

    def test_l2_stub_discontinuous_segments(self):
        stub = _StubKpath(
            path=[["A", "B"], ["C", "D"]],
            kpoints={"A": (0.0, 0.0, 0.0), "B": (0.5, 0.0, 0.0),
                     "C": (0.5, 0.5, 0.0), "D": (0.0, 0.5, 0.0)},
        )
        text = KpointsGenerator()._render_line_mode(stub, 30, "C")
        lines = text.split("\n")
        assert lines[:4] == ["C", "30", "Line-mode", "Reciprocal"]
        body = lines[4:]
        assert body[-1] == ""  # 单个换行结尾
        body = body[:-1]
        assert body.count("") == 1
        endpoints = [line for line in body if line]
        assert len(endpoints) == 4
        assert endpoints[0].endswith("! A")
        assert endpoints[1].endswith("! B")
        assert endpoints[2].endswith("! C")
        assert endpoints[3].endswith("! D")

    def test_l3_line_mode_for_cubic_structure(self):
        text = KpointsGenerator().line_mode(NACL_POSCAR, divisions=60)
        lines = text.splitlines()
        assert lines[1] == "60"
        assert lines[2] == "Line-mode"
        assert lines[3] == "Reciprocal"
        assert text.endswith("\n") and not text.endswith("\n\n")
        body = text.split("Reciprocal\n", 1)[1]
        raw_blocks = body.rstrip("\n").split("\n\n")
        assert raw_blocks, "line-mode must list endpoint pairs"
        endpoints = []
        for block in raw_blocks:
            pair = block.split("\n")
            assert len(pair) == 2, f"每块必须恰为 2 个端点，实际 {pair}"
            for line in pair:
                assert "!" in line
                assert len(line.split("!")[0].split()) == 3
                assert line.split("!")[1].strip(), "端点必须带高对称点标签"
            endpoints.extend(pair)
        # 端点行数 = 2 × 端点对数，且与正文非空行数一致（无孤行）。
        assert len(endpoints) == 2 * len(raw_blocks)
        assert len([line for line in body.splitlines() if line.strip()]) == len(endpoints)
        parsed = parse_kpoints(text)
        assert parsed.line_mode is True
        assert parsed.mode == "Line-mode"
        assert parsed.nkpts == 60

    @pytest.mark.parametrize("divisions", [0, -1, True, False, "60", 60.0, None])
    def test_l4_invalid_divisions_rejected(self, divisions):
        with pytest.raises(KpointsGenerationFailed):
            KpointsGenerator().line_mode(NACL_POSCAR, divisions=divisions)

    def test_line_mode_deterministic(self):
        generator = KpointsGenerator()
        assert generator.line_mode(NACL_POSCAR) == generator.line_mode(NACL_POSCAR)

    def test_invalid_poscar_rejected(self):
        with pytest.raises(KpointsGenerationFailed):
            KpointsGenerator().line_mode("not a poscar")


class TestBandLineModeFailClosed:
    """L7：pymatgen 返回异常路径数据时 fail closed，绝不渲染残缺 KPOINTS。"""

    def test_l7a_empty_path_rejected(self):
        stub = _StubKpath(path=[], kpoints={"A": (0.0, 0.0, 0.0)})
        with pytest.raises(KpointsGenerationFailed):
            KpointsGenerator()._render_line_mode(stub, 20, "C")

    def test_l7b_segment_with_single_label_rejected(self):
        stub = _StubKpath(path=[["A"]], kpoints={"A": (0.0, 0.0, 0.0)})
        with pytest.raises(KpointsGenerationFailed):
            KpointsGenerator()._render_line_mode(stub, 20, "C")

    def test_l7b_empty_segment_rejected(self):
        stub = _StubKpath(path=[[]], kpoints={"A": (0.0, 0.0, 0.0)})
        with pytest.raises(KpointsGenerationFailed):
            KpointsGenerator()._render_line_mode(stub, 20, "C")

    def test_l7c_missing_label_coordinates_rejected(self):
        stub = _StubKpath(path=[["A", "B"]], kpoints={"A": (0.0, 0.0, 0.0)})
        with pytest.raises(KpointsGenerationFailed):
            KpointsGenerator()._render_line_mode(stub, 20, "C")

    def test_l7d_short_coordinate_rejected(self):
        stub = _StubKpath(path=[["A", "B"]],
                          kpoints={"A": (0.0, 0.0, 0.0), "B": (0.5, 0.0)})
        with pytest.raises(KpointsGenerationFailed):
            KpointsGenerator()._render_line_mode(stub, 20, "C")

    @pytest.mark.parametrize("coords", [
        (float("nan"), 0.0, 0.0), (0.0, float("inf"), 0.0), (0.0, 0.0, float("-inf")),
        (True, 0.0, 0.0), ("0.5", 0.0, 0.0), (None, 0.0, 0.0),
    ])
    def test_l7d_non_finite_coordinate_rejected(self, coords):
        stub = _StubKpath(path=[["A", "B"]],
                          kpoints={"A": (0.0, 0.0, 0.0), "B": coords})
        with pytest.raises(KpointsGenerationFailed):
            KpointsGenerator()._render_line_mode(stub, 20, "C")

    def test_l7_missing_kpoints_mapping_rejected(self):
        class _NoMapping:
            kpath = {"path": [["A", "B"]]}

        with pytest.raises(KpointsGenerationFailed):
            KpointsGenerator()._render_line_mode(_NoMapping(), 20, "C")
