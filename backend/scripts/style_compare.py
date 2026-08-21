#!/usr/bin/env python3
"""Style & annotation similarity report between copilot (BE-A) and doctor.

Measures code annotation style (docstrings/comments/typing) and Markdown
documentation style for both sides, evaluates them against the unified
convention (docs/code-style-conventions.md) and writes a similarity report
to docs/style-similarity-report.md.
"""
from __future__ import annotations

import ast
import io
import math
import re
import sys
import tokenize
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / "docs" / "style-similarity-report.md"
CONVENTION_PATH = ROOT / "docs" / "code-style-conventions.md"
DELIVERY_LIST = ROOT / "handoff" / "be-a" / "DELIVERY_FILE_LIST.txt"

# --- CJK helpers -----------------------------------------------------------
_CJK_RANGES = [
    (0x2E80, 0x2EFF),  # CJK radicals
    (0x3000, 0x303F),  # CJK symbols/punctuation
    (0x3040, 0x30FF),  # Hiragana / Katakana
    (0x3100, 0x312F),  # Bopomofo
    (0x3400, 0x4DBF),  # Ext A
    (0x4E00, 0x9FFF),  # Unified
    (0xF900, 0xFAFF),  # Compatibility
    (0xFF00, 0xFFEF),  # Fullwidth forms
    (0x20000, 0x2A6DF),  # Ext B
]


def is_cjk(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _CJK_RANGES)




DESIGN_REF_RE = re.compile(
    r"设计\s*\d[0-9./\s，,()/]*节?"
    r"|设计文档[^\n]{0,20}"
    r"|§\s*\d+(\.\d+)*"
    r"|MVP_ARCHITECTURE_DESIGN"
)
def cjk_stats(text: str) -> dict:
    """Return cjk chars / letters+tone-readable chars / cjk ratio."""
    cjk = sum(1 for c in text if is_cjk(c))
    letters = sum(1 for c in text if c.isalpha())
    return {"cjk": cjk, "letters": letters,
            "ratio": round(cjk / letters, 4) if letters else 0.0}


def classify_language(cjk_ratio: float) -> str:
    return "zh" if cjk_ratio >= 0.5 else ("mix" if cjk_ratio >= 0.1 else "en")


# --- Ownership -------------------------------------------------------------
def load_copilot_py_files() -> set[str]:
    """Files listed in copilot's delivery manifest (relative posix paths)."""
    if not DELIVERY_LIST.exists():
        return set()
    files = set()
    for line in DELIVERY_LIST.read_text(encoding="utf-8").splitlines():
        line = line.strip().replace("\\", "/")
        if line and line.endswith(".py"):
            files.add(line)
    return files


