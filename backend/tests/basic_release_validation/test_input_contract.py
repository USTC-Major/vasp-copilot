"""Risk-focused checks for the shared minimum input gate."""

from __future__ import annotations

import math

import pytest

from backend.input_validation import (
    InputValidationError,
    validate_incar,
    validate_kpoints,
    validate_poscar,
    validate_potcar,
)


def _poscar(*, scale="1", first_vector="1 0 0", count="2", coordinates="0 0 0\n0.5 0.5 0.5"):
    return (
        f"Si fixture\n{scale}\n{first_vector}\n0 1 0\n0 0 1\n"
        f"Si\n{count}\nDirect\n{coordinates}\n"
    )


@pytest.mark.parametrize("text", [
    _poscar(coordinates="0 0 0"),
    _poscar(scale="NaN"),
    _poscar(first_vector="0 0 0"),
    _poscar(coordinates="0 0 Inf\n0.5 0.5 0.5"),
])
def test_invalid_geometry_fails_as_controlled_input_error(text):
    with pytest.raises(InputValidationError) as error:
        validate_poscar(text)
    assert error.value.code.startswith(("POSCAR_", "INPUT_"))
    assert error.value.message


def test_supported_scaled_selective_cartesian_keeps_geometry_and_flags():
    text = (
        "Si two\n-8\n1 0 0\n0 1 0\n0 0 1\nSi\n2\n"
        "Selective dynamics\nCartesian\n0D0 0D0 0D0 T F T\n"
        "1D0 1D0 1D0 F T F\n"
    )
    info = validate_poscar(text)
    assert info.elements == ("Si",)
    assert info.counts == (2,)
    assert info.coordinate_mode == "cartesian"
    assert info.selective_dynamics is True
    assert math.prod(info.matrix[i][i] for i in range(3)) == pytest.approx(8.0)


def test_three_component_scale_and_empty_comment_do_not_shift_header():
    text = "\n2 3 4\n1 0 0\n0 1 0\n0 0 1\nSi\n1\nDirect\n0 0 0\n"
    info = validate_poscar(text)
    assert info.elements == ("Si",)
    assert [info.matrix[i][i] for i in range(3)] == pytest.approx([2, 3, 4])


def test_incar_and_kpoints_common_legal_syntax_is_not_rejected():
    validate_incar("ENCUT = 520; EDIFF = 1D-5 ! user note\nUNKNOWN_TAG = 1\nLREAL = \\\n Auto\n")
    validate_kpoints(
        "path\n40\nLine-mode\nreciprocal\n0 0 0 ! G\n0.5 0 0 ! X\n"
    )


def test_incomplete_tetrahedron_trailer_is_not_certified_as_valid_kpoints():
    # An explicit list may be followed by tetrahedra, but this starts the
    # section without its required count/weight and tetrahedron rows.
    with pytest.raises(InputValidationError):
        validate_kpoints("explicit\n1\nReciprocal\n0 0 0 1\nTetrahedra\n")


def test_potcar_metadata_order_checked_without_real_potential_body():
    def block(element):
        return f"VRHFIN = {element}: synthetic\nTITEL = PAW_PBE {element}_pv test\n End of Dataset\n"

    validate_potcar((block("Si") + block("O")).encode(), ("Si", "O"))
    with pytest.raises(InputValidationError) as error:
        validate_potcar((block("O") + block("Si")).encode(), ("Si", "O"))
    assert error.value.code == "POTCAR_SPECIES_MISMATCH"
