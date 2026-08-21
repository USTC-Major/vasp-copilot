"""BE-A Recipe schema（设计文档 7.16/7.17/10.4 节）。

schema-first：Recipe 只能是严格 schema 数据。所有模型 ``extra="forbid"``，
对未知字段与非法表达式 fail closed。派生函数名只能引用注册表白名单。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class RecipeKind(str, Enum):
    BASE = "base"
    TASK = "task"
    ELECTRONIC_TYPE = "electronic_type"
    MODIFIER = "modifier"
    PRECISION = "precision"


class RecipeStatus(str, Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    DEPRECATED = "deprecated"


class LayerOrder(int, Enum):
    """固定合并层级（7.17/10.3 节）。数值即合并顺序，越小越先合并。"""

    BASE = 10
    TASK = 20
    ELECTRONIC_TYPE = 30
    MODIFIER = 40
    PRECISION = 50
    USER_PATCH = 60


KIND_TO_LAYER: Dict[RecipeKind, LayerOrder] = {
    RecipeKind.BASE: LayerOrder.BASE,
    RecipeKind.TASK: LayerOrder.TASK,
    RecipeKind.ELECTRONIC_TYPE: LayerOrder.ELECTRONIC_TYPE,
    RecipeKind.MODIFIER: LayerOrder.MODIFIER,
    RecipeKind.PRECISION: LayerOrder.PRECISION,
}


class TaskType(str, Enum):
    RELAX = "relax"
    STATIC = "static"
    DOS = "dos"
    BAND = "band"


class ElectronicType(str, Enum):
    METAL = "metal"
    SEMICONDUCTOR = "semiconductor"
    UNKNOWN = "unknown"


class PrecisionLevel(str, Enum):
    QUICK = "quick"
    STANDARD = "standard"
    HIGH = "high"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RecipeScope(_StrictModel):
    tasks: List[TaskType] = Field(default_factory=list)
    electronic_types: List[ElectronicType] = Field(default_factory=list)
    vasp_versions: List[str] = Field(default_factory=list)


class RecipeWarning(_StrictModel):
    code: str
    severity: str = "medium"
    message: Optional[str] = None


class RecipeConfirmation(_StrictModel):
    key: str
    required: bool = True
    prompt: str


class DerivedParameterRef(_StrictModel):
    """引用白名单派生函数（10.4 节）。不允许任意代码文本。"""

    function: str
    parameter: str
    target: str = "INCAR"  # INCAR | KPOINTS
    inputs: List[str] = Field(default_factory=list)

    @field_validator("function")
    @classmethod
    def _function_is_identifier(cls, value: str) -> str:
        if not value.isidentifier():
            raise ValueError(f"derived function must be a plain identifier: {value!r}")
        return value


class AllowedOverrideRule(_StrictModel):
    type: str  # number | integer | boolean | string
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    exclusive_minimum: Optional[float] = None
    exclusive_maximum: Optional[float] = None
    enum: Optional[List[Any]] = None
    removable: bool = False


class RecipeProvenanceMeta(_StrictModel):
    source_type: str = "project_curated"
    source_note: Optional[str] = None
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[str] = None


class RecipeTests(_StrictModel):
    test_status: str = "not_run"
    case_count: int = 0
    last_tested_at: Optional[str] = None


class RecipeManifest(_StrictModel):
    """单个 Recipe 的完整声明（7.16 节）。"""

    schema_version: str = "1.0"
    recipe_id: str
    version: str
    kind: RecipeKind
    recipe_status: RecipeStatus = RecipeStatus.DRAFT
    display_name: Optional[str] = None
    description: Optional[str] = None
    scope: RecipeScope = Field(default_factory=RecipeScope)
    parameters: Dict[str, Any] = Field(default_factory=dict)
    derived_parameters: List[DerivedParameterRef] = Field(default_factory=list)
    requires: List[str] = Field(default_factory=list)
    conflicts: List[str] = Field(default_factory=list)
    confirmations: List[RecipeConfirmation] = Field(default_factory=list)
    warnings: List[RecipeWarning] = Field(default_factory=list)
    allowed_overrides: Dict[str, AllowedOverrideRule] = Field(default_factory=dict)
    provenance: RecipeProvenanceMeta = Field(default_factory=RecipeProvenanceMeta)
    tests: RecipeTests = Field(default_factory=RecipeTests)
    sha256: Optional[str] = None  # loader 计算填充

    @property
    def layer(self) -> LayerOrder:
        return KIND_TO_LAYER[self.kind]

    @model_validator(mode="after")
    def _published_requirements(self) -> "RecipeManifest":
        if self.recipe_status == RecipeStatus.PUBLISHED:
            if not self.recipe_id or not self.version:
                raise ValueError("published recipe requires stable id and version")
            if self.tests.test_status != "passed":
                raise ValueError(
                    f"published recipe {self.recipe_id} must have tests.test_status=passed"
                )
        return self


class RecipePackManifest(_StrictModel):
    pack_id: str
    version: str
    display_name: Optional[str] = None
    schema_version: str = "1.0"
    sha256: Optional[str] = None


class RecipeRef(_StrictModel):
    """选择器/组合器之间的稳定引用。"""

    recipe_id: str
    version: str

    @property
    def key(self) -> str:
        return f"{self.recipe_id}@{self.version}"


class SelectionContext(_StrictModel):
    """RecipeSelector 的确定性输入（LLM 只能提供这些枚举）。"""

    task: TaskType
    electronic_type: ElectronicType = ElectronicType.UNKNOWN
    precision: PrecisionLevel = PrecisionLevel.STANDARD
    magnetic: bool = False
    dftu: bool = False
    elements: List[str] = Field(default_factory=list)
