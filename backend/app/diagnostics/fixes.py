from __future__ import annotations

import json
import re
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

MANUAL_STEPS = [
    "核对建议、关联问题及风险是否适用于当前计算目标。",
    "备份原始 INCAR，保留其他输入和计算记录。",
    "对照参数旧值、新值及文件差异，审阅 INCAR.fixed。",
    "由用户手动应用已审阅的修改；下载不表示已应用或问题已修复。",
    "按需要重新诊断或自行提交计算；本工具不会覆盖输入或重启作业。",
]

FIX_REASONS = {
    "candidate_ready": "候选修复已生成，应用前请审阅。",
    "missing_incar": "缺少原始非空 INCAR，无法判断并生成参数修改。请补充 INCAR 后重新诊断。",
    "missing_value": "建议缺少具体参数新值，请依据计算目标补充信息并人工核验。",
    "no_issues": "当前诊断未发现问题，暂无可生成的参数修改。",
    "no_rule": "当前问题暂无可用的参数修复规则，请查看诊断建议。",
    "no_changes": "没有有效参数变化，暂无可生成的参数修改。",
    "manual_review": "已有建议需要人工核验，无法安全生成候选文件。",
    "safety_rejected": "修复建议未通过白名单或静态安全复核，请依诊断依据人工处理。",
    "candidate_missing": "候选所需文件缺失或为空，请重新运行诊断生成候选；诊断报告仍可使用。",
    "candidate_invalid": "候选文件与参数修改或差异不一致，请重新运行诊断生成候选；诊断报告仍可使用。",
    "generation_failed": "候选生成失败，请重试诊断或依诊断建议人工处理；诊断报告仍可使用。",
    "diagnosis_not_ready": "请先运行诊断，再查看是否有可下载的候选修改。",
}


def fix_reason_fields(code: str) -> dict:
    if code == "candidate_ready":
        steps = list(MANUAL_STEPS)
    elif code == "missing_incar":
        steps = ["补充与当前计算对应的原始非空 INCAR。", "重新运行诊断，再查看具体参数建议与候选是否可用。"]
    elif code == "missing_value":
        steps = ["查看关联问题与依据，核实所需参数的具体新值及适用条件。", "按计算目标补充输入后重新诊断，或备份原输入后人工处理；不要猜测科研参数默认值。"]
    elif code in ("candidate_missing", "candidate_invalid", "generation_failed"):
        steps = ["查看仍可使用的诊断报告及问题依据。", "重新运行诊断生成候选；若仍失败，依据建议人工核验并处理原输入。", "只有新候选确实可下载时，再按其差异和人工说明审阅应用。"]
    elif code in ("manual_review", "safety_rejected"):
        steps = ["查看关联问题、拟议参数变化及不能生成的原因。", "结合当前计算目标核验建议，备份原始输入后由用户人工处理。", "按需要重新诊断或自行提交计算，本工具不会自动应用或重启作业。"]
    elif code == "diagnosis_not_ready":
        steps = ["先运行诊断，再查看报告和修复建议。"]
    else:
        steps = ["查看诊断报告中的问题依据及规则覆盖范围。", "当前没有可下载的参数修改；如仍需处理问题，请核验原输入并依诊断建议人工处理。"]
    return {"reason_code": code, "reason": FIX_REASONS[code], "manual_steps": steps}


def _unplanned_changes(original: dict, fixed: dict, parameters: set[str]) -> set[str]:
    return {name for name in original.keys() | fixed.keys()
            if name not in parameters and
            (name not in original or name not in fixed or original[name] != fixed[name])}


