"""Materials Project display identifiers; API identifiers remain unchanged."""

from __future__ import annotations

import re


_ALPHA_ID = re.compile(r"mp-[a-z]{1,8}\Z")
_MATERIAL_ID = re.compile(r"mp-(?:[0-9]{1,10}|[a-z]{1,8})\Z")
_LEGACY_CUTOFF = 3_347_529


def valid_material_id(raw: str) -> bool:
    """Bound the identifier before it enters a one-line POSCAR comment."""
    return bool(_MATERIAL_ID.fullmatch(raw))


def display_material_id(raw: str) -> str:
    """Match frontend/materialId.ts's verified AlphaID legacy range."""
    if not _ALPHA_ID.fullmatch(raw):
        return raw
    value = 0
    for character in raw[3:]:
        value = value * 26 + ord(character) - ord("a")
        if value > _LEGACY_CUTOFF:
            return raw
    return f"mp-{value}"
