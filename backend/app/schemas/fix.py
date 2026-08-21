from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict

from .status import FixStatus


class FixChange(BaseModel):
    model_config = ConfigDict(extra="ignore")

    target_file: str
    parameter: Optional[str] = None
    operation: str  # add|replace|remove
    old_value: Optional[str] = None
    new_value: Optional[str] = None


class RecommendedFix(BaseModel):
    """MVP 7.7 修复操作结果。"""

    model_config = ConfigDict(extra="ignore")

    fix_id: str
    issue_ids: list[str] = []
    target_file: str = ""
    strategy: str = "parameter_patch"
    fix_status: FixStatus = FixStatus.PROPOSED
    safe_to_generate: bool = False
    requires_user_confirmation: bool = True
    changes: list[FixChange] = []
    diff: Optional[str] = None
    generated_file_id: Optional[str] = None
    warnings: list[str] = []