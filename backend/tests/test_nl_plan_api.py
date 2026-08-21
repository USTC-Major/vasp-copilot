"""方案 A：LLM 驱动工作流生成（nl_planner 模块 + /workflows/plan_from_nl 端点）。

覆盖：validate_plan 白名单化解析、build_default_plan 降级、
LlmWorkflowPlanner 端到端（带 stub explainer）、plan_from_nl 端点集成。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.v1 import deps
from app.llm.provider import reset_explainer, set_explainer
from app.main import app
from app.schemas.structure import build_structure_summary
from app.workflow.nl_planner import (
    LlmWorkflowPlanner,
    build_default_plan,
    validate_plan,
)

client = TestClient(app)

POSCAR = """Fe2O3
1.0
5.03 0.0 0.0
-2.515 4.356 0.0
0.0 0.0 13.75
Fe O
2 3
Direct
0.0 0.0 0.0
0.5 0.5 0.5
0.3 0.3 0.25
0.7 0.7 0.5
0.1 0.1 0.75
"""


def _seed_structure(diag_id: str = "nl_diag_ok") -> str:
    summary = build_structure_summary(
        poscar_text=POSCAR,
        elements=["Fe", "O"],
        counts=[2, 3],
        source_file="POSCAR",
        structure_id=diag_id_helper(),
    )
    return summary


def diag_id_helper() -> str:
    return "str_nl_seed"


# --- module-level tests -------------------------------------------------

def test_module_validate_plan():
    plan = validate_plan({
        "requested_tasks": ["relax", "dos"],
        "assumptions": {"electronic_type": "metal", "magnetic": True},
        "patches": [{"parameter": "ENCUT", "value": 620, "reason": "tune"}],
        "step_explanations": [{"step": "01_relax", "label": "relax", "explanation": "结构优化"}],
        "user_needs": "plan me",
    })
    assert plan is not None
    assert plan.requested_tasks == ["relax", "dos"]
    assert plan.assumptions["electronic_type"] == "metal"
    assert len(plan.patches) == 1
    assert plan.patches[0].parameter == "ENCUT"
    assert plan.patches[0].value == 620
    assert plan.step_explanations[0]["explanation"] == "结构优化"


def test_module_validate_plan_rejects_bad():
    bad = validate_plan({"requested_tasks": ["evil"]})
    assert bad is not None  # falls back to defaults but stays usable
    assert bad.requested_tasks == ["relax", "static", "dos"]
    assert validate_plan(None) is None
    assert validate_plan(42) is None


def test_module_build_default_plan():
    plan = build_default_plan({"transition_metals": ["Fe"]}, "default")
    assert plan.requested_tasks == ["relax", "static", "dos"]
    assert plan.assumptions["magnetic"] is True
    assert plan.step_explanations


class StubExplainer:
    def __init__(self, payload: str):
        self._payload = payload

    def complete(self, messages):
        return self._payload


def test_module_planner_with_stub():
    stub = StubExplainer(
        '{"requested_tasks": ["relax"], "assumptions": {"electronic_type": "semiconductor", "soc": true}, '
        '"patches": [], "step_explanations": [{"step": "01_relax", "label": "relax", '
        '"explanation": "半导体体系结构优化"}], "user_needs": "只做结构优化"}'
    )
    planner = LlmWorkflowPlanner(explainer=stub, max_retries=1)
    plan = planner.plan(
        {"formula": "Fe2O3", "elements": ["Fe", "O"], "counts": [2, 3]},
        "只做结构优化",
    )
    assert plan is not None
    assert plan.requested_tasks == ["relax"]
    assert plan.assumptions["electronic_type"] == "semiconductor"
    assert plan.assumptions["soc"] is True


def test_module_planner_degrades_on_error():
    class Boom:
        def complete(self, messages):
            raise RuntimeError("boom")

    planner = LlmWorkflowPlanner(explainer=Boom(), max_retries=1)
    assert planner.plan({}, "hi") is None


# --- endpoint tests -----------------------------------------------------

def _seed_store(diag_id: str) -> str:
    summary = build_structure_summary(
        poscar_text=POSCAR,
        elements=["Fe", "O"],
        counts=[2, 3],
        source_file="POSCAR",
        structure_id=diag_id,
    )
    rec = deps.file_store.store_structure(file_id="file_nl", summary=summary)
    return rec.structure_id


def test_endpoint_plan_from_nl_degraded_without_llm():
    structure_id = _seed_store("str_nl_degraded")
    r = client.post(
        "/api/v1/workflows/plan_from_nl",
        json={"structure_id": structure_id, "goals": ["结构优化"]},
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["workflow_id"]
    assert data["ai"]["degraded"] is True
    assert data["ai"]["requested_tasks"] == ["relax", "static", "dos"]
    assert [s["task"] for s in data["steps"]] == ["relax", "static", "dos"]
    assert data["workflow_status"] == data["status"]


def test_endpoint_plan_from_nl_with_stub_llm():
    structure_id = _seed_store("str_nl_llm")
    stub = StubExplainer(
        '{"requested_tasks": ["relax", "dos"], "assumptions": {"electronic_type": "metal", "magnetic": true}, '
        '"patches": [{"parameter": "ENCUT", "value": 620, "reason": "要求更高精度"}], '
        '"step_explanations": [{"step": "01_relax", "label": "relax", "explanation": "铁氧化物做结构优化"}, '
        '{"step": "03_dos", "label": "dos", "explanation": "铁氧化物计算态密度"}], '
        '"user_needs": "对铁氧化物做结构优化，再算态密度"}'
    )
    set_explainer(stub)
    try:
        r = client.post(
            "/api/v1/workflows/plan_from_nl",
            json={"structure_id": structure_id, "goals": ["对铁氧化物做结构优化，再算态密度"]},
        )
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["ai"]["degraded"] is False
        assert data["ai"]["enabled"] is True
        assert data["ai"]["requested_tasks"] == ["relax", "dos"]
        assert len(data["ai"]["explanations"]) == 2
        assert [s["task"] for s in data["steps"]] == ["relax", "dos"]
    finally:
        reset_explainer()


def test_endpoint_plan_from_nl_missing_structure_409():
    r = client.post("/api/v1/workflows/plan_from_nl", json={"structure_id": "", "goals": ["x"]})
    assert r.status_code in (404, 409)