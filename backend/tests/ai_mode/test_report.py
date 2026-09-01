"""M10 报告与收尾测试：解析 OUTCAR/OSZICAR + 报告渲染 + 清理建议（纯内存）。"""

import pytest

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
convergence has been achieved
"""

OSZICAR_OK = """
DAV:   1    -31.976812E+00   ...
DAV:   2    -32.016527E+00   ...
   1 F= -.32016527E+02 E0= -.32016527E+02  d E =-.320165E+02
"""


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
