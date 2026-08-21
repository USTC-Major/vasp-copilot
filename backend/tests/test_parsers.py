from __future__ import annotations

from app.parsers.incar import parse_incar
from app.parsers.outcar import parse_outcar
from app.parsers.oszicar import parse_oszicar
from app.parsers.poscar import parse_poscar
from app.parsers.kpoints import parse_kpoints
from app.parsers.job_log import parse_job_log


# ---------- INCAR ----------
def test_incar_repetition_expansion():
    text = (
        "SYSTEM = test\n"
        "LDAU = .TRUE.\n"
        "NELM = 200\n"
        "EDIFF = 1e-5\n"
        "MAGMOM = 68*0.6 4*3.0\n"
        "UNKNOWN_USER = hello\n"
        "ISPIN = 2\n"
    )
    d = parse_incar(text)
    assert d.effective["LDAU"] is True
    assert d.effective["NELM"] == 200
    assert d.effective["EDIFF"] == 1e-5
    assert len(d.effective["MAGMOM"]) == 72
    assert d.effective["MAGMOM"][:4] == [0.6, 0.6, 0.6, 0.6]
    assert d.effective["MAGMOM"][-4:] == [3.0, 3.0, 3.0, 3.0]
    assert "UNKNOWN_USER" in d.unknown
    assert d.effective["UNKNOWN_USER"] == "hello"


def test_incar_comment_and_duplicate():
    text = "SYSTEM = a ! inline\nSYSTEM = b # second\n"
    d = parse_incar(text)
    assert d.effective["SYSTEM"] == "b"
    assert len(d.duplicate) == 1
    assert d.duplicate[0].name == "SYSTEM"


# ---------- OUTCAR ----------
def test_outcar_basic():
    sample = (
        " vasp.5.4.4.18Apr17-6-g9f103f2a35 (build X) complex\n"
        " ISPIN     =   2\n"
        " LNONCOLLINEAR =      F\n"
        " LSORBIT = F\n"
        " free  energy   TOTEN  =       -125.38624000 eV\n"
        " magnetization (x)\n"
        " # of ion       s       p       d       tot\n"
        " -------------------------------------------------\n"
        "     1            0.001   0.000   4.687   4.688\n"
        "     2           -0.003   0.000   4.601   4.598\n"
        " -------------------------------------------------\n"
        " tot            0.560   1.240   1.870   3.670\n"
        " General timing and accounting informations\n"
    )
    d = parse_outcar(sample)
    assert d.vasp_version == "5.4.4.18Apr17-6-g9f103f2a35"
    assert d.vasp_binary_hint == "complex"
    assert d.normal_termination is True
    assert d.truncated is False
    assert d.final_energy == -125.38624
    assert d.calculation_mode.is_spin_polarized is True
    assert len(d.final_magnetization) == 2
    assert d.magnetization_total["tot"] == 3.67


def test_outcar_truncated():
    sample = " vasp.5.4.4 complex\n RMM-DIIS: failed to reach selfconsistency\n something\n"
    d = parse_outcar(sample)
    assert d.truncated is True
    assert d.error_lines


# ---------- OSZICAR ----------
def test_oszicar_steps():
    sample = (
        "   1 F= -.12538624E+03 E0= -.12530617E+03  d E =-.12538624E+03  mag=    4.6868\n"
        "   2 F= -.12525646E+03 E0= -.12525646E+03  d E =-.12119588E-02  mag=    4.6882\n"
    )
    d = parse_oszicar(sample)
    assert d.last_step == 2
    assert d.ionic_steps[0]["F"] == -125.38624
    assert d.ionic_steps[0]["E0"] == -125.30617
    assert len(d.energy_series) == 2


# ---------- POSCAR ----------
def test_poscar_elements_counts():
    text = (
        "comment\n1.0\n5.43 0 0\n0 5.43 0\n0 0 5.43\nSi O\n2 4\nDirect\n0 0 0\n"
    )
    d = parse_poscar(text)
    assert d.elements == ["Si", "O"]
    assert d.counts == [2, 4]


# ---------- KPOINTS ----------
def test_kpoints():
    d = parse_kpoints("c\n0\nGamma\n4 4 4\n")
    assert d.line_mode is False
    d2 = parse_kpoints("band\n2\nLine-mode\n")
    assert d2.line_mode is True


# ---------- JOB LOG ----------
def test_job_log_keywords():
    text = (
        "srun: error: Node failure\n"
        "slurmstepd: error\n"
        "ModuleNotFoundError: No module named ase\n"
        "No such file: INCAR\n"
    )
    d = parse_job_log(text, path="slurm-1.out", tail=10)
    cats = {k["category"] for k in d.keywords}
    assert "scheduler" in cats
    assert "module" in cats
    assert "path" in cats
    assert d.path == "slurm-1.out"