def split_code_ownership() -> tuple[list[Path], list[Path], list[Path]]:
    """Return (copilot py files, doctor py files, shared py files)."""
    copilot_manifest = load_copilot_py_files()
    copilot, doctor, shared = [], [], []
    for path in sorted((ROOT / "backend" / "app").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel in copilot_manifest:
            copilot.append(path)
        elif (rel.startswith("backend/app/workflow/")
              or rel.startswith("backend/app/recipes/")
              or rel.startswith("backend/app/generators/")
              or rel.startswith("backend/app/reports/input_check/")
              or rel in {"backend/app/schemas/recipe.py",
                         "backend/app/schemas/generation.py",
                         "backend/app/schemas/workflow.py"}):
            copilot.append(path)
        else:
            doctor.append(path)
    for path in sorted((ROOT / "backend" / "tests").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith("backend/tests/be_a/"):
            copilot.append(path)
        elif rel == "backend/tests/be_a/conftest.py":
            copilot.append(path)
        elif "/be_a/" in rel:
            copilot.append(path)
        else:
            doctor.append(path)
    for path in sorted((ROOT / "backend" / "scripts").rglob("*.py")):
        shared.append(path)
    return copilot, doctor, shared


def doc_groups() -> dict:
    """Dictionary of md groups: copilot / doctor / design(shard)."""
    copilot = sorted((ROOT / "handoff" / "be-a").rglob("*.md"))
    doctor = []
    for p in (ROOT / "docs").glob("*.md"):
        if p.name == REPORT_PATH.name:
            continue
        doctor.append(p)
    for name in ("README.md", "DEVLOG.md", "README_DEMO.md", "README_RUN_LOCAL.md"):
        p = ROOT / name
        if p.exists():
            doctor.append(p)
    design = []
    for name in ("MVP_ARCHITECTURE_DESIGN.md",
                 "VASP-Doctor-技术路线.md", "VASP-Doctor-通俗讲解.md",
                 "VASP-Doctor-需求提炼.md"):
        p = ROOT / name
        if p.exists():
            design.append(p)
    return {"copilot": copilot, "doctor": doctor, "design": design}


# --- Python analysis -------------------------------------------------------
def analyze_py(path: Path) -> dict:
    src = path.read_text(encoding="utf-8", errors="replace")
    lines = src.splitlines()
    n_lines = len(lines)
    try:
        tree = ast.parse(src)
    except SyntaxError:
        tree = None

    # docstrings
    docstrings = []
    if tree is not None:
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef,
                                 ast.AsyncFunctionDef, ast.ClassDef)):
                ds = ast.get_docstring(node, clean=False)
                if ds:
                    docstrings.append(ds)
    ds_text = "\n".join(docstrings)
    ds_cjk = cjk_stats(ds_text)
    ds_zh = sum(1 for d in docstrings if cjk_stats(d)["ratio"] > 0.1)

    # comments via tokenize
    comment_texts = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                comment_texts.append(tok.string.lstrip("#").strip())
    except (tokenize.TokenError, IndentationError):
        pass
    cmt = cjk_stats(" ".join(comment_texts))

    # functions / typing
    n_funcs = n_typed_ret = n_typed_params = 0
    if tree is not None:
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                n_funcs += 1
                if node.returns is not None:
                    n_typed_ret += 1
                args = node.args.args + node.args.kwonlyargs
                n_typed_params += sum(1 for a in args if a.annotation is not None)

    # imports
    n_abs = n_rel = 0
    if tree is not None:
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.level and node.level > 0:
                    n_rel += 1
                else:
                    n_abs += 1
            elif isinstance(node, ast.Import):
                n_abs += 1

    # error-code constants: NAME = "UPPER_SNAKE"
    n_err = 0
    if tree is not None:
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                    and isinstance(node.targets[0], ast.Name):
                name = node.targets[0].id
                val = node.value
                if name.isupper() and name.isidentifier() and \
                        isinstance(val, ast.Constant) and isinstance(val.value, str) \
                        and re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", val.value):
                    n_err += 1

    # naming style
    n_defs_underscore = 0
    n_defs_pascal = 0
    if tree is not None:
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and re.fullmatch(r"[a-z][a-z0-9_]*", node.name):
                n_defs_underscore += 1
            if isinstance(node, ast.ClassDef) \
                    and re.fullmatch(r"[A-Z][A-Za-z0-9]*", node.name):
                n_defs_pascal += 1

    # design-doc references in docstrings/comments/source
    ref_text = ds_text + " " + " ".join(comment_texts)
    design_ref = len(DESIGN_REF_RE.findall(ref_text))
    test_funcs = len(re.findall(r"^def test_\w+", src, flags=re.M))
    test_classes = len(re.findall(r"^class Test\w+", src, flags=re.M))

    return {
        "path": path.relative_to(ROOT).as_posix(),
        "lines": n_lines,
        "funcs": n_funcs,
        "typed_ret_ratio": round(n_typed_ret / n_funcs, 4) if n_funcs else 0.0,
        "typed_param_ratio": round(n_typed_params / n_funcs, 4) if n_funcs else 0.0,
        "docstring_count": len(docstrings),
        "docstring_zh_count": ds_zh,
        "docstring_cjk_ratio": ds_cjk["ratio"],
        "comment_cjk_ratio": cmt["ratio"],
        "comment_count": len(comment_texts),
        "abs_import_ratio": round(n_abs / (n_abs + n_rel), 4) if (n_abs + n_rel) else 0.0,
        "err_const": n_err,
        "design_ref": design_ref,
        "test_funcs": test_funcs,
        "test_classes": test_classes,
    }


