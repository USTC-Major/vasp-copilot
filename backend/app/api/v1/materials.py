"""Materials Project import endpoints (workflow upload step).

POST /materials/search  - search MP from a formula or confirmed strict criteria.
POST /materials/import  - fetch the selected material, build a POSCAR, store
                          it and run the same analyze step as /structure/analyze
                          so the structure_id drops into the existing flow.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from backend.materials_criteria import MaterialCriteria, formula_elements
from backend.input_validation import InputValidationError, validate_poscar
from ...core.errors import ValidationError, err
from ...schemas.api import ApiEnvelope
from ...schemas.structure import build_structure_summary
from ...material_identity import display_material_id, valid_material_id
from .deps import file_store, get_request_id, settings

router = APIRouter()


def _runtime_mp_api_key(request: Request) -> str:
    """Use the active Toolbox owner's credential chain on every MP request."""
    from backend.toolbox.config import load_mp_api_key
    from backend.toolbox.secrets import SecretStorageError

    owner = getattr(request.app.state, 'toolbox', None)
    if owner is None:
        raise err('MP_CREDENTIAL_UNAVAILABLE', 'Toolbox 凭据服务暂不可用，请稍后重试', 503)
    try:
        key = load_mp_api_key(config_path=owner.root / 'toolbox_config.json')
    except (SecretStorageError, OSError, ValueError) as exc:
        raise err('MP_CREDENTIAL_UNAVAILABLE', '无法读取 Materials Project 凭据，请检查 Toolbox 设置后重试', 503) from exc
    if not key:
        raise ValidationError('MP_NOT_CONFIGURED',
                              '未配置 Materials Project API key，请前往 Toolbox 设置保存密钥')
    return key


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = ""
    criteria: MaterialCriteria | None = None
    confirmed: StrictBool = False
    unresolved_conditions: List[str] = Field(default_factory=list)
    limit: int = Field(default=20, ge=1, le=50)


class ImportRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    material_id: str = ""


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
        "source_material_id": summary.source_material_id,
        "standardized": False,
        "warnings": [],
    }


@router.post("/materials/search", response_model=ApiEnvelope)
async def search_materials(
    req: SearchRequest,
    request: Request,
    x_request_id: str = Depends(get_request_id),
) -> ApiEnvelope:
    """List MP candidates only after every condition has been reviewed."""
    query = (req.query or "").strip()
    if not query and req.criteria is None:
        raise ValidationError("MP_EMPTY_QUERY", "请输入材料需求或化学式")
    if req.unresolved_conditions:
        raise ValidationError("MP_UNRESOLVED_CONDITIONS", "仍有未能映射的筛选条件，请先修改查询")
    if req.criteria is not None:
        if not req.confirmed:
            raise ValidationError("MP_CONFIRMATION_REQUIRED", "请先核对并确认结构化筛选条件")
        criteria = req.criteria.to_mp()
    elif formula_elements(query) is not None:
        criteria = MaterialCriteria(formula=query).to_mp()
    else:
        raise ValidationError("MP_INTERPRETATION_REQUIRED", "自然语言需求需先通过智能模式解释并确认")

    mp_api_key = _runtime_mp_api_key(request)

    from ...services.materials_project import MaterialsProjectClient

    client = MaterialsProjectClient(
        api_key=mp_api_key,
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
        "llm_used": False,
        "count": len(results),
        "materials": results,
    })


@router.post("/materials/import", response_model=ApiEnvelope)
async def import_material(
    req: ImportRequest,
    request: Request,
    x_request_id: str = Depends(get_request_id),
) -> ApiEnvelope:
    """Fetch an MP material, build a POSCAR, store & analyze it."""
    material_id = (req.material_id or "").strip()
    if not material_id:
        raise ValidationError("MP_EMPTY_MATERIAL_ID",
                              "缺少 material_id")
    if not valid_material_id(material_id):
        raise ValidationError("MP_INVALID_MATERIAL_ID", "Materials Project 材料编号格式无效")
    mp_api_key = _runtime_mp_api_key(request)

    from ...services.materials_project import MaterialsProjectClient

    client = MaterialsProjectClient(
        api_key=mp_api_key,
        base_url=settings.materials_project.base_url,
        timeout_seconds=settings.materials_project.timeout_seconds,
    )
    try:
        doc = client.get_structure_doc(material_id)
    finally:
        client.close()

    returned_id = doc.get("material_id") if isinstance(doc, dict) else None
    if (not isinstance(returned_id, str) or not valid_material_id(returned_id)
            or display_material_id(returned_id) != display_material_id(material_id)):
        raise ValidationError("MP_ID_MISMATCH", "Materials Project 返回的材料编号与请求不一致")

    try:
        poscar_text = _structure_to_poscar(doc, material_id)
    except (ValueError, TypeError, KeyError, IndexError, OverflowError) as exc:
        raise ValidationError("MP_INVALID_STRUCTURE", "Materials Project 结构数据不完整或无效") from exc
    try:
        parsed = validate_poscar(poscar_text)
        summary = build_structure_summary(
            poscar_text=poscar_text,
            elements=list(parsed.elements), counts=list(parsed.counts),
            source_file="MaterialsProject/" + material_id, validated=parsed,
            source_material_id=returned_id,
        )
    except InputValidationError as exc:
        raise ValidationError(exc.code, str(exc)) from exc
    stored = file_store.store_file("POSCAR", "poscar",
                                   poscar_text.encode("utf-8"))
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
        "display_material_id": display_material_id(material_id),
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
        return None

    elements: List[str] = []
    counts: List[int] = []
    order: Dict[str, int] = {}
    coords: Dict[str, List[List[float]]] = {}
    for site in sites:
        if not isinstance(site, dict):
            raise ValidationError("MP_INVALID_STRUCTURE", "structure sites 包含无效位点")
        abc = site.get("abc")
        if not isinstance(abc, list) or len(abc) != 3:
            raise ValidationError("MP_INVALID_STRUCTURE", "structure sites 缺少完整坐标")
        el = _el(site)
        if not el:
            raise ValidationError("MP_INVALID_STRUCTURE", "structure sites 缺少可靠物种")
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
