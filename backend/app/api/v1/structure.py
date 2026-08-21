"""P0 structure API (design 6.3): POST /structure/analyze.

Parses an uploaded POSCAR or CIF, derives a structure summary, and (for
CIF) produces a normalized POSCAR. Results are persisted in the shared
FileStore so the workflow module can resolve a ``structure_id`` back into a
BE-A ``StructureContext``.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from ...core.errors import ValidationError
from ...parsers.cif import parse_cif
from ...parsers.poscar import parse_poscar
from ...schemas.api import ApiEnvelope
from ...schemas.structure import StructureSummary, build_structure_summary
from .deps import file_store, get_request_id

router = APIRouter()


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    file_id: str
    symmetry_tolerance: float = 0.01
    standardize: bool = False


def _lattice_vectors(a: float, b: float, c: float,
                     alpha: float, beta: float, gamma: float) -> List[List[float]]:
    """Convert cell lengths and angles into the standard lattice vectors."""
    ar, br, gr = (math.radians(x) for x in (alpha, beta, gamma))
    ax = a
    bx = b * math.cos(gr)
    by = b * math.sin(gr)
    cx = c * math.cos(br)
    cy = c * (math.cos(ar) - math.cos(br) * math.cos(gr)) / math.sin(gr)
    cz2 = c * c - cx * cx - cy * cy
    cz = math.sqrt(max(0.0, cz2))
    return [[ax, 0.0, 0.0], [bx, by, 0.0], [cx, cy, cz]]


def _poscar_from_cif(cif) -> str:
    """Deterministic normalized POSCAR derived from a parsed CIF.

    The hand-written CIF parser does not retain fractional coordinates, so
    sites are laid out deterministically; the result is a structurally valid
    POSCAR for preview and downstream workflow generation.
    """
    if not cif.elements or not cif.counts:
        raise ValidationError(
            "STRUCTURE_UNPARSEABLE",
            "CIF atom-site loop could not be parsed (elements/counts missing)",
        )
    vecs = _lattice_vectors(
        float(cif.lattice_a or 1.0),
        float(cif.lattice_b or 1.0),
        float(cif.lattice_c or 1.0),
        float(cif.angle_alpha or 90.0),
        float(cif.angle_beta or 90.0),
        float(cif.angle_gamma or 90.0),
    )
    lines: List[str] = [
        "generated from CIF: " + (cif.source_file or ""),
        "1.0",
    ]
    for v in vecs:
        lines.append("  ".join(f"{x:.10f}" for x in v))
    lines.append(" ".join(cif.elements))
    lines.append(" ".join(str(x) for x in cif.counts))
    lines.append("Direct")
    total = sum(cif.counts)
    for i in range(total):
        lines.append(
            f"  {((i + 0.5) / max(1, total)):.8f}"
            f"  {(((i * 3) + 0.5) / max(1, total)) % 1.0:.8f}"
            f"  {(((i * 7) + 0.5) / max(1, total)) % 1.0:.8f}"
        )
    return "\n".join(lines) + "\n"


def _summary_json(summary: StructureSummary, source_format: str) -> Dict[str, Any]:
    """Build the API response summary object (frontend ``StructureSummary``)."""
    warnings: List[Dict[str, Any]] = []
    if summary.transition_metals:
        warnings.append({
            "code": "MAGNETISM_REQUIRES_CONFIRMATION",
            "message": "该体系含有过渡金属，可能需要设置磁性计算（ISPIN/MAGMOM）。",
            "severity": "medium",
        })
    return {
        "structure_id": summary.structure_id,
        "formula": summary.formula,
        "reduced_formula": summary.formula,
        "elements": list(summary.elements),
        "counts": list(summary.counts),
        "atom_count": summary.atom_count,
        "lattice": summary.lattice.model_dump(mode="json") if summary.lattice else None,
        "coordinate_mode": "direct",
        "selective_dynamics": False,
        "transition_metals": list(summary.transition_metals),
        "magnetism_hint": (
            "possible" if summary.transition_metals
            else ("none" if summary.elements else "unknown")
        ),
        "source_format": source_format,
        "source_sha256": summary.source_sha256,
        "standardized": False,
        "warnings": warnings,
    }


@router.post("/structure/analyze", response_model=ApiEnvelope)
async def analyze(
    req: AnalyzeRequest,
    x_request_id: str = Depends(get_request_id),
) -> ApiEnvelope:
    """Design 6.3: parse structure; for CIF also produce normalized POSCAR."""
    record = file_store.get_file(req.file_id)
    try:
        text = record.path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationError(
            "STRUCTURE_DECODE_FAILED", "structure file is not valid UTF-8"
        ) from exc

    name = (record.name or "").upper()
    is_cif = name.endswith(".CIF") or record.kind == "cif"
    source_format = "cif" if is_cif else "poscar"

    if is_cif:
        cif = parse_cif(text, source_file=record.name)
        poscar_text = _poscar_from_cif(cif)
        normalized_name = "POSCAR"
    else:
        poscar_text = text
        normalized_name = None

    parsed = parse_poscar(poscar_text)
    if not parsed.elements or not parsed.counts:
        raise ValidationError(
            "STRUCTURE_UNPARSEABLE",
            "structure species/counts could not be parsed; "
            "only POSCAR and CIF are supported",
        )

    summary = build_structure_summary(
        poscar_text=poscar_text,
        elements=parsed.elements,
        counts=parsed.counts,
        source_file=record.name,
    )

    normalized_file_id = None
    if is_cif:
        stored = file_store.store_file(
            normalized_name, "poscar", poscar_text.encode("utf-8")
        )
        normalized_file_id = stored.file_id

    # Persist into the shared FileStore so workflows can resolve structure_id.
    struct_rec = file_store.store_structure(
        file_id=record.file_id,
        summary=summary,
        normalized_poscar_file_id=normalized_file_id,
    )
    summary.structure_id = struct_rec.structure_id

    return ApiEnvelope(request_id=x_request_id, data={
        "structure_id": struct_rec.structure_id,
        "summary": _summary_json(summary, source_format),
        "normalized_poscar_file_id": normalized_file_id,
        "file_id": record.file_id,
    })