"""Ordered input-reference / latest-projection comparison, never a phase verdict."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re

from ..parsers.magnetic_evidence import finite
from ..schemas.mode import MagnetizationAnalysisMode
from ..schemas.parsed import ParsedRunData

THRESHOLDS = {"near_zero": 0.05, "absolute_change": 0.10, "relative_change": 0.20,
              "attenuation_ratio": 0.50, "source": "display_heuristic"}
_NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eEdD][+-]?\d+)?$")
_ELEMENTS = set("H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv Ts Og".split())


def _assignment(parsed: ParsedRunData, name: str):
    return next((a for a in reversed(parsed.incar.assignments) if a.name.upper() == name), None)


def _reference(parsed: ParsedRunData, count: int | None) -> dict:
    assignment = _assignment(parsed, "MAGMOM")
    raw = assignment.raw_value if assignment else None
    values: list[float | None] = []
    valid = bool(raw)
    if raw:
        for token in raw.split():
            repeated = token.split("*")
            repetitions = 1
            number = token
            if len(repeated) == 2 and repeated[0].isdigit():
                try:
                    repetitions = int(repeated[0])
                except ValueError:
                    repetitions = 0
                number = repeated[1]
            elif len(repeated) != 1:
                valid = False
                values.append(None)
                continue
            if not 0 < repetitions <= 10_000 or len(values) + repetitions > 100_000:
                valid = False
                values.append(None)
                continue
            value = finite(number) if _NUMBER.fullmatch(number) else None
            valid = valid and value is not None
            values.extend([value] * repetitions)
    else:
        # Preserve old explicitly supplied values as evidence; absent raw source
        # cannot establish complete MAGMOM token expansion and is not trusted.
        old = parsed.incar.effective.get("MAGMOM")
        old = old if isinstance(old, list) else [] if old is None else [old]
        values = [finite(v) for v in old]
    restart = []
    for name in ("ISTART", "ICHARG"):
        value = parsed.incar.effective.get(name)
        if value is not None:
            restart.append(f"INCAR {name}={value}：输入设置迹象，不证明实际初始化磁矩。")
    for name in ("WAVECAR", "CHGCAR"):
        if name in parsed.source_files:
            restart.append(f"存在 {name}：重启密度/波函数迹象，MAGMOM 不一定用于初始化局域磁矩。")
    return {"source_file": "INCAR", "source_line": assignment.source_line if assignment else None,
            "raw": raw, "values": values, "valid": valid and count is not None and len(values) == count,
            "restart_notes": restart}


def _group(value: float | None) -> str:
    if value is None:
        return "unknown"
    if abs(value) <= THRESHOLDS["near_zero"]:
        return "near_zero"
    return "positive" if value > 0 else "negative"


def _guards(parsed: ParsedRunData, evidence: dict) -> tuple[bool, bool, list[str]]:
    unsupported = (parsed.calculation_mode.is_soc or parsed.calculation_mode.is_noncollinear
                   or parsed.calculation_mode.magnetization_analysis_mode == MagnetizationAnalysisMode.UNSUPPORTED_NONCOLLINEAR_OR_SOC
                   or any(t["axis"] != "x" for t in evidence.get("tables", []))
                   or any(len(t.get("raw", [])) == 3 for t in evidence.get("direct_totals", []) + parsed.oszicar.magnetic_evidence))
    constrained = False
    notes = []
    for name in ("LSORBIT", "LNONCOLLINEAR", "BEXT", "NUPDOWN", "I_CONSTRAINED_M"):
        candidates = []
        assignment = _assignment(parsed, name)
        if assignment:
            candidates.append(("INCAR", assignment.raw_value))
        elif name in parsed.incar.effective:
            candidates.append(("INCAR", str(parsed.incar.effective[name])))
        candidates.extend(("OUTCAR", p["raw"]) for p in evidence.get("parameters", []) if p["name"] == name)
        interpreted = []
        for source, raw in candidates:
            if name in ("LSORBIT", "LNONCOLLINEAR"):
                token = raw.split()[0].upper() if raw.split() else ""
                value = True if token in ("T", ".TRUE.", "TRUE") else False if token in ("F", ".FALSE.", "FALSE") else None
                if source == "INCAR" and len(raw.split()) != 1:
                    value = None
                if value is None or value:
                    unsupported = True
                    notes.append(f"{source} {name}={raw}：标量磁矩对照不适用或模式证据不充分。")
            else:
                tokens = raw.split()
                values = [finite(t) for t in tokens] if name == "BEXT" else [finite(tokens[0])] if tokens else []
                if source == "INCAR" and name != "BEXT" and len(tokens) != 1:
                    values = []  # reject a valid first token followed by malformed input
                if source == "INCAR" and name == "BEXT" and len(tokens) not in (1, 3):
                    values = []
                value = tuple(values) if values and all(v is not None for v in values) else None
                active = value is None or (any(v != 0 for v in value) if name != "NUPDOWN" else value != (-1.0,))
                if active:
                    constrained = True
                    notes.append(f"{source} {name}={raw}：约束/外场或无效设置，整体反向仅保留逐原子原值。")
            interpreted.append(value)
        if len(set(interpreted)) > 1:
            constrained = True
            notes.append(f"{name} 输入/输出回显存在冲突，不能验证该输出采用的设置。")
    return unsupported, constrained, notes


def _geometry(structure, output_positions: list[dict], count: int) -> str | None:
    if structure.source_file != "POSCAR" or len(output_positions) != count or not structure.positions_complete:
        return None
    if any(p["mode"] != "Direct" for p in structure.positions):
        return None
    try:
        for a, b in zip(structure.positions, output_positions):
            for x, y in zip(a["raw"], b["raw"]):
                dx, dy = Decimal(x.replace("D", "E").replace("d", "e")), Decimal(y.replace("D", "E").replace("d", "e"))
                tolerance = (Decimal(10) ** dx.as_tuple().exponent + Decimal(10) ** dy.as_tuple().exponent) / 2
                if tolerance >= Decimal("0.25"):
                    return None  # coarse tokens cannot safely verify atom correspondence
                difference = dx - dy
                periodic = abs(difference - difference.to_integral_value())
                if periodic > tolerance:
                    return "mismatch"
        return "geometry_checked"
    except (InvalidOperation, ValueError, OverflowError):
        return None


def build_magnetic_analysis(parsed: ParsedRunData) -> dict:
    structure = parsed.poscar
    valid_counts = bool(structure.counts) and all(isinstance(c, int) and not isinstance(c, bool) and c >= 0 for c in structure.counts)
    count = sum(structure.counts) if valid_counts else None
    if count is not None and not 0 < count <= 100_000:
        count = None
    structure_notes = []
    mapping = "index_aligned" if count and structure.positions_complete else "unavailable"
    if mapping == "unavailable":
        structure_notes.append("结构原子数量或有序坐标不完整，无法验证输入与输出原子对应。")
    if structure.elements and len(structure.elements) != len(structure.counts):
        mapping = "mismatch"
        structure_notes.append("结构元素分组与数量分组不一致。")
    evidence = parsed.outcar.magnetic_evidence
    for item in evidence.get("nions", []):
        if item["value"] != count:
            mapping = "mismatch"
            structure_notes.append(f"OUTCAR:{item['source_line']} NIONS 与结构原子数量不一致或无效。")
    for item in evidence.get("counts", []):
        if item["values"] != structure.counts:
            mapping = "mismatch"
            structure_notes.append(f"OUTCAR:{item['source_line']} ions per type 与结构数量分组不一致。")
    species = evidence.get("species", [])
    if structure.elements and species:
        # OUTCAR can repeat a full POTCAR header; only compare complete sequences.
        n = len(structure.elements)
        if any(element not in _ELEMENTS for element in structure.elements):
            structure_notes.append("结构元素标签含非确切化学符号，仅保留原标签；其化学身份未核验。")
        if len(species) % n == 0 and any(actual != label for i in range(0, len(species), n) for actual, label in zip(species[i:i+n], structure.elements) if label in _ELEMENTS):
            mapping = "mismatch"
            structure_notes.append("OUTCAR VRHFIN 元素回显与结构元素顺序不一致。")
    geometry = _geometry(structure, evidence.get("positions", []), count) if count else None
    if geometry == "mismatch":
        mapping = "mismatch"
        structure_notes.append("可直接比较的初始分数坐标与 POSCAR 原子顺序不一致（考虑周期等价与打印精度）。")
    elif geometry and mapping == "index_aligned":
        mapping = geometry
    if mapping == "index_aligned":
        structure_notes.append("按提交结构与 OUTCAR 原子编号对应；数量一致不证明文件来自同一次计算，同元素位点顺序未核验。")
    if not structure.elements:
        structure_notes.append("结构未提供元素标签，保留原子索引，元素未知。")
    tables = evidence.get("tables", [])
    scalar = next((t for t in tables if t["axis"] == "x"), None)
    raw_rows = []
    for table in tables:
        for i, row in enumerate(table["rows"]):
            raw_rows.append({"row_id": f"{table['header_line']}:{i}", "axis": table["axis"], "atom_index": row["ion"],
                             "values": row["values"], "raw": row["raw"], "source_line": row["source_line"], "table_header_line": table["header_line"]})
    rows = scalar["rows"] if scalar else []
    # Old parsed payloads retain raw output, but lack complete table provenance.
    if not tables:
        for i, row in enumerate(parsed.outcar.final_magnetization or []):
            raw_rows.append({"row_id": f"legacy:{i}", "axis": "x", "atom_index": row.get("ion"),
                             "values": {k: finite(v) for k, v in row.items() if k != "ion"}, "raw": {k: str(v) if v is not None else None for k, v in row.items() if k != "ion"},
                             "source_line": None, "table_header_line": None})
    ids = [row["ion"] for row in rows]
    valid_ids = bool(count) and all(isinstance(i, int) and 1 <= i <= count for i in ids)
    invalid_order = len(set(ids)) != len(ids) or (valid_ids and ids != sorted(ids)) or bool(ids) and not valid_ids
    if invalid_order:
        mapping = "mismatch"
        structure_notes.append("磁矩表含重复、无效、越界或乱序原子编号，禁止整体对应结论；原始行保留。")
    by_id = {row["ion"]: row for row in rows} if not invalid_order else {}
    valid_columns = bool(scalar and "tot" in scalar["columns"] and len(set(scalar["columns"])) == len(scalar["columns"]))
    table_complete = bool(scalar and valid_columns and scalar["closed"] and count and ids == list(range(1, count + 1))
                          and all(row["values"].get("tot") is not None for row in rows))
    reference = _reference(parsed, count)
    unsupported, constrained, guard_notes = _guards(parsed, evidence)
    if parsed.calculation_mode.magnetization_analysis_mode == MagnetizationAnalysisMode.UNAVAILABLE:
        scalar_allowed = False
    else:
        scalar_allowed = not unsupported
    paired = scalar_allowed and mapping in ("index_aligned", "geometry_checked") and reference["valid"]
    atoms = []
    labels = [element for element, quantity in zip(structure.elements, structure.counts) for _ in range(quantity)] if count and len(structure.elements) == len(structure.counts) else []
    for i in range(count or 0):
        row = by_id.get(i + 1)
        ref = reference["values"][i] if reference["valid"] and not unsupported and i < len(reference["values"]) else None
        out = row["values"].get("tot") if row and not unsupported else None
        available = paired and valid_columns and ref is not None and out is not None
        rg, og = _group(ref), _group(out)
        orientation, magnitude, attenuated = "unavailable", "unavailable", False
        reasons = []
        if available:
            orientation = "undefined" if "near_zero" in (rg, og) else "retained" if rg == og else "reversed"
            if og == "near_zero":
                magnitude = "near_zero"
            elif rg == "near_zero":
                magnitude = "emerged"
            else:
                # Decimal from finite value spellings avoids binary rounding
                # excluding the exact 20%/0.10 display boundary (2 -> 1.6).
                ref_abs, out_abs = Decimal(str(abs(ref))), Decimal(str(abs(out)))
                change = out_abs - ref_abs
                threshold = max(Decimal("0.10"), Decimal("0.20") * ref_abs)
                magnitude = "similar" if abs(change) < threshold else "increased" if change > 0 else "decreased"
            ref_abs, out_abs = Decimal(str(abs(ref))), Decimal(str(abs(out)))
            attenuated = out_abs <= Decimal("0.50") * ref_abs and ref_abs - out_abs >= Decimal("0.10")
        else:
            if not reference["valid"]:
                reasons.append("MAGMOM 原始赋值/完整展开或数量不满足对应要求。")
            if not paired:
                reasons.append("模式或结构/输出对应证据不足，未作变化分类。")
            if out is None:
                reasons.append("最新磁矩表该原子 tot 缺失、无效或非有限；不填零。")
        atoms.append({"atom_index": i + 1, "element": labels[i] if i < len(labels) else "",
                      "position": structure.positions[i] if i < len(structure.positions) else None,
                      "input_reference": ref, "output_moment": out, "output_raw": row["raw"].get("tot") if row and not unsupported else None,
                      "output_source_line": row["source_line"] if row and not unsupported else None, "delta_moment": finite(out - ref) if available else None,
                      "reference_group": rg, "output_group": og, "orientation": orientation, "magnitude": magnitude,
                      "attenuated": attenuated, "comparison_available": available, "reasons": reasons})
    complete_pairing = table_complete and paired and all(a["comparison_available"] for a in atoms)
    significant = [a for a in atoms if a["reference_group"] in ("positive", "negative")]
    pattern = "insufficient"
    if complete_pairing:
        if not significant:
            pattern = "near_zero_reference"
        elif len(significant) >= 2 and not constrained and all(a["orientation"] == "reversed" for a in significant) and all(a["output_group"] == "near_zero" for a in atoms if a["reference_group"] == "near_zero"):
            pattern = "global_reversed"
        elif all(a["orientation"] == "retained" for a in significant) and all(a["output_group"] == "near_zero" for a in atoms if a["reference_group"] == "near_zero"):
            pattern = "retained"
        else:
            pattern = "local_changes"
    output_notes = []
    provisional = parsed.outcar.truncated is not False or not table_complete
    if provisional:
        output_notes.append("输出/末次磁矩表不完整或缺少正常结束依据：最近投影为暂态/待核实，不替换为历史完整表。")
    output_notes.append("正常结束页脚不证明电子收敛；磁矩投影与全局停止文本的时间归属未验证。")
    if parsed.outcar.electronic_convergence_evidence:
        output_notes.append("OUTCAR 有电子停止文本，但不能将其归属到该磁矩表或证明磁态已收敛。")
    status = "unsupported" if unsupported else "ready" if complete_pairing and not provisional else "partial" if any(a["comparison_available"] for a in atoms) else "unavailable"
    summaries = {"retained": "显著输入参考的相对符号排列保持；幅值变化单独列示。",
                 "global_reversed": "全部显著参考相对输出整体反向，相对符号排列保持；这不证明物理等价或磁性基态。",
                 "local_changes": "存在局部符号、近零或诱导投影变化；幅值变化单独列示。",
                 "near_zero_reference": "输入参考均为显示近零组；输出投影原值与出现的局域磁矩单独列示。",
                 "insufficient": "完整排列证据不足，仅展示可验证的逐原子原值/对照。"}
    totals = []
    for item in evidence.get("direct_totals", []) + parsed.oszicar.magnetic_evidence:
        values = item["values"]
        if unsupported and values is not None and len(values) != 3:
            values = None
        totals.append({"kind": "cell_direct", "label": f"全胞总磁矩（{item['source_file']} 直接输出）",
                       "values": values, "raw": item["raw"], "source_file": item["source_file"], "source_line": item["source_line"],
                       "complete": values is not None, "scope": "latest_observation_unassigned"})
    if scalar and not unsupported:
        if scalar["total"]:
            total = scalar["total"]
            value = total["values"].get("tot")
            raw = total["raw"].get("tot")
            totals.append({"kind": "projection_reported", "label": "局域投影表 tot（非全胞总磁矩）", "values": [value] if value is not None else None,
                           "raw": [raw] if raw is not None else [], "source_file": "OUTCAR", "source_line": total["source_line"],
                           "complete": value is not None, "scope": "latest_observation_unassigned"})
        values = [r["values"].get("tot") for r in rows if r["values"].get("tot") is not None]
        totals.append({"kind": "projection_sum", "label": "已读取局域投影之和" + ("（部分行，非全胞总磁矩）" if not table_complete else "（非全胞总磁矩）"),
                       "values": [finite(sum(values))] if values and finite(sum(values)) is not None else None, "raw": [], "source_file": "OUTCAR", "source_line": scalar["header_line"],
                       "complete": table_complete, "scope": "latest_observation_unassigned"})
    notes = ["MAGMOM 是输入参考，不是实测初始局域磁矩；投影依赖 LORBIT/投影方法。", "参考分组不是磁性子晶格；总磁矩为零不能据此称为 AFM，也不判断磁性基态。",
             "全胞直接输出、投影表总计和局域投影之和分别列示；不同来源可能处于不同阶段。"] + guard_notes + reference["restart_notes"]
    if unsupported:
        notes.append("SOC/非共线或矢量模式：保留原始分量/未解析输出，不适用标量符号分类。")
    return {"version": "u5-display-v1", "status": status, "pattern": pattern, "summary": [summaries[pattern]], "notes": notes,
            "thresholds": dict(THRESHOLDS), "structure": {"source_file": structure.source_file or None, "atom_count": count, "mapping": mapping, "notes": structure_notes},
            "reference": reference, "output": {"source_file": "OUTCAR", "header_line": scalar["header_line"] if scalar else tables[0]["header_line"] if tables else None,
                                              "table_complete": table_complete, "provisional": provisional, "notes": output_notes},
            "atoms": atoms, "raw_rows": raw_rows, "totals": totals}
