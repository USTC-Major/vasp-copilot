"""M10 报告与收尾测试：解析 OUTCAR/OSZICAR + 报告渲染 + 清理建议（纯内存）。"""

import pytest
from ai_mode.report.extract import verify_run

from ai_mode.report import (
    cleanup_text,
    parse_outcar,
    parse_osziacar,
    parse_osziacar,
    render_report,
    suggest_cleanup,
    summarize_run,
)
from ai_mode.report.render import JobResultItem
from ai_mode.schemas import JobEntry, JobStatus, PlanSnapshot, PlanStep, RequirementSnapshot, Session


OUTCAR_OK = """
ENCUT  =   520.0
EDIFF  =  0.10000E-04
IBRION       =      2
ISIF         =      3
ISMEAR       =      1
SIGMA        =    0.2000
NSW          =      60
E-fermi :   4.8362     XMU=     4.8362
  free  energy   TOTEN  =      -32.016527 eV
reached required accuracy - stopping structural energy minimisation
General timing and accounting informations for this job:
"""

OSZICAR_OK = """
DAV:   1    -31.976812E+00   ...
DAV:   2    -32.016527E+00   ...
   1 F= -.32016527E+02 E0= -.32016527E+02  d E =-.320165E+02
"""


def _verified_output(*, nsw=0, ibrion=-1, stop=""):
    return (f"EDIFF=1e-5 NELM=60 NSW={nsw} IBRION={ibrion}\n"
            "free energy TOTEN = -10 eV\n" + stop
            + "\nGeneral timing and accounting informations for this job:\n")


@pytest.mark.parametrize("algorithm", ["DAV", "RMM", "CG", "DMP", "SDA"])
def test_verification_accepts_complete_electronic_evidence(algorithm):
    osz = f" {algorithm}: 5 -10 -1e-7 -2e-7 20 1e-6\n 1 F= -10 E0= -10\n"
    assert verify_run(_verified_output(), osz)["status"] == "completed"


@pytest.mark.parametrize("outcar,oszicar,expected", [
    ("free energy TOTEN = -10 eV", "", "unknown"),
    (_verified_output(), "", "unknown"),
    (_verified_output(), "DAV: 60 -10 -0.1 -0.2 20 1e-6\n1 F= -10", "not_converged"),
    (_verified_output(), "DAV: 5 -10 -1e-7 -0.2 20 1e-6\n1 F= -10", "not_converged"),
    (_verified_output(), "DAV: 5 -10 -1e-7 -1e-7 20 1e-6\n", "unknown"),
    (_verified_output(nsw=1, ibrion=2), "DAV: 5 -10 -1e-7 -1e-7 20 1e-6\n1 F= -10", "not_converged"),
    (_verified_output(nsw=10, ibrion=2), "DAV: 5 -10 -1e-7 -1e-7 20 1e-6\n1 F= -10", "unknown"),
    (_verified_output() + "Unrecoverable error", "", "failed"),
])
def test_verification_never_promotes_partial_results(outcar, oszicar, expected):
    assert verify_run(outcar, oszicar)["status"] == expected


def test_ionic_stop_does_not_substitute_for_electronic_convergence():
    out = _verified_output(nsw=10, ibrion=2,
                           stop="reached required accuracy - stopping structural energy minimisation")
    assert verify_run(out, "DAV: 60 -10 -0.1 -0.2 20 1e-6\n1 F= -10")["status"] == "not_converged"
    assert verify_run(out, "DAV: 5 -10 -1e-7 -2e-7 20 1e-6\n1 F= -10")["status"] == "completed"


def test_band_failure_has_existing_dependency_rule_and_smearing_evidence():
    result = verify_run("charge density could not be read from CHGCAR", "",
                        incar_text="ICHARG=11\nISMEAR=-5", kpoints_text="band\n10\nLine-mode\nReciprocal\n",
                        source_files=["INCAR", "KPOINTS"], job_kind="band")
    assert result["status"] == "failed"
    assert {i["rule_id"] for i in result["issues"]} == {
        "KPOINTS_LINE_MODE_WITHOUT_STATIC", "BAND_LINE_MODE_TETRAHEDRON"}
    assert result["evidence"][0]["file"] == "OUTCAR"
    assert any("CHGCAR" in r for r in result["recommendations"])


