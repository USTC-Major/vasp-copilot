"""VASP-Doctor -> BE-A workflow endpoints (IR-01/IR-03/IR-05).

POST /workflows/plan      - recipe combination preview + pending confirmations
POST /workflows/generate  - run BE-A generation pipeline, cache bundle
GET  /workflows/{id}      - plan/status/revision/file_tree metadata (design 6.6)
GET  /workflows/{id}/download - deterministic zip bundle

Requests may carry a ``structure_id`` (from /structure/analyze), a
``diagnosis_id`` (mapped from the stored run POSCAR), or an explicit
``workflow.structure``. ``generate`` may also replay a previously planned
``workflow_id`` (frontend sends only ``{workflow_id}``)."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from backend.app.schemas.generation import (
    DftuSettings,
    MaterialAssumptions,
    ParameterPatch,
    SchedulerSettings,
    StructureContext,
    WorkflowGenerateRequest,
)
from backend.app.schemas.recipe import (
    ElectronicType,
    PrecisionLevel,
    TaskType,
)
from backend.app.schemas.structure import build_structure_summary, to_structure_context
from ...llm import get_explainer
from backend.app.workflow.nl_planner import (
    LlmWorkflowPlanner,
    NlPlan,
    build_default_plan,
)
from backend.app.services.workflow_service import WorkflowService

from ...core.errors import ConflictError, NotFoundError
from ...parsers.poscar import parse_poscar
from ...schemas.api import ApiEnvelope
from .deps import file_store, get_request_id, settings, store

router = APIRouter()
workflow_service = WorkflowService(
    potcar_prepared=settings.feature_flags.potcar_concat,
    file_store=file_store,
)


class WorkflowConfig(BaseModel):
    """WorkflowGenerateRequest relaxed request mirror.

    ``structure`` is optional so clients may rely on ``structure_id`` /
    ``diagnosis_id``; the resolver fills it in before building the strict
    request."""

    model_config = ConfigDict(extra="ignore")

    workflow_id: str = "wf_local"
    structure: Optional[StructureContext] = None
    requested_tasks: List[TaskType] = Field(default_factory=lambda: [TaskType.RELAX])
    goal_text: Optional[str] = None
    material_assumptions: MaterialAssumptions = Field(default_factory=MaterialAssumptions)
    precision: PrecisionLevel = PrecisionLevel.STANDARD
    dftu: DftuSettings = Field(default_factory=DftuSettings)
    scheduler: SchedulerSettings = Field(default_factory=SchedulerSettings)
    patches: List[ParameterPatch] = Field(default_factory=list)
    element_initial_moments: Dict[str, float] = Field(default_factory=dict)
    enable_band_workflow: bool = False
    confirm: bool = True

    def to_request(self, structure: StructureContext) -> WorkflowGenerateRequest:
        return WorkflowGenerateRequest(
            workflow_id=self.workflow_id,
            structure=structure,
            requested_tasks=self.requested_tasks,
            goal_text=self.goal_text,
            material_assumptions=self.material_assumptions,
            precision=self.precision,
            dftu=self.dftu,
            scheduler=self.scheduler,
            patches=self.patches,
            element_initial_moments=self.element_initial_moments,
            enable_band_workflow=self.enable_band_workflow,
            confirm=self.confirm,
        )


class WorkflowApiRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    workflow_id: Optional[str] = None
    diagnosis_id: Optional[str] = None
    structure_id: Optional[str] = None
    goals: Optional[List[str]] = None
    assumptions: Optional[Dict[str, Any]] = None
    patches: Optional[List[ParameterPatch]] = None
    workflow: Optional[WorkflowConfig] = None


def _new_workflow_id() -> str:
    return "wf_" + uuid.uuid4().hex[:8]


def _map_goals(goals: Optional[List[str]]) -> List[TaskType]:
    """Map free-form goal/task names to TaskType (unknowns ignored).

    Defaults to a relax workflow when nothing maps."""
    if not goals:
        return [TaskType.RELAX]
    tasks: List[TaskType] = []
    for goal in goals:
        try:
            tasks.append(TaskType(goal.strip().lower()))
        except ValueError:
            continue
    return tasks or [TaskType.RELAX]


def _map_assumptions(assumptions: Optional[Dict[str, Any]]) -> MaterialAssumptions:
    material = MaterialAssumptions()
    precision = PrecisionLevel.STANDARD
    if not assumptions:
        return material
    raw_type = assumptions.get("electronic_type")
    if raw_type in {member.value for member in ElectronicType}:
        material.electronic_type = ElectronicType(raw_type)
    material.magnetic = bool(assumptions.get("magnetic", False))
    material.soc = bool(assumptions.get("soc", False))
    raw_precision = assumptions.get("precision")
    if raw_precision in {member.value for member in PrecisionLevel}:
        precision = PrecisionLevel(raw_precision)
    material.precision = precision
    return material


def _read_structure_text(base_dir: Path):
    """Return (text, source_name) for POSCAR/CONTCAR, else (None, None)."""
    for names in (("POSCAR", "poscar"), ("CONTCAR", "CONTCAR-", "contcar")):
        for name in names:
            target = base_dir / name
            if target.is_file():
                try:
                    return target.read_text(encoding="utf-8", errors="replace"), name
                except OSError:
                    return None, None
    return None, None


def _structure_from_diagnosis(diagnosis_id: str) -> StructureContext:
    """IR-05: map a stored doctor run structure to a BE-A StructureContext."""
    record = store.get(diagnosis_id)
    text, source = _read_structure_text(record.base_dir)
    if text is None:
        raise NotFoundError(
            "STRUCTURE_NOT_FOUND",
            "no POSCAR/CONTCAR found for diagnosis_id",
        )
    parsed = parse_poscar(text)
    if not parsed.elements or not parsed.counts:
        raise ConflictError(
            "STRUCTURE_UNPARSEABLE",
            "POSCAR/CONTCAR species/counts could not be parsed",
        )
    summary = build_structure_summary(
        poscar_text=text,
        elements=parsed.elements,
        counts=parsed.counts,
        source_file=source or "POSCAR",
        structure_id=diagnosis_id,
    )
    return to_structure_context(summary)


def _structure_from_file_store(structure_id: str) -> StructureContext:
    record = file_store.get_structure(structure_id)
    return to_structure_context(record.summary)


def _resolve_workflow(req: WorkflowApiRequest, config: WorkflowConfig) -> WorkflowGenerateRequest:
    if config.structure is not None and config.structure.poscar_text:
        return config.to_request(config.structure)
    if req.structure_id:
        return config.to_request(_structure_from_file_store(req.structure_id))
    if req.diagnosis_id:
        return config.to_request(_structure_from_diagnosis(req.diagnosis_id))
    raise ConflictError(
        "STRUCTURE_REQUIRED",
        "provide workflow.structure, structure_id, or a diagnosis_id with a POSCAR",
    )


@router.post("/workflows/plan", response_model=ApiEnvelope, tags=["workflows"])
async def plan(
    req: WorkflowApiRequest,
    request: Request,
    x_request_id: str = Depends(get_request_id),
) -> ApiEnvelope:
    config = req.workflow or WorkflowConfig()
    config.workflow_id = req.workflow_id or _new_workflow_id()
    if req.goals:
        config.requested_tasks = _map_goals(req.goals)
        config.goal_text = "、".join(req.goals)
    if req.assumptions:
        config.material_assumptions = _map_assumptions(req.assumptions)
        config.precision = _map_assumptions(req.assumptions).precision
    workflow = _resolve_workflow(req, config)
    data: Dict[str, Any] = workflow_service.plan(workflow)
    data["workflow_id"] = workflow.workflow_id
    data["status"] = "needs_confirmation" if data["needs_confirmation"] else "planned"
    # design 6.4 uses workflow_status field name; keep in parallel with status.
    data["workflow_status"] = data["status"]
    return ApiEnvelope(request_id=x_request_id, data=data)


@router.post("/workflows/generate", response_model=ApiEnvelope, tags=["workflows"])
async def generate(
    req: WorkflowApiRequest,
    request: Request,
    x_request_id: str = Depends(get_request_id),
) -> ApiEnvelope:
    workflow: WorkflowGenerateRequest
    config = req.workflow or WorkflowConfig()
    if not req.structure_id and not req.diagnosis_id and not config.structure:
        # Frontend calls generate with only {workflow_id}: replay the plan request.
        if req.workflow_id:
            workflow = workflow_service.replay_request(req.workflow_id)
            patches = req.patches or config.patches
            if patches:
                workflow = workflow.model_copy(update={"patches": patches})
        else:
            raise ConflictError(
                "STRUCTURE_REQUIRED",
                "provide structure or a previously planned workflow_id",
            )
    else:
        if req.structure_id:
            config.workflow_id = req.workflow_id or _new_workflow_id()
            if req.goals:
                config.requested_tasks = _map_goals(req.goals)
            if req.assumptions:
                config.material_assumptions = _map_assumptions(req.assumptions)
        workflow = _resolve_workflow(req, config)
    data = workflow_service.generate(workflow)
    workflow_id = data["workflow_id"]
    data["download_url"] = f"/api/v1/workflows/{workflow_id}/download"
    return ApiEnvelope(request_id=x_request_id, data=data)


@router.get("/workflows/{workflow_id}", tags=["workflows"])
async def get_workflow(
    workflow_id: str,
    request: Request,
    x_request_id: str = Depends(get_request_id),
) -> ApiEnvelope:
    """Design 6.6: plan/status/revision/confirmations/file_tree metadata."""
    data = workflow_service.get_workflow(workflow_id)
    return ApiEnvelope(request_id=x_request_id, data=data)


@router.get("/workflows/{workflow_id}/download", tags=["workflows"])
async def download(
    workflow_id: str,
    request: Request,
    x_request_id: str = Depends(get_request_id),
) -> Response:
    artifact = workflow_service.get_artifact(workflow_id)
    return Response(
        content=artifact.zip_bytes,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="vasp_workflow_{workflow_id}.zip"'
        },
    )


@router.post("/workflows/plan_from_nl", response_model=ApiEnvelope, tags=["workflows"])
async def plan_from_nl(
    req: WorkflowApiRequest,
    request: Request,
    x_request_id: str = Depends(get_request_id),
) -> ApiEnvelope:
    """方案 A：用户自然语言需求 -> LLM 解析 -> 复用规则管线出计划（不写文件）。

    LLM 仅把自然语言需求解析为 requested_tasks/assumptions/patches，
    文件生成仍走既有 preview_plan/generate 确定性管线，原始文件不进 prompt。
    LLM 不可用/非法输出时降级为默认计划（degraded=true）。
    """
    goals_text = (req.goals or [""])[0] if req.goals else (req.workflow.goal_text if req.workflow else "")
    if not req.structure_id:
        raise ConflictError("STRUCTURE_REQUIRED", "provide structure_id for AI planning")
    record = file_store.get_structure(req.structure_id)
    summary = record.summary

    explainer = settings and get_explainer(settings)
    planner = LlmWorkflowPlanner(explainer=explainer)
    nl_plan: Optional[NlPlan] = None
    requires_confirmation = True
    degraded = False
    if explainer is not None:
        try:
            nl_plan = planner.plan(summary, goals_text, enable_band_workflow=settings.feature_flags.band_feature)
        except Exception:  # noqa: BLE001 - LLM 调用失败降级
            nl_plan = None
    if nl_plan is None:
        degraded = True
        nl_plan = build_default_plan(summary, goals_text)

    # 映射 NLP 计划到 WorkflowConfig -> WorkflowGenerateRequest
    config = WorkflowConfig(
        workflow_id=req.workflow_id or _new_workflow_id(),
        requested_tasks=[TaskType(t) for t in nl_plan.requested_tasks],
        goal_text=goals_text,
        material_assumptions=MaterialAssumptions(
            electronic_type=ElectronicType(nl_plan.assumptions.get("electronic_type", "unknown")),
            magnetic=nl_plan.assumptions.get("magnetic", False),
            soc=nl_plan.assumptions.get("soc", False),
            precision=PrecisionLevel(nl_plan.assumptions.get("precision", "standard")),
        ),
        patches=nl_plan.patches[:],
        enable_band_workflow=settings.feature_flags.band_feature,
        confirm=True,
    )
    workflow = _resolve_workflow(req, config)
    data = workflow_service.plan(workflow)
    data["workflow_id"] = workflow.workflow_id
    data["status"] = "needs_confirmation" if data["needs_confirmation"] else "planned"
    data["workflow_status"] = data["status"]
    data["ai"] = {
        "enabled": explainer is not None,
        "degraded": degraded,
        "user_needs": goals_text,
        "requested_tasks": nl_plan.requested_tasks,
        "explanations": nl_plan.step_explanations,
    }
    return ApiEnvelope(request_id=x_request_id, data=data)
