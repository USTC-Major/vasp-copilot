"""OUTCAR energy records for PP-22A, independent of diagnostic final_energy.

Only the final ionic ``FREE ENERGIE`` summary is an energy record. The
similarly shaped energy table inside every electronic iteration is not one.
The caller owns source bytes, SHA256 and storage identity; this adapter is pure
and never opens user files or POTCAR. Line numbers are one based.
"""
from __future__ import annotations

import math
import re
from typing import Any

PARSER_VERSION = "pp22a-outcar-1"
ENERGY_FIELDS = ("sigma_to_zero_ev", "without_entropy_ev", "free_energy_toten_ev")
_BANNER = re.compile(r"^\s*vasp\.(\S+)", re.I)
_ITERATION = re.compile(r"\bIteration\s+(\d+)\s*\(\s*(\d+)\s*\)")
_SUMMARY = re.compile(r"^\s*FREE\s+ENERGIE\s+OF\s+THE\s+ION-ELECTRON\s+SYSTEM\s*\(eV\)\s*$", re.I)
_TOTEN = re.compile(r"^\s*free\s+energy\s+TOTEN\s*=\s*(\S+)\s+eV\s*$", re.I)
_PAIR = re.compile(r"^\s*energy\s+without\s+entropy\s*=\s*(\S+)\s+energy\s*\(sigma\s*->\s*0\)\s*=\s*(\S+)\s*$", re.I)
_EDIFF_STOP = re.compile(r"^\s*-*\s*aborting\s+loop\s+because\s+EDIFF\s+is\s+reached\s*[.!]?\s*-*\s*$", re.I)
_ELECTRONIC_FAILED = re.compile(r"^\s*-*\s*(?:aborting\s+loop\s+because\s+NELM\s+is\s+reached|electronic\s+self-consistency\s+was\s+not\s+achieved)\s*[.!]?\s*-*\s*$", re.I)
_IONIC_STOP = re.compile(r"reached\s+required\s+accuracy\s*[-\u2013\u2014]?\s*stopping\s+structural\s+energy\s+minimi[sz]ation", re.I)
_FOOTER = "General timing and accounting informations"
_NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eEdD][+-]?\d+)?$")
_ELEMENTS = set("H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv Ts Og".split())
_INT_PARAMS = {"ISPIN", "ICHARG", "IBRION", "NSW", "NELM", "NELMIN", "NELMDL", "ISMEAR", "LDAUTYPE", "IMAGES", "I_CONSTRAINED_M", "IVDW", "NBANDS", "NKPTS", "IALGO"}
_FLOAT_PARAMS = {"SIGMA", "ENCUT", "EDIFF", "EDIFFG", "AEXX", "HFSCREEN", "NELECT", "POTIM", "TEBEG", "TEEND", "AMIX", "BMIX"}
_BOOL_PARAMS = {"LNONCOLLINEAR", "LSORBIT", "LDAU", "LHFCALC", "LEPSILON", "LCALCEPS", "LOPTICS", "LPEAD", "LCLIMB", "ML_LMLFF", "ML_LML", "LSFBXC"}
_STR_PARAMS = {"GGA", "METAGGA", "ALGO", "SYSTEM", "LASPH", "ML_MODE"}
_ARRAY_PARAMS = {"LDAUL", "LDAUU", "LDAUJ"}
_PARAMS = _INT_PARAMS | _FLOAT_PARAMS | _BOOL_PARAMS | _STR_PARAMS | _ARRAY_PARAMS
_ASSIGNMENT = re.compile(r"\b(" + "|".join(sorted(_PARAMS, key=len, reverse=True)) + r")\s*=\s*", re.I)


def _number(raw: str) -> float:
    value = float(raw.replace("D", "E").replace("d", "e"))
    if not math.isfinite(value):
        raise ArithmeticError("nonfinite")
    if not _NUMBER.fullmatch(raw):
        raise ValueError("not a VASP number")
    return value


