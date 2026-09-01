"""M8 工序 8 步与任意起止 + 规划强约束测试（纯内存，不碰网络）。"""

import pytest

from ai_mode.schemas import PlanSnapshot, PlanStep
from ai_mode.workflow import (
    Coverage,
    PlanError,
    STEP_KEYS,
    STEP_LABELS,
    coverage,
    coverage_text,
    fixed_order,
    gate_jobs,
    is_step,
    normalize_step,
    require_step,
    step_after,
    step_before,
    step_index,
    step_label,
    validate_plan,
)


# ---------------- 步骤目录与任意起止 ----------------
def test_eight_steps_fixed_order():
    assert STEP_KEYS == [
        "understand", "plan", "prepare_input", "setup",
        "precheck", "submit_monitor", "finish", "report",
    ]


def test_aliases_and_positions():
    assert normalize_step("理解需求") == "understand"
    assert normalize_step("plan") == "plan"
    assert normalize_step("PLAN") == "plan"
    assert normalize_step("prepare-input") == "prepare_input"
    assert normalize_step(1) == "understand"
    assert normalize_step(8) == "report"
    assert normalize_step("提交前检查") == "precheck"
    assert normalize_step("监控") == "submit_monitor"
    assert normalize_step("结束确认") == "finish"


def test_unknown_step_returns_default_or_raises():
    assert normalize_step("nope") is None
    assert normalize_step("nope", default="understand") == "understand"
    assert is_step("report") and not is_step("bogus")
    with pytest.raises(ValueError):
        require_step("bogus")
    assert require_step("3") == "prepare_input"


def test_coverage_full_and_segment():
    full = coverage("understand", "report")
    assert full.full and len(full.steps) == 8
    seg = coverage("plan", "report")
    assert not seg.full
    assert seg.steps == ["plan", "prepare_input", "setup", "precheck",
                         "submit_monitor", "finish", "report"]
    assert seg.labels[0] == "规划作业" and seg.labels[-1] == "结果与报告"
    assert "(" not in seg.text and "7" in seg.text
    assert str(seg) == seg.text
    assert coverage_text("understand", "report") == str(coverage("understand", "report"))


def test_coverage_reversed_raises():
    with pytest.raises(ValueError):
        coverage("report", "plan")


def test_coverage_unknown_raises():
    with pytest.raises(ValueError):
        coverage("bogus", "report")


def test_step_before_after():
    assert step_after("understand") == "plan"
    assert step_before("report") == "finish"
    assert step_after("report") is None
    assert step_before("understand") is None
    assert step_index("precheck") == 4


def test_step_label_unknown():
    assert step_label("ghost") == "ghost"


# ---------------- 规划强约束 ----------------
def _plan(steps):
    return PlanSnapshot(steps=[PlanStep(**kw) for kw in steps])


def test_validate_ok():
    p = _plan([
        {"job_key": "r1", "label": "relax", "requires": []},
        {"job_key": "r2", "label": "relax-alt", "requires": []},
        {"job_key": "dos", "label": "dos", "requires": ["r1", "r2"]},
    ])
    assert validate_plan(p) == []


def test_validate_unknown_prereq():
    p = _plan([
        {"job_key": "dos", "label": "dos", "requires": ["ghost"]},
    ])
    issues = validate_plan(p)
    assert any("未知前置" in i and "ghost" in i for i in issues)


def test_validate_self_and_duplicate():
    p = _plan([
        {"job_key": "x", "label": "x", "requires": ["x"]},
        {"job_key": "x", "label": "dup", "requires": []},
    ])
    issues = validate_plan(p)
    assert any("自依赖" in i for i in issues)
    assert any("重复作业键" in i for i in issues)


def test_validate_cycle():
    p = _plan([
        {"job_key": "a", "label": "", "requires": ["b"]},
        {"job_key": "b", "label": "", "requires": ["a"]},
    ])
    issues = validate_plan(p)
    assert any("成环" in i for i in issues)
    with pytest.raises(PlanError):
        fixed_order(p)


