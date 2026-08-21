"""WorkflowPlanner（设计文档 4.1 节第 8 步、6.4/7.2/7.20 节）。

任务列表 → 有序 step DAG + FileInheritancePlan。
MVP 固定继承语义（7.20 节）：
- relax/CONTCAR → static/POSCAR
- static/CHGCAR → dos/CHGCAR
- static/CHGCAR → band/CHGCAR（band 需 ENABLE_BAND_WORKFLOW）

planner 只产出计划结构；门控求值在 ``gating.py``（README/报告/plan 共用同一 plan）。
"""

from __future__ import annotations

from typing import Dict, List, Sequence

from backend.app.recipes.errors import BeAError
from backend.app.recipes.errors import BAND_WORKFLOW_DISABLED
from backend.app.schemas.recipe import TaskType
from backend.app.schemas.workflow import FileInheritancePlan, RuntimeDependency, WorkflowStep

TASK_ORDER = {
    TaskType.RELAX: 0,
    TaskType.STATIC: 1,
    TaskType.DOS: 2,
    TaskType.BAND: 3,
}

TASK_DIRECTORIES = {
    TaskType.RELAX: "01_relax",
    TaskType.STATIC: "02_static",
    TaskType.DOS: "03_dos",
    TaskType.BAND: "04_band",
}

TASK_PRODUCES = {
    TaskType.RELAX: ["CONTCAR", "OUTCAR", "OSZICAR"],
    TaskType.STATIC: ["CHGCAR", "WAVECAR", "OUTCAR", "OSZICAR"],
    TaskType.DOS: ["DOSCAR", "OUTCAR"],
    TaskType.BAND: ["EIGENVAL", "OUTCAR"],
}

# (上游任务, 源文件, 下游任务, 目标文件)
INHERITANCE_EDGES = (
    (TaskType.RELAX, "CONTCAR", TaskType.STATIC, "POSCAR"),
    (TaskType.STATIC, "CHGCAR", TaskType.DOS, "CHGCAR"),
    (TaskType.STATIC, "CHGCAR", TaskType.BAND, "CHGCAR"),
)


class WorkflowPlanner:
    def plan(
        self,
        workflow_id: str,
        requested_tasks: Sequence[TaskType],
        enable_band_workflow: bool = False,
        revision: int = 1,
    ) -> Dict[str, object]:
        tasks = self._normalize_tasks(requested_tasks, enable_band_workflow)
        steps: List[WorkflowStep] = []
        step_by_task: Dict[TaskType, WorkflowStep] = {}
        for task in tasks:
            step = WorkflowStep(
                step_id=TASK_DIRECTORIES[task],
                task=task.value,
                label=f"{task.value} step",
                directory=TASK_DIRECTORIES[task],
                produces=list(TASK_PRODUCES[task]),
            )
            step_by_task[task] = step
            steps.append(step)

        dependencies: List[RuntimeDependency] = []
        for source_task, source_file, target_task, target_file in INHERITANCE_EDGES:
            if source_task not in step_by_task or target_task not in step_by_task:
                continue
            dependency_id = (
                f"dep_{source_task.value}_{source_file.lower()}"
            )
            dependencies.append(
                RuntimeDependency(
                    dependency_id=dependency_id,
                    from_step_id=TASK_DIRECTORIES[source_task],
                    source_file=source_file,
                    to_step_id=TASK_DIRECTORIES[target_task],
                    target_file=target_file,
                )
            )
            target_step = step_by_task[target_task]
            if TASK_DIRECTORIES[source_task] not in target_step.depends_on:
                target_step.depends_on.append(TASK_DIRECTORIES[source_task])
            source_path = f"{TASK_DIRECTORIES[source_task]}/{source_file}"
            if source_path not in target_step.requires_runtime_outputs:
                target_step.requires_runtime_outputs.append(source_path)

        plan = FileInheritancePlan(
            plan_id=f"fip_{workflow_id}",
            workflow_id=workflow_id,
            revision=revision,
            dependencies=dependencies,
        )
        return {"steps": steps, "file_inheritance_plan": plan}

    def _normalize_tasks(
        self, requested_tasks: Sequence[TaskType], enable_band_workflow: bool
    ) -> List[TaskType]:
        if not requested_tasks:
            raise BeAError(
                "requested_tasks must not be empty",
                code="RECIPE_SCOPE_MISMATCH",
                details={"requested_tasks": []},
            )
        seen = set()
        unique: List[TaskType] = []
        for task in requested_tasks:
            if task in seen:
                continue
            seen.add(task)
            unique.append(task)
        if TaskType.BAND in unique and not enable_band_workflow:
            raise BeAError(
                "band workflow requested but ENABLE_BAND_WORKFLOW is false",
                code=BAND_WORKFLOW_DISABLED,
                details={"flag": "ENABLE_BAND_WORKFLOW"},
            )
        return sorted(unique, key=lambda task: TASK_ORDER[task])