def _bool(raw: str) -> bool | None:
    raw = raw.strip(".").upper()
    return True if raw in {"T", "TRUE"} else False if raw in {"F", "FALSE"} else None


def _issue(result: dict, code: str, message: str, *, warning: bool = False,
           line: int | None = None) -> None:
    item = {"code": code, "severity": "warning" if warning else "error", "message": message,
            "recoverable_by_manual": code.startswith("COMPOSITION_")}
    if line is not None:
        item["source_line"] = line
    if item not in result["issues"]:
        result["issues"].append(item)
        result["warnings" if warning else "errors"].append(message)


def _repeated(sequence: list, width: int) -> list | None:
    """OUTCAR may repeat the complete species list; do not deduplicate atoms."""
    if not sequence or len(sequence) % width:
        return None
    first = sequence[:width]
    return first if all(sequence[i:i + width] == first for i in range(0, len(sequence), width)) else None


def _header(lines: list[str], end: int, result: dict) -> None:
    titles, vrhfins, counts, nions = [], [], [], []
    title_lines, vrhfin_lines, count_lines = [], [], []
    parameters, parameter_lines = {}, {}
    for i, line in enumerate(lines[:end], 1):
        match = re.match(r"\s*TITEL\s*=\s*(.*?)\s*$", line)
        if match:
            titles.append(match[1]); title_lines.append(i)
        match = re.match(r"\s*VRHFIN\s*=\s*([A-Z][a-z]?)\s*:", line)
        if match:
            vrhfins.append(match[1]); vrhfin_lines.append(i)
        match = re.search(r"\bions\s+per\s+type\s*=\s*(.*?)\s*$", line, re.I)
        if match:
            count_lines.append(i)
            try:
                values = [_number(raw) for raw in match[1].split()]
                if not values or any(value <= 0 or value != int(value) for value in values):
                    raise ValueError("invalid counts")
                counts.append([int(value) for value in values])
            except (ValueError, ArithmeticError, OverflowError):
                _issue(result, "COMPOSITION_INVALID", "ions per type 必须为有限正整数", line=i)
        for match in re.finditer(r"\bNIONS\s*=\s*(\S+)", line):
            try:
                value = _number(match[1])
                if value <= 0 or value != int(value):
                    raise ValueError("invalid NIONS")
                nions.append(int(value))
            except (ValueError, ArithmeticError, OverflowError):
                _issue(result, "COMPOSITION_INVALID", "NIONS 必须为有限正整数", line=i)
        # Parameter echoes start with an assignment: descriptions and SYSTEM
        # prose must not become scientific metadata.
        if not re.match(r"^\s*[A-Za-z_]+\s*=", line):
            continue
        assignments = list(_ASSIGNMENT.finditer(line))
        if assignments and assignments[0][1].upper() == "SYSTEM":
            parameters["SYSTEM"] = line[assignments[0].end():].strip()
            parameter_lines["SYSTEM"] = i
            continue
        for j, match in enumerate(assignments):
            name = match[1].upper()
            raw = line[match.end():assignments[j + 1].start() if j + 1 < len(assignments) else len(line)].strip()
            tokens = raw.split()
            if not tokens:
                continue
            try:
                if name in _ARRAY_PARAMS:
                    value = []
                    for token in tokens:
                        if not _NUMBER.fullmatch(token):
                            break
                        value.append(_number(token))
                    if not value:
                        raise ValueError("missing array")
                elif name in _INT_PARAMS:
                    value = _number(tokens[0].rstrip(";"))
                    if value != int(value):
                        raise ValueError("not integer")
                    value = int(value)
                elif name in _FLOAT_PARAMS:
                    value = _number(tokens[0].rstrip(";"))
                elif name in _BOOL_PARAMS:
                    value = _bool(tokens[0].rstrip(";"))
                    if value is None:
                        raise ValueError("not boolean")
                else:
                    value = tokens[0].rstrip(";").upper()
            except (ValueError, ArithmeticError, OverflowError):
                _issue(result, "METADATA_INVALID", f"{name} 回显无法解析，需核对原始输入", warning=True, line=i)
                continue
            parameters[name] = value
            parameter_lines[name] = i
    metadata = result["metadata"]
    metadata.update(parameters=parameters, parameter_lines=parameter_lines)
    # Dataset declarations are independent of ion counts. Preserve their known
    # identity even when a composition-only gap needs explicit manual repair;
    # never infer species counts or U-array ordering from these declarations.
    metadata["dataset_declarations"] = []
    for title, source_line in zip(titles, title_lines):
        parts = title.split()
        match = re.match(r"([A-Z][a-z]?)(?:_|$)", parts[1]) if len(parts) >= 2 else None
        if not match or match[1] not in _ELEMENTS:
            continue
        element = match[1]
        metadata["dataset_declarations"].append({"element": element, "titel": title, "source_line": source_line})
        previous = metadata["potcar_datasets"].get(element)
        if previous is not None and previous != title:
            _issue(result, "DATASET_CONFLICT", f"{element} 同时使用不同赝势数据集，首版不能自动比较", line=source_line)
        metadata["potcar_datasets"][element] = title
    if not counts:
        _issue(result, "COMPOSITION_MISSING", "缺少可靠 ions per type 组成计数，请提供人工组成")
        if parameters.get("LDAU"):
            _issue(result, "METHOD_METADATA_INCOMPLETE", "缺少类型计数，无法可靠对齐逐元素 U 定义，需核对", warning=True)
        return
    if any(value != counts[0] for value in counts[1:]):
        _issue(result, "COMPOSITION_CONFLICT", "同段 ions per type 计数冲突，不能自动确定组成")
        return
    count = counts[0]
    width = len(count)
    title_types = _repeated(titles, width) if titles else None
    vrhfin_types = _repeated(vrhfins, width) if vrhfins else None
    if (titles and title_types is None) or (vrhfins and vrhfin_types is None):
        _issue(result, "COMPOSITION_CONFLICT", "元素身份的重复顺序或数据集声明冲突")
        return
    title_elements = []
    for title in title_types or []:
        parts = title.split()
        element = re.match(r"([A-Z][a-z]?)(?:_|$)", parts[1]) if len(parts) >= 2 else None
        if not element or element[1] not in _ELEMENTS:
            _issue(result, "COMPOSITION_INVALID", "TITEL 元素身份无法明确识别")
            return
        title_elements.append(element[1])
    elements = vrhfin_types or title_elements
    if not elements or any(element not in _ELEMENTS for element in elements):
        _issue(result, "COMPOSITION_MISSING", "缺少与计数对齐的 TITEL／VRHFIN 元素身份")
        return
    if title_elements and vrhfin_types and title_elements != vrhfin_types:
        _issue(result, "COMPOSITION_CONFLICT", "TITEL 与 VRHFIN 元素顺序不一致")
        return
    if nions and any(value != sum(count) for value in nions):
        _issue(result, "COMPOSITION_NIONS_MISMATCH", "ions per type 总数与 NIONS 不一致")
        return
    for index, (element, amount) in enumerate(zip(elements, count)):
        title = title_types[index] if title_types else None
        species = {"element": element, "count": amount, "titel": title,
                   "vrhfin": vrhfin_types[index] if vrhfin_types else None,
                   "source_lines": {"titel": title_lines[index] if title_types else None,
                                    "vrhfin": vrhfin_lines[index] if vrhfin_types else None,
                                    "ions_per_type": count_lines[0]}}
        metadata["species"].append(species)
        result["composition"][element] = result["composition"].get(element, 0) + amount
        if title:
            old = metadata["potcar_datasets"].get(element)
            if old is not None and old != title:
                _issue(result, "DATASET_CONFLICT", f"{element} 同时使用不同赝势数据集，首版不能自动比较")
            metadata["potcar_datasets"][element] = title
    metadata["potcar"] = list(metadata["species"])
    result["provenance"]["composition_lines"] = {
        "titel": title_lines, "vrhfin": vrhfin_lines, "ions_per_type": count_lines}
    if parameters.get("LDAU"):
        arrays = [parameters.get(name) for name in ("LDAUL", "LDAUU", "LDAUJ")]
        if "LDAUTYPE" not in parameters or any(not array or len(array) != width for array in arrays):
            _issue(result, "METHOD_METADATA_INCOMPLETE", "DFT+U 的类型或逐元素 LDAUL／U／J 不完整，需核对", warning=True)
        else:
            for index, element in enumerate(elements):
                u = dict(zip(("l", "u", "j"), (array[index] for array in arrays)))
                if u["l"] != int(u["l"]):
                    _issue(result, "METHOD_U_CONFLICT", "LDAUL 必须为整数轨道角动量")
                old = metadata["u_by_element"].get(element)
                if old is not None and old != u:
                    _issue(result, "METHOD_U_CONFLICT", f"{element} 的多个类型有不同 U 定义，首版不能自动比较")
                metadata["u_by_element"][element] = u


