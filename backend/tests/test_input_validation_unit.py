"""Mechanical format edge cases that do not require a service or calculation."""
from __future__ import annotations

import pytest

from backend.input_validation import (
    InputValidationError, validate_incar, validate_kpoints, validate_poscar,
    validate_potcar,
)
from backend.tests.valid_vasp_inputs import FILES


def test_negative_volume_and_three_axis_scales_are_reflected_in_lattice():
    original = FILES["POSCAR"].decode()
    target = validate_poscar(original.replace("1.0\n", "-216\n", 1))
    assert target.volume == pytest.approx(216)
    assert target.matrix[0][0] == pytest.approx(6)
    axes = validate_poscar(original.replace("1.0\n", "2 3 4\n", 1))
    assert axes.matrix == ((6.0, 0.0, 0.0), (0.0, 9.0, 0.0), (0.0, 0.0, 12.0))
    assert axes.volume == pytest.approx(648)


def test_blank_comment_selective_cartesian_and_d_exponents():
    text = ("\n1.0\n3 0 0\n0 3 0\n0 0 3\nSi\n1\nSelective dynamics\n"
            "Cartesian\n1D-1 2d-1 3e-1 T F T\n\nCartesian\n0 0 0\n")
    info = validate_poscar(text)
    assert info.elements == ("Si",)
    assert info.coordinate_mode == "cartesian"
    assert info.selective_dynamics is True


@pytest.mark.parametrize("replacement", ["NaN", "Infinity", "0", "9" * 5000])
def test_bad_scale_or_count_fails_with_stable_validation_error(replacement):
    text = FILES["POSCAR"].decode()
    if replacement.isdigit() and len(replacement) > 1:
        text = text.replace("Si\n1\n", f"Si\n{replacement}\n")
    else:
        text = text.replace("1.0\n", replacement + "\n", 1)
    with pytest.raises(InputValidationError):
        validate_poscar(text)


def test_fractional_explicit_kpoints_and_incar_continuation():
    validate_kpoints("explicit\n1\nFractional\n0 0 0 1 ! gamma\n")
    validate_incar("# comment\nSYSTEM = synthetic; ENCUT = 400\nMAGMOM = 1*1 "
                   + chr(92) + "\n 0*1\n")


def test_synthetic_potcar_variant_matches_element_but_wrong_order_blocks():
    variant = FILES["POTCAR"].replace(b"PAW_PBE Si", b"PAW_PBE Si_pv")
    assert validate_potcar(variant, ("Si",)) == ("Si",)
    with pytest.raises(InputValidationError) as error:
        validate_potcar(variant, ("O",))
    assert error.value.code == "POTCAR_SPECIES_MISMATCH"
