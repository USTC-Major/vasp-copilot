from __future__ import annotations

import re
from typing import Any, Optional

from ..schemas.parsed import OszicarData


_TOK = r"([A-Za-z ]+?)\s*=\s*([-\d.EeDd+]+)"
_STEP_RE = re.compile(r"^\s*(\d+)\s+(.*)$")
_TOKEN_RE = re.compile(r"([A-Za-z][A-Za-z0-9]*(?:\s+[A-Za-z0-9]+)*)\s*=\s*(-?[\d.]+(?:[EeDd][-+]?\d+)?)")


def _to_float(v: str) -> Optional[float]:
    try:
        return float(v.replace("D", "E"))
    except ValueError:
        return None


def _parse_fields(tail: str) -> dict[str, Any]:
    """从步进行尾部提取 F=、E0=、d E=、dns=、mag= 等 token。"""
    out: dict[str, Any] = {}
    for m in _TOKEN_RE.finditer(tail):
        key = m.group(1).strip().replace(" ", "_")
        key_norm = "d" if key == "d" else key
        out[key_norm] = _to_float(m.group(2))
    return out


def parse_oszicar(text: str) -> OszicarData:
    data = OszicarData()
    ionic: list[dict[str, Any]] = []
    steps_seen = 0
    total_elec_lines = 0
    last_tail = ""

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        # skip the OSZICAR header block (NION/NELEC/NVASP)
        if line.upper().startswith("NION") or line.upper().startswith("NELEC"):
            continue
        m = _STEP_RE.match(line)
        if not m:
            continue
        step = int(m.group(1))
        tail = m.group(2)
        last_tail = tail
        fields = _parse_fields(tail)
        fields["step"] = step
        ionic.append(fields)
        total_elec_lines += 1
        steps_seen = max(steps_seen, step)

    data.ionic_steps = ionic
    data.last_step = steps_seen
    data.total_electronic_lines = total_elec_lines
    data.converged = "reached required accuracy" in (last_tail or "")
    # provide a convenience energy series (F) for the SCF analyzer
    energy_series: list[float] = []
    for s in ionic:
        f = s.get("F") or s.get("E0")
        if f is not None:
            energy_series.append(float(f))
    data.energy_series = energy_series
    return data