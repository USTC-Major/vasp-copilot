"""BE-A workflow 包：planner / gating / pipeline（设计文档 4.1/6.5/7.2 节）。

对外唯一门面：``WorkflowGenerationPipeline.generate(request)``。
"""

from backend.app.workflow.gating import StepGatingEvaluator
from backend.app.workflow.models import ValidationResult, WorkflowGenerationResult
from backend.app.workflow.pipeline import WorkflowGenerationPipeline
from backend.app.workflow.planner import WorkflowPlanner

__all__ = [
    "StepGatingEvaluator",
    "ValidationResult",
    "WorkflowGenerationPipeline",
    "WorkflowGenerationResult",
    "WorkflowPlanner",
]