def _multi_assignment_targets(parsed: ParsedRunData, parameters: set[str]) -> set[str]:
    """The current parser/apply path handles one assignment per line only.

    Fail closed when rewriting a line could drop a second VASP assignment,
    including assignments the parser did not recognize as a separate tag.
    """
    targets = set()
    for assignment in parsed.incar.assignments:
        if assignment.name not in parameters:
            continue
        line = parsed.incar.raw_lines[assignment.source_line - 1]
        line = re.split(r"[#!]", line, maxsplit=1)[0]
        if len(re.findall(r"[A-Za-z_][A-Za-z0-9_]*\s*=", line)) > 1:
            targets.add(assignment.name)
    return targets


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
                fix_status=FixStatus.UNAVAILABLE, safe_to_generate=False,
                warnings=["缺少原始 INCAR 文本，无法生成修复"],
                **fix_reason_fields("missing_incar"),
            )
            return fix, {}

        plan, issue_ids, warnings = self._plan_changes(parsed, issues)
        if not plan:
            if warnings:
                code = "missing_value" if any("缺少具体新值" in w for w in warnings) else "safety_rejected"
            elif any(_is_patch_rec(r) for i in issues for r in i.recommendations):
                code = "no_changes" if issue_ids else "manual_review"
            else:
                code = "no_rule" if issues else "no_issues"
            fix = RecommendedFix(
                fix_id=_new_fix_id(issue_ids), issue_ids=issue_ids,
                target_file="INCAR", fix_status=FixStatus.UNAVAILABLE,
                safe_to_generate=False,
                warnings=warnings + ["没有可通过白名单自动修复的参数变更"],
                **fix_reason_fields(code),
            )
            return fix, {}

        new_text, diff = self._apply(parsed, plan)

        # Round-trip: unknown params must survive untouched.
        rt_err = self._roundtrip_unknown(parsed, incar_text, new_text)
        # Static gate: fixed INCAR must not introduce a new HIGH consistency issue.
        gate_err = self._static_gate(parsed, incar_text, new_text)
        problems = rt_err + gate_err
        multi = _multi_assignment_targets(parsed, {c["parameter"] for c in plan})
        if multi:
            problems.append(f"参数 {sorted(multi)} 所在行含多个赋值，无法安全逐行修改；请人工处理")
        effective = parse_incar(new_text).effective
        unplanned = _unplanned_changes(parsed.incar.effective, effective,
                                       {c["parameter"] for c in plan})
        if unplanned:
            problems.append(f"候选修改了未列入计划的参数 {sorted(unplanned)}；拒绝生成，请人工处理")
        for change in plan:
            name = change["parameter"]
            expected = parse_incar(f"{name} = {change['new']}").effective.get(name)
            if ((change["operation"] == "remove" and name in effective)
                    or (change["operation"] != "remove" and effective.get(name) != expected)):
                problems.append(f"参数 {name} 的候选有效值与建议不一致；请人工处理")
        safe = not problems

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
            **fix_reason_fields("candidate_ready" if safe else "safety_rejected"),
        )

        if not safe:
            return fix, {}

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
                new = rec.new_value if rec.new_value is not None and rec.new_value.strip() else None
                if op != "remove" and new is None:
                    warnings.append(
                        f"{iss.rule_id}: 参数 {param} 缺少具体新值，无法自动修复，需人工给定")
                    continue
                # Changes must affect the effective parameter, not just formatting.
                if op == "remove" and param not in parsed.incar.effective:
                    continue
                if op != "remove" and parse_incar(f"{param} = {new}").effective.get(param) == old:
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
        "# VASP-Copilot 修复改动清单（请人工确认后手动应用）",
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
        "- 修改后请重新运行 VASP-Copilot 的静态一致性诊断确认无新增 HIGH 问题。",
        "- 涉及磁矩/DFT+U/资源/科研阈值的改动务必人工核验后再提交计算。",
    ]
    lines += ["", "## 原因与风险", "", fix.reason]
    lines += [f"- {warning}" for warning in fix.warnings]
    lines += ["", "## 人工步骤", ""] + [f"{n}. {step}" for n, step in enumerate(fix.manual_steps, 1)]
    return "\n".join(lines)


