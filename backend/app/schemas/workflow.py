"""BE-A Workflow schema（设计文档 7.2/7.17/7.20/7.22 节与 6.4 节）。

包含 WorkflowPlan/RuntimeDependency/FileInheritancePlan/RecipeComposition 与
workflow_plan.json 文件模型。blocked_by 采用 7.2 节字符串码数组形式。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


# IR-04: reuse the single CheckStatus definition from backend.app.schemas.status.
from backend.app.schemas.status import CheckStatus  # noqa: F401


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- 分层状态枚举（7.22 节，BE-A 侧自持最小集合，见 IR-04） ---


class WorkflowStatus(str, Enum):
    DRAFT = "draft"
    NEEDS_CONFIRMATION = "needs_confirmation"
    PLANNED = "planned"
    GENERATED = "generated"
    READY_TO_DOWNLOAD = "ready_to_download"
    FAILED = "failed"


class RecipeCompositionStatus(str, Enum):
    DRAFT = "draft"
    NEEDS_CONFIRMATION = "needs_confirmation"
    CONFIRMED = "confirmed"
    INVALID = "invalid"


class ConfirmationStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


# --- 阻塞码（门控事实，README/报告/plan 共用） ---

POTCAR_NOT_PREPARED = "POTCAR_NOT_PREPARED"
UPSTREAM_OUTPUT_MISSING = "UPSTREAM_OUTPUT_MISSING"
UPSTREAM_DIAGNOSIS_NOT_PASSED = "UPSTREAM_DIAGNOSIS_NOT_PASSED"
BAND_WORKFLOW_DISABLED = "BAND_WORKFLOW_DISABLED"


class RuntimeDependency(_StrictModel):
    """7.20 节。satisfied 是运行时事实，生成时恒为 false。"""

    dependency_id: str
    dependency_type: str = "runtime_file"
    from_step_id: str
    source_file: str
    to_step_id: str
    target_file: str
    required: bool = True
    satisfied: bool = False
    requires_upstream_diagnosis_pass: bool = True
    validation: Dict[str, Any] = Field(
        default_factory=lambda: {
            "checks": ["SOURCE_EXISTS", "SOURCE_NONEMPTY", "UPSTREAM_NOT_BLOCKING"],
            "passed": False,
        }
    )
    evidence: List[Dict[str, Any]] = Field(default_factory=list)
    blocking_codes: List[str] = Field(
        default_factory=lambda: ["RUNTIME_OUTPUT_MISSING", "UPSTREAM_DIAGNOSIS_BLOCKING"]
    )


class FileInheritancePlan(_StrictModel):
    plan_id: str
    workflow_id: Optional[str] = None
    revision: int = 1
    dependencies: List[RuntimeDependency] = Field(default_factory=list)
    evaluated_at: Optional[str] = None


class WorkflowStep(_StrictModel):
    step_id: str
    task: str
    label: Optional[str] = None
    directory: str
    depends_on: List[str] = Field(default_factory=list)
    runnable: bool = False
    blocked_by: List[str] = Field(default_factory=list)
    requires_runtime_outputs: List[str] = Field(default_factory=list)
    produces: List[str] = Field(default_factory=list)
    parameters: Dict[str, Any] = Field(default_factory=dict)


class WorkflowPlan(_StrictModel):
    schema_version: str = "1.0"
    workflow_id: str
    steps: List[WorkflowStep] = Field(default_factory=list)
    file_inheritance_plan: FileInheritancePlan


class SelectedRecipeEntry(_StrictModel):
    """composition 中的已选 Recipe（7.17 节 selected[]）。"""

    recipe_id: str
    version: str
    layer: str
    order: int
    sha256: Optional[str] = None
    selection_reason: str = ""
    matched_context: Dict[str, Any] = Field(default_factory=dict)


class RecipeConflict(_StrictModel):
    parameter: str
    layer: int
    values: Dict[str, Any]  # recipe_id -> value
    resolution: Optional[str] = None


class PendingConfirmation(_StrictModel):
    key: str
    recipe_id: str
    prompt: str
    required: bool = True


class RecipeComposition(_StrictModel):
    """单 step 的组合结果（7.17 节）。"""

    composition_id: str
    step_id: str
    revision: int = 1
    composition_status: RecipeCompositionStatus = RecipeCompositionStatus.DRAFT
    recipe_pack: Dict[str, Any] = Field(default_factory=dict)
    selected: List[SelectedRecipeEntry] = Field(default_factory=list)
    resolved_parameters: Dict[str, Any] = Field(default_factory=dict)
    derived_outputs: Dict[str, Any] = Field(default_factory=dict)
    provenance: List[Any] = Field(default_factory=list)
    patches: List[Any] = Field(default_factory=list)
    confirmations: List[PendingConfirmation] = Field(default_factory=list)
    conflicts: List[RecipeConflict] = Field(default_factory=list)
    warnings: List[Dict[str, Any]] = Field(default_factory=list)
    composition_sha256: Optional[str] = None


class ConfirmationEntry(_StrictModel):
    key: str
    prompt: str
    confirmation_status: ConfirmationStatus = ConfirmationStatus.PENDING
    confirmed_at: Optional[str] = None


class WarningEntry(_StrictModel):
    code: str
    message: str
    severity: str = "medium"


class StructureBlock(_StrictModel):
    structure_id: Optional[str] = None
    formula: str
    elements: List[str]
    counts: List[int]
    source_sha256: Optional[str] = None


class GoalBlock(_StrictModel):
    original_text: Optional[str] = None
    requested_tasks: List[str] = Field(default_factory=list)


class AssumptionsBlock(_StrictModel):
    electronic_type: str = "unknown"
    magnetic: bool = False
    soc: bool = False
    precision: str = "standard"


class DftuBlock(_StrictModel):
    enabled: bool = False
    entries: List[Dict[str, Any]] = Field(default_factory=list)


class SchedulerBlock(_StrictModel):
    scheduler_profile_id: Optional[str] = None
    scheduler_type: str = "slurm"
    nodes: int = 1
    tasks_per_node: int = 1
    walltime: str = "12:00:00"
    vasp_binary_hint: str = "vasp_std"


class RemoteExecutionBlock(_StrictModel):
    enabled: bool = False
    mode: str = "disabled"
    cluster_profile_id: Optional[str] = None
    deploy_requires_confirmation: bool = True
    submit_requires_confirmation: bool = True
    auto_resubmit: bool = False


class CompositionFileEntry(_StrictModel):
    step_id: str
    composition_id: str
    revision: int
    recipe_pack: Dict[str, Any] = Field(default_factory=dict)
    selected: List[Dict[str, Any]] = Field(default_factory=list)
    patch_ids: List[str] = Field(default_factory=list)
    composition_sha256: Optional[str] = None


class WorkflowPlanFile(_StrictModel):
    """workflow_plan.json（7.2 节稳定字段）。"""

    schema_version: str = "1.0"
    workflow_id: str
    revision: int = 1
    created_at: Optional[str] = None
    structure: StructureBlock
    goal: GoalBlock
    assumptions: AssumptionsBlock = Field(default_factory=AssumptionsBlock)
    dftu: DftuBlock = Field(default_factory=DftuBlock)
    scheduler: SchedulerBlock = Field(default_factory=SchedulerBlock)
    remote_execution: RemoteExecutionBlock = Field(default_factory=RemoteExecutionBlock)
    steps: List[WorkflowStep] = Field(default_factory=list)
    file_inheritance_plan: FileInheritancePlan
    recipe_compositions: List[CompositionFileEntry] = Field(default_factory=list)
    confirmations: List[ConfirmationEntry] = Field(default_factory=list)
    warnings: List[WarningEntry] = Field(default_factory=list)
    template_versions: Dict[str, str] = Field(default_factory=dict)
