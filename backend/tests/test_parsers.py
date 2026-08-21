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
    assert d.last_ionic_step == 2
    assert d.ionic_steps[0]["F"] == -125.38624
    assert d.ionic_steps[0]["E0"] == -125.30617
    assert len(d.energy_series) == 2
    # 仅离子汇总、无电子迭代行
    assert d.electronic_steps == []
    assert d.last_electronic_step == 0
    assert d.total_electronic_lines == 0


def test_oszicar_single_ionic_step_with_electronic_block():
    # 矩阵 1/5/6/7：单离子步 3 条 DAV + 1 条 F 汇总
    sample = (
        "DAV:   1    -0.123456789E+03   -0.12345E+03   -0.10000E+02   512   0.123E+01\n"
        "DAV:   2    -0.123400000E+03   -0.56789E-01   -0.50000E-01   512   0.100E+00\n"
        "DAV:   3    -0.123390000E+03   -0.10000E-01   -0.10000E-01   512   0.100E-01\n"
        "   1 F= -.12339000E+03 E0= -.12339000E+03  d E =-.10000000E-01\n"
    )
    d = parse_oszicar(sample)
    assert len(d.electronic_steps) == 3
    assert len(d.ionic_steps) == 1
    assert d.total_electronic_lines == 3
    assert d.last_ionic_step == 1 and d.last_step == 1
    assert d.last_electronic_step == 3
    assert all(e.ionic_step == 1 for e in d.electronic_steps)
    assert [e.electronic_step for e in d.electronic_steps] == [1, 2, 3]
    e1 = d.electronic_steps[0]
    assert e1.algorithm == "DAV"
    assert e1.energy == -123.456789
    assert e1.delta_energy == -123.45
    assert e1.delta_epsilon == -10.0
    assert e1.ncg == 512
    assert e1.rms == 1.23
    assert e1.source_line == 1
    # 电子行不混入 ionic_steps；离子汇总不混入 electronic_steps
    assert d.ionic_steps[0]["step"] == 1
    assert d.electronic_energy_series == [-123.456789, -123.4, -123.39]


def test_oszicar_two_ionic_blocks_restart_numbering():
    # 矩阵 2：两个离子步，电子步编号分别从 1 重新开始
    sample = (
        "DAV:   1    -0.10000000E+02   -0.1E+02   -0.1E+02   96   0.1E+01\n"
        "DAV:   2    -0.10100000E+02   -0.1E+00   -0.1E+00   96   0.1E+00\n"
        "   1 F= -.10100000E+02 E0= -.10100000E+02  d E =-.1E+00\n"
        "RMM:   1    -0.10200000E+02   -0.1E+00   -0.1E+00   72   0.1E-01\n"
        "RMM:   2    -0.10210000E+02   -0.1E-01   -0.1E-01   72   0.1E-02\n"
        "   2 F= -.10210000E+02 E0= -.10210000E+02  d E =-.11E+00\n"
    )
    d = parse_oszicar(sample)
    assert d.last_ionic_step == 2
    assert d.last_electronic_step == 2
    assert [e.ionic_step for e in d.electronic_steps] == [1, 1, 2, 2]
    assert [e.electronic_step for e in d.electronic_steps] == [1, 2, 1, 2]
    assert [e.algorithm for e in d.electronic_steps] == ["DAV", "DAV", "RMM", "RMM"]
    # electronic_energy_series 只取最后一个电子块，不跨离子步拼接
    assert d.electronic_energy_series == [-10.2, -10.21]


def test_oszicar_duplicate_ionic_step_numbers_kept_separate():
    # 重启片段：两个独立电子块都以 `1 F=` 结束，ionic_step 编号重复；
    # 最后电子块只含第二块，不得因编号相同而合并第一块。
    sample = (
        "DAV:   1    -0.100000000E+02   -0.1E+02   -0.1E+02   96   0.1E+01\n"
        "DAV:   2    -0.101000000E+02   -0.1E+00   -0.1E+00   96   0.1E+00\n"
        "   1 F= -.10100000E+02 E0= -.10100000E+02  d E =-.1E+00\n"
        "DAV:   1    -0.200000000E+02   -0.2E+02   -0.2E+02   96   0.1E+01\n"
        "DAV:   2    -0.201000000E+02   -0.1E+00   -0.1E+00   96   0.1E+00\n"
        "   1 F= -.20100000E+02 E0= -.20100000E+02  d E =-.1E+00\n"
    )
    d = parse_oszicar(sample)
    assert len(d.ionic_steps) == 2
    assert len(d.electronic_steps) == 4
    assert all(e.ionic_step == 1 for e in d.electronic_steps)
    assert d.total_electronic_lines == 4
    # 最后电子块证据只来自第二块
    assert d.electronic_energy_series == [-20.0, -20.1]
    assert d.last_electronic_step == 2


