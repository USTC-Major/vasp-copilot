"""BE-A 生成相关 schema（设计文档 7.4/7.18/7.19/7.24 节与 6.4/6.5 节）。

包含 typed ParameterPatch、ParameterProvenance、最小结构输入 StructureContext、
DFT+U、scheduler profile、GeneratedFileTree、InputCheckReportMetadata、
bundle manifest 以及库级请求 ``WorkflowGenerateRequest``。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.app.schemas.recipe import (
    ElectronicType,
    PrecisionLevel,
    TaskType,
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PatchOperation(str, Enum):
    ADD = "add"
    REPLACE = "replace"
    REMOVE = "remove"


class ProvenanceSourceType(str, Enum):
    RECIPE = "recipe"
    DERIVED_FUNCTION = "derived_function"
    USER_PATCH = "user_patch"
    RULE_FIX = "rule_fix"
    SCHEDULER_PROFILE = "scheduler_profile"


class PatchValidationResult(_StrictModel):
    allowed: bool
    rule_ids: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class ParameterPatch(_StrictModel):
    """typed patch（7.18 节）。operation 枚举固定为 add/replace/remove。"""

    patch_id: str
    composition_id: Optional[str] = None
    expected_revision: int = 1
    step_id: Optional[str] = None
    parameter: str
    operation: PatchOperation
    value: Optional[Any] = None
    source: str = "user_confirmed"
    reason: Optional[str] = None
    confirmed_by_user: bool = False
    validation: Optional[PatchValidationResult] = None

    @field_validator("parameter")
    @classmethod
    def _parameter_is_tag(cls, value: str) -> str:
        if not value or " " in value or "=" in value or "\n" in value:
            raise ValueError(f"parameter must be a single INCAR tag: {value!r}")
        return value.upper()

    @model_validator(mode="after")
    def _value_required(self) -> "ParameterPatch":
        if self.operation in (PatchOperation.ADD, PatchOperation.REPLACE) and self.value is None:
            raise ValueError(f"{self.operation.value} patch requires a value")
        return self


class ParameterProvenance(_StrictModel):
    """每个最终参数的来源链（7.19 节）。"""

    parameter: str
    value: Any
    source_type: ProvenanceSourceType
    source_id: str
    source_revision: Optional[str] = None
    overrode: Optional[Dict[str, Any]] = None
    derived_by: Optional[str] = None
    requires_confirmation: bool = False
    confirmed: bool = False


class LatticeInfo(_StrictModel):
    matrix: List[List[float]] = Field(default_factory=list)
    a: Optional[float] = None
    b: Optional[float] = None
    c: Optional[float] = None
    alpha: Optional[float] = None
    beta: Optional[float] = None
    gamma: Optional[float] = None
    volume: Optional[float] = None


class StructureContext(_StrictModel):
    """BE-A 最小结构输入契约（R3 / IR-05）。

    由结构解析模块的 StructureSummary 映射而来，字段为其子集。
    """

    structure_id: Optional[str] = None
    formula: str
    elements: List[str]
    counts: List[int]
    atom_count: Optional[int] = None
    lattice: Optional[LatticeInfo] = None
    coordinate_mode: str = "direct"
    poscar_text: Optional[str] = None
    source_sha256: Optional[str] = None
    transition_metals: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> "StructureContext":
        if len(self.elements) != len(self.counts):
            raise ValueError("elements and counts length mismatch")
        total = sum(self.counts)
        if self.atom_count is None:
            self.atom_count = total
        elif self.atom_count != total:
            raise ValueError("atom_count inconsistent with counts")
        return self


class DftuEntry(_StrictModel):
    element: str
    l: int
    u_ev: float
    j_ev: float = 0.0
    source_note: Optional[str] = None
    confirmed_by_user: bool = False


class DftuSettings(_StrictModel):
    enabled: bool = False
    entries: List[DftuEntry] = Field(default_factory=list)

    @property
    def all_confirmed(self) -> bool:
        return all(e.confirmed_by_user for e in self.entries)


class MaterialAssumptions(_StrictModel):
    electronic_type: ElectronicType = ElectronicType.UNKNOWN
    magnetic: bool = False
    soc: bool = False
    precision: PrecisionLevel = PrecisionLevel.STANDARD


class SchedulerSettings(_StrictModel):
    """用户可编辑的资源字段（6.4 节 scheduler）。"""

    type: str = "slurm"  # slurm | cbatch | generic
    nodes: int = 1
    tasks_per_node: int = 1
    walltime: str = "12:00:00"
    partition: Optional[str] = None
    account: Optional[str] = None
    job_name: Optional[str] = None
    vasp_binary_hint: str = "vasp_std"
    module_loads: List[str] = Field(default_factory=list)
    parallel_defaults: Dict[str, int] = Field(default_factory=dict)


class SchedulerProfile(_StrictModel):
    """配置化 scheduler（7.10 节），生成阶段只渲染模板不执行命令。"""

    scheduler_type: str
    script_template: str  # templates/scheduler 下的文件名
    launcher_prefix: str = ""  # 例如 "srun" / "mpirun"，来自管理员 profile
    submit_command_hint: Optional[str] = None  # 仅作展示，不执行
    allow_user_command_override: bool = False


class KpointsSpec(_StrictModel):
    mode: str = "automatic_density"  # automatic_density | line_mode
    kppa: Optional[float] = None
    grid: Optional[List[int]] = None
    centering: Optional[str] = None  # Gamma | Monkhorst
    line_density: Optional[int] = None


class GeneratedFileNode(_StrictModel):
    """generated_file_tree 节点（7.4 节）。"""

    name: str
    type: str  # directory | file
    relative_path: str = "."
    children: List["GeneratedFileNode"] = Field(default_factory=list)
    file_id: Optional[str] = None
    mime_type: Optional[str] = None
    size_bytes: Optional[int] = None
    sha256: Optional[str] = None
    preview_available: Optional[bool] = None
    generated_by: Optional[str] = None


GeneratedFileNode.model_rebuild()


class InputCheckItem(_StrictModel):
    check_id: str
    section: str
    status: str = "passed"  # passed | warning | failed
    message: str


class InputCheckReportMetadata(_StrictModel):
    report_id: str
    workflow_id: str
    workflow_revision: int
    format: str = "markdown"
    ready: bool = True
    sections: List[str] = Field(default_factory=list)
    check_summary: Dict[str, int] = Field(default_factory=dict)
    generated_at: Optional[str] = None
    generator_version: str = "0.1.0"


class WorkflowBundleManifest(_StrictModel):
    """immutable revision / manifest / hash（任务书 17）。"""

    workflow_id: str
    revision: int = 1
    bundle_sha256: str
    recipe_pack_version: Optional[str] = None
    recipe_pack_sha256: Optional[str] = None
    files: List[Dict[str, Any]] = Field(default_factory=list)
    created_at: Optional[str] = None
    generator_version: str = "0.1.0"


class WorkflowGenerateRequest(_StrictModel):
    """库级请求，对齐 6.4/6.5 节字段子集。"""

    workflow_id: str = "wf_local"
    structure: StructureContext
    requested_tasks: List[TaskType] = Field(default_factory=lambda: [TaskType.RELAX])
    goal_text: Optional[str] = None
    material_assumptions: MaterialAssumptions = Field(default_factory=MaterialAssumptions)
    precision: PrecisionLevel = PrecisionLevel.STANDARD
    dftu: DftuSettings = Field(default_factory=DftuSettings)
    scheduler: SchedulerSettings = Field(default_factory=SchedulerSettings)
    patches: List[ParameterPatch] = Field(default_factory=list)
    element_initial_moments: Dict[str, float] = Field(default_factory=dict)
    enable_band_workflow: bool = False
    confirm: bool = True
