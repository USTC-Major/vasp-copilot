"""M8 规划强约束：先定死先后顺序，平行可并行、递进必须等前置成功。

对齐 WORKFLOW.md v14 §2/§6 与 MODULE_INTERFACES v1.2 §5.3：
- 一切开始前先定死各作业先后顺序（本模块的拓扑排序即该「死顺序」唯一来源）。
- 平行（parallel_group）内可并行；``requires`` 递进必须等前置成功，
  前置未成功（未运行/排队/运行中/失败/未收敛）一律不得提交后续。
- 前置故障（failed）属于被禁止提前跑后续的典型场景，给出明确原因。
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Mapping, Sequence

from ..schemas import JobStatus, PlanSnapshot

#: 视为「前置成功」的状态集合（可按需配置）。
SUCCESS_STATUSES: frozenset[str] = frozenset({"completed"})


class PlanError(ValueError):
    """规划不合法（重复键/未知前置/自依赖/成环）时抛出。"""


def _status_str(status) -> str:
    if hasattr(status, "value"):
        return str(status.value)
    return str(status)


def _find_cycle(plan: PlanSnapshot):
    """DFS 找依赖环；有环返回环（作业键列表，含首尾重复），无环返回 None。"""
    adj = {s.job_key: set(s.requires) for s in plan.steps}
    color: dict[str, int] = {k: 0 for k in adj}
    stack: list[str] = []

    def visit(node: str):
        color[node] = 1
        stack.append(node)
        for nxt in adj[node]:
            if color.get(nxt, 0) == 0:
                res = visit(nxt)
                if res is not None:
                    return res
            elif color.get(nxt, 0) == 1:
                idx = stack.index(nxt)
                return stack[idx:] + [nxt]
        stack.pop()
        color[node] = 2
        return None

    for k in adj:
        if color[k] == 0:
            res = visit(k)
            if res is not None:
                return res
    return None


def validate_plan(plan: PlanSnapshot) -> list[str]:
    """检查规划合法性，返回问题清单（空=合法）。

    检查项：重复作业键、自依赖、未知前置、依赖成环。
    """
    issues: list[str] = []
    keys = [s.job_key for s in plan.steps]
    seen: set[str] = set()
    for step in plan.steps:
        if step.job_key in seen:
            issues.append(f"重复作业键: {step.job_key}")
        seen.add(step.job_key)
        for req in step.requires:
            if req == step.job_key:
                issues.append(f"自依赖: {step.job_key}")
            if req not in keys:
                issues.append(f"未知前置: {step.job_key} -> {req}")
    if not issues:
        cycle = _find_cycle(plan)
        if cycle:
            issues.append("依赖成环: " + " -> ".join(cycle))
    return issues


def require_valid_plan(plan: PlanSnapshot) -> None:
    issues = validate_plan(plan)
    if issues:
        raise PlanError("; ".join(issues))


def fixed_order(plan: PlanSnapshot) -> list[str]:
    """Kahn 拓扑排序 → 定死各作业先后顺序（先序强约束依据）。

    平行成员顺序稳定（取规划声明顺序）；成环/不合法抛 ``PlanError``。
    返回的列表只保证「后项依赖的所有前置已在其前面」，不代表全部线性串行。
    """
    require_valid_plan(plan)
    remaining = {s.job_key: len(s.requires) for s in plan.steps}
    children: dict[str, list[str]] = {s.job_key: [] for s in plan.steps}
    for step in plan.steps:
        for req in step.requires:
            children[req].append(step.job_key)
    queue = deque(k for k, n in remaining.items() if n == 0)
    order: list[str] = []
    while queue:
        key = queue.popleft()
        order.append(key)
        for child in children[key]:
            remaining[child] -= 1
            if remaining[child] == 0:
                queue.append(child)
    if len(order) != len(remaining):
        cycle = [s.job_key for s in plan.steps
                 if remaining.get(s.job_key, 0) > 0]
        raise PlanError("依赖成环: " + " -> ".join(cycle))
    return order


@dataclass
class GateResult:
    """对一张作业计划的「可提交」判定结果。"""

    eligible: list[str] = field(default_factory=list)
    blocked: dict[str, str] = field(default_factory=dict)
    ignored: list[str] = field(default_factory=list)

    def canonical(self) -> dict:
        return {
            "eligible": list(self.eligible),
            "blocked": dict(self.blocked),
            "ignored": list(self.ignored),
        }


def _is_success(status_obj, success=SUCCESS_STATUSES) -> bool:
    return _status_str(status_obj) in success


_IN_FLIGHT = {"submitted", "queued", "running", "waiting",
              "cancelled", "canceled"}      # 已在途/已取消，重复提交无意义
_DONE = {"completed", "failed", "not_converged"}  # 自身已终态处理过同类问题


def gate_jobs(plan: PlanSnapshot, statuses: Mapping[str, object]) -> GateResult:
    """先序强约束核心：给定作业实况，判定哪些现在可以提交。

    :param plan: 作业规划（必须通过 :func:`validate_plan`）。
    :param statuses: job_key -> 状态（字符串或 Enum，来源可为
        ``ai_mode.schemas.JobEntry`` 或 ``ai_mode.jobs.Job`` 的 status）。
    :return: GateResult。eligible=现在可以提交的键（前置全部成功、自身未启动）；
        blocked=不能提交的原因；ignored=已在途 / 已取消 / 无需处理的键。
        顺序按 fixed_order。
    """
    require_valid_plan(plan)
    result = GateResult()
    index = {s.job_key: s for s in plan.steps}
    for key in (s.job_key for s in plan.steps):
        step = index[key]
        st = _status_str(statuses[key]) if key in statuses else "planned"
        if st in _DONE:
            continue                       # 已成功/失败等终态，无需提交
        if st in _IN_FLIGHT:
            result.ignored.append(key)     # 已在途，等监控层处理
            continue
        # 自身可启动（planned / draft / 未记录），检查前置（递进必须等前置成功）
        unmet = [r for r in step.requires
                 if not (r in statuses and _is_success(statuses[r]))]
        if unmet:
            parts = []
            for r in unmet:
                rst = _status_str(statuses[r]) if r in statuses else "未开始"
                if rst in {"failed", "not_converged"}:
                    parts.append(f"前置 {r} 失败（{rst}），禁止提前提交")
                else:
                    parts.append(f"等待前置 {r} 成功（当前 {rst}）")
            result.blocked[key] = "；".join(parts)
        else:
            result.eligible.append(key)
    return result
