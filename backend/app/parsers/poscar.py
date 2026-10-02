from __future__ import annotations

import re
from typing import Optional

from ..schemas.parsed import PoscarData
from .magnetic_evidence import finite


_NUM_RE = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eEdD][+-]?\d+)?$")
_SPECIES_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*(\s+[A-Za-z][A-Za-z0-9]*)*$")


def _is_num(tok: str) -> bool:
    return bool(_NUM_RE.match(tok.strip()))


def parse_poscar(text: str) -> PoscarData:
    data = PoscarData()
    lines = [ln.rstrip() for ln in text.splitlines()]
    i = 0
    # skip leading blank lines, first content line is the comment
    while i < len(lines) and lines[i].strip() == "":
        i += 1
    if i + 5 >= len(lines):
        return data
    i += 1  # skip comment
    # scale factor
    i += 1
    # 3 lattice vectors
    i += 3
    # next meaningful line: element names (non-numeric) or counts (numeric)
    while i < len(lines) and lines[i].strip() == "":
        i += 1
    if i >= len(lines):
        return data
    first = lines[i].split()
    if first and _is_num(first[0]):
        # v4 style: no species names, this line is already the counts
        counts_tokens = first
    else:
        # species names line
        data.elements = [t for t in first if t]
        i += 1
        while i < len(lines) and lines[i].strip() == "":
            i += 1
        if i >= len(lines):
            return data
        counts_tokens = lines[i].split()
    try:
        data.counts = [int(t) for t in counts_tokens]
    except ValueError:
        data.counts = []
    # Preserve the legacy header result even for incomplete structures. New
    # magnetic correspondence additionally requires all ordered position rows.
    count = sum(data.counts)
    if not data.counts or any(c < 0 for c in data.counts) or not 0 < count <= 100_000:
        return data
    i += 1
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines) and lines[i].strip().lower().startswith("s"):
        i += 1
    if i >= len(lines):
        return data
    marker = lines[i].strip().lower()
    mode = "Direct" if marker.startswith("d") else "Cartesian" if marker.startswith(("c", "k")) else None
    if mode is None:
        return data
    for j in range(i + 1, min(i + 1 + count, len(lines))):
        raw = lines[j].split()[:3]
        values = [finite(token) for token in raw]
        data.positions.append({"mode": mode, "values": values if len(values) == 3 and all(v is not None for v in values) else None,
                               "raw": raw, "source_line": j + 1})
    data.positions_complete = len(data.positions) == count and all(p["values"] is not None for p in data.positions)
    return data
