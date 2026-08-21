from __future__ import annotations

import json
import uuid

from ..schemas.fix import FixChange, RecommendedFix
from ..schemas.issue import Issue, Recommendation
from ..schemas.parsed import ParsedRunData
from ..schemas.status import FixStatus, Severity
from ..parsers.incar import parse_incar
from .rules import all_rules

# Parameters Doctor is allowed to auto-patch (MVP 5.4 / safe whitelist).
ALLOWED_FIX_WHITELIST = {
    "NBANDS", "ALGO", "AMIX", "AMIX_MAG", "BMIX", "BMIX_MAG", "MAXMIX",
    "NSW", "IBRION", "EDIFF", "EDIFFG", "SIGMA", "ISMEAR", "LMAXMIX",
    "ISPIN", "NELM", "NELMDL", "LREAL", "PREC", "ENCUT",
}

# Parameter groups that always require explicit user confirmation before apply.
CONFIRMATION_PARAM_PREFIX = ("NBANDS", "ALGO", "AMIX", "BMIX", "MAXMIX", "EDIFF",
                             "EDIFFG", "SIGMA", "ISMEAR", "LMAXMIX", "ISPIN",
                             "NELM", "NELMDL", "PREC", "ENCUT")


def _format_value(v) -> str:
    if isinstance(v, bool):
        return ".TRUE." if v else ".FALSE."
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, int):
        return str(v)
    if isinstance(v, (list, tuple)):
        return " ".join(_format_value(x) for x in v)
    return str(v)


def _change_requires_confirmation(parameter: str) -> bool:
    return parameter.startswith(CONFIRMATION_PARAM_PREFIX) or True


def _static_high_set(parsed: ParsedRunData) -> set[str]:
    """重解析后静态（一致性）规则中的高严重度 issue。"""
    out = set()
    for rule in all_rules():
        if rule.category not in ("files", "parameters"):
            continue
        for iss in rule.run(parsed):
            if iss.severity in (Severity.HIGH, Severity.CRITICAL):
                out.add(iss.rule_id)
    return out


class FixGenerator:
    """白名单驱动的 INCAR 修复生成器（MVP 5.4 / 8.3 节安全策略）。

    绝不改动原始文件。产出 INCAR.fixed + parameter_diff.json +
    APPLY_MANUALLY.md。重新解析修复后的 INCAR，保证未知参数可往返，
    且不引入新的 HIGH 级静态一致性 issue。"""

    def __init__(self) -> None:
        self._static_rules = [r for r in all_rules()
                              if r.category in ("files", "parameters")]

    def generate(self, *, parsed: ParsedRunData, issues: list[Issue],
                 incar_text: str) -> tuple[RecommendedFix, dict[str, str]]:
        if not incar_text.strip():
            fix = RecommendedFix(
                fix_id=_new_fix_id([]), issue_ids=[], target_file="INCAR",
                safe_to_generate=False,
                warnings=["缺少原始 INCAR 文本，无法生成修复"],
            )
            return fix, {}

        plan, issue_ids, warnings = self._plan_changes(parsed, issues)
        if not plan:
            fix = RecommendedFix(
                fix_id=_new_fix_id(issue_ids), issue_ids=issue_ids,
                target_file="INCAR", fix_status=FixStatus.UNAVAILABLE,
                safe_to_generate=False,
                warnings=warnings + ["没有可通过白名单自动修复的参数变更"],
            )
            return fix, {}

        new_text, diff = self._apply(parsed, plan)

        # Round-trip: unknown params must survive untouched.
        rt_err = self._roundtrip_unknown(parsed, incar_text, new_text)
        # Static gate: fixed INCAR must not introduce a new HIGH consistency issue.
        gate_err = self._static_gate(parsed, incar_text, new_text)
        problems = rt_err + gate_err
        safe = not problems

        new_parsed = parse_incar(new_text)
        fixed_incar = new_parsed if safe else parsed

        changes = [FixChange(
            target_file="INCAR",
            parameter=c["parameter"],
            operation=c["operation"],
            old_value=_format_value(c["old"]) if c["old"] is not None else None,
            new_value=c["new"],
        ) for c in plan]

        fix = RecommendedFix(
            fix_id=_new_fix_id(issue_ids), issue_ids=sorted(issue_ids),
            target_file="INCAR", strategy="parameter_patch",
            fix_status=FixStatus.GENERATED if safe else FixStatus.PROPOSED,
            safe_to_generate=safe,
            requires_user_confirmation=any(c["confirm"] for c in plan),
            changes=changes,
            diff=diff,
            generated_file_id="INCAR.fixed" if safe else None,
            warnings=warnings + problems,
        )

        files = {
            "INCAR.fixed": new_text,
            "parameter_diff.json": json.dumps({
                "fix_id": fix.fix_id, "safe_to_generate": safe,
                "target_file": "INCAR",
                "changes": [c.model_dump(exclude_none=True)
                            for c in changes],
            }, ensure_ascii=False, indent=2),
            "APPLY_MANUALLY.md": _apply_manual_md(fix, plan),
        }
        return fix, files

    def _plan_changes(self, parsed: ParsedRunData, issues: list[Issue]):
        plan: list[dict] = []
        seen: set[str] = set()
        issue_ids: set[str] = set()
        warnings: list[str] = []

        for iss in issues:
            if not iss.auto_fixable:
                continue
            for rec in iss.recommendations:
                if not _is_patch_rec(rec):
                    continue
                param = rec.parameter or ""
                issue_ids.add(iss.issue_id)
                if param not in ALLOWED_FIX_WHITELIST:
                    warnings.append(
                        f"{iss.rule_id}: 参数 {param} 不在白名单，跳过自动修复")
                    continue
                if param in seen:
                    continue
                seen.add(param)
                op = _op_for_action(rec.action, parsed, param)
                old = parsed.incar.effective.get(param)
                new = rec.new_value if rec.new_value not in (None, "") else None
                if op != "remove" and new is None:
                    warnings.append(
                        f"{iss.rule_id}: 参数 {param} 缺少具体新值，无法自动修复，需人工给定")
                    continue
                plan.append({
                    "parameter": param, "operation": op,
                    "old": old, "new": new if op != "remove" else None,
                    "confirm": _change_requires_confirmation(param),
                    "rationale": rec.rationale,
                })
        return plan, issue_ids, warnings

    def _apply(self, parsed: ParsedRunData, plan: list[dict]):
        lines = list(parsed.incar.raw_lines)
        idx: dict[str, int] = {}
        for a in parsed.incar.assignments:
            idx[a.name] = a.source_line - 1  # last occurrence wins
        diff_lines: list[str] = []
        for c in plan:
            name, op, new = c["parameter"], c["operation"], c["new"]
            if op == "remove":
                li = idx.get(name)
                if li is not None and li < len(lines):
                    diff_lines.append(f"- {lines[li]}")
                    diff_lines.append(f"+ (removed {name})")
                    lines[li] = None
                continue
            new_line = f"{name} = {_format_value(new)}"
            if name in idx:
                li = idx[name]
                diff_lines.append(f"- {lines[li]}")
                diff_lines.append(f"+ {new_line}")
                lines[li] = new_line
            else:
                diff_lines.append(f"+ {new_line}")
                lines.append(new_line)
        final_lines = [ln for ln in lines if ln is not None]
        return "\n".join(final_lines) + "\n", "\n".join(diff_lines)

    def _roundtrip_unknown(self, parsed: ParsedRunData, old_text: str,
                           new_text: str) -> list[str]:
        orig_unknown = {u for u in parsed.incar.unknown}
        new_parsed = parse_incar(new_text)
        new_unknown = {u for u in new_parsed.unknown}
        if orig_unknown != new_unknown:
            lost = sorted(orig_unknown - new_unknown)
            added = sorted(new_unknown - orig_unknown)
            return [f"unknown 参数 round-trip 未保留: 丢失{lost} 新增{added}；拒绝提供修复"]
        return []

    def _static_gate(self, parsed: ParsedRunData, old_text: str,
                     new_text: str) -> list[str]:
        def high(incar_text: str) -> set[str]:
            incar = parse_incar(incar_text)
            pr = ParsedRunData(incar=incar, poscar=parsed.poscar,
                               source_files=parsed.source_files)
            return _static_high_set(pr)
        new_high = high(new_text) - high(old_text)
        if new_high:
            return [f"修复后引入新的 HIGH 静态一致性问题 {sorted(new_high)}；拒绝生成"]
        return []