def test_loose_convergence_words_are_not_a_stop_marker():
    assert not parse_outcar("convergence not achieved\nfree energy TOTEN = -10").converged


def test_fixed_charge_band_still_requires_electronic_and_termination_evidence():
    output = _verified_output() + "ICHARG=11\n"
    osz = "RMM: 8 -10 -1e-7 -2e-7 20 1e-6\n1 F= -10\n"
    assert verify_run(output, osz, job_kind="band")["status"] == "completed"
    assert verify_run(output, "", job_kind="band")["status"] == "unknown"


def test_editable_incar_does_not_override_missing_executed_ediff():
    output = _verified_output().replace("EDIFF=1e-5", "")
    osz = "DAV: 5 -10 -1e-7 -2e-7 20 1e-6\n1 F= -10\n"
    assert verify_run(output, osz, incar_text="EDIFF=1e-4")["status"] == "unknown"


def test_fortran_ediff_exponent_is_not_truncated_to_a_looser_tolerance():
    output = _verified_output().replace("1e-5", "0.1D-04")
    osz = "DAV: 5 -10 -1e-3 -2e-3 20 1e-6\n1 F= -10\n"
    assert verify_run(output, osz)["status"] == "not_converged"


# ---------------- OUTCAR ----------------
def test_parse_outcar_basic():
    s = parse_outcar(OUTCAR_OK)
    assert s.settings["ENCUT"] == 520.0
    assert s.settings["EDIFF"] == 1e-05
    assert s.settings["IBRION"] == 2
    assert s.settings["ISIF"] == 3
    assert s.settings["ISMEAR"] == 1
    assert s.settings["SIGMA"] == 0.2
    assert s.final_energy == pytest.approx(-32.016527)
    assert s.converged is True and s.n_ionic_steps == 1
    assert s.efermi == pytest.approx(4.8362)


# ---------------- OSZICAR ----------------
def test_parse_osziacar_basic():
    z = parse_osziacar("""DAV:   1   -10.0000E+00 x
DAV:   2   -10.0100E+00 x
   1 F= -.10001000E+02 E0= -.10001000E+02 d E =-...
""")
    assert z.final_energy == pytest.approx(-10.001)
    assert len(z.dav_energies) == 2
    assert z.ionic_energies[0] == pytest.approx(-10.001)


def test_summarize_run():
    d = summarize_run(OUTCAR_OK, OSZICAR_OK)
    assert d["outcar"]["converged"] is True
    assert d["outcar"]["final_energy"] == pytest.approx(-32.016527)
    assert d["osziacar"]["n_ionic_steps"] >= 1
    assert d["outcar"]["settings"]["ENCUT"] == 520.0


# ---------------- 报告渲染 ----------------
def _session(jobs=None, req=None, plan=None):
    return Session(
        title="Fe2O3 结构优化 + DOS",
        project_id="proj_demo",
        calc_dir="/home/user/scratch/feo",
        start_step="understand", end_step="report",
        requirement=req or RequirementSnapshot(
            raw_goal="把 Fe2O3 优化后算态密度",
            clarified_goal="relax -> static -> dos",
            coverage="understand->report"),
        plan=plan or PlanSnapshot(
            strategy="r1 递进 dos（dos 须等 r1 成功）",
            steps=[PlanStep(job_key="r1", label="relax", requires=[]),
                   PlanStep(job_key="dos", label="dos", requires=["r1"])]),
        jobs=jobs or [],
    )


def test_render_no_jobs():
    report = render_report(_session())
    assert report.title == "Fe2O3 结构优化 + DOS"
    assert "## 概览" in report.markdown
    assert "understand->report" in report.markdown
    assert "无作业记录" in report.markdown
    assert "尚无已完成的作业" in report.markdown
    assert "未默认下载" in report.suggestion_note


