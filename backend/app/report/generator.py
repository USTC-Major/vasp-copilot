from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..schemas.report import ReportMetadata
from ..schemas.result import DiagnosisResult
from ..schemas.status import Severity

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_SECTIONS = ["summary", "input_overview", "issues", "fixes",
             "missing_evidence", "disclaimer"]


class ReportGenerator:
    """由结构化 DiagnosisResult 渲染 Markdown 诊断报告。

    按 MVP 5.5 可在完全无 LLM 下运行（mode=rule_based）。下载包包含
    报告、修复文件与 diff JSON，绝不包含大型原始源文件。"""

    def __init__(self, template_dir=None, generator_version: str = "0.1.0"):
        tdir = template_dir or _TEMPLATE_DIR
        self._env = Environment(
            loader=FileSystemLoader(str(tdir)),
            autoescape=select_autoescape(["html", "xml"]),
        )
        self._template = self._env.get_template("diagnosis.md.j2")
        self.generator_version = generator_version

    def generate(self, result: DiagnosisResult) -> tuple[str, ReportMetadata]:
        severity_counts = {s.value: 0 for s in Severity}
        for i in result.issues:
            severity_counts[i.severity.value] = (
                severity_counts.get(i.severity.value, 0) + 1
            )

        generated_at = datetime.now(timezone.utc).isoformat()
        body = self._template.render(
            result=result,
            issues=result.issues,
            severity_counts=severity_counts,
        )
        size = len(body.encode("utf-8"))
        sha = hashlib.sha256(body.encode("utf-8")).hexdigest()

        metadata = ReportMetadata(
            report_id="RPT-" + uuid.uuid4().hex[:8].upper(),
            diagnosis_id=result.diagnosis_id,
            format="markdown",
            language="zh",
            title="VASP-Doctor 诊断报告",
            generated_at=generated_at,
            size_bytes=size,
            sha256=sha,
            sections=list(_SECTIONS),
            generator_version=self.generator_version,
        )
        return body, metadata