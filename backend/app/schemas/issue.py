from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict

from .status import Severity


class Evidence(BaseModel):
    model_config = ConfigDict(extra="ignore")

    file: str
    line: Optional[int] = None
    message: str = ""
    data_ref: Optional[str] = None
    excerpt: Optional[str] = None


class Recommendation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    action: str  # set_parameter|add_parameter|remove_parameter|review
    target: str  # INCAR|KPOINTS|submit|manifest|user
    parameter: Optional[str] = None
    new_value: Optional[str] = None
    rationale: str = ""
    requires_user_confirmation: bool = True


class Issue(BaseModel):
    """前端稳定契约（MVP 7.6）。"""

    model_config = ConfigDict(extra="ignore")

    issue_id: str
    rule_id: str
    severity: Severity
    category: str = ""
    title: str = ""
    summary: str = ""
    evidence: list[Evidence] = []
    recommendations: list[Recommendation] = []
    auto_fixable: bool = False
    confidence: float = 0.0
    blocking: bool = False
    possible_causes: list[str] = []
    data_ref: Optional[str] = None
    related_issue_ids: list[str] = []
    root_cause_candidate: Optional[str] = None