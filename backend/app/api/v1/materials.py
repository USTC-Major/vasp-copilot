"""Materials Project import endpoints (workflow upload step).

POST /materials/search  - search the MP database from a natural-language /
                          structured query (LLM-assisted criteria parsing,
                          deterministic fallback) and list candidates.
POST /materials/import  - fetch the selected material, build a POSCAR, store
                          it and run the same analyze step as /structure/analyze
                          so the structure_id drops into the existing flow.
"""
from __future__ import annotations

from json import loads as _loads
import re as _re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from ...core.errors import ValidationError
from ...llm import get_explainer
from ...parsers.poscar import parse_poscar
from ...schemas.api import ApiEnvelope
from ...schemas.structure import build_structure_summary
from .deps import file_store, get_request_id, settings

router = APIRouter()


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    query: str = ""
    limit: int = 20


class ImportRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    material_id: str = ""


_MP_CRITERIA_PROMPT = """你是材料数据库查询助手。把用户的材料需求解析为
Materials Project API 的 criteria JSON。只输出 JSON，不要额外文字。

可用的 criteria 字段（仅为建议，缺失字段可省略）：
- "elements": ["Fe", "O"]  （元素列表）
- "elements_type": "=" | ">=" | "<=" （该列表是精确/超集/子集）
- "formula": "Fe2O3"  （精确简化式，命中更精准，可只用 formula）
- "chemsys": "Fe-O"
- "band_gap": {"$gte": 0.5} / {"$lte": 2.0}
- "energy_above_hull": {"$lte": 0.0}（稳定结构）
- "is_metal": true/false
- "ordering": "FM"/"AFM"/"NM"

需求示例：
- 「找 Fe2O3 结构」-> {"formula": "Fe2O3"}
- 「带 1-3 eV 带隙的稳定氧化物」-> {"elements":["O"],"elements_type":">=","band_gap":{"$gte":1,"$lte":3},"energy_above_hull":{"$lte":0.05}}
- 「铁磁性含 Ni 的化合物」-> {"elements":["Ni"],"elements_type":">=","ordering":"FM","is_stable":true}

用户需求：
{query}
请输出 criteria JSON。"""


def _query_llm_criteria(query: str) -> Dict[str, Any]:
    """Optional: use the configured explainer to refine criteria from NL."""
    explainer = get_explainer(settings)
    if explainer is None:
        return {}
    try:
        raw = explainer.complete([{"role": "user", "content":
                                   _MP_CRITERIA_PROMPT.format(query=query)}])
        content = (raw or "").strip()
        if content.startswith("```"):
            content = content.split("```")[1].strip()
        data = _loads(content)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def _compact_summary(summary) -> Dict[str, Any]:
    lattice = summary.lattice.model_dump(mode="json") if summary.lattice else None
    return {
        "structure_id": summary.structure_id,
        "formula": summary.formula,
        "reduced_formula": summary.formula,
        "elements": list(summary.elements),
        "counts": list(summary.counts),
        "atom_count": summary.atom_count,
        "lattice": lattice,
        "coordinate_mode": "direct",
        "selective_dynamics": False,
        "transition_metals": list(summary.transition_metals),
        "magnetism_hint": ("possible" if summary.transition_metals else "none"),
        "source_format": "poscar",
        "source_sha256": summary.source_sha256,
        "standardized": False,
        "warnings": [],
    }


@router.post("/materials/search", response_model=ApiEnvelope)
async def search_materials(
    req: SearchRequest,
    x_request_id: str = Depends(get_request_id),
) -> ApiEnvelope:
    """List MP candidates matching a natural-language / formula query."""
    query = (req.query or "").strip()
    if not query:
        raise ValidationError("MP_EMPTY_QUERY", "请输入材料需求或化学式")
    if not settings.materials_project.api_key:
        raise ValidationError(
            "MP_NOT_CONFIGURED",
            "未配置 Materials Project API key（后端 MP_API_KEY）",
        )

    from ...services.materials_project import (
        MaterialsProjectClient,
        parse_requirement,
    )

    criteria: Dict[str, Any] = {}
    llm_used = False
    raw_llm = _query_llm_criteria(query)
    if raw_llm:
        criteria = raw_llm
        llm_used = True
    else:
        criteria = parse_requirement(query)

    client = MaterialsProjectClient(
        api_key=settings.materials_project.api_key,
        base_url=settings.materials_project.base_url,
        timeout_seconds=settings.materials_project.timeout_seconds,
    )
    try:
        results = client.search(criteria, limit=req.limit)
    finally:
        client.close()

    return ApiEnvelope(request_id=x_request_id, data={
        "query": query,
        "criteria": criteria,
        "llm_used": llm_used,
        "count": len(results),
        "materials": results,
    })