def _support(result: dict) -> None:
    meta = result["metadata"]
    p = meta["parameters"]
    reasons = []
    if p.get("ICHARG", 0) >= 10:
        reasons.append("非自洽固定电荷密度")
    if p.get("IBRION") == 0:
        reasons.append("分子动力学")
    if p.get("IBRION") not in {None, -1, 1, 2, 3}:
        reasons.append("响应或特殊离子算法")
    if p.get("LSORBIT") or p.get("LNONCOLLINEAR"):
        reasons.append("NCL／SOC")
    if p.get("ISPIN") not in {None, 1, 2}:
        reasons.append("未知自旋模式")
    if any(p.get(key) for key in ("LEPSILON", "LCALCEPS", "LOPTICS", "LPEAD", "LCLIMB", "ML_LMLFF", "ML_LML")) or p.get("IMAGES", 0) > 0:
        reasons.append("响应、多图像或 ML 输出")
    algo = p.get("ALGO", "").upper()
    if re.search(r"G0?W", algo) or algo in {"BSE", "CHI", "ACFDT", "ACFDTR", "RPA", "RPAR", "CRPA", "TDHF", "TIMEEV"} or p.get("ML_MODE"):
        reasons.append("GW／响应／ML 算法")
    if algo in {"NONE", "NOTHING", "EIGENVAL"} or p.get("IALGO") in {-1, 2, 3}:
        reasons.append("固定轨道后处理或性能测试")
    if p.get("LSFBXC") or p.get("METAGGA") == "MBJ":
        reasons.append("无一致总能定义的势方法")
    if p.get("LDAU") and p.get("LDAUTYPE") == 3:
        reasons.append("线性响应 U 参数计算")
    if reasons:
        _issue(result, "RUN_UNSUPPORTED", "首版不支持自动能量比较：" + "、".join(reasons))
    ibrion, nsw = p.get("IBRION"), p.get("NSW")
    if nsw == 0 or (ibrion == -1 and nsw is not None):
        run_type = "static"
    elif ibrion in {1, 2, 3} and nsw is not None and nsw > 0:
        run_type = "relaxation"
    elif ibrion == 0:
        run_type = "md"
    else:
        run_type = "unknown"
    meta["run_type"] = run_type
    result["status"]["ionic_applicability"] = "not_applicable" if run_type == "static" else "applicable" if run_type == "relaxation" else "unknown"
    if run_type == "unknown" or "ICHARG" not in p or "ISPIN" not in p:
        _issue(result, "RUN_TYPE_UNKNOWN", "运行类型或自洽／自旋信息不完整，请核对", warning=True)
    gga = p.get("GGA", "")
    titles = list(meta["potcar_datasets"].values())
    if p.get("LHFCALC"):
        method = "hybrid"
    elif p.get("METAGGA", "") not in {"", "--", "F", "NONE"}:
        method = "meta-GGA:" + str(p["METAGGA"])
    elif gga == "PE" or (gga in {"", "--"} and titles and all(title.startswith("PAW_PBE ") for title in titles)):
        method = "PBE+U" if p.get("LDAU") else "PBE"
    else:
        method = "GGA:" + gga if gga not in {"", "--"} else "unknown"
    meta["method"] = method
    if method not in {"PBE", "PBE+U"} or p.get("IVDW", 0):
        _issue(result, "METHOD_UNKNOWN", "该泛函或修正未在首版自动比较支持范围内，需核对方法", warning=True)
    if not titles:
        _issue(result, "METADATA_MISSING", "缺少赝势数据集 TITEL；同元素方法一致性无法确认", warning=True)
    meta["potcar_identity_verified"] = False  # TITEL is not a POTCAR hash.
    codes = [item["code"] for item in result["issues"] if item["severity"] == "error" or item["code"] in {
        "RUN_TYPE_UNKNOWN", "METHOD_UNKNOWN", "METHOD_METADATA_INCOMPLETE", "METADATA_MISSING", "METADATA_INVALID", "STEP_UNKNOWN"}]
    meta["support"] = {"automatic_comparison": not codes, "reasons": list(dict.fromkeys(codes))}