def test_fixed_order_respects_prereq():
    p = _plan([
        {"job_key": "dos", "label": "", "requires": ["r1", "r2"]},
        {"job_key": "r1", "label": "", "requires": []},
        {"job_key": "r2", "label": "", "requires": []},
    ])
    order = fixed_order(p)
    assert order.index("r1") < order.index("dos")
    assert order.index("r2") < order.index("dos")
    assert set(order) == {"r1", "r2", "dos"}


def test_gate_basic_sequential():
    p = _plan([
        {"job_key": "r1", "label": "", "requires": []},
        {"job_key": "dos", "label": "", "requires": ["r1"]},
    ])
    assert [k for k in gate_jobs(p, {}).eligible] == ["r1"]     # r1 空前置
    res = gate_jobs(p, {"r1": "completed"})
    assert res.eligible == ["dos"]                              # dos 前置成功
    assert res.blocked == {}


def test_gate_rejects_early_sequential():
    p = _plan([
        {"job_key": "r1", "label": ""},
        {"job_key": "dos", "label": "", "requires": ["r1"]},
    ])
    res = gate_jobs(p, {"r1": "running"})
    assert res.eligible == []
    assert res.ignored == ["r1"]
    assert "等待前置 r1 成功（当前 running）" in res.blocked["dos"]


def test_gate_rejects_failed_prereq():
    p = _plan([
        {"job_key": "r1", "label": ""},
        {"job_key": "dos", "label": "", "requires": ["r1"]},
    ])
    res = gate_jobs(p, {"r1": "failed"})
    assert res.eligible == []
    assert "前置 r1 失败（failed），禁止提前提交" in res.blocked["dos"]


def test_gate_parallel_ok():
    p = _plan([
        {"job_key": "r1", "label": "", "parallel_group": "root"},
        {"job_key": "r2", "label": "", "parallel_group": "root"},
    ])
    res = gate_jobs(p, {})
    assert set(res.eligible) == {"r1", "r2"}


def test_gate_mixed():
    p = _plan([
        {"job_key": "r1", "label": ""},
        {"job_key": "r2", "label": "", "parallel_group": "p"},
        {"job_key": "dos", "label": "", "requires": ["r1", "r2"]},
    ])
    res = gate_jobs(p, {"r1": "completed"})
    assert "r2" in res.eligible and "dos" in res.blocked
    assert "等待前置 r2 成功（当前 未开始）" in res.blocked["dos"]


def test_gate_accepts_enum_status():
    from ai_mode.schemas import JobStatus as ScStatus
    p = _plan([{"job_key": "r1", "label": ""},
               {"job_key": "dos", "label": "", "requires": ["r1"]}])
    res = gate_jobs(p, {"r1": ScStatus.COMPLETED})
    assert res.eligible == ["dos"]


def test_gate_ignores_inflight():
    p = _plan([{"job_key": "r1", "label": ""},
               {"job_key": "dos", "label": "", "requires": ["r1"]}])
    res = gate_jobs(p, {"r1": "queued"})
    assert res.ignored == ["r1"] and res.blocked["dos"]
    res2 = gate_jobs(p, {"r1": "cancelled"})
    assert "r1" in res2.ignored and "dos" in res2.blocked


def test_gate_not_converged_blocks():
    p = _plan([{"job_key": "r1", "label": ""},
               {"job_key": "dos", "label": "", "requires": ["r1"]}])
    res = gate_jobs(p, {"r1": "not_converged"})
    assert "前置 r1 失败（not_converged），禁止提前提交" in res.blocked["dos"]


def test_gate_terminal_self_skips():
    p = _plan([{"job_key": "a", "label": ""}])
    assert gate_jobs(p, {"a": "completed"}).eligible == []
    assert gate_jobs(p, {"a": "failed"}).blocked == {}


def test_done_own_state_not_resubmitted():
    p = _plan([{"job_key": "a", "label": ""}])
    assert "a" not in gate_jobs(p, {"a": "failed"}).eligible


def test_require_valid_plan_raises():
    from ai_mode.workflow import require_valid_plan
    p = _plan([{"job_key": "a", "label": "", "requires": ["ghost"]}])
    with pytest.raises(PlanError):
        require_valid_plan(p)
