import pytest

from ai_mode.report.extract import summarize_run
from ai_mode.report.render import render_report
from ai_mode.schemas import JobEntry, JobStatus, Session


@pytest.mark.parametrize("summaries, expected", [([1], 1), ([1, 2], 2), ([1, 1], 2), ([], None)])
def test_report_counts_ionic_summaries_not_outcar_energy_prints(summaries, expected):
    outcar = "free energy TOTEN = -10\n" * 12
    oszicar = "".join(f"DAV: 1 -10 -1e-7 -2e-7 20 1e-6\n{i} F= -10\n" for i in summaries)
    result = summarize_run(outcar, oszicar)
    assert result["outcar"]["n_ionic_steps"] == expected
    assert result["outcar"]["final_energy"] == -10


def test_empty_optional_refinement_keeps_evidence_based_conclusion():
    session = Session(jobs=[JobEntry(job_key="si_scf", status=JobStatus.COMPLETED)])
    report = render_report(session, extractions={"si_scf": {"outcar": {
        "final_energy": -10.0, "n_ionic_steps": 1, "converged": True}}}, refine=lambda _: "")
    assert "已完成作业：si_scf" in report.markdown
    assert "提炼回调未返回内容" not in report.markdown
