"""LLM 工作流规划（方案 A）。

用户自然语言需求 + 结构摘要 → 结构化 NL 计划（task/assumptions/patches）
复用既有 preview_plan/generate 管线生成文件。
LLM 只负责把「需求文本」翻译成受校验的结构化计划，不直接产出文件内容。
协议见 docs/LLM_DRIVEN_WORKFLOW_DESIGN.md。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from backend.app.schemas.generation import PatchValidationResult, ParameterPatch

DEFAULT_TASKS: List[str] = ["relax", "static", "dos"]


def _as_dict(summary) -> Dict[str, Any]:
    """Pydantic 模型或 dict 统一转 dict（FileStore 存的是 slim StructureSummary）。"""
    if isinstance(summary, dict):
        return summary
    if isinstance(summary, BaseModel):
        return summary.model_dump(mode="json")
    return {}


@dataclass
class NlPlan:
    """LLM 解析出的结构化计划（已通过校验）。"""

    requested_tasks: List[str]
    assumptions: Dict[str, Any]
    patches: List[ParameterPatch]
    step_explanations: List[Dict[str, str]]
    user_needs: str


def _build_messages(summary, user_text: str, enable_band_workflow: bool = False) -> List[Dict[str, str]]:
    compact = _compact_summary(summary)
    system = (
        "你是 VASP-Copilot 的工作流规划助手。只能依据给定的结构摘要与用户自然语言需求，"
        "输出一份严格 JSON 的计划，禁止编造未给出的参数值。"
        f"输出字段：requested_tasks(string[]，可从 {_task_choices(enable_band_workflow)} 选择)、"
        "assumptions{ electronic_type: metal|semiconductor|unknown; magnetic: bool; "
        "soc: bool; precision: quick|standard|high }、"
        "patches（仅当用户明确要求修改参数，如 “把 ENCUT 提到 620”；对象含 parameter/value/reason）、"
        "step_explanations（对每个选定 task 给出中文解释，对象含 step/label/explanation）。"
        "只输出 JSON，不要 Markdown 围栏（代码块）之外的文字。"
    )
    user = f"结构摘要：\n{compact}\n\n用户需求：\n{user_text}"
    return [{"role": "system", "content": system},
            {"role": "user", "content": user}]


def _compact_summary(summary) -> str:
    data = _as_dict(summary)
    lat = ""
    lattice = data.get("lattice")
    if isinstance(lattice, dict):
        lat = (f"  bounds: a={lattice.get('a')} b={lattice.get('b')} c={lattice.get('c')} "
               f"volume={lattice.get('volume')} alpha/beta/gamma="
               f"{lattice.get('alpha')}/{lattice.get('beta')}/{lattice.get('gamma')}")
    warnings = data.get("warnings", []) or []
    warning_msgs = [w.get("message") if isinstance(w, dict) else w for w in warnings]
    return (
        f"formula: {data.get('formula')} elements: {data.get('elements')} "
        f"counts: {data.get('counts')} atom_count: {data.get('atom_count')}"
        f"{lat}\ntransition_metals: {data.get('transition_metals')} "
        f"coordinate_mode: {data.get('coordinate_mode')} "
        f"selective_dynamics: {data.get('selective_dynamics')} "
        f"warnings: {warning_msgs}"
    )


TASK_ALIASES = {
    "relax": "relax", "结构优化": "relax", "优化": "relax",
    "static": "static", "静态": "static", "静态计算": "static",
    "dos": "dos", "态密度": "dos",
    "band": "band", "能带": "band",
}
VALID_TASKS = {"relax", "static", "dos", "band"}

def _allowed_tasks(enable_band_workflow: bool) -> frozenset:
    """Returns currently generatable tasks; band gated by the flag."""
    if enable_band_workflow:
        return VALID_TASKS
    return VALID_TASKS - {"band"}

def _task_choices(enable_band_workflow: bool) -> str:
    order = ["relax", "static", "dos", "band"]
    return "/".join(t for t in order if t in _allowed_tasks(enable_band_workflow))

PRECISION_ALIASES = {
    "quick": "quick", "快速": "quick",
    "standard": "standard", "标准": "standard",
    "high": "high", "高精度": "high",
}
ELECTRONIC_ALIASES = {
    "metal": "metal", "金属": "metal",
    "semiconductor": "semiconductor", "半导体": "semiconductor",
    "unknown": "unknown", "不确定": "unknown",
}

_FIXED_PARAMETERS = frozenset({
    "ENCUT", "EDIFF", "EDIFFG", "NSW", "ALGO", "ISMEAR", "SIGMA",
    "ISPIN", "NELM", "NEDOS", "EMIN", "EMAX",
})


def _normalize_task_value(raw: Any) -> str:
    s = str(raw).strip().lower()
    return TASK_ALIASES.get(s, s)


def _valid_assumption_key(key: str) -> bool:
    return key in {"electronic_type", "magnetic", "soc", "precision"}


def _normalize_assumptions(raw) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in (raw or {}).items():
        key = key.strip()
        if not _valid_assumption_key(key):
            continue
        if key == "electronic_type":
            try:
                out[key] = ELECTRONIC_ALIASES[str(value).strip().lower()]
            except KeyError:
                continue
        elif key == "precision":
            out[key] = PRECISION_ALIASES[str(value).strip().lower()]
        elif key == "magnetic":
            if isinstance(value, bool):
                out[key] = value
            elif isinstance(value, str) and value.strip().lower() in ("true", "yes", "1"):
                out[key] = True
            elif isinstance(value, str) and value.strip().lower() in ("false", "no", "0"):
                out[key] = False
        elif key == "soc":
            out[key] = isinstance(value, bool) and value or (
                isinstance(value, str) and value.strip().lower() in ("true", "yes", "1")
            )
    return out


def _normalize_patches(raw) -> List[ParameterPatch]:
    patches: List[ParameterPatch] = []
    for i, item in enumerate(raw or []):
        if not isinstance(item, dict):
            continue
        parameter = str(item.get("parameter") or "").strip().upper()
        if not parameter or parameter not in _FIXED_PARAMETERS:
            continue
        value = item.get("value")
        if value is None:
            continue
        try:
            patch = ParameterPatch(
                patch_id=f"nl_{i:04d}_{parameter}",
                expected_revision=1,
                parameter=parameter,
                operation="replace",
                value=value,
                source="ai_plan",
                reason=str(item.get("reason") or f"AI 规划 {parameter}"),
                confirmed_by_user=False,
                validation=PatchValidationResult(allowed=True, rule_ids=["NL_PLAN"]),
            )
        except Exception:  # noqa: BLE001 - 非法补丁直接丢弃
            continue
        patches.append(patch)
    return patches


def _normalize_tasks(raw, enable_band_workflow: bool = False) -> List[str]:
    if not isinstance(raw, list) or not raw:
        return []
    tasks: List[str] = []
    allowed = _allowed_tasks(enable_band_workflow)
    for item in raw:
        norm = _normalize_task_value(item)
        if norm in allowed and norm not in tasks:
            tasks.append(norm)
    return tasks or list(DEFAULT_TASKS)


def validate_plan(plan, enable_band_workflow: bool = False) -> Optional[NlPlan]:
    """结构白名单化地解析 LLM 输出；无效时返回 None -> 用默认值（degraded）。"""
    if not isinstance(plan, dict):
        return None
    tasks = _normalize_tasks(plan.get("requested_tasks"), enable_band_workflow=enable_band_workflow)
    explanations: List[Dict[str, str]] = []
    raw_explanations = plan.get("step_explanations")
    if isinstance(raw_explanations, list):
        for item in raw_explanations:
            if not isinstance(item, dict):
                continue
            step = str(item.get("step") or item.get("task") or "").strip()
            label = str(item.get("label") or "").strip()
            explanation = str(item.get("explanation") or "").strip()
            if step and explanation:
                explanations.append({"step": step, "label": label, "explanation": explanation})
    return NlPlan(
        requested_tasks=tasks,
        assumptions=_normalize_assumptions(plan.get("assumptions")),
        patches=_normalize_patches(plan.get("patches")),
        step_explanations=explanations,
        user_needs=str(plan.get("user_needs") or "").strip(),
    )


class LlmWorkflowPlanner:
    """基于 LLM 的规划器；失败时降级（不抛错）。

    ``explainer`` 可选注入；默认通过 ``get_explainer(settings)`` 获取。
    """

    def __init__(self, explainer=None, max_retries: int = 3):
        self._explainer = explainer
        self._max_retries = max_retries

    @property
    def explainer(self):
        return self._explainer

    @explainer.setter
    def explainer(self, value) -> None:
        self._explainer = value

    def plan(self, summary, user_text: str, enable_band_workflow: bool = False) -> Optional[NlPlan]:
        """解析请求；失败/超时/非法输出返回 None（调用方降级为默认规则计划）。"""
        if self._explainer is None:
            return None
        messages = _build_messages(summary, user_text, enable_band_workflow=enable_band_workflow)
        for _ in range(max(1, self._max_retries)):
            try:
                raw = self._explainer.complete(messages)
            except Exception:  # noqa: BLE001 - LLM 不可用即降级
                return None
            parsed = _parse_json(raw)
            plan = validate_plan(parsed, enable_band_workflow=enable_band_workflow)
            if plan is not None:
                return plan
        return None


def _parse_json(raw: str) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def build_default_plan(summary, user_text: str) -> NlPlan:
    """无 LLM / 降级时的确定性默认计划（用户仍可手动修改）。"""
    data = _as_dict(summary)
    transition_metals = data.get("transition_metals") or []
    return NlPlan(
        requested_tasks=list(DEFAULT_TASKS),
        assumptions={
            "electronic_type": "unknown",
            "magnetic": bool(transition_metals),
            "soc": False,
            "precision": "standard",
        },
        patches=[],
        step_explanations=[
            {"step": "01_relax", "label": "relax step", "explanation": "结构优化（relax）：AI 未启用或需求不明，按默认参数计划，可手动微调。"},
            {"step": "02_static", "label": "static step", "explanation": "静态计算（static）：由 relax 的收敛结构继续，用于获取稳定电子结构。"},
            {"step": "03_dos", "label": "dos step", "explanation": "态密度（DOS）：基于静态计算的 CHGCAR 分析电子态密度。"},
        ],
        user_needs=user_text,
    )