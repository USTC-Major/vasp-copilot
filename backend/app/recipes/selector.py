"""RecipeSelector（设计文档 4.1 节第 5 步、8.2 节）。

确定性映射：task/体系假设/精度 → 候选 Recipe ID + selection_reason。
LLM 只能提供 SelectionContext 中的枚举，不能返回模板正文。
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from backend.app.schemas.recipe import (
    ElectronicType,
    PrecisionLevel,
    RecipeRef,
    SelectionContext,
    TaskType,
)

_TASK_RECIPE_IDS: Dict[TaskType, str] = {
    TaskType.RELAX: "task.relax.standard",
    TaskType.STATIC: "task.static.standard",
    TaskType.DOS: "task.dos.standard",
    TaskType.BAND: "task.band.standard",
}

_ELECTRONIC_RECIPE_IDS: Dict[ElectronicType, str] = {
    ElectronicType.METAL: "electronic.metal",
    ElectronicType.SEMICONDUCTOR: "electronic.semiconductor",
    ElectronicType.UNKNOWN: "electronic.unknown",
}

_PRECISION_RECIPE_IDS: Dict[PrecisionLevel, str] = {
    PrecisionLevel.QUICK: "precision.quick",
    PrecisionLevel.STANDARD: "precision.standard",
    PrecisionLevel.HIGH: "precision.high",
}

DEFAULT_RECIPE_VERSION = "1.0.0"


class SelectionEntry:
    def __init__(self, ref: RecipeRef, layer_name: str, reason: str, matched: Dict) -> None:
        self.ref = ref
        self.layer_name = layer_name
        self.reason = reason
        self.matched = matched


class RecipeSelector:
    def select(self, context: SelectionContext) -> List[SelectionEntry]:
        entries: List[SelectionEntry] = []
        entries.append(
            SelectionEntry(
                RecipeRef(recipe_id="base.vasp", version=DEFAULT_RECIPE_VERSION),
                "base",
                "所有 VASP 任务共享的受审查基础参数",
                {"task": context.task.value},
            )
        )
        task_id = _TASK_RECIPE_IDS[context.task]
        entries.append(
            SelectionEntry(
                RecipeRef(recipe_id=task_id, version=DEFAULT_RECIPE_VERSION),
                "task",
                f"目标包含 {context.task.value} 且精度档位由 precision 层控制",
                {"task": context.task.value, "precision": context.precision.value},
            )
        )
        # electronic 层只控制 relax/static 的展宽；DOS/band 的 ISMEAR
        # 是任务语义（ICHARG=11 需要 tetrahedron/小 SIGMA），由 task recipe 拥有。
        if context.task in (TaskType.RELAX, TaskType.STATIC):
            electronic_id = _ELECTRONIC_RECIPE_IDS[context.electronic_type]
            entries.append(
                SelectionEntry(
                    RecipeRef(recipe_id=electronic_id, version=DEFAULT_RECIPE_VERSION),
                    "electronic_type",
                    f"用户将体系电子类型标记为 {context.electronic_type.value}",
                    {"electronic_type": context.electronic_type.value},
                )
            )
        if context.magnetic:
            entries.append(
                SelectionEntry(
                    RecipeRef(recipe_id="modifier.magnetic", version=DEFAULT_RECIPE_VERSION),
                    "modifier",
                    "用户确认磁性；MAGMOM 按 POSCAR 顺序派生",
                    {"magnetic": True, "elements": list(context.elements)},
                )
            )
        if context.dftu:
            entries.append(
                SelectionEntry(
                    RecipeRef(recipe_id="modifier.dftu", version=DEFAULT_RECIPE_VERSION),
                    "modifier",
                    "用户启用 DFT+U；L/U/J 必须由用户输入并确认",
                    {"dftu": True, "elements": list(context.elements)},
                )
            )
        precision_id = _PRECISION_RECIPE_IDS[context.precision]
        entries.append(
            SelectionEntry(
                RecipeRef(recipe_id=precision_id, version=DEFAULT_RECIPE_VERSION),
                "precision",
                f"用户选择精度档位 {context.precision.value}",
                {"precision": context.precision.value},
            )
        )
        return entries

    def select_refs(self, context: SelectionContext) -> Tuple[List[RecipeRef], Dict[str, str]]:
        entries = self.select(context)
        refs = [e.ref for e in entries]
        reasons = {e.ref.key: e.reason for e in entries}
        return refs, reasons