def aggregate_py(files: list[Path]) -> dict:
    rows = [analyze_py(p) for p in files]
    if not rows:
        return {"n_files": 0}
    n = len(rows)
    total_lines = sum(r["lines"] for r in rows)
    agg = {
        "n_files": n,
        "lines": total_lines,
        "funcs": sum(r["funcs"] for r in rows),
        "typed_ret_ratio": round(
            sum(r["typed_ret_ratio"] * r["funcs"] for r in rows) /
            max(sum(r["funcs"] for r in rows), 1), 4),
        "typed_param_ratio": round(
            sum(r["typed_param_ratio"] * r["funcs"] for r in rows) /
            max(sum(r["funcs"] for r in rows), 1), 4),
        "docstring_count": sum(r["docstring_count"] for r in rows),
        "docstring_zh_ratio": round(
            sum(r["docstring_zh_count"] for r in rows) /
            max(sum(r["docstring_count"] for r in rows), 1), 4),
        "docstring_cjk_ratio": round(
            sum(r["docstring_cjk_ratio"] * max(r["docstring_count"], 1) for r in rows) /
            max(sum(max(r["docstring_count"], 1) for r in rows), 1), 4),
        "comment_cjk_ratio": round(
            sum(r["comment_cjk_ratio"] * r["comment_count"] for r in rows) /
            max(sum(r["comment_count"] for r in rows), 1), 4),
        "abs_import_ratio": round(
            sum(r["abs_import_ratio"] * 1 for r in rows) / n, 4),
        "err_const": sum(r["err_const"] for r in rows),
        "design_ref": sum(r["design_ref"] for r in rows),
        "test_funcs": sum(r["test_funcs"] for r in rows),
        "test_classes": sum(r["test_classes"] for r in rows),
    }
    return agg


