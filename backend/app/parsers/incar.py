from __future__ import annotations

import re
from typing import Any

from ..schemas.parsed import DuplicateParam, IncarAssignment, IncarData

_TRUE = ".TRUE."
_FALSE = ".FALSE."
BOOL_MAP = {
    ".TRUE.": True, ".FALSE.": False,
    "TRUE": True, "FALSE": False,
    "T": True, "F": False,
}
MAX_REPETITION = 10000
MAX_TOKENS = 100_000

# tags we understand as native INCAR parameters (a curated common set; anything
# outside is treated as an "unrecognized" user tag and preserved verbatim).
KNOWN_TAGS = {
    "SYSTEM", "ENCUT", "EDIFF", "EDIFFG", "NSW", "IBRION", "ISIF", "ISYM",
    "ISMEAR", "SIGMA", "ISPIN", "MAGMOM", "NELM", "NELMDL", "ALGO", "PREC",
    "NBANDS", "POTIM", "LREAL", "LMAXMIX", "LDAU", "LDAUL", "LDAUU", "LDAUJ",
    "LDAUTYPE", "ICHARG", "ISTART", "IWRITE", "LWAVE", "LCHARG", "LVTOT",
    "LHFCALC", "NPAR", "NCORE", "KPAR", "NKRED", "LORBIT", "LNONCOLLINEAR",
    "LSORBIT", "AMIX", "BMIX", "AMIX_MAG", "BMIX_MAG", "MAXMIX", "NELECT",
    "GGA", "VOSKOWN", "ADDGRID", "METAGGA", "LMETAGGA", "LASPH", "LSCALU",
    "LMAXMIX", "NOMEGA", "LEPSILON", "LCALCPOL", "LPEAD", "NEDOS", "ISYM",
}

ASSIGN_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")
REP_RE = re.compile(r"^(\d+)\*(.+)$")
NUM_RE = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eEdD][+-]?\d+)?$")


def _strip_inline_comment(s: str) -> str:
    for ch in ("#", "!"):
        i = s.find(ch)
        if i != -1:
            s = s[:i]
    return s.strip()


def _convert(raw: str) -> tuple[Any, str]:
    up = raw.upper()
    if up in BOOL_MAP:
        return BOOL_MAP[up], "bool"
    norm = up.replace("D", "E")
    if NUM_RE.match(norm):
        if "E" in norm or "." in raw:
            return float(norm), "float"
        try:
            return int(up), "int"
        except ValueError:
            return float(norm), "float"
    return raw, "string"


def _expand(rest: str, data: IncarData, ln: int, name: str) -> list[Any]:
    out: list[Any] = []
    for tok in rest.split():
        m = REP_RE.match(tok)
        if m:
            n = int(m.group(1))
            val = m.group(2)
            if n <= 0 or n > MAX_REPETITION:
                data.warnings.append(f"line {ln}: invalid repetition '{tok}'")
                continue
            v, _ = _convert(val)
            for _ in range(n):
                out.append(v)
                if len(out) > MAX_TOKENS:
                    break
        else:
            v, _ = _convert(tok)
            out.append(v)
        if len(out) > MAX_TOKENS:
            break
    return out


def _classify(values: list[Any]) -> str:
    kinds = {_conv_kind(v) for v in values}
    if len(values) == 0:
        return "empty"
    if kinds == {"bool"}:
        return "bool_array" if len(values) > 1 else "bool"
    if kinds == {"int"}:
        return "int_array" if len(values) > 1 else "int"
    if kinds <= {"int", "float"}:
        return "float_array" if len(values) > 1 else "float"
    return "array"


def _conv_kind(v: Any) -> str:
    return "bool" if isinstance(v, bool) else ("int" if isinstance(v, int) else ("float" if isinstance(v, float) else "string"))


def parse_incar(text: str) -> IncarData:
    data = IncarData()
    lines = text.splitlines()
    data.raw_lines = list(lines)
    last_assign: dict[str, IncarAssignment] = {}

    for i, raw in enumerate(lines):
        ln = i + 1
        line = _strip_inline_comment(raw)
        if not line:
            continue
        m = ASSIGN_RE.match(line)
        if not m:
            data.warnings.append(f"line {ln}: not an assignment, skipped")
            continue
        name = m.group(1).strip()
        if name in ("#", "!"):
            continue
        rest = m.group(2).strip()
        unknown = name not in KNOWN_TAGS
        if not rest:
            data.warnings.append(f"line {ln}: param {name} empty value")
            data.assignments.append(
                IncarAssignment(name=name, raw_value="", value_type="empty",
                                source_line=ln, is_unknown=True)
            )
            if name not in data.unknown:
                data.unknown.append(name)
            continue

        values = _expand(rest, data, ln, name)
        vtype = _classify(values)
        eff = values[0] if len(values) == 1 else values
        prev = last_assign.get(name)
        if prev is not None:
            data.duplicate.append(
                DuplicateParam(
                    name=name,
                    lines=[prev.source_line, ln],
                    original_values=[prev.raw_value, rest],
                )
            )
            data.warnings.append(f"line {ln}: duplicate parameter {name}")
        data.assignments.append(
            IncarAssignment(name=name, value=eff, raw_value=rest,
                            value_type=vtype, source_line=ln, is_unknown=unknown)
        )
        last_assign[name] = data.assignments[-1]
        data.effective[name] = eff
        if unknown and name not in data.unknown:
            data.unknown.append(name)
    return data