def test_oszicar_summary_without_electronic_lines_clears_last_block():
    # 最后一个 F= 汇总前无电子行：最后电子块证据清空，
    # electronic_steps 仍保留第一块历史数据供绘图。
    sample = (
        "DAV:   1    -0.100000000E+02   -0.1E+02   -0.1E+02   96   0.1E+01\n"
        "DAV:   2    -0.101000000E+02   -0.1E-01   -0.1E-01   96   0.1E+00\n"
        "   1 F= -.10100000E+02 E0= -.10100000E+02  d E =-.1E-01\n"
        "   2 F= -.10200000E+02 E0= -.10200000E+02  d E =-.1E-01\n"
    )
    d = parse_oszicar(sample)
    assert len(d.electronic_steps) == 2  # 历史第一块保留
    assert d.total_electronic_lines == 2
    assert d.last_ionic_step == 2 and d.last_step == 2
    assert d.last_electronic_step == 0
    assert d.electronic_energy_series == []


def test_oszicar_trailing_pending_is_last_block():
    # 尾部无 F= 的 pending 块仍作为最后电子块，保留推断 warning
    sample = (
        "DAV:   1    -0.100000000E+02   -0.1E+02   -0.1E+02   96   0.1E+01\n"
        "   1 F= -.10000000E+02 E0= -.10000000E+02  d E =-.1E-01\n"
        "DAV:   1    -0.200000000E+02   -0.2E+02   -0.2E+02   96   0.1E+01\n"
        "DAV:   2    -0.201000000E+02   -0.1E-01   -0.1E-01   96   0.1E+00\n"
    )
    d = parse_oszicar(sample)
    assert d.electronic_energy_series == [-20.0, -20.1]
    assert d.last_electronic_step == 2
    assert d.last_ionic_step == 1
    assert any("inferred" in w for w in d.parser_warnings)


def test_oszicar_mixed_algorithms_and_exponent_forms():
    # 矩阵 3/4：DAV/RMM/CG 混合（含 CG 冒号前空格）与 E/e/D 计数法、前导小数点
    sample = (
        "DAV:   1    -0.100000000E+02  -0.1E+02  -0.1E+02  96  0.1E+01\n"
        "RMM:   2    -0.101000000e+02  -0.1e+00  -0.1e+00  72  0.1e-01\n"
        "CG :   3    -0.101100000D+02  -0.1D-01  -0.1D-01  64  0.1D-02\n"
        "   1 F= -.1011E+02 E0= -.1011E+02  d E =-.1E-01\n"
    )
    d = parse_oszicar(sample)
    assert [e.algorithm for e in d.electronic_steps] == ["DAV", "RMM", "CG"]
    assert d.electronic_steps[0].energy == -10.0
    assert d.electronic_steps[1].energy == -10.1
    assert d.electronic_steps[2].energy == -10.11
    assert d.electronic_steps[2].delta_energy == -0.01
    assert d.electronic_steps[2].ncg == 64
    assert d.ionic_steps[0]["F"] == -10.11  # 前导小数点数值


def test_oszicar_truncated_fragment_ionic_attribution():
    # 截断片段从第 37 离子步的电子块开始：归属必须为 37 而非 1
    sample = (
        "DAV:   1    -0.50000000E+02   -0.5E+02   -0.5E+02   96   0.1E+01\n"
        "DAV:   2    -0.50100000E+02   -0.1E+00   -0.1E+00   96   0.1E+00\n"
        "  37 F= -.50100000E+02 E0= -.50100000E+02  d E =-.1E+00\n"
    )
    d = parse_oszicar(sample)
    assert [e.ionic_step for e in d.electronic_steps] == [37, 37]
    assert d.last_ionic_step == 37
    assert not any("inferred" in w for w in d.parser_warnings)