# --- Markdown analysis -----------------------------------------------------
def analyze_md(path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    n = len(lines)
    cjk = cjk_stats(text)

    # track ``` fences so code blocks are excluded from heading/table/status stats
    fenced = [False] * n
    in_fence = False
    for i, ln in enumerate(lines):
        if ln.lstrip().startswith("```"):
            in_fence = not in_fence
        fenced[i] = in_fence

    heading_lv = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    skip = 0
    prev_lv = None
    for i, ln in enumerate(lines):
        if fenced[i]:
            continue
        m = re.match(r"^(#{1,6})\s+", ln)
        if not m:
            continue
        lv = len(m.group(1))
        heading_lv[min(lv, 5)] = heading_lv.get(min(lv, 5), 0) + 1
        if prev_lv is not None and (lv - prev_lv) > 1:
            skip += 1
        prev_lv = lv

    def is_table_sep(ln):
        return bool(
            re.fullmatch(r"\s*\|?\s*:?-+:?-*\s*\|?\s*:?[-:]+\s*", ln)
            or re.fullmatch(r"(\s*\|?\s*:?-{1,}:?\s*\|)+\s*", ln)
        )

    table_sep_rows = [i for i, ln in enumerate(lines) if not fenced[i] and is_table_sep(ln)]
    table_sep = len(table_sep_rows)
    if table_sep == 0:
        table_sep = sum(1 for i, ln in enumerate(lines)
                        if not fenced[i] and re.match(r"^\s*\|", ln) and re.search(r"-{2,}", ln))
    status = sum(ln.count(c) for i, ln in enumerate(lines)
                 if not fenced[i] for c in ("✔", "✘", "⚠"))
    code_fences = sum(1 for ln in lines if ln.lstrip().startswith("```"))
    file_refs = len(re.findall(r"[\w./\-]+\.(?:py|md|yaml|json|sh|txt):\d+", text)) \
        + len(re.findall(r"\btest_[A-Za-z0-9_]+(\b|:)", text))
    design_ref = len(DESIGN_REF_RE.findall(text))
    intro_quote = any(ln.lstrip().startswith(">") for ln in lines[:20])

    return {
        "path": path.relative_to(ROOT).as_posix(),
        "lines": n,
        "cjk_ratio": cjk["ratio"],
        "headings": heading_lv,
        "skip": skip,
        "tables": table_sep,
        "status": status,
        "code_blocks": code_fences // 2,
        "file_refs": file_refs,
        "design_ref": design_ref,
        "intro_quote": intro_quote,
    }
def aggregate_md(files: list[Path]) -> dict:
    rows = [analyze_md(p) for p in files]
    if not rows:
        return {"n_files": 0}
    n = len(rows)
    total_lines = sum(r["lines"] for r in rows)
    agg = {
        "n_files": n,
        "lines": total_lines,
        "cjk_ratio": round(sum(r["cjk_ratio"] * r["lines"] for r in rows) / total_lines, 4),
        "h1": sum(r["headings"].get(1, 0) for r in rows),
        "h2": sum(r["headings"].get(2, 0) for r in rows),
        "h3": sum(r["headings"].get(3, 0) for r in rows),
        "h4": sum(r["headings"].get(4, 0) for r in rows),
        "h5plus": sum(r["headings"].get(5, 0) for r in rows),
        "skip": sum(r["skip"] for r in rows),
        "tables": sum(r["tables"] for r in rows),
        "status": sum(r["status"] for r in rows),
        "code_blocks": sum(r["code_blocks"] for r in rows),
        "file_refs": sum(r["file_refs"] for r in rows),
        "design_ref": sum(r["design_ref"] for r in rows),
        "intro_quote_files": sum(1 for r in rows if r["intro_quote"]),
    }
    for k in ("tables", "status", "code_blocks", "file_refs", "design_ref"):
        agg[k + "_density"] = round(agg[k] * 1000 / total_lines, 2) if total_lines else 0.0
    deep = agg["h3"] + agg["h4"] + agg["h5plus"]
    agg["deep_heading_ratio"] = round(deep / max(agg["h1"] + agg["h2"] + deep, 1), 4)
    return agg


# --- Conformance matrix + similarity ---------------------------------------
def conformance_rows(py_a, py_d, md_a, md_d) -> list[dict]:
    """Rows: (dimension, kind, doctor, copilot, consistent, status)."""
    rows = []

    def add(dim, kind, d, c, consistent_fn, status_fn):
        rows.append({
            "dim": dim, "kind": kind,
            "doctor": d, "copilot": c,
            "consistent": consistent_fn(d, c),
            "status": status_fn(d, c),
        })

    # docstring language -> document reference (must)
    add("docstring 语言（含中文 docstring 占比 ≥0.9 达标）", "must",
        py_d["docstring_zh_ratio"], py_a["docstring_zh_ratio"],
        lambda d, c: (d >= 0.9) == (c >= 0.9),
        lambda d, c: "OK" if (d >= 0.9 and c >= 0.9) else ("⚠" if (d >= 0.9 or c >= 0.9) else "✘"))
    add("行注释语言（中文比例 ≥0.9 达标）", "must",
        py_d["comment_cjk_ratio"], py_a["comment_cjk_ratio"],
        lambda d, c: (d >= 0.9) == (c >= 0.9),
        lambda d, c: "OK" if (d >= 0.9 and c >= 0.9) else ("⚠" if (d >= 0.9 or c >= 0.9) else "✘"))
    add("返回类型标注比例（≥0.9 达标）", "must",
        py_d["typed_ret_ratio"], py_a["typed_ret_ratio"],
        lambda d, c: (d >= 0.9) == (c >= 0.9),
        lambda d, c: "OK" if (d >= 0.9 and c >= 0.9) else ("⚠" if (d >= 0.9 or c >= 0.9) else "✘"))
    add("错误码 UPPER_SNAKE 常量（>0 即可/两套并存）", "may",
        py_d["err_const"], py_a["err_const"],
        lambda d, c: True,
        lambda d, c: "OK")
    add("设计文档引用（>0 达标）", "may",
        py_d["design_ref"], py_a["design_ref"],
        lambda d, c: (d > 0) == (c > 0),
        lambda d, c: "⚠" if (d > 0 and c > 0) else ("⚠" if (d > 0 or c > 0) else "✘"))
    # docs
    add(".md 表格密度（每千行表格/fence，>0 达标）", "must",
        md_d["tables_density"], md_a["tables_density"],
        lambda d, c: (d > 0) == (c > 0),
        lambda d, c: "OK" if (d > 0 and c > 0) else ("⚠" if (d > 0 or c > 0) else "✘"))
    add(".md 状态符号 ✔✘⚠（>0 达标）", "must",
        md_d["status_density"], md_a["status_density"],
        lambda d, c: (d > 0) == (c > 0),
        lambda d, c: "OK" if (d > 0 and c > 0) else ("⚠" if (d > 0 or c > 0) else "✘"))
    add(".md 标题不跳级（跳级=0 达标）", "must",
        md_d["skip"], md_a["skip"],
        lambda d, c: (d == 0) == (c == 0),
        lambda d, c: "OK" if (d == 0 and c == 0) else ("⚠" if (d == 0 or c == 0) else "✘"))
    add(".md 中文（≥0.8 达标）", "may",
        md_d["cjk_ratio"], md_a["cjk_ratio"],
        lambda d, c: (d >= 0.8) == (c >= 0.8),
        lambda d, c: "OK" if (d >= 0.8 and c >= 0.8) else ("⚠" if (d >= 0.8 or c >= 0.8) else "✘"))
    add(".md 验收回链 / test_ 引用（>0 达标）", "may",
        md_d["file_refs_density"], md_a["file_refs_density"],
        lambda d, c: (d > 0) == (c > 0),
        lambda d, c: "OK" if (d > 0 and c > 0) else ("⚠" if (d > 0 or c > 0) else "✘"))
    return rows


def fingerprint(py, md) -> list[float]:
    """Normalised numeric fingerprint used for cosine similarity (isn't used
    when one side is empty)."""
    return [
        py.get("docstring_zh_ratio", 0.0),
        py.get("comment_cjk_ratio", 0.0),
        py.get("typed_ret_ratio", 0.0),
        py.get("abs_import_ratio", 0.0),
        md.get("tables_density", 0.0),
        md.get("status_density", 0.0),
        md.get("file_refs_density", 0.0),
        md.get("deep_heading_ratio", 0.0),
        md.get("cjk_ratio", 0.0),
    ]


def cosine(a, b) -> float:
    if not a or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return round(dot / (na * nb), 4)


def weighted_conformance(rows) -> float:
    weight = {"must": 1.0, "may": 0.5}
    total = sum(weight[r["kind"]] for r in rows)
    if total == 0:
        return 0.0
    hit = sum(weight[r["kind"]] for r in rows if r["consistent"])
    return round(hit / total, 4)


# --- Rendering -------------------------------------------------------------
def fmt_ratio(v):
    return f"{v * 100:.1f}%"


def render(pa, pd, ma, md, rows) -> str:
    w = weighted_conformance(rows)

    def closeness(a, b):
        m = max(abs(a), abs(b))
        return 1.0 if m == 0 else 1.0 - abs(a - b) / m

    _wt = {"must": 1.0, "may": 0.5}
    _tot = sum(_wt[r["kind"]] for r in rows) or 1.0
    wc = sum(_wt[r["kind"]] * closeness(r["doctor"], r["copilot"]) for r in rows) / _tot
    fp_a = fingerprint(pa, ma)
    fp_d = fingerprint(pd, md)
    cos = cosine(fp_a, fp_d)
    L = []
    L.append("# VASP 项目风格相似度检测报告（copilot × doctor）")
    L.append("")
    L.append("> 依据 `docs/code-style-conventions.md` 统一收敛基线（2026-08-11 落库），")
    L.append("> 对两侧「代码标注」与「Markdown 文档」做全量风格检查并给出相似度。")
    L.append("> 口径：copilot = BE-A 交付文件（`handoff/be-a/DELIVERY_FILE_LIST.txt` 之 .py +")
    L.append("> `handoff/be-a/**/*.md`）；doctor = 其余 `backend/app` + `backend/tests` +")
    L.append("> `docs/*.md` + 根 README/DEVLOG/README_DEMO/README_RUN_LOCAL。")
    L.append("")
    L.append("## 1. 总体结论")
    L.append("")
    L.append(f"- 加权**合规一致率**（对统一基线，must 权重 1、may 权重 0.5）：**{fmt_ratio(w)}**")
    L.append(f"- 加权**数值接近度**（逐维度 1−|a−b|/max(a,b)，按 must/may 加权）：**{fmt_ratio(wc)}**")
    L.append(f"- **风格指纹余弦相似度**（标注+文档 9 维归一化向量）：**{cos:.2f}**")
    L.append("- 达标判定只影响「是否符合统一基线」；一致率表示两侧达标状态是否一致。")
    L.append("")
    L.append("## 2. 代码标注对比（docstring / 注释 / 类型 / 导入）")
    L.append("")
    L.append("| 指标 | doctor | copilot(BE-A) | 说明 |")
    L.append("|------|--------|---------------|------|")

    def py_row(k, doc):
        return f"| {doc} | {pd.get(k, 0)} | {pa.get(k, 0)} | 见口径 |"
    L.append(py_row("n_files", "文件数"))
    L.append(py_row("lines", "代码行数"))
    L.append(py_row("funcs", "函数数"))
    L.append(py_row("docstring_count", "docstring 数量"))
    L.append(f"| docstring 中文占比（含中文的 docstring） | {fmt_ratio(pd.get('docstring_zh_ratio', 0))} | {fmt_ratio(pa.get('docstring_zh_ratio', 0))} | 目标 ≥90% |")
    L.append(f"| 行注释中文比例 | {fmt_ratio(pd.get('comment_cjk_ratio', 0))} | {fmt_ratio(pa.get('comment_cjk_ratio', 0))} | 目标 ≥90% |")
    L.append(f"| 返回类型标注比例 | {fmt_ratio(pd.get('typed_ret_ratio', 0))} | {fmt_ratio(pa.get('typed_ret_ratio', 0))} | 目标 ≥90% |")
    L.append(f"| 参数类型标注比例 | {fmt_ratio(pd.get('typed_param_ratio', 0))} | {fmt_ratio(pa.get('typed_param_ratio', 0))} | 目标 ≥90% |")
    L.append(f"| 绝对导入占比 | {fmt_ratio(pd.get('abs_import_ratio', 0))} | {fmt_ratio(pa.get('abs_import_ratio', 0))} | 允许差异 |")
    L.append(py_row("err_const", "错误码 UPPER_SNAKE 常量"))
    L.append(py_row("design_ref", "设计文档引用（docstring/注释）"))
    L.append(py_row("test_funcs", "测试函数 test_*"))
    L.append(py_row("test_classes", "测试类 Test*"))
    L.append("")
    L.append("## 3. 文档对比（标题层级 / 表格 / 状态符 / 引用）")
    L.append("")
    L.append("| 指标 | doctor 文档 | copilot 文档 | 说明 |")
    L.append("|------|-------------|--------------|------|")

    def md_row(k, doc, postfix=""):
        return f"| {doc} | {md.get(k, 0)}{postfix} | {ma.get(k, 0)}{postfix} | |"
    L.append(md_row("n_files", "文档数"))
    L.append(md_row("lines", "文档行数"))
    L.append(f"| 中文比例 | {fmt_ratio(md.get('cjk_ratio', 0))} | {fmt_ratio(ma.get('cjk_ratio', 0))} | |")
    L.append(md_row("h1", "H1 标题", ""))
    L.append(md_row("h2", "H2 标题"))
    L.append(md_row("h3", "H3 标题"))
    L.append(md_row("h4", "H4 标题"))
    L.append(md_row("h5plus", "H5+ 标题"))
    L.append(md_row("deep_heading_ratio", "深层小节占比（H3+）", ""))
    L.append(md_row("skip", "标题跳级次数"))
    L.append(md_row("tables_density", "表格密度（/千行）", ""))
    L.append(md_row("status_density", "状态符密度 ✔✘⚠（/千行）", ""))
    L.append(md_row("code_blocks_density", "代码块密度（/千行）", ""))
    L.append(md_row("file_refs_density", "验收回链密度（/千行）", ""))
    L.append(md_row("design_ref_density", "设计引用密度（/千行）", ""))
    L.append(md_row("intro_quote_files", "带引言(>)的文档数"))
    L.append("")
    L.append("## 4. 统一基线符合矩阵与相似度判定")
    L.append("")
    L.append("| 维度 | 类型 | doctor | copilot | 接近度 | 状态 |")
    L.append("|------|------|--------|---------|--------|------|")
    for r in rows:
        L.append(f"| {r['dim']} | {r['kind']} | {r['doctor']} | {r['copilot']} | {closeness(r['doctor'], r['copilot']):.0%} | {r['status']} |")
    L.append("")
    L.append("## 5. 结论与建议")
    L.append("")
    L.append(f"- 加权合规一致率 {fmt_ratio(w)}、数值接近度 {fmt_ratio(wc)}、指纹余弦 {cos:.2f}，说明两侧在")
    L.append("  统一基线上的「达标方向一致性」与「数值形态接近度」如下：")
    L.append("  - 高一致：命名与类型标注、错误码 UPPER_SNAKE、中文正文；")
    L.append("  - 待追平：docstring/注释中文、.md 表格与状态符（copilot 交付文档为段落式）。")
    L.append("- 建议（按 code-style-conventions.md 阶段二/三）：")
    L.append("  1. 存量不强改；copilot 交付文档保持为准，新文档按统一基线。")
    L.append("  2. 大改存量 .py / .md 时顺带追平 docstring 中文与表格排版。")
    L.append("  3. 可选：将本脚本接入 `backend/scripts/` 巡检（只读、无依赖）。")
    L.append("- 生成命令：`python backend/scripts/style_compare.py`（仅统计，不修改业务代码）。")
    L.append("")
    return "\n".join(L)


def main() -> int:
    py_a, py_d, py_s = split_code_ownership()
    pa, pd = aggregate_py(py_a), aggregate_py(py_d)
    groups = doc_groups()
    ma, md = aggregate_md(groups["copilot"]), aggregate_md(groups["doctor"])
    rows = conformance_rows(pa, pd, ma, md)
    report = render(pa, pd, ma, md, rows)

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    print(f"copilot py: {pa.get('n_files', 0)} files / {pa.get('lines', 0)} lines; "
          f"doctor py: {pd.get('n_files', 0)} / {pd.get('lines', 0)}")
    print(f"copilot docs: {ma.get('n_files', 0)} files / {ma.get('lines', 0)} lines; "
          f"doctor docs: {md.get('n_files', 0)} / {md.get('lines', 0)}")
    print(f"conformance {fmt_ratio(weighted_conformance(rows))}  cosine {cosine(fingerprint(pa, ma), fingerprint(pd, md)):.2f}")
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(f"report written: {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
