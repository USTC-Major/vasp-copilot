"""M10 报告与收尾：把会话浓缩成一份 markdown 报告。

对齐 WORKFLOW.md v14 步8、MODULE_INTERFACES v1.2 §1.8：
- 本地只留报告；数据/图表不默认下载（只在报告里提示，后续按需从超算提取）。
- ``refine`` 是可选 LLM 提炼回调；不传时用结构化模板摘要（离线可用）。
- 不 import 工具箱任何代码。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Mapping, Optional, Sequence

from ..schemas import JobEntry, JobStatus, Session


def _status_label(status) -> str:
    return getattr(status, "value", status) if status else "?"


def _coverage_text(s: Session) -> str:
    if s.requirement and s.requirement.coverage:
        return s.requirement.coverage
    return f"{s.start_step}->{s.end_step}"


def _clean_cell(value) -> str:
    return "—" if value in (None, "", []) else str(value)


#: LLM 提炼回调：输入作业结果列表（含提取的 keyword 概要），返回结论段落。
RefineFn = Callable[[Sequence["JobResultItem"]], str]


@dataclass
class JobResultItem:
    """单个作业的结果（含提取摘要与提炼文本）。"""

    job: JobEntry
    extraction: dict = field(default_factory=dict)
    refined: str = ""

    @property
    def keyword_text(self) -> str:
        if not self.extraction:
            return ""
        oc = self.extraction.get("outcar") or {}
        parts = []
        if oc.get("unrecoverable_error"):
            # M57：失败原因必须进报告要点，不能只展示成功作业的结果
            parts.append("OUTCAR 含 Unrecoverable error（计算失败）")
        if oc.get("final_energy") is not None:
            parts.append(f"自由能 {oc['final_energy']:.6f} eV")
        if oc.get("converged"):
            parts.append("已收敛")
        if oc.get("n_ionic_steps") is not None:
            parts.append(f"{oc['n_ionic_steps']} 离子步")
        return "；".join(parts)


@dataclass
class ReportReport:
    """一份报告产物：标题、markdown 正文、收尾提示。"""

    title: str
    markdown: str
    suggestion_note: str = (
        "数据/图表未默认下载到本地；如需提取，请在超算侧按需获取。"
    )


def _render_body(session: Session, jobs: Sequence[JobResultItem],
                 refine: Optional[RefineFn]) -> str:
    lines: list[str] = []

    # 概览
    lines.append("## 概览")
    lines.append(f"- 任务：{session.title or '（未命名）'}")
    if session.project_id:
        lines.append(f"- 项目：{session.project_id}")
    lines.append(f"- 计算目录：`{session.calc_dir}`")
    lines.append(f"- 本次覆盖：{_coverage_text(session)}")
    lines.append(f"- 完成时间：{session.updated_at}")

    # 需求
    if session.requirement:
        lines.append("## 需求")
        lines.append(f"- 原始目标：{_clean_cell(session.requirement.raw_goal)}")
        if session.requirement.clarified_goal:
            lines.append(
                f"- 澄清后目标：{_clean_cell(session.requirement.clarified_goal)}")

    # 规划
    if session.plan:
        lines.append("## 规划")
        lines.append(f"- 策略：{_clean_cell(session.plan.strategy)}")
        if session.plan.steps:
            lines.append("- 作业顺序：")
            for step in session.plan.steps:
                req = f"（依赖 {', '.join(step.requires)}）" if step.requires else ""
                lines.append(f"  - {step.job_key}【{step.label}】{req}")

    # 作业一览
    lines.append("## 作业")
    if not jobs:
        lines.append("- 无作业记录。")
    else:
        lines.append("| 作业 | 状态 | 超算作业号 | 步 | 描述 | 提取要点 |")
        lines.append("|---|---|---|---|---|---|")
        for item in jobs:
            j = item.job
            lines.append(
                f"| {j.job_key} | {_status_label(j.status)} | "
                f"{_clean_cell(j.slurm_job_id)} | {_clean_cell(j.step)} | "
                f"{_clean_cell(j.description)} | {_clean_cell(item.keyword_text)} |")

    # 提炼结论
    lines.append("## 结论")
    if refine is not None:
        try:
            conclusion = refine(list(jobs)) or "（提炼回调未返回内容）"
        except Exception as exc:  # noqa: BLE001
            conclusion = f"（提炼失败：{exc}）"
    else:
        conclusion = _fallback_conclusion(jobs)
    lines.append(conclusion)

    return "\n".join(lines) + "\n"


def _fallback_conclusion(jobs: Sequence[JobResultItem]) -> str:
    finished = [i for i in jobs
                if i.job.status and i.job.status in (JobStatus.COMPLETED,)]
    failed = [i for i in jobs
              if i.job.status in (JobStatus.FAILED, JobStatus.NOT_CONVERGED,
                                  JobStatus.CANCELLED)]
    # M57：有失败时先点明失败作业与原因，再谈成功结果
    head = ""
    if failed:
        rows = "；".join(
            f"{i.job.job_key}（{_status_label(i.job.status)}）"
            f"{('：' + i.keyword_text) if i.keyword_text else ''}"
            for i in failed)
        head = f"注意：{len(failed)} 个作业未成功——{rows}。\n"
    if not finished:
        return (head + "本任务尚无已完成的作业，报告仅含规划与进展概览。").strip()
    parts = []
    for item in finished:
        kw = item.keyword_text
        parts.append(f"{item.job.job_key}：{kw or '完成'}")
    return (head + "已完成作业：" + "；".join(parts)
            + "。如需进一步提取数据/图表请告知。")


def render_report(session: Session, *,
                  extractions: Optional[Mapping[str, dict]] = None,
                  refine: Optional[RefineFn] = None) -> ReportReport:
    """把会话渲染为 markdown 报告（纯文本生成，不写盘）。

    :param session: 会话（M2 schema）。
    :param extractions: job_key -> summarize_run() 的字典结果（按需注入）。
    :param refine: 可选 LLM 提炼回调；缺省用结构化模板结论。
    """
    results: list[JobResultItem] = []
    for job in session.jobs or []:
        results.append(JobResultItem(
            job=job,
            extraction=dict((extractions or {}).get(job.job_key) or {}),
        ))
    if refine is not None:
        for item in results:
            try:
                item.refined = refine([item]) or ""
            except Exception:  # noqa: BLE001
                item.refined = ""
    title = session.title or "VASP 计算报告"
    body = _render_body(session, results, refine)
    return ReportReport(title=title, markdown=body)