def test_render_jobs_table():
    s = _session(jobs=[
        JobEntry(job_key="r1", status=JobStatus.COMPLETED,
                 slurm_job_id="11223344", step="submit_monitor",
                 description="relax"),
        JobEntry(job_key="dos", status=JobStatus.PLANNED, description="dos"),
    ])
    report = render_report(s)
    assert "| r1 | completed | 11223344 |" in report.markdown
    assert "11223344" in report.markdown
    assert "已完成作业" in report.markdown


def test_render_with_extractions():
    s = _session(jobs=[JobEntry(job_key="r1", status=JobStatus.COMPLETED)])
    extra = summarize_run(OUTCAR_OK)
    report = render_report(s, extractions={"r1": extra})
    assert "自由能" in report.markdown
    assert "已收敛" in report.markdown


def test_render_with_refine():
    s = _session(jobs=[JobEntry(job_key="r1", status=JobStatus.COMPLETED)])
    report = render_report(s, refine=lambda items: "LLM 提炼结论段落")
    assert "LLM 提炼结论段落" in report.markdown


# ---------------- M57：失败原因必须进报告 ----------------
def test_failed_job_error_in_keyword_and_conclusion():
    """OUTCAR 含 Unrecoverable error：要点与结论都要点明失败作业与原因。"""
    outcar_err = OUTCAR_OK + "\nERROR: Unrecoverable error, please check\n"
    s = _session(jobs=[
        JobEntry(job_key="r1", status=JobStatus.COMPLETED,
                 description="relax"),
        JobEntry(job_key="dos", status=JobStatus.FAILED,
                 description="dos"),
    ])
    extractions = {
        "r1": summarize_run(OUTCAR_OK),
        "dos": summarize_run(outcar_err),
    }
    report = render_report(s, extractions=extractions)
    assert "Unrecoverable error（计算失败）" in report.markdown
    assert "1 个作业未成功" in report.markdown
    assert "dos（failed）" in report.markdown
    assert "已完成作业" in report.markdown


@pytest.mark.parametrize("title,expect", [
    ("", "VASP 计算报告"),
    ("我的任务", "我的任务"),
])
def test_render_title_fallback(title, expect):
    s = _session()
    s.title = title
    assert render_report(s).title == expect  # 手写 title 时以手写为准


# ---------------- 清理建议 ----------------
def test_cleanup_suggestions():
    files = [
        {"name": "core.1234", "size": 0},
        {"name": "CHGCAR", "size": 1024 * 1024 * 1024},
        {"name": "WAVECAR.1", "size": 10},
        {"name": "slurm-123.out", "size": 5, "job_done": True},
        {"name": "foo.bak", "size": 2},
        {"name": "POSCAR", "size": 1},
    ]
    s = suggest_cleanup(files)
    names = {x.name for x in s}
    assert "core.1234" in names
    assert "CHGCAR" in names
    assert "WAVECAR.1" in names
    assert "slurm-123.out" in names
    assert "foo.bak" in names
    assert "POSCAR" not in names
    for x in s:
        assert x.action == "建议清理（不自动删除）"


def test_cleanup_dedup_and_small_wavecar():
    files = [
        {"name": "WAVECAR", "size": 10},           # 小文件不触发
        {"name": "WAVECAR", "size": 10},           # 重复去重
        {"name": "slurm-1.out", "size": 1},        # 无 job_done 不触发
    ]
    assert suggest_cleanup(files) == []


def test_cleanup_text():
    s = suggest_cleanup([{"name": "core", "size": 0}])
    text = cleanup_text(s)
    assert "核心转储" in text and "不自动删除" in text


def test_cleanup_text_empty():
    assert "未发现" in cleanup_text([])


def test_suggest_never_writes():
    # 空安全 / 缺 name 忽略
    assert suggest_cleanup([{}, {"name": ""}, None]) == []