def parse_energy_outcar(text: str) -> dict[str, Any]:
    """Return JSON-safe candidates, independent completion and scoped evidence.

    Parse errors are data, not exceptions. Only COMPOSITION_* errors may be
    repaired through an explicit caller-owned manual composition override.
    Unknown/incomplete convergence requires a separate user inclusion decision.
    """
    result: dict[str, Any] = {
        "parser_version": PARSER_VERSION, "energy_unit": "eV",
        "energy_fields": dict.fromkeys(ENERGY_FIELDS), "composition": {},
        "status": {"completion": "unknown", "electronic_converged": None,
                   "ionic_converged": None, "ionic_applicability": "unknown"},
        "metadata": {"parameters": {}, "species": [], "potcar": [], "potcar_datasets": {},
                     "u_by_element": {}, "method": "unknown", "run_type": "unknown",
                     "support": {"automatic_comparison": False, "reasons": []}},
        "provenance": {"run_segment": None, "run_segment_count": 0, "selected_ionic_step": None,
                       "selected_block_index": None, "electronic_iteration": None,
                       "field_lines": {}, "block_start_line": None, "block_end_line": None},
        "evidence": {"run_ended": [], "electronic": [], "ionic": []},
        "errors": [], "warnings": [], "issues": [],
    }
    if not isinstance(text, str):
        _issue(result, "ENERGY_INVALID", "OUTCAR 必须为已解码文本")
        return result
    lines = text.splitlines()
    banners = [(i, match[1]) for i, line in enumerate(lines, 1) if (match := _BANNER.match(line))]
    result["provenance"]["run_segment_count"] = len(banners) or (1 if lines else 0)
    if len(banners) > 1:
        _issue(result, "RUN_MULTIPLE_SEGMENTS", "检测到多个 VASP 运行段，请拆分来源后导入")
        result["metadata"]["support"]["reasons"] = ["RUN_MULTIPLE_SEGMENTS"]
        return result
    result["provenance"]["run_segment"] = 1 if lines else None
    result["metadata"]["vasp_version"] = banners[0][1] if banners else None
    first_context = next((i for i, line in enumerate(lines) if _ITERATION.search(line) or _SUMMARY.match(line)), len(lines))
    _header(lines, first_context, result)
    current_step, electronic, scope = None, None, 0
    iterations, summaries, electronic_events, ionic_events, footers = [], [], [], [], []
    for i, line in enumerate(lines, 1):
        match = _ITERATION.search(line)
        if match:
            step, iteration = int(match[1]), int(match[2])
            if current_step is not None and step < current_step:
                _issue(result, "RUN_SEGMENT_AMBIGUOUS", "离子步编号倒退，可能为拼接运行，请拆分来源", line=i)
            if step == current_step and electronic is not None and iteration < electronic:
                _issue(result, "RUN_SEGMENT_AMBIGUOUS", "电子迭代编号重置且未开始新离子步，来源归属不明确", line=i)
            if step != current_step:
                scope += 1
            current_step, electronic = step, iteration
            iterations.append({"line": i, "step": step, "iteration": iteration, "scope": scope})
        if i > first_context + 1 and re.match(r"\s*(?:TITEL\s*=|VRHFIN\s*=|ions\s+per\s+type\s*=)", line, re.I):
            _issue(result, "RUN_SEGMENT_AMBIGUOUS", "计算开始后再次出现组成头部，可能为拼接运行", line=i)
        if _EDIFF_STOP.match(line) or _ELECTRONIC_FAILED.match(line):
            electronic_events.append({"source_line": i, "ionic_step": current_step,
                                      "electronic_iteration": electronic, "scope": scope,
                                      "kind": "electronic_ediff_stop" if _EDIFF_STOP.match(line) else "electronic_explicit_failure"})
        if _IONIC_STOP.search(line):
            ionic_events.append({"source_line": i, "ionic_step": current_step, "scope": scope,
                                 "kind": "ionic_required_accuracy_stop"})
        if _FOOTER in line:
            footers.append(i)
        if _SUMMARY.match(line):
            summaries.append({"start": i, "step": current_step, "iteration": electronic, "scope": scope})
    if footers and (any(item["line"] > footers[0] for item in iterations) or
                    any(item["start"] > footers[0] for item in summaries)):
        _issue(result, "RUN_SEGMENT_AMBIGUOUS", "结束标记后又出现计算迭代，来源可能包含多个运行")
    blocks = []
    for index, summary in enumerate(summaries, 1):
        boundary = min([len(lines) + 1] + [item["line"] for item in iterations if item["line"] > summary["start"]]
                       + [item["start"] for item in summaries if item["start"] > summary["start"]]
                       + [line for line in footers if line > summary["start"]])
        values, source_lines, invalid = {}, {}, False
        for line_no in range(summary["start"] + 1, boundary):
            line = lines[line_no - 1]
            toten, pair = _TOTEN.match(line), _PAIR.match(line)
            if toten or pair:
                pairs = [("free_energy_toten_ev", toten[1])] if toten else [("without_entropy_ev", pair[1]), ("sigma_to_zero_ev", pair[2])]
                for name, raw in pairs:
                    if name in values:
                        _issue(result, "ENERGY_BLOCK_AMBIGUOUS", "同一离子汇总块出现重复能量字段", line=line_no)
                        invalid = True
                    try:
                        values[name] = _number(raw)
                        source_lines[name] = line_no
                    except ArithmeticError:
                        _issue(result, "ENERGY_NONFINITE", "离子汇总能量含 NaN／Inf 或数值溢出，不能纳入计算", line=line_no)
                        invalid = True
                    except (ValueError, OverflowError):
                        _issue(result, "ENERGY_INVALID", "离子汇总能量字段无效，不能纳入计算", line=line_no)
                        invalid = True
                if pair:
                    break
            elif line.strip() and not re.fullmatch(r"\s*-+\s*", line):
                # No crossing force/eigenvalue tables or electronic energy
                # headings to complete a missing field in another context.
                break
        block = {**summary, "index": index, "values": values, "field_lines": source_lines,
                 "end": max(source_lines.values(), default=summary["start"]),
                 "complete": not invalid and len(values) == 3}
        blocks.append(block)
    ambiguous = any(issue["code"] in {"RUN_SEGMENT_AMBIGUOUS", "ENERGY_BLOCK_AMBIGUOUS"} for issue in result["issues"])
    candidates = [block for block in blocks if block["complete"]]
    if ambiguous:
        _support(result)
        return result
    # Completion is an independent file fact even when no usable energy
    # exists; an ENERGY_MISSING error still prevents calculation.
    result["status"]["completion"] = "completed" if footers else "incomplete" if lines else "unknown"
    result["evidence"]["run_ended"] = [{"kind": "timing_footer", "source_line": line, "run_segment": 1} for line in footers]
    if not candidates:
        _issue(result, "ENERGY_MISSING", "未找到同一完整离子汇总块的三种能量字段")
        _support(result)
        return result
    selected = candidates[-1]
    if selected["step"] is not None and sum(block["complete"] and block["scope"] == selected["scope"] for block in blocks) > 1:
        _issue(result, "ENERGY_BLOCK_AMBIGUOUS", "同一离子步存在多个完整汇总块，不能确定取值归属")
        _support(result)
        return result
    result["energy_fields"].update(selected["values"])
    result["provenance"].update(selected_ionic_step=selected["step"], selected_block_index=selected["index"],
                                electronic_iteration=selected["iteration"], field_lines=selected["field_lines"],
                                block_start_line=selected["start"], block_end_line=selected["end"],
                                complete_block_count=len(candidates), summary_block_count=len(blocks))
    if selected["step"] is None:
        _issue(result, "STEP_UNKNOWN", "汇总块没有可核对的离子步编号；收敛证据不自动归属", warning=True)
    trailing = any(item["line"] > selected["end"] for item in iterations) or blocks[-1] is not selected
    ending = [line for line in footers if line > selected["end"]]
    result["evidence"]["run_ended"] = [{"kind": "timing_footer", "source_line": line, "run_segment": 1} for line in ending]
    result["status"]["completion"] = "completed" if ending and not trailing else "incomplete"
    if trailing:
        _issue(result, "TRAILING_INCOMPLETE", "后续离子步或汇总块不完整，当前取值来自较早完整块", warning=True)
    if not ending:
        _issue(result, "RUN_INCOMPLETE", "未观察到运行结束计时标记，文件可能未完成或被截断", warning=True)
    scoped_electronic = [event for event in electronic_events if event["scope"] == selected["scope"] and
                         event["ionic_step"] == selected["step"] and selected["step"] is not None and
                         event["electronic_iteration"] == selected["iteration"] and event["source_line"] < selected["start"]]
    result["evidence"]["electronic"] = [{**event, "selected_block_index": selected["index"]} for event in scoped_electronic]
    if scoped_electronic:
        last = scoped_electronic[-1]
        if last["kind"] == "electronic_explicit_failure":
            result["status"]["electronic_converged"] = False
        elif result["metadata"]["parameters"].get("EDIFF") != 0:
            result["status"]["electronic_converged"] = True
    if result["status"]["electronic_converged"] is None:
        _issue(result, "ELECTRONIC_UNKNOWN", "所选离子步没有明确可归属的电子收敛证据", warning=True)
    _support(result)
    next_context = min([len(lines) + 1] + [item["line"] for item in iterations if item["line"] > selected["end"]]
                       + [block["start"] for block in blocks if block["start"] > selected["end"]])
    scoped_ionic = [event for event in ionic_events if event["scope"] == selected["scope"] and selected["step"] is not None
                    and selected["end"] < event["source_line"] < next_context]
    if result["metadata"]["run_type"] == "relaxation":
        result["evidence"]["ionic"] = [{**event, "selected_block_index": selected["index"]} for event in scoped_ionic]
        if scoped_ionic and result["metadata"]["parameters"].get("EDIFFG") != 0:
            result["status"]["ionic_converged"] = True
        else:
            _issue(result, "IONIC_UNKNOWN", "所选弛豫步没有明确可归属的离子收敛停止证据", warning=True)
    return result
