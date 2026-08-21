"""KPOINTS：KPPA/网格公式、最小值 1、Gamma/Monkhorst、band line-mode。"""

import pytest

from backend.app.generators.kpoints import KpointsGenerator
from backend.app.recipes.derived import KPPA_TABLE, generate_kpoint_grid
from backend.app.recipes.errors import KpointsGenerationFailed
from backend.app.schemas.generation import KpointsSpec


class TestGridFormula:
    def test_grid_minimum_one(self):
        grid = generate_kpoint_grid(
            {"kppa": 100.0, "lattice": {"abc": [5.0, 5.0, 100.0]}}
        )["grid"]
        assert all(n >= 1 for n in grid)
        assert grid[0] == grid[1] >= 1
        assert grid[2] >= 1

    def test_long_axis_gets_finer_grid(self):
        info = generate_kpoint_grid(
            {"kppa": 1000.0, "lattice": {"abc": [5.0, 5.0, 20.0]}}
        )
        assert info["grid"][2] > info["grid"][0]

    def test_formula_reference_value(self):
        """n_i = max(1, round(kppa^(1/3) * L_i / 几何均值))，立方 5Å、kppa=1000 → 10。"""

        info = generate_kpoint_grid(
            {"kppa": 1000.0, "lattice": {"abc": [5.0, 5.0, 5.0]}}
        )
        assert info["grid"] == [10, 10, 10]

    def test_hexagonal_uses_gamma_centering(self):
        info = generate_kpoint_grid(
            {"kppa": 1000.0, "lattice": {"abc": [5.0, 5.0, 13.7], "angles": [90, 90, 120]}}
        )
        assert info["centering"] == "Gamma"

    def test_kppa_table_versioned_by_precision(self):
        assert KPPA_TABLE["relax"]["quick"] < KPPA_TABLE["relax"]["standard"]
        assert KPPA_TABLE["relax"]["standard"] < KPPA_TABLE["relax"]["high"]
        assert KPPA_TABLE["dos"]["standard"] > KPPA_TABLE["relax"]["standard"]

    def test_invalid_lattice_rejected(self):
        from backend.app.recipes.errors import DerivedParameterUnresolved

        with pytest.raises(DerivedParameterUnresolved):
            generate_kpoint_grid({"kppa": 1000.0, "lattice": {"abc": [5.0, 5.0]}})


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
    def test_line_mode_for_cubic_structure(self):
        poscar = (
            "NaCl\n1.0\n5.6 0.0 0.0\n0.0 5.6 0.0\n0.0 0.0 5.6\n"
            "Na Cl\n1 1\nDirect\n0.0 0.0 0.0\n0.5 0.5 0.5\n"
        )
        text = KpointsGenerator().line_mode(poscar, divisions=60)
        lines = text.splitlines()
        assert lines[1] == "60"
        assert lines[2] == "Reciprocal"
        coordinates = [line for line in lines[3:] if line.strip()]
        assert coordinates, "line-mode must list k-points"
        for line in coordinates:
            assert "!" in line  # 每个端点带标签

    def test_line_mode_deterministic(self):
        poscar = (
            "NaCl\n1.0\n5.6 0.0 0.0\n0.0 5.6 0.0\n0.0 0.0 5.6\n"
            "Na Cl\n1 1\nDirect\n0.0 0.0 0.0\n0.5 0.5 0.5\n"
        )
        generator = KpointsGenerator()
        assert generator.line_mode(poscar) == generator.line_mode(poscar)

    def test_invalid_poscar_rejected(self):
        with pytest.raises(KpointsGenerationFailed):
            KpointsGenerator().line_mode("not a poscar")
