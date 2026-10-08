"""StepGatingEvaluator（设计文档 4.1 节第 8 步、7.2/7.20 节）。

门控事实（生成时刻）：
- 无 POTCAR（ENABLE_POTCAR_ASSEMBLY=false，系统不拼接赝势）→ 所有 step
  ``blocked_by`` 含 ``POTCAR_NOT_PREPARED``；
- 有 ``requires_runtime_outputs`` 的 step 还带 ``UPSTREAM_OUTPUT_MISSING`` 与
  ``UPSTREAM_DIAGNOSIS_NOT_PASSED``（运行时产物尚未产生，上游诊断未执行）；
- 因此生成时所有 step ``runnable=false``；目录生成成功 ≠ 可提交。

blocked_by 为 7.2 节字符串码数组；README/INPUT_CHECK_REPORT/workflow_plan.json
必须共用同一 FileInheritancePlan 求值结果，禁止各自维护规则。
"""

from __future__ import annotations

from typing import Dict, List

from backend.app.schemas.workflow import (
    POTCAR_NOT_PREPARED,
    UPSTREAM_DIAGNOSIS_NOT_PASSED,
    UPSTREAM_OUTPUT_MISSING,
    FileInheritancePlan,
    WorkflowStep,
)


class StepGatingEvaluator:
    def __init__(self, potcar_prepared: bool = False) -> None:
        # Kept for old callers; only this generation's prepared_steps is proof.
        pass

    def evaluate(
        self, steps: List[WorkflowStep], plan: FileInheritancePlan,
        prepared_steps: set[str] | None = None,
    ) -> Dict[str, List[str]]:
        """Only actual validated artifacts may mark individual steps prepared."""

        dependency_by_target = {dep.to_step_id: dep for dep in plan.dependencies}
        result: Dict[str, List[str]] = {}
        for step in steps:
            codes: List[str] = []
            if step.step_id not in (prepared_steps or set()):
                codes.append(POTCAR_NOT_PREPARED)
            if step.requires_runtime_outputs:
                codes.append(UPSTREAM_OUTPUT_MISSING)
                dependency = dependency_by_target.get(step.step_id)
                if dependency is not None and dependency.requires_upstream_diagnosis_pass:
                    codes.append(UPSTREAM_DIAGNOSIS_NOT_PASSED)
            step.blocked_by = codes
            step.runnable = not codes
            result[step.step_id] = codes
        return result
