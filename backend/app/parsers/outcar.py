from __future__ import annotations

import re
from typing import Any, Optional

from ..schemas.mode import CalculationMode, MagnetizationAnalysisMode
from ..schemas.parsed import OutcarData


_VERSION_RE = re.compile(r"vasp\.(\S+)")
_BINARY_HINT_RE = re.compile(r"vasp\.\S+\s+\(.*?\)\s+(\S+)")
_ISPIN_RE = re.compile(r"ISPIN\s*=\s*(\d)")
_LNONCOLLINEAR_RE = re.compile(r"LNONCOLLINEAR\s*=\s*([TFtf])")
_LSORBIT_RE = re.compile(r"LSORBIT\s*=\s*([TFtf])")

# lines that indicate an aborted / errored run (evidence for rules)
_ERROR_PATTERNS = [
    r"ZBRENT",
    r"ZHEGV",
    r"BRMIX",
    r"EDDDAV:\s*could not update charge density",
    r"TOO FEW BANDS",
    r"NO POTCAR",
    r"POTCAR.*not.*found",
    r"internal error",
    r"error reading",
    r"couldn't open",
    r"out of memory",
    r"segmentation",
    r"killed",
    r"ran out of memory",
    r"Fatal error",
    r"Error EDDDAV",
    r"RMM-DIIS: failed",
    r"general lattice",
    r"Too few bands",
]

_MAG_HEADER_COLLINEAR = "magnetization (x)"
_MAG_HEADER_NONCOLLINEAR = "magnetization (x,y,z)"
_FINFO_KEY = "General timing and accounting informations"

# 结构优化收敛停止的明确文本（离子收敛证据）：仅完整语句才算证据，
# 容忍空白差异与 minimisation/minimization 拼写；无上下文的
# "reached required accuracy" 不构成证据。未命中保持 None（证据不足）。
_IONIC_CONVERGENCE_RE = re.compile(
    r"reached\s+required\s+accuracy\s*[-\u2013\u2014]?\s*stopping\s+structural"
    r"\s+energy\s+minimi[sz]ation",
    re.IGNORECASE)


def _is_t(value: Optional[str]) -> bool:
    return bool(value) and value.strip().upper().startswith("T")


def _classify(v: Any) -> str:
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    return "string"


def _to_float(tok: str) -> Optional[float]:
    try:
        return float(tok)
    except ValueError:
        return None


def parse_outcar(text: str) -> OutcarData:
    data = OutcarData()
    lines = text.splitlines()
    n = len(lines)

    # 1) version + binary hint from the banner (top of file)
    for line in lines[:80]:
        m = _BINARY_HINT_RE.search(line)
        if m:
            data.vasp_binary_hint = m.group(1)
        m2 = _VERSION_RE.search(line)
        if m2 and data.vasp_version is None:
            data.vasp_version = m2.group(1)
        if data.vasp_version and data.vasp_binary_hint:
            break

    # 2) calculation mode flags (echoed by VASP in OUTCAR)
    spin_pol = None
    noncollinear = None
    soc = None
    for line in lines:
        m = _ISPIN_RE.search(line)
        if m:
            try:
                spin_pol = int(m.group(1)) == 2
            except ValueError:
                spin_pol = None
        m = _LNONCOLLINEAR_RE.search(line)
        if m:
            noncollinear = _is_t(m.group(1))
        m = _LSORBIT_RE.search(line)
        if m:
            soc = _is_t(m.group(1))

    mode = data.calculation_mode
    if noncollinear is not None:
        mode.is_noncollinear = bool(noncollinear)
    if soc is not None:
        mode.is_soc = bool(soc)
    if spin_pol is not None:
        mode.is_spin_polarized = bool(spin_pol)

    if mode.is_noncollinear or mode.is_soc:
        mode.magnetization_analysis_mode = (
            MagnetizationAnalysisMode.UNSUPPORTED_NONCOLLINEAR_OR_SOC
        )
    elif mode.is_spin_polarized:
        mode.magnetization_analysis_mode = MagnetizationAnalysisMode.COLLINEAR
    else:
        mode.magnetization_analysis_mode = MagnetizationAnalysisMode.UNAVAILABLE

    # 3) error lines (timestamped or not)
    for i, line in enumerate(lines):
        for pat in _ERROR_PATTERNS:
            if re.search(pat, line):
                data.error_lines.append({"line": i + 1, "text": line.strip()})
                break

    # 4) termination / truncation
    has_final_info = any(_FINFO_KEY in ln for ln in lines)
    data.normal_termination = has_final_info and not data.error_lines
    data.truncated = (not has_final_info) or bool(data.error_lines)
    if not has_final_info and data.normal_termination is not None:
        data.normal_termination = False

    # 5) final energy: last "free  energy   TOTEN" line (energetic)
    last_toten: Optional[float] = None
    for ln in reversed(lines):
        m = re.search(r"free\s+energy\s+TOTEN\s*=\s*([-\d.EeDd+]+)", ln)
        if m:
            try:
                last_toten = float(m.group(1).replace("D", "E"))
                break
            except ValueError:
                continue
    data.final_energy = last_toten

    # 5.5) 结构优化收敛证据：仅完整停止语句命中时为 True，否则 None（证据不足）。
    data.ionic_convergence_reached = (
        True if any(_IONIC_CONVERGENCE_RE.search(ln) for ln in lines) else None)

    # 6) final magnetization (only meaningful for collinear)
    if mode.magnetization_analysis_mode == MagnetizationAnalysisMode.COLLINEAR:
        _extract_magnetization(data, lines)

    return data


def _extract_magnetization(data: OutcarData, lines: list[str]) -> None:
    n = len(lines)
    per_atom: list[dict[str, Any]] = []
    total: Optional[dict[str, Any]] = None
    i = n - 1
    while i >= 0:
        if _MAG_HEADER_COLLINEAR in lines[i]:
            j = i + 1
            # skip the "# of ion" header row and advance to the first separator
            while j < n and "----" not in lines[j]:
                j += 1
            j += 1  # past the opening separator
            while j < n:
                ln = lines[j].strip()
                if not ln:
                    j += 1
                    continue
                if "----" in ln:
                    # end of atom rows; next non-empty line is tot (skip it)
                    j += 1
                    while j < n and lines[j].strip() == "":
                        j += 1
                    if j < n:
                        parts = lines[j].split()
                        if len(parts) >= 4 and (parts[0].lower() == "tot" or _to_float(parts[0]) == 0.0):
                            vals = [_to_float(p) for p in parts[1:]]
                            total = {"s": vals[0] if len(vals) > 0 else None,
                                     "p": vals[1] if len(vals) > 1 else None,
                                     "d": vals[2] if len(vals) > 2 else None,
                                     "tot": vals[3] if len(vals) > 3 else None}
                    break
                parts = ln.split()
                try:
                    first = int(parts[0])
                except (ValueError, IndexError):
                    j += 1
                    continue
                vals = [_to_float(p) for p in parts[1:]]
                per_atom.append({"ion": first,
                                 "s": vals[0] if len(vals) > 0 else None,
                                 "p": vals[1] if len(vals) > 1 else None,
                                 "d": vals[2] if len(vals) > 2 else None,
                                 "tot": vals[3] if len(vals) > 3 else None})
                j += 1
            break
        i -= 1
    if per_atom:
        data.final_magnetization = per_atom
    data.magnetization_total = total
