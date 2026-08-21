"""workflow 包结果模型（设计文档 6.5 节响应的库级对应物）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from backend.app.generators.archive import BundleResult
from backend.app.schemas.generation import GeneratedFileNode
from backend.app.schemas.recipe import RecipePackManifest
from backend.app.schemas.workflow import (
    FileInheritancePlan,
    RecipeComposition,
    WorkflowPlanFile,
    WorkflowStep,
)


@dataclass
class ValidationResult:
    valid: bool
    recipe_pack_version: str | None = None
    provenance_complete: bool = True
    warnings: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class WorkflowGenerationResult:
    """``WorkflowGenerationPipeline.generate`` 的唯一门面输出。

    WorkflowService 只消费这个对象（见 handoff/be-a/INTEGRATION_REQUEST IR-03）。
    """

    workflow_id: str
    revision: int
    workflow_status: str
    plan_file: WorkflowPlanFile
    steps: List[WorkflowStep]
    file_inheritance_plan: FileInheritancePlan
    compositions: Dict[str, RecipeComposition]
    file_tree: GeneratedFileNode
    validation: ValidationResult
    bundle: BundleResult
    pack: RecipePackManifest | None = None

    def to_response_body(self) -> Dict[str, Any]:
        """对齐 6.5 节 response 的库级 JSON（不含 request_id/download_url）。"""

        return {
            "workflow_id": self.workflow_id,
            "workflow_status": self.workflow_status,
            "revision": self.revision,
            "file_tree": self.file_tree.model_dump(mode="json"),
            "validation": {
                "valid": self.validation.valid,
                "recipe_pack_version": self.validation.recipe_pack_version,
                "provenance_complete": self.validation.provenance_complete,
                "warnings": self.validation.warnings,
            },
            "manifest": self.bundle.manifest.model_dump(mode="json"),
        }
