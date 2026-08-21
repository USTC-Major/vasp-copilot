from __future__ import annotations

from app.parsers.cif import parse_cif

FE2O3_CIF = """
data_Fe2O3
_cell_length_a   5.0380
_cell_length_b   5.0380
_cell_length_c   13.7720
_cell_angle_alpha 90.0000
_cell_angle_beta  90.0000
_cell_angle_gamma 120.0000
_symmetry_space_group_name_H-M  'R -3 c H'
loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
Fe1 Fe 0.0000 0.0000 0.3553
Fe2 Fe 0.0000 0.0000 0.6447
O1   O  0.3054 0.3054 0.2500
O2   O  0.6946 0.6946 0.2500
O3   O  0.0000 0.0000 0.5000
"""


def test_parse_fe2o3_cif():
    data = parse_cif(FE2O3_CIF, source_file="structure.cif")
    assert data.formula == "Fe2O3"
    assert data.elements == ["Fe", "O"]
    assert data.counts == [2, 3]
    assert data.lattice_a == 5.0380
    assert data.lattice_c == 13.7720
    assert abs(data.angle_gamma - 120.0) < 1e-9
    assert data.space_group == "R -3 c H"
    assert data.source_file == "structure.cif"


def test_cif_symbol_strips_index():
    # label column used as symbol fallback should drop numeric suffix
    text = FE2O3_CIF.replace("_atom_site_type_symbol", "_atom_site_ignore")
    data = parse_cif(text)
    assert data.elements == ["Fe", "O"]
    assert data.counts == [2, 3]


def test_cif_empty_returns_defaults():
    data = parse_cif("# no structure\n")
    assert data.elements == []
    assert data.counts == []
    assert data.lattice_a is None


def test_cif_handles_comment_lines():
    text = FE2O3_CIF.replace("'R -3 c H'", "'R -3 c H'  # comment")
    data = parse_cif(text, source_file="x.cif")
    assert data.elements == ["Fe", "O"]
    assert data.counts == [2, 3]
