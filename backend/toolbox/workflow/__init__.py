"""M8 工序子包：8 步固定顺序 + 任意起止覆盖 + 规划强约束。"""
from .steps import (
    STEP_KEYS,
    STEP_LABELS,
    Coverage,
    coverage,
    coverage_text,
    is_step,
    normalize_step,
    require_step,
    step_label,
    step_after,
    step_before,
    step_index,
)
from .plan import (
    GateResult,
    PlanError,
    SUCCESS_STATUSES,
    fixed_order,
    gate_jobs,
    require_valid_plan,
    validate_plan,
)

__all__ = [
    "STEP_KEYS", "STEP_LABELS",
    "Coverage", "coverage", "coverage_text",
    "is_step", "normalize_step", "require_step",
    "step_label", "step_after", "step_before", "step_index",
    "GateResult", "PlanError", "SUCCESS_STATUSES",
    "fixed_order", "gate_jobs", "require_valid_plan", "validate_plan",
]
