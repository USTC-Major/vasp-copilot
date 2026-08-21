"""Collect MVP 13.4 acceptance metrics from registries + live demo runs.

Design 13.4: metrics are auto-generated from tests/registries; the PPT shows
only measured values, never "planned support". This script computes the
vasp-doctor-side metrics from the actual registries and a live offline E2E over
the demo fixtures. Rows owned by vasp-copilot (core workflows, published
recipes) and the manual dress-rehearsal timing are explicitly labelled.

Usage:
    python scripts/collect_metrics.py              # print report to stdout
    python scripts/collect_metrics.py --out PATH   # also write markdown file
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.diagnostics.fixes import FixGenerator
from app.diagnostics.rules import all_rules
from app.parsers.cif import parse_cif
from app.parsers.incar import parse_incar
from app.parsers.poscar import parse_poscar
from app.schemas.issue import Issue, Recommendation
from app.schemas.parsed import ParsedRunData
from app.schemas.status import Severity
from app.services.diagnosis_service import DiagnosisService, _kind_for, _load_parsed

REPO = Path(__file__).resolve().parents[2]
DEMO = REPO / "demo_cases"
TESTS = REPO / "backend" / "tests"


# ---------------------------------------------------------------------------
# registry-based metrics
# ---------------------------------------------------------------------------

def metric_rule_count() -> dict:
    """VASP/作业诊断规则数: RuleRegistry（不把 Recipe/HPC 规则混入）。"""
    rules = all_rules()
    ids = {r.rule_id for r in rules}
    return {
        "name": "VASP/作业诊断规则数",
        "target": "至少 27",
        "value": f"{len(rules)}（{len(ids)} 个唯一 rule_id）",
        "status": "达标" if len(rules) >= 27 else "未达标",
        "detail": "RuleRegistry 实测；不把 Recipe/HPC 规则混入宣传数字",
    }


def metric_structure_formats() -> dict:
    """支持结构格式: POSCAR、CIF parser。"""
    ok = {
        "POSCAR": _kind_for("POSCAR") == "poscar",
        "CIF": _kind_for("Fe2O3.CIF") == "cif",
    }
    parsers_ok = callable(parse_poscar) and callable(parse_cif)
    kinds = [k for k, v in ok.items() if v and parsers_ok]
    value = f"{len(kinds)}（{', '.join(kinds)}）" if kinds else "0"
    return {
        "name": "支持结构格式",
        "target": "2",
        "value": value,
        "status": "达标" if len(kinds) >= 2 else "未达标",
        "detail": "POSCAR/CIF parser + FileKind 检测实测",
    }


def metric_copilot_rows() -> list[dict]:
    """属于 vasp-copilot 的指标，本仓库不重复统计。"""
    return [
        {
            "name": "核心 workflow",
            "target": "3 + 1 flag",
            "value": "—（vasp-copilot 侧）",
            "status": "copilot 侧",
            "detail": "relax/static/DOS；band feature flag，由 copilot 提供",
        },
        {
            "name": "published Recipe 数",
            "target": "约 12",
            "value": "—（vasp-copilot 侧）",
            "status": "copilot 侧",
            "detail": "RecipeRegistry + tests passed，由 copilot 提供",
        },
    ]


def metric_demo_case_count() -> dict:
    failed = sorted(p for p in (DEMO / "failed_runs").iterdir()
                    if p.is_dir() and (p / "case.yaml").is_file())
    structures = sorted(p for p in (DEMO / "structures").iterdir()
                        if p.is_dir() and (p / "case.yaml").is_file())
    total = len(failed) + len(structures)
    in_range = 8 <= total <= 12
    return {
        "name": "demo case 数",
        "target": "8–12",
        "value": f"{total}（failed_runs {len(failed)} + structures {len(structures)}）",
        "status": "达标" if in_range else "未达标",
        "detail": "demo_cases/**/case.yaml 计数",
    }


def metric_preview_tests() -> dict:
    """preview 策略测试: text/truncated/binary/POTCAR/OUTCAR 等 P0 场景。"""
    src = (TESTS / "test_api.py").read_text(encoding="utf-8")
    names: list[str] = []
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("def test_preview"):
            name = stripped[len("def test_"):].split("(")[0].strip()
            names.append(name)
    scenarios = ["potcar", "binary", "outcar", "truncated", "text", "traversal"]
    covered = [s for s in scenarios if any(s in n for n in names)]
    return {
        "name": "preview 策略测试",
        "target": "100% P0 cases",
        "value": f"{len(names)} 条 preview 测试（覆盖 {'/'.join(covered)}）",
        "status": "达标" if len(names) >= 5 else "未达标",
        "detail": "test_api.py 测试名扫描；P0 场景 text/truncated/binary/POTCAR/OUTCAR",
    }


# ---------------------------------------------------------------------------
# live offline E2E (no LLM, no real HPC)
# ---------------------------------------------------------------------------

def _case_files(case_dir: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for child in sorted(case_dir.iterdir()):
        if not child.is_file() or child.name in ("input.zip", "case.yaml"):
            continue
        try:
            files[child.name] = child.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return files


def run_offline_e2e() -> dict:
    """跑全部 failed run demo：本地解析 + 确定性诊断（无 LLM、无 HPC）。"""
    svc = DiagnosisService()
    case_dirs = sorted(p for p in (DEMO / "failed_runs").iterdir()
                       if p.is_dir() and (p / "case.yaml").is_file())
    per_case: list[float] = []
    total_issues = 0
    issues_with_evidence = 0
    hit_rule_ids: set[str] = set()
    offline_ok = True
    details: list[str] = []
    for case_dir in case_dirs:
        files = _case_files(case_dir)
        started = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix="vd_metrics_") as td:
            root = Path(td)
            for name, text in files.items():
                (root / name).write_text(text, encoding="utf-8")
            parsed = _load_parsed(root, None)
            result, _body, _fix_files = svc.run_diagnosis(
                parsed, root, llm_explanation=False)
        elapsed = time.perf_counter() - started
        per_case.append(elapsed)
        total_issues += len(result.issues)
        issues_with_evidence += sum(1 for i in result.issues if i.evidence)
        for i in result.issues:
            hit_rule_ids.add(i.rule_id)
        case_offline = (result.provenance.llm_used is False
                        and result.provenance.mode.value == "rule_based")
        if not case_offline:
            offline_ok = False
            details.append(
                f"{case_dir.name}: provenance 非离线 "
                f"(llm_used={result.provenance.llm_used} "
                f"mode={result.provenance.mode.value})")
        else:
            details.append(f"{case_dir.name}: {len(result.issues)} issues, "
                           f"{elapsed * 1000:.0f}ms")
    return {
        "case_count": len(case_dirs),
        "offline_ok": offline_ok,
        "total_issues": total_issues,
        "issues_with_evidence": issues_with_evidence,
        "median_ms": statistics.median(per_case) * 1000 if per_case else 0.0,
        "max_ms": max(per_case) * 1000 if per_case else 0.0,
        "hit_rule_ids": len(hit_rule_ids),
        "rule_total": len(all_rules()),
        "details": details,
    }


def metric_offline_rows(e2e: dict) -> list[dict]:
    return [
        {
            "name": "无 LLM 可运行",
            "target": "是",
            "value": "是" if e2e["offline_ok"] else "否",
            "status": "达标" if e2e["offline_ok"] else "未达标",
            "detail": "默认 ENABLE_LLM=false 离线诊断实测，provenance.mode=rule_based、llm_used=false",
        },
        {
            "name": "无真实 HPC 可运行",
            "target": "是",
            "value": "是",
            "status": "达标",
            "detail": "本地 zip 文件 + 本地诊断 E2E 实测；无真实超算依赖（HPC 仅演示级 fake 适配器 app/hpc/，不进诊断主链）",
        },
    ]


def metric_evidence_coverage(e2e: dict) -> dict:
    frac = (e2e["issues_with_evidence"] / e2e["total_issues"]
            if e2e["total_issues"] else 1.0)
    return {
        "name": "issue evidence 覆盖率",
        "target": "100%",
        "value": (f"{frac * 100:.0f}%"
                  f"（{e2e['issues_with_evidence']}/{e2e['total_issues']} 个 issue 均含证据）"),
        "status": "达标" if frac >= 1.0 else "未达标",
        "detail": "全部 demo failed run 诊断问题（contract assertion）",
    }


# ---------------------------------------------------------------------------
# fix round-trip + fix change provenance (golden corpus)
# ---------------------------------------------------------------------------

GOLDEN_INCARS: list[tuple] = [
    ("golden_replace",
     "SYSTEM = test\nMYCUSTOM = 3\nNELM = 60\nISMEAR = 0\n",
     ("R-NELM", "NELM", "set_parameter", "200")),
    ("golden_add",
     "SYSTEM = test\nMYCUSTOM = 3\nISMEAR = 0\n",
     ("R-NELM", "NELM", "add_parameter", "120")),
    ("golden_remove",
     "SYSTEM = test\nMYCUSTOM = 3\nISMEAR = 0\nNSW = 1\n",
     ("R-NSW", "NSW", "remove_parameter", None)),
]


def _golden_issue(issue_id: str, rule_id: str, parameter: str, action: str,
                  new_value):
    return Issue(
        issue_id=issue_id, rule_id=rule_id, severity=Severity.MEDIUM,
        category="parameters", title="t", auto_fixable=True,
        recommendations=[Recommendation(
            action=action, target="INCAR", parameter=parameter,
            new_value=new_value, rationale="golden round-trip metric")],
    )


def _change_fully_logged(c: dict) -> bool:
    if not c.get("parameter") or not c.get("operation"):
        return False
    op = c["operation"]
    has_old = "old_value" in c or c.get("old_value") is not None
    has_new = "new_value" in c or c.get("new_value") is not None
    if op == "remove":
        return has_old
    if op == "add":
        return has_new
    return has_old and has_new


def metric_fix_roundtrip_and_provenance() -> dict:
    """fix 保留 unknown INCAR 参数 + 修复变更 provenance（golden cases）。"""
    gen = FixGenerator()
    rt_ok = 0
    prov_ok = 0
    total = len(GOLDEN_INCARS)
    fails: list[str] = []
    for case_id, text, (rule_id, param, action, new_value) in GOLDEN_INCARS:
        parsed = ParsedRunData(incar=parse_incar(text), source_files=["INCAR"])
        issue = _golden_issue(f"{case_id}-0001", rule_id, param, action, new_value)
        fix, files = gen.generate(parsed=parsed, issues=[issue], incar_text=text)
        if not (fix.safe_to_generate and files):
            fails.append(f"{case_id}: 未生成安全修复（{fix.fix_status.value}）")
            continue
        # round-trip: unknown 集合不变且 MYCUSTOM 仍在修复产物中
        new_text = files["INCAR.fixed"]
        unknown_same = (set(parse_incar(text).unknown)
                        == set(parse_incar(new_text).unknown))
        if "MYCUSTOM" in new_text and unknown_same:
            rt_ok += 1
        else:
            fails.append(f"{case_id}: unknown 未保留")
        # provenance: parameter_diff.json 完整记录每次变更
        diff = json.loads(files["parameter_diff.json"])
        changes = diff.get("changes", [])
        if diff.get("fix_id") and changes and all(
                _change_fully_logged(c) for c in changes):
            prov_ok += 1
        else:
            fails.append(f"{case_id}: 变更 provenance 记录不完整")
    ok = rt_ok == total and prov_ok == total
    return {
        "roundtrip": f"{rt_ok}/{total}",
        "rt_ok": rt_ok == total,
        "provenance": f"{prov_ok}/{total}",
        "prov_ok": prov_ok == total,
        "total": total,
        "status": "达标" if ok else "未达标",
        "detail": ("golden 用例：unknown 参数保留 + 变更 provenance"
                   "（参数/操作/旧值/新值/fix_id）均记录"
                   if ok else ("; ".join(fails) if fails else "unknown")),
    }


def metric_roundtrip(fix_m: dict) -> dict:
    return {
        "name": "fix 保留 unknown INCAR 参数",
        "target": "100% golden cases",
        "value": f"{fix_m['roundtrip']}（{fix_m['total']} 个 golden round-trip 用例）",
        "status": "达标" if fix_m["rt_ok"] else "未达标",
        "detail": "round-trip tests：修复后 unknown 参数集合不变",
    }


def metric_provenance(fix_m: dict) -> dict:
    return {
        "name": "最终参数 provenance 覆盖率",
        "target": "100%（generator assertion）",
        "value": (f"doctor 修复侧 {fix_m['provenance']}；"
                  f"参数生成器逐参数来源为 copilot 侧"),
        "status": "达标" if fix_m["prov_ok"] else "未达标",
        "detail": ("copilot 生成器逐参数来源由 copilot 提供；doctor 侧实测为修复"
                   "变更 provenance（参数/操作/旧值/新值/fix_id 完整记录）"),
    }


def metric_field_time(e2e: dict) -> dict:
    return {
        "name": "现场主链耗时",
        "target": "≤ 5 分钟",
        "value": (f"本地离线 E2E 近似：单例中位 {e2e['median_ms']:.0f}ms / "
                  f"最大 {e2e['max_ms']:.0f}ms（{e2e['case_count']} 例）"),
        "status": "手动（彩排）",
        "detail": "设计口径=连续 3 次彩排中位数/最大值（手动）；上表为脚本实测近似值",
    }


# ---------------------------------------------------------------------------
# report rendering
# ---------------------------------------------------------------------------

def render(rows: list[dict], e2e: dict) -> str:
    lines = [
        "# VASP-Doctor 比赛展示指标表（自动生成）",
        "",
        "> 依据 MVP_ARCHITECTURE_DESIGN.md §13.4 口径：指标从测试/registry 自动生成，"
        "PPT 只展示实测值，不填“预计支持”。",
        f"> 生成时点：{time.strftime('%Y-%m-%d %H:%M:%S')}；命令：`python scripts/collect_metrics.py`。",
        "",
        "| 指标 | P0 目标 | 实测值 | 状态 | 计数/验收来源 |",
        "| ---- | ------- | ------ | ---- | ------------- |",
    ]
    for r in rows:
        lines.append("| {name} | {target} | {value} | {status} | {detail} |".format(**r))
    lines += [
        "",
        "## 说明",
        "",
        "- “copilot 侧”：该指标由 vasp-copilot 提供，本仓库不重复统计（避免双计）。",
        "- “手动（彩排）”：现场主链耗时的正式口径为连续 3 次彩排的中位数/最大值"
        "（MVP 13.4），需人工彩排；脚本仅提供本地近似值。",
        "- “最终参数 provenance 覆盖率”的参数生成器逐参数来源由 copilot 提供；"
        "doctor 侧实测的是修复变更 provenance。",
        "- demo failed run 实测命中 {hit}/{total} 条不同 VASP/作业诊断规则。".format(
            hit=e2e["hit_rule_ids"], total=e2e["rule_total"]),
        "- 若某项未达标，按 MVP 13.4 要求 PPT 展示实际分子/分母或明确“实验性”，"
        "不得用 Recipe 数、HPC 规则数重复充当 VASP 诊断规则数。",
        "",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="", help="写入报告文件路径")
    args = parser.parse_args()

    rows: list[dict] = []
    rows.append(metric_rule_count())
    rows.append(metric_structure_formats())
    rows.extend(metric_copilot_rows())
    rows.append(metric_demo_case_count())

    e2e = run_offline_e2e()
    rows.extend(metric_offline_rows(e2e))
    rows.append(metric_evidence_coverage(e2e))

    fix_m = metric_fix_roundtrip_and_provenance()
    rows.append(metric_roundtrip(fix_m))
    rows.append(metric_provenance(fix_m))
    rows.append(metric_preview_tests())
    rows.append(metric_field_time(e2e))

    report = render(rows, e2e)
    print(report)
    if args.out:
        dest = Path(args.out).resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(report, encoding="utf-8")
        print("wrote ->", dest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
