"""Magnetic output evidence, without magnetic-state or convergence inference."""
from __future__ import annotations

import math
import re
from typing import Any


def finite(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(str(value).replace("D", "E").replace("d", "e"))
        return result if math.isfinite(result) else None
    except (ValueError, TypeError, OverflowError):
        return None


_TABLE = re.compile(r"^\s*magnetization\s*\((x|y|z|x,y,z)\)\s*$", re.I)
_PARAMS = "ISPIN|LSORBIT|LNONCOLLINEAR|BEXT|NUPDOWN|I_CONSTRAINED_M|ISTART|ICHARG"


def _fields(tokens: list[str], columns: list[str]) -> tuple[dict, dict]:
    raw = {column: tokens[i] if i < len(tokens) else None for i, column in enumerate(columns)}
    return {k: finite(v) for k, v in raw.items()}, raw


def parse_outcar_magnetic(text: str) -> dict:
    lines = text.splitlines()
    groups: list[list[dict]] = []
    parameters, nions, counts, species, direct, positions = [], [], [], [], [], []
    for i, line in enumerate(lines):
        lineno = i + 1
        if re.match(rf"^\s*(?:{_PARAMS})\s*=", line, re.I):
            for m in re.finditer(rf"\b({_PARAMS})\s*=\s*([^;]+?)(?=\s+[A-Z_]+\s*=|;|$)", line, re.I):
                name, raw = m.groups()
                parameters.append({"name": name.upper(), "raw": raw.strip(), "source_line": lineno})
        m = re.match(r"^\s*(?:(?:number of dos\s+NEDOS\s*=\s*\d+\s+)?number of ions\s+)?NIONS\s*=\s*(\S+)", line, re.I)
        if m:
            nions.append({"raw": m[1], "value": finite(m[1]), "source_line": lineno})
        m = re.match(r"^\s*ions per type\s*=\s*(.*)$", line, re.I)
        if m:
            raw = m[1].split()
            counts.append({"raw": raw, "values": [finite(v) for v in raw], "source_line": lineno})
        m = re.match(r"^\s*VRHFIN\s*=\s*([A-Z][a-z]?)\s*:", line)
        if m:
            species.append(m[1])
        m = re.match(r"^\s*number of electron\s+\S+\s+magnetization\s*(.*?)\s*$", line, re.I)
        if m:
            raw = m[1].split()
            values = [finite(v) for v in raw]
            direct.append({"raw": raw, "values": values if len(values) in (1, 3) and all(v is not None for v in values) else None,
                           "source_line": lineno, "source_file": "OUTCAR"})
        if re.match(r"^\s*position of ions in fractional coordinates\s*\(direct lattice\)\s*$", line, re.I):
            records = []
            for j in range(i + 1, len(lines)):
                raw = lines[j].split()
                values = [finite(v) for v in raw]
                if len(raw) != 3 or any(v is None for v in values):
                    break
                records.append({"raw": raw, "values": values, "source_line": j + 1})
            if not positions:
                positions = records  # only the explicitly initial fractional-position block
        match = _TABLE.match(line)
        if not match:
            continue
        axis = match[1].lower()
        if axis in ("x", "x,y,z") or not groups:
            groups.append([])
        table = {"axis": axis, "header_line": lineno, "columns": [], "rows": [], "total": None, "closed": False}
        groups[-1].append(table)
        started = False
        for j in range(i + 1, len(lines)):
            content = lines[j].strip()
            if not content:
                continue
            header = re.match(r"^#\s*of ion\s+(.*)$", content, re.I)
            if header and not started:
                table["columns"] = header[1].lower().split()
                continue
            if re.fullmatch(r"-+", content):
                if started:
                    table["closed"] = True
                    # A reported projection total belongs only immediately after the close.
                    k = j + 1
                    while k < len(lines) and not lines[k].strip():
                        k += 1
                    if k < len(lines) and re.match(r"^\s*tot\s+", lines[k], re.I):
                        values, raw = _fields(lines[k].split()[1:], table["columns"])
                        table["total"] = {"values": values, "raw": raw, "source_line": k + 1}
                    break
                started = True
                continue
            if not started:
                break
            tokens = content.split()
            if not re.fullmatch(r"[+-]?\d+", tokens[0]):
                break  # do not consume unrelated numeric output beyond a table
            try:
                ion = int(tokens[0])
            except ValueError:
                ion = None
            values, raw = _fields(tokens[1:], table["columns"])
            table["rows"].append({"ion": ion, "values": values, "raw": raw, "source_line": j + 1})
    return {"tables": groups[-1] if groups else [], "parameters": parameters, "nions": nions,
            "counts": counts, "species": species, "positions": positions, "direct_totals": direct[-1:]}


def parse_oszicar_magnetic(text: str) -> list[dict]:
    """Keep all mag tokens; noncollinear triples must never become one scalar."""
    result = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if not re.match(r"^\s*\d+\s+F\s*=", line):
            continue
        match = re.search(r"\bmag\s*=\s*(.*?)(?=\s+[A-Za-z_]+\s*=|$)", line)
        if match:
            raw = match[1].split()
            values = [finite(v) for v in raw]
            result.append({"raw": raw, "values": values if len(values) in (1, 3) and all(v is not None for v in values) else None,
                           "source_line": lineno, "source_file": "OSZICAR"})
    return result[-1:]
