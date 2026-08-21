"""InputCheckReportGenerator（设计文档 10.9 节）。

固定七章节：结构与顺序 / 数组展开 / 任务步骤 / 文件继承 / POTCAR 准备 /
参数来源 / warning 与门控结论。与 README、workflow_plan.json 共用同一
FileInheritancePlan（不得各自维护继承规则）。

报告为自检证据，措辞上 Recipe 初始推荐绝不写成"正确值"。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from backend.app.generators.archive import FIXED_TIMESTAMP
from backend.app.schemas.generation import (
    DftuSettings,
    InputCheckReportMetadata,
    StructureContext,
)
from backend.app.schemas.workflow import (
    FileInheritancePlan,
    RecipeComposition,
    WorkflowStep,
)

SECTIONS = (
    "结构与顺序",
    "数组展开",
    "任务步骤",
    "文件继承",
    "POTCAR 准备",
    "参数来源",
    "warning 与门控结论",
)

LABEL_RECIPE = "Recipe 初始推荐"
LABEL_USER = "用户已确认"
LABEL_DERIVED = "确定性派生/规则修复"

_SEVERITY_ORDER = ("high", "medium", "info")


def _fmt_value(value: Any) -> str:
    if isinstance(value, bool):
        return ".TRUE." if value else ".FALSE."
    if isinstance(value, float) and value == int(value) and abs(value) < 1e16:
        return str(int(value))
    if isinstance(value, list):
        return " ".join(_fmt_value(item) for item in value)
    return str(value)


def _compact_array(items: List[Any]) -> str:
    """n*value 压缩展示（仅用于报告）。"""

    tokens: List[str] = []
    i = 0
    while i < len(items):
        item = items[i]
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            tokens.append(_fmt_value(item))
            i += 1
            continue
        run = 1
        while (
            i + run < len(items)
            and isinstance(items[i + run], (int, float))
            and not isinstance(items[i + run], bool)
            and float(items[i + run]) == float(item)
        ):
            run += 1
        tokens.append(f"{run}*{_fmt_value(item)}" if run > 1 else _fmt_value(item))
        i += run
    return " ".join(tokens)


class InputCheckReportGenerator:
    def generate(
        self,
        *,
        workflow_id: str,
        revision: int,
        structure: StructureContext,
        steps: List[WorkflowStep],
        plan: FileInheritancePlan,
        compositions: Dict[str, RecipeComposition],
        dftu: Optional[DftuSettings] = None,
        potcar_prepared: bool = False,
    ) -> Tuple[str, InputCheckReportMetadata]:
        warnings: List[Dict[str, Any]] = []
        pending_confirmations: List[Dict[str, Any]] = []
        for composition in compositions.values():
            warnings.extend(composition.warnings)
            for confirmation in composition.confirmations:
                pending_confirmations.append(
                    {
                        "key": confirmation.key,
                        "step_id": composition.step_id,
                        "prompt": confirmation.prompt,
                    }
                )

        lines: List[str] = []
        lines.append(f"# INPUT_CHECK_REPORT — {workflow_id} (revision {revision})")
        lines.append("")
        lines.append(f"> 生成时刻（固定基准）：{FIXED_TIMESTAMP}；本报告为生成时自检证据。")
        lines.append("")
        self._section_structure(lines, structure)
        self._section_arrays(lines, structure, compositions, dftu)
        self._section_steps(lines, steps, compositions)
        self._section_inheritance(lines, plan)
        self._section_potcar(lines, structure, potcar_prepared)
        self._section_provenance(lines, compositions)
        self._section_conclusion(lines, steps, warnings, pending_confirmations)
        markdown = "\n".join(lines).rstrip("\n") + "\n"

        severity_counts = {severity: 0 for severity in _SEVERITY_ORDER}
        severity_counts["blocking"] = sum(1 for step in steps if not step.runnable)
        for warning in warnings:
            severity = warning.get("severity", "medium")
            severity_counts[severity] = severity_counts.get(severity, 0) + 1
        metadata = InputCheckReportMetadata(
            report_id=f"icr_{workflow_id}_r{revision}",
            workflow_id=workflow_id,
            workflow_revision=revision,
            sections=list(SECTIONS),
            check_summary=severity_counts,
            generated_at=FIXED_TIMESTAMP,
        )
        return markdown, metadata

    # --- 章节 1：结构与顺序 ---

    @staticmethod
    def _section_structure(lines: List[str], structure: StructureContext) -> None:
        lines.append("## 1. 结构与顺序")
        lines.append("")
        lines.append(f"- 化学式：{structure.formula}")
        pairs = ", ".join(
            f"{element} × {count}"
            for element, count in zip(structure.elements, structure.counts)
        )
        lines.append(f"- POSCAR 元素顺序：{pairs}")
        lines.append(f"- 总原子数：{structure.atom_count}")
        lines.append(
            f"- source_sha256：{structure.source_sha256 or '未提供（建议在上传时记录）'}"
        )
        lines.append("")

    # --- 章节 2：数组展开 ---

    @staticmethod
    def _section_arrays(
        lines: List[str],
        structure: StructureContext,
        compositions: Dict[str, RecipeComposition],
        dftu: Optional[DftuSettings],
    ) -> None:
        lines.append("## 2. 数组展开")
        lines.append("")
        magmom: Optional[List[Any]] = None
        for composition in compositions.values():
            value = composition.resolved_parameters.get("MAGMOM")
            if isinstance(value, list):
                magmom = value
                break
        if magmom is not None:
            lines.append("### MAGMOM")
            lines.append("")
            lines.append(f"- compact：`{_compact_array(magmom)}`")
            lines.append(f"- raw：`{_fmt_value(magmom)}`")
            lines.append(f"- 展开后总数：{len(magmom)}（原子数 {structure.atom_count}）")
            start = 1
            for element, count in zip(structure.elements, structure.counts):
                end = start + count - 1
                segment = magmom[start - 1 : end]
                lines.append(f"- {element}：原子 {start}–{end}，磁矩 {segment}")
                start = end + 1
            lines.append("")
        else:
            lines.append("- MAGMOM：未启用（非磁性组合）。")
            lines.append("")

        ldau_l = None
        for composition in compositions.values():
            value = composition.resolved_parameters.get("LDAUL")
            if isinstance(value, list):
                ldau_l = value
                ldau_u = composition.resolved_parameters.get("LDAUU")
                ldau_j = composition.resolved_parameters.get("LDAUJ")
                break
        if ldau_l is not None:
            lines.append("### LDAUL / LDAUU / LDAUJ（按 POSCAR 元素顺序）")
            lines.append("")
            lines.append("| 元素 | L | U (eV) | J (eV) | 状态 |")
            lines.append("|---|---|---|---|---|")
            entry_confirmed: Dict[str, bool] = {}
            if dftu is not None:
                for entry in dftu.entries:
                    entry_confirmed[entry.element] = entry.confirmed_by_user
            for index, element in enumerate(structure.elements):
                l_value = ldau_l[index]
                u_value = ldau_u[index] if isinstance(ldau_u, list) else "-"
                j_value = ldau_j[index] if isinstance(ldau_j, list) else "-"
                if float(l_value) < 0:
                    status = "不使用 U（L=-1）"
                elif entry_confirmed.get(element):
                    status = "用户确认"
                else:
                    status = "待确认"
                lines.append(
                    f"| {element} | {_fmt_value(l_value)} | {_fmt_value(u_value)} "
                    f"| {_fmt_value(j_value)} | {status} |"
                )
            lines.append("")
        else:
            lines.append("- LDAU：未启用。")
            lines.append("")

    # --- 章节 3：任务步骤 ---

    @staticmethod
    def _section_steps(
        lines: List[str],
        steps: List[WorkflowStep],
        compositions: Dict[str, RecipeComposition],
    ) -> None:
        lines.append("## 3. 任务步骤")
        lines.append("")
        lines.append("| step | 任务 | composition revision | runnable | blocked_by | 静态校验 |")
        lines.append("|---|---|---|---|---|---|")
        for step in steps:
            composition = compositions.get(step.step_id)
            revision = composition.revision if composition else "-"
            blocked = ", ".join(step.blocked_by) if step.blocked_by else "-"
            static_check = (
                "passed"
                if composition and composition.composition_status.value != "invalid"
                else "failed"
            )
            lines.append(
                f"| `{step.step_id}` | {step.task} | {revision} | "
                f"{'true' if step.runnable else 'false'} | {blocked} | {static_check} |"
            )
        lines.append("")

    # --- 章节 4：文件继承 ---

    @staticmethod
    def _section_inheritance(lines: List[str], plan: FileInheritancePlan) -> None:
        lines.append("## 4. 文件继承")
        lines.append("")
        if not plan.dependencies:
            lines.append("- 本工作流没有跨 step 文件依赖。")
            lines.append("")
            return
        lines.append("| 上游步骤 | 源文件 | 下游步骤 | 目标文件 | 当前满足 | 提交前检查 |")
        lines.append("|---|---|---|---|---|---|")
        for dep in plan.dependencies:
            lines.append(
                f"| `{dep.from_step_id}` | {dep.source_file} | `{dep.to_step_id}` "
                f"| {dep.target_file} | {'true' if dep.satisfied else 'false'} "
                "| 源文件实际产生、非空、格式/哈希通过且上游诊断无 blocking |"
            )
        lines.append("")

    # --- 章节 5：POTCAR 准备 ---

    @staticmethod
    def _section_potcar(
        lines: List[str], structure: StructureContext, potcar_prepared: bool
    ) -> None:
        lines.append("## 5. POTCAR 准备")
        lines.append("")
        lines.append(
            "- 系统不内置、不下载、不分发 POTCAR（VASP 许可证限制，"
            "`ENABLE_POTCAR_ASSEMBLY=false`）。"
        )
        if potcar_prepared:
            lines.append("- 用户声明已自备 POTCAR；BE-A 仅记录该声明，不校验正文。")
        else:
            lines.append(
                "- 请在合法授权环境按下列 POSCAR 元素顺序拼接 POTCAR："
            )
            for index, element in enumerate(structure.elements, start=1):
                lines.append(f"  {index}. {element}")
            lines.append("- 未完成前所有 step 保持 `POTCAR_NOT_PREPARED` 阻塞。")
        lines.append("")

    # --- 章节 6：参数来源 ---

    @staticmethod
    def _section_provenance(
        lines: List[str], compositions: Dict[str, RecipeComposition]
    ) -> None:
        lines.append("## 6. 参数来源")
        lines.append("")
        for step_id in sorted(compositions):
            composition = compositions[step_id]
            lines.append(f"### `{step_id}`")
            lines.append("")
            lines.append("| parameter | value | 标识 | source ID | version |")
            lines.append("|---|---|---|---|---|")
            for entry in composition.provenance:
                label = InputCheckReportGenerator._provenance_label(entry)
                source_id = entry.get("source_id", "-")
                version = "-"
                if "@" in str(source_id):
                    source_id, version = str(source_id).rsplit("@", 1)
                lines.append(
                    f"| {entry.get('parameter')} | `{_fmt_value(entry.get('value'))}` "
                    f"| {label} | {source_id} | {version} |"
                )
            lines.append("")
        lines.append("> 说明：`Recipe 初始推荐` 只是受审查起点，不是唯一正确值。")
        lines.append("")

    @staticmethod
    def _provenance_label(entry: Dict[str, Any]) -> str:
        source_type = entry.get("source_type")
        if source_type == "user_patch":
            return LABEL_USER
        if source_type == "recipe":
            if entry.get("requires_confirmation") and entry.get("confirmed"):
                return LABEL_USER
            return LABEL_RECIPE
        return LABEL_DERIVED

    # --- 章节 7：warning 与门控结论 ---

    @staticmethod
    def _section_conclusion(
        lines: List[str],
        steps: List[WorkflowStep],
        warnings: List[Dict[str, Any]],
        pending_confirmations: List[Dict[str, Any]],
    ) -> None:
        lines.append("## 7. warning 与门控结论")
        lines.append("")
        counts = {severity: 0 for severity in _SEVERITY_ORDER}
        for warning in warnings:
            severity = warning.get("severity", "medium")
            counts[severity] = counts.get(severity, 0) + 1
        lines.append(
            f"- warning 统计：high {counts.get('high', 0)} / "
            f"medium {counts.get('medium', 0)} / info {counts.get('info', 0)}"
        )
        not_runnable = [step.step_id for step in steps if not step.runnable]
        if not_runnable:
            lines.append(f"- 当前不可提交步骤：{', '.join('`' + s + '`' for s in not_runnable)}")
        else:
            lines.append("- 当前所有步骤可提交。")
        lines.append("- 待补文件：POTCAR；上游运行时产物（CONTCAR/CHGCAR）需实际计算后产生。")
        if pending_confirmations:
            lines.append("- 待确认字段：")
            for confirmation in pending_confirmations:
                lines.append(
                    f"  - `{confirmation['step_id']}` / {confirmation['key']}："
                    f"{confirmation['prompt']}"
                )
        else:
            lines.append("- 待确认字段：无。")
        if warnings:
            lines.append("- warning 明细：")
            for warning in warnings:
                lines.append(
                    f"  - [{warning.get('severity', 'medium')}] {warning.get('code')}："
                    f"{warning.get('message', warning.get('code'))}"
                )
        lines.append("")
