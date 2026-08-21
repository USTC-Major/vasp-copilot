from __future__ import annotations

import re
from typing import Optional

from ..schemas.parsed import KpointsData


def parse_kpoints(text: str) -> KpointsData:
    data = KpointsData()
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return data
    data.comment = lines[0]
    if len(lines) >= 2:
        try:
            data.nkpts = int(lines[1].split()[0])
        except (ValueError, IndexError):
            data.nkpts = None
    if len(lines) >= 3:
        data.mode = lines[2]
    low = data.mode.lower()
    data.line_mode = "line" in low or low == "l"
    return data