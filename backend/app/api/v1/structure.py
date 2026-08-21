"""P0 structure API (design 6.3): POST /structure/analyze.

Parses an uploaded POSCAR or CIF, derives a structure summary, and (for
CIF) produces a normalized POSCAR via pymatgen, preserving the real atomic
fractional coordinates. Results are persisted in the shared FileStore so
the workflow module can resolve a ``structure_id`` back into a BE-A
``StructureContext``.
"""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from ...core.errors import ValidationError
from ...parsers.poscar import parse_poscar
from ...schemas.api import ApiEnvelope
from ...schemas.structure import StructureSummary, build_structure_summary
from ...services.cif_converter import convert_cif_to_poscar
from .deps import file_store, get_request_id

router = APIRouter()


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    file_id: str
    symmetry_tolerance: float = 0.01
    standardize: bool = False


def _summary_json(summary: StructureSummary, source_format: str,
                  standardized: bool = False) -> Dict[str, Any]:
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
        "standardized": standardized,
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
        # pymatgen 对称性展开 + 真实分数坐标；失败时 fail closed，不落盘任何产物。
        conversion = convert_cif_to_poscar(
            text,
            source_file=record.name,
            standardize=req.standardize,
            symmetry_tolerance=req.symmetry_tolerance,
        )
        poscar_text = conversion.poscar_text
        normalized_name = "POSCAR"
    else:
        conversion = None
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
        "summary": _summary_json(
            summary, source_format,
            standardized=conversion.standardized if conversion else False,
        ),
        "normalized_poscar_file_id": normalized_file_id,
        "file_id": record.file_id,
    })