def test_oszicar_trailing_block_without_summary_warns():
    # 尾部电子块无 F= 汇总：推断为前序汇总+1 且显式 warning
    sample = (
        "   5 F= -.50000000E+02 E0= -.50000000E+02  d E =-.1E+00\n"
        "DAV:   1    -0.50100000E+02   -0.1E+00   -0.1E+00   96   0.1E+00\n"
        "DAV:   2    -0.50110000E+02   -0.1E-01   -0.1E-01   96   0.1E-01\n"
    )
    d = parse_oszicar(sample)
    assert [e.ionic_step for e in d.electronic_steps] == [6, 6]
    assert d.last_electronic_step == 2
    assert any("inferred" in w for w in d.parser_warnings)


def test_oszicar_electronic_only_fragment_local_index():
    # 整个片段无任何 F= 汇总：暂归 1 且 warning 说明为局部/推断编号
    sample = "DAV:   1    -0.50100000E+02   -0.1E+00   -0.1E+00   96   0.1E+00\n"
    d = parse_oszicar(sample)
    assert d.electronic_steps[0].ionic_step == 1
    assert d.last_ionic_step == 0
    assert any("local inferred" in w for w in d.parser_warnings)


def test_oszicar_star_overflow_no_field_shift():
    # 中间列为 **** 时仅该列 None，后续 ncg/rms 不错位
    sample = (
        "DAV:   1    -0.10000000E+02   -0.1E+00   ********   96   0.1E+00\n"
        "   1 F= -.10000000E+02 E0= -.10000000E+02  d E =-.1E+00\n"
    )
    d = parse_oszicar(sample)
    e = d.electronic_steps[0]
    assert e.energy == -10.0
    assert e.delta_energy == -0.1
    assert e.delta_epsilon is None
    assert e.ncg == 96
    assert e.rms == 0.1
    assert any("field delta_epsilon" in w for w in d.parser_warnings)
    # warning 只含行号与字段名，不含路径或大段原文
    assert all("\\" not in w and "/" not in w for w in d.parser_warnings)


def test_oszicar_truncated_electronic_line_fail_soft():
    # 截断行：缺失尾部字段仅对应列为 None，不崩溃不伪造
    sample = "DAV:   1    -0.10000000E+02   -0.1E+00\n"
    d = parse_oszicar(sample)
    e = d.electronic_steps[0]
    assert e.energy == -10.0 and e.delta_energy == -0.1
    assert e.delta_epsilon is None and e.ncg is None
    assert e.rms is None and e.rms_c is None


def test_oszicar_unknown_algorithm_tag_not_parsed():
    # 未知标签不误解析，仅简短 warning，不影响诊断
    sample = (
        "FOO:   1    -0.10000000E+02   -0.1E+00\n"
        "DAV:   1    -0.10000000E+02   -0.1E+00   -0.1E+00   96   0.1E+00\n"
        "   1 F= -.10000000E+02 E0= -.10000000E+02  d E =-.1E+00\n"
    )
    d = parse_oszicar(sample)
    assert len(d.electronic_steps) == 1
    assert d.electronic_steps[0].algorithm == "DAV"
    assert any("unknown algorithm tag FOO" in w for w in d.parser_warnings)


def test_oszicar_zero_F_not_replaced_by_E0():
    # F=0.0 是合法数值，不得回退到 E0
    sample = "   1 F= 0.0 E0= -.10000000E+02  d E =-.1E+00\n"
    d = parse_oszicar(sample)
    assert d.energy_series == [0.0]


def test_oszicar_empty_input():
    d = parse_oszicar("")
    assert d.last_ionic_step == 0 and d.last_electronic_step == 0
    assert d.electronic_steps == [] and d.ionic_steps == []


def test_outcar_ionic_convergence_evidence():
    # 仅完整结构优化收敛停止语句才算证据；无上下文短语不算；缺失保持 None
    full_z = " reached required accuracy - stopping structural energy minimisation\n"
    assert parse_outcar(full_z).ionic_convergence_reached is True
    full_s = "REACHED REQUIRED ACCURACY - STOPPING STRUCTURAL ENERGY MINIMIZATION\n"
    assert parse_outcar(full_s).ionic_convergence_reached is True
    phrase_only = " reached required accuracy\n"
    assert parse_outcar(phrase_only).ionic_convergence_reached is None
    assert parse_outcar("").ionic_convergence_reached is None


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