def _candidate_valid(fix: RecommendedFix, files: dict, incar_text: str) -> bool:
    """Validate the existing three-file delivery against the current recommendation."""
    if (not fix.fix_id or any(not (c.isascii() and (c.isalnum() or c in "_-")) for c in fix.fix_id)
            or not fix.changes or not fix.diff or not fix.diff.strip()
            or fix.generated_file_id != "INCAR.fixed" or not incar_text.strip()):
        return False
    try:
        metadata = json.loads(files["parameter_diff.json"])
        if (metadata.get("fix_id") != fix.fix_id
                or metadata.get("safe_to_generate") is not True
                or metadata.get("target_file") != "INCAR"
                or metadata.get("changes") != [c.model_dump(exclude_none=True) for c in fix.changes]):
            return False
        original = ParsedRunData(incar=parse_incar(incar_text))
        if _multi_assignment_targets(original, {c.parameter for c in fix.changes}):
            return False
        fixed = parse_incar(files["INCAR.fixed"]).effective
        if _unplanned_changes(original.incar.effective, fixed, {c.parameter for c in fix.changes}):
            return False
        plan = []
        for c in fix.changes:
            name = c.parameter
            if (c.target_file != "INCAR" or name not in ALLOWED_FIX_WHITELIST
                    or c.operation not in ("add", "replace", "remove")):
                return False
            old = original.incar.effective.get(name)
            stated_old = parse_incar(f"{name} = {c.old_value}").effective.get(name) if c.old_value is not None else None
            if old != stated_old:
                return False
            if c.operation == "remove":
                if name not in original.incar.effective or name in fixed or c.new_value is not None:
                    return False
            else:
                if c.new_value is None or not c.new_value.strip():
                    return False
                new = parse_incar(f"{name} = {c.new_value}").effective.get(name)
                if new is None or new == old or fixed.get(name) != new:
                    return False
            plan.append({"parameter": name, "operation": c.operation, "old": old, "new": c.new_value})
            row = f"| `{name}` | {c.operation} | {_format_value(old) if old is not None else '-'} | {c.new_value if c.new_value is not None else '-'} |"
            if row not in files["APPLY_MANUALLY.md"]:
                return False
        expected_text, expected_diff = FixGenerator()._apply(original, plan)
        return (expected_text == files["INCAR.fixed"] and expected_diff == fix.diff
                and fix.fix_id in files["APPLY_MANUALLY.md"])
    except (ValueError, TypeError, KeyError, AttributeError):
        return False


def assess_fix_delivery(result, packages: dict[str, dict[str, str]], incar_text: str):
    """Single authority for run/get/download, including legacy recommendations.

    Return compatibility fields, enriched copies, and only validated packages.
    Never mutate the scientific recommendation/status during a read.
    """
    fixes = []
    valid = {}
    packages = packages if isinstance(packages, dict) else {}
    for fix in result.recommended_fixes if result is not None else []:
        code = fix.reason_code
        if fix.fix_status == FixStatus.GENERATED and fix.safe_to_generate:
            files = packages.get(fix.fix_id, {})
            required = ("INCAR.fixed", "parameter_diff.json", "APPLY_MANUALLY.md")
            if not isinstance(files, dict) or any(not isinstance(files.get(n), str) or not files[n].strip() for n in required):
                code = "candidate_missing"
            elif not _candidate_valid(fix, files, incar_text):
                code = "candidate_invalid"
            else:
                code = "candidate_ready"
                # Package only known delivery names; legacy stray paths never enter ZIP.
                valid[fix.fix_id] = {n: files[n] for n in required}
        elif code not in FIX_REASONS or code == "candidate_ready":
            if not incar_text.strip():
                code = "missing_incar"
            elif fix.changes:
                code = "manual_review"
            elif any("缺少具体新值" in w for w in fix.warnings):
                code = "missing_value"
            elif any("白名单" in w and "跳过" in w for w in fix.warnings):
                code = "safety_rejected"
            elif any("拒绝" in w for w in fix.warnings):
                code = "safety_rejected"
            elif any(_is_patch_rec(rec) for i in result.issues for rec in i.recommendations):
                code = "manual_review"
            else:
                code = "no_rule" if result.issues else "no_issues"
        fixes.append(fix.model_copy(update=fix_reason_fields(code)))
    if valid:
        code = "candidate_ready"
    elif fixes:
        code = fixes[0].reason_code
    elif result is None:
        code = "diagnosis_not_ready"
    elif not incar_text.strip():
        code = "missing_incar"
    elif result.issues:
        code = "no_rule"
    else:
        code = "no_issues"
    fields = fix_reason_fields(code)
    summary = {"fix_available": bool(valid), "fix_reason_code": code,
               "fix_reason": fields["reason"], "fix_manual_steps": fields["manual_steps"]}
    return summary, fixes, valid