def _is_patch_rec(rec: Recommendation) -> bool:
    return (rec.action in ("set_parameter", "add_parameter", "remove_parameter")
            and rec.target == "INCAR" and rec.parameter)


def _op_for_action(action: str, parsed: ParsedRunData, param: str) -> str:
    if action == "remove_parameter":
        return "remove"
    if action == "add_parameter":
        return "add"
    present = param in parsed.incar.effective
    return "replace" if present else "add"


def _new_fix_id(issue_ids: list[str]) -> str:
    if issue_ids:
        return "FIX-" + "-".join(sorted(set(issue_ids)))[:48]
    return "FIX-" + uuid.uuid4().hex[:8].upper()


def _apply_manual_md(fix: RecommendedFix, plan: list[dict]) -> str:
    lines = [
        "# VASP-Doctor 修复改动清单（请人工确认后手动应用）",
        "",
        "> 说明：本文件不自动覆盖任何原件。请核对下方改动，确认后自行应用到 INCAR。",
        "",
        f"- fix_id: `{fix.fix_id}`",
        f"- 目标文件: `INCAR`（生成件为 `INCAR.fixed`，原始 INCAR 保持不变）",
        f"- 是否可直接生成: `{fix.safe_to_generate}`",
        f"- 是否需要用户确认: `{fix.requires_user_confirmation}`",
        "",
        "## 改动列表",
        "",
        "| 参数 | 操作 | 旧值 | 新值 |",
        "|------|------|------|------|",
    ]
    for c in plan:
        old = _format_value(c["old"]) if c["old"] is not None else "-"
        new = _format_value(c["new"]) if c["new"] is not None else "-"
        lines.append(f"| `{c['parameter']}` | {c['operation']} | {old} | {new} |")
    lines += [
        "",
        "## 应用建议",
        "",
        "- 修改后请重新运行 VASP-Doctor 的静态一致性诊断确认无新增 HIGH 问题。",
        "- 涉及磁矩/DFT+U/资源/科研阈值的改动务必人工核验后再提交计算。",
    ]
    return "\n".join(lines)