@router.post("/materials/import", response_model=ApiEnvelope)
async def import_material(
    req: ImportRequest,
    x_request_id: str = Depends(get_request_id),
) -> ApiEnvelope:
    """Fetch an MP material, build a POSCAR, store & analyze it."""
    material_id = (req.material_id or "").strip()
    if not material_id:
        raise ValidationError("MP_EMPTY_MATERIAL_ID",
                              "缺少 material_id")
    if not settings.materials_project.api_key:
        raise ValidationError(
            "MP_NOT_CONFIGURED",
            "未配置 Materials Project API key（后端 MP_API_KEY）",
        )

    from ...services.materials_project import MaterialsProjectClient

    client = MaterialsProjectClient(
        api_key=settings.materials_project.api_key,
        base_url=settings.materials_project.base_url,
        timeout_seconds=settings.materials_project.timeout_seconds,
    )
    try:
        doc = client.get_structure_doc(material_id)
    finally:
        client.close()

    poscar_text = _structure_to_poscar(doc, material_id)
    parsed = parse_poscar(poscar_text)
    if not parsed.elements or not parsed.counts:
        raise ValidationError(
            "STRUCTURE_UNPARSEABLE",
            "从 Materials Project 返回的结构无法生成 POSCAR",
        )

    stored = file_store.store_file("POSCAR", "poscar",
                                   poscar_text.encode("utf-8"))
    summary = build_structure_summary(
        poscar_text=poscar_text,
        elements=parsed.elements,
        counts=parsed.counts,
        source_file="MaterialsProject/" + material_id,
    )
    struct_rec = file_store.store_structure(
        file_id=stored.file_id, summary=summary,
        normalized_poscar_file_id=stored.file_id,
    )
    summary.structure_id = struct_rec.structure_id

    return ApiEnvelope(request_id=x_request_id, data={
        "structure_id": struct_rec.structure_id,
        "summary": _compact_summary(summary),
        "normalized_poscar_file_id": stored.file_id,
        "file_id": stored.file_id,
        "material_id": material_id,
    })


def _structure_to_poscar(doc: Dict[str, Any], material_id: str) -> str:
    """Build a VASP POSCAR (Direct) from an MP structure document.

    MP returns a Pymatgen-serialized Structure: lattice.matrix (3x3 rows)
    plus sites[] with species[] and abc[] (fractional).
    """
    struct = doc.get("structure") if isinstance(doc, dict) else None
    if not isinstance(struct, dict):
        raise ValidationError("MP_INVALID_STRUCTURE",
                              "Materials Project 未返回 structure 文档")
    lattice = struct.get("lattice") or {}
    matrix = lattice.get("matrix")
    sites = struct.get("sites")
    if not isinstance(matrix, list) or len(matrix) != 3 or not isinstance(sites, list):
        raise ValidationError("MP_INVALID_STRUCTURE",
                              "structure/lattice 或 sites 缺失，无法构造 POSCAR")

    def _el(site: Dict[str, Any]) -> Optional[str]:
        species = site.get("species") or []
        for sp in species:
            if isinstance(sp, dict) and (sp.get("element") or sp.get("symbol")):
                return str(sp.get("element") or sp.get("symbol"))
        label = site.get("label")
        if label:
            m = _re.match(r"^([A-Za-z])", str(label))
            if m:
                return m.group(1)
        return None

    elements: List[str] = []
    counts: List[int] = []
    order: Dict[str, int] = {}
    coords: Dict[str, List[List[float]]] = {}
    for site in sites:
        if not isinstance(site, dict):
            continue
        abc = site.get("abc")
        if not isinstance(abc, list) or len(abc) != 3:
            continue
        el = _el(site)
        if not el:
            continue
        if el not in order:
            order[el] = len(elements)
            elements.append(el)
            counts.append(0)
            coords[el] = []
        idx = order[el]
        counts[idx] += 1
        coords[el].append([float(x) for x in abc])

    if not elements:
        raise ValidationError("MP_INVALID_STRUCTURE",
                              "structure sites 中未识别到元素")

    lines = [f"Imported from Materials Project: {material_id}", "1.0"]
    for row in matrix:
        lines.append("  ".join(f"{float(v):.10f}" for v in row))
    lines.append(" ".join(elements))
    lines.append(" ".join(str(c) for c in counts))
    lines.append("Direct")
    for el in elements:
        for abc in coords[el]:
            lines.append(
                f"  {abc[0]:.8f}  {abc[1]:.8f}  {abc[2]:.8f}")
    return "\n".join(lines) + "\n"