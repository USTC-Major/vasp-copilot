from __future__ import annotations

from app.services.diagnosis_service import _load_parsed, detect_files
from app.diagnostics.rules.files import FileMissingRule
from app.schemas.parsed import ParsedRunData

CONTCAR_TEXT = (
    "CONTCAR (relax output)\n1.0\n5.43 0 0\n0 5.43 0\n0 0 5.43\nSi O\n2 4\nDirect\n0 0 0  8\n"
)


def _write_run(tmp_path, files):
    for name, text in files.items():
        p = tmp_path / name
        p.write_text(text, encoding="utf-8")
    return tmp_path


def test_detect_contcar_kind_and_missing(tmp_path):
    base = _write_run(tmp_path, {
        "INCAR": "SYSTEM = demo\nNELM = 60\n",
        "CONTCAR": CONTCAR_TEXT,
        "OSZICAR": "  1 F=-100.0\n",
        "OUTCAR": "Normal termination\n",
    })
    detected = detect_files(base)
    kinds = [f.kind for f in detected.files]
    assert "concar" in kinds
    # POSCAR 缺失但 CONTCAR 存在：结构就绪，不算缺失；POTCAR 不在 doctor 范围
    assert "POSCAR" not in detected.missing_recommended
    assert "POTCAR" not in detected.missing_recommended
    assert detected.missing_recommended == ["KPOINTS"]


def test_load_parsed_falls_back_to_contcar(tmp_path):
    base = _write_run(tmp_path, {
        "INCAR": "SYSTEM = demo\n",
        "CONTCAR": CONTCAR_TEXT,
    })
    parsed = _load_parsed(base, job_log=None)
    assert parsed.poscar.elements == ["Si", "O"]
    assert parsed.poscar.counts == [2, 4]
    assert parsed.poscar.source_file == "CONTCAR"
    assert "CONTCAR" in parsed.source_files
    assert "POSCAR" not in parsed.source_files


def test_load_parsed_prefers_poscar(tmp_path):
    base = _write_run(tmp_path, {
        "POSCAR": "comment\n1.0\n5.43 0 0\n0 5.43 0\n0 0 5.43\nSi O\n2 4\nDirect\n",
        "CONTCAR": CONTCAR_TEXT,
    })
    parsed = _load_parsed(base, job_log=None)
    assert parsed.poscar.source_file == "POSCAR"
    assert "POSCAR" in parsed.source_files


def test_file_missing_rule_accepts_contcar():
    rule = FileMissingRule()
    # CONTCAR 提供结构，与 POSCAR 等价
    ok = ParsedRunData(source_files=["INCAR", "CONTCAR", "OUTCAR"])
    assert rule.run(ok) == []
    # 两者都缺才报结构缺失
    bad = ParsedRunData(source_files=["OUTCAR"])
    issues = rule.run(bad)
    assert len(issues) == 1 and issues[0].rule_id == "REQUIRED_FILE_MISSING"
    assert {e.file for e in issues[0].evidence} == {"INCAR", "POSCAR"}

from app.api.v1.diagnosis import _preview_kind


CIF_TEXT = (
    "data_Fe2O3\n"
    "_cell_length_a   5.0380\n"
    "_cell_length_b   5.0380\n"
    "_cell_length_c   13.7720\n"
    "_cell_angle_alpha 90.0000\n"
    "_cell_angle_beta  90.0000\n"
    "_cell_angle_gamma 120.0000\n"
    "_symmetry_space_group_name_H-M  'R -3 c H'\n"
    "loop_\n"
    "_atom_site_label\n"
    "_atom_site_type_symbol\n"
    "_atom_site_fract_x\n"
    "_atom_site_fract_y\n"
    "_atom_site_fract_z\n"
    "Fe1 Fe 0.0000 0.0000 0.3553\n"
    "Fe2 Fe 0.0000 0.0000 0.6447\n"
    "O1   O  0.3054 0.3054 0.2500\n"
    "O2   O  0.6946 0.6946 0.2500\n"
    "O3   O  0.0000 0.0000 0.5000\n"
)

MAGMOM_OUTCAR = (
    " vasp.6.3.2 (build Mar 2025) (parallel)\n"
    "   ISPIN =      2\n"
    "   LOOP+:  electronic self-consistency\n"
    "     1 F= -50.00000000 E0= -50.00000000 d E =-5.00000000e-02\n"
    " magnetization (x)\n"
    " # of ion       s       p       d       tot\n"
    " -------------------------------------------------\n"
    "    1         0.000   0.000   0.040   0.040\n"
    " -------------------------------------------------\n"
    " tot          0.000   0.000   0.040   0.040\n"
    " General timing and accounting informations for this run:\n"
    "     Total CPU time used (sec):        1.00\n"
)


def test_detect_cif_kind(tmp_path):
    base = _write_run(tmp_path, {"Fe2O3.cif": CIF_TEXT, "INCAR": "SYSTEM = demo\n"})
    detected = detect_files(base)
    kinds = [f.kind for f in detected.files]
    assert "cif" in kinds, kinds


def test_load_parsed_reads_cif(tmp_path):
    base = _write_run(tmp_path, {"Fe2O3.cif": CIF_TEXT, "INCAR": "SYSTEM = demo\n"})
    parsed = _load_parsed(base, job_log=None)
    assert parsed.cif is not None
    assert parsed.cif.elements == ["Fe", "O"]
    assert parsed.cif.counts == [2, 3]
    assert parsed.cif.lattice_a == 5.0380
    assert parsed.cif.source_file == "Fe2O3.cif"
    assert "Fe2O3.cif" in parsed.source_files


def test_preview_kind_handles_cif():
    assert _preview_kind("Fe2O3.CIF") == "cif"
    assert _preview_kind("structure.cif") == "cif"


def test_load_parsed_syncs_calculation_mode_from_outcar(tmp_path):
    base = _write_run(tmp_path, {
        "INCAR": "ISPIN  = 2\nMAGMOM = 2*2.0\n",
        "POSCAR": ("comment\n1.0\n2.86 0 0\n0 2.86 0\n0 0 2.86\n"
                   "Fe\n2\nDirect\n0 0 0\n0.5 0.5 0.5\n"),
        "OUTCAR": MAGMOM_OUTCAR,
    })
    parsed = _load_parsed(base, job_log=None)
    assert parsed.calculation_mode.magnetization_analysis_mode.value == "collinear"

