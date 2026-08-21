from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict

from .detected import DetectedRun
from .fix import RecommendedFix
from .issue import Issue
from .mode import CalculationMode
from .report import ReportMetadata
from .status import DiagnosisStatus, ModeKind


class NextStep(BaseModel):
    model_config = ConfigDict(extra="ignore")

    allowed: bool
    reason: str = ""
    suggested_task: Optional[str] = None


class Provenance(BaseModel):
    model_config = ConfigDict(extra="ignore")

    parser_version: str = ""
    rule_set_version: str = ""
    recipe_pack_version: Optional[str] = None
    composition_sha256: Optional[str] = None
    vasp_version: Optional[str] = None
    vasp_binary_hint: Optional[str] = None
    calculation_mode: CalculationMode = CalculationMode()
    llm_used: bool = False
    mode: ModeKind = ModeKind.RULE_BASED


class DiagnosisResult(BaseModel):
    """MVP 7.5：结构化诊断输出。"""

    model_config = ConfigDict(extra="ignore")

    schema_version: str = "1.0"
    diagnosis_id: str
    diagnosis_status: DiagnosisStatus
    summary: str = ""
    detected_run: Optional[DetectedRun] = None
    issues: list[Issue] = []
    plots: dict[str, Any] = {}
    recommended_fixes: list[RecommendedFix] = []
    missing_evidence: list[str] = []
    next_step: NextStep
    report: Optional[ReportMetadata] = None
    llm_explanation: Optional[str] = None
    provenance: Provenance = Provenance()