"""验收 11/12：WorkflowPlanner DAG、runnable/blocked_by 门控、band flag。"""

import pytest

from backend.app.recipes.errors import BeAError
from backend.app.schemas.recipe import TaskType
from backend.app.schemas.workflow import (
    POTCAR_NOT_PREPARED,
    UPSTREAM_DIAGNOSIS_NOT_PASSED,
    UPSTREAM_OUTPUT_MISSING,
)
from backend.app.workflow.gating import StepGatingEvaluator
from backend.app.workflow.planner import WorkflowPlanner


class TestPlanner:
    def test_relax_static_dos_chain(self):
        planned = WorkflowPlanner().plan("wf_01", [TaskType.RELAX, TaskType.STATIC, TaskType.DOS])
        steps = planned["steps"]
        assert [step.step_id for step in steps] == ["01_relax", "02_static", "03_dos"]
        assert steps[0].depends_on == []
        assert steps[1].depends_on == ["01_relax"]
        assert steps[2].depends_on == ["02_static"]

    def test_task_order_always_sorted(self):
        planned = WorkflowPlanner().plan(
            "wf_01", [TaskType.DOS, TaskType.RELAX, TaskType.STATIC]
        )
        assert [step.task for step in planned["steps"]] == ["relax", "static", "dos"]

    def test_inheritance_edges(self):
        planned = WorkflowPlanner().plan("wf_01", [TaskType.RELAX, TaskType.STATIC, TaskType.DOS])
        dependencies = planned["file_inheritance_plan"].dependencies
        edges = [
            (dep.from_step_id, dep.source_file, dep.to_step_id, dep.target_file)
            for dep in dependencies
        ]
        assert ("01_relax", "CONTCAR", "02_static", "POSCAR") in edges
        assert ("02_static", "CHGCAR", "03_dos", "CHGCAR") in edges

    def test_dependencies_satisfied_false_at_generation(self):
        planned = WorkflowPlanner().plan("wf_01", [TaskType.RELAX, TaskType.STATIC])
        for dep in planned["file_inheritance_plan"].dependencies:
            assert dep.satisfied is False
            assert dep.validation["passed"] is False

    def test_requires_runtime_outputs_paths(self):
        planned = WorkflowPlanner().plan("wf_01", [TaskType.RELAX, TaskType.STATIC, TaskType.DOS])
        steps = {step.step_id: step for step in planned["steps"]}
        assert steps["01_relax"].requires_runtime_outputs == []
        assert steps["02_static"].requires_runtime_outputs == ["01_relax/CONTCAR"]
        assert steps["03_dos"].requires_runtime_outputs == ["02_static/CHGCAR"]

    def test_band_requires_flag(self):
        with pytest.raises(BeAError) as excinfo:
            WorkflowPlanner().plan("wf_01", [TaskType.RELAX, TaskType.BAND])
        assert excinfo.value.code == "BAND_WORKFLOW_DISABLED"

    def test_band_enabled_by_flag(self):
        planned = WorkflowPlanner().plan(
            "wf_01",
            [TaskType.RELAX, TaskType.STATIC, TaskType.BAND],
            enable_band_workflow=True,
        )
        steps = planned["steps"]
        assert steps[-1].step_id == "04_band"
        assert steps[-1].requires_runtime_outputs == ["02_static/CHGCAR"]

    def test_empty_tasks_rejected(self):
        with pytest.raises(BeAError):
            WorkflowPlanner().plan("wf_01", [])


class TestGating:
    def test_no_potcar_blocks_all_steps(self):
        planned = WorkflowPlanner().plan("wf_01", [TaskType.RELAX, TaskType.STATIC, TaskType.DOS])
        evaluator = StepGatingEvaluator(potcar_prepared=False)
        evaluator.evaluate(planned["steps"], planned["file_inheritance_plan"])
        for step in planned["steps"]:
            assert step.runnable is False
            assert POTCAR_NOT_PREPARED in step.blocked_by

    def test_relax_blocked_only_by_potcar(self):
        planned = WorkflowPlanner().plan("wf_01", [TaskType.RELAX, TaskType.STATIC])
        StepGatingEvaluator().evaluate(planned["steps"], planned["file_inheritance_plan"])
        relax = planned["steps"][0]
        assert relax.blocked_by == [POTCAR_NOT_PREPARED]

    def test_static_blocked_by_upstream_codes(self):
        planned = WorkflowPlanner().plan("wf_01", [TaskType.RELAX, TaskType.STATIC])
        StepGatingEvaluator().evaluate(planned["steps"], planned["file_inheritance_plan"])
        static = planned["steps"][1]
        assert UPSTREAM_OUTPUT_MISSING in static.blocked_by
        assert UPSTREAM_DIAGNOSIS_NOT_PASSED in static.blocked_by

    def test_potcar_prepared_keeps_upstream_gates(self):
        planned = WorkflowPlanner().plan("wf_01", [TaskType.RELAX, TaskType.STATIC])
        StepGatingEvaluator(potcar_prepared=True).evaluate(
            planned["steps"], planned["file_inheritance_plan"]
        )
        relax, static = planned["steps"]
        assert relax.runnable is True
        assert static.runnable is False
        assert POTCAR_NOT_PREPARED not in static.blocked_by
