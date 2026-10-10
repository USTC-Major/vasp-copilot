"""Toolbox surface builder HTTP routes; no AI or remote execution dependency."""
from fastapi import APIRouter, Query, Request
from fastapi.responses import Response

from ..contracts import ToolboxError
from .schemas import (CreateDraft, PatchDraft, BuildSurfaces, SetConstraints, DraftResponse, DraftListResponse,
                      SetAdsorbate, FindAdsorptionSites, BuildAdsorptionCandidates,
                      SelectAdsorptionCandidates, ExportAdsorptionCandidates, BindWorkflow, WorkflowBindingResponse)
from .service import CatalysisService

router = APIRouter(prefix='/catalysis', tags=['Catalysis preparation'])


def service(request):
    return CatalysisService(request.app.state.toolbox.root)


def resolve_structure(structure_id):
    # Reuse the live app's authoritative FileStore, avoiding duplicate indexes.
    import sys
    module = sys.modules.get('app.api.v1.deps') or sys.modules.get('backend.app.api.v1.deps')
    if module is None:
        raise ToolboxError('CAT_SOURCE_UNAVAILABLE', '当前独立 Toolbox 未连接结构来源；请导入 POSCAR/CIF', 422)
    try:
        return module.file_store.get_structure(structure_id)
    except Exception as exc:
        from backend.app.core.errors import AppError
        if isinstance(exc, AppError) or getattr(exc, 'http_status', None) == 404:
            raise ToolboxError('CAT_SOURCE_NOT_FOUND', '已有结构不存在或已过期，请重新导入', 404) from exc
        raise


def workflow_file_store():
    import sys
    module = sys.modules.get('app.api.v1.deps') or sys.modules.get('backend.app.api.v1.deps')
    if module is None:
        raise ToolboxError('CAT_WORKFLOW_UNAVAILABLE', '当前独立 Toolbox 未连接 Workflow；请在工作台内传入', 422)
    return module.file_store


@router.post('/drafts/{draft_id}/workflow-binding', response_model=WorkflowBindingResponse)
def bind_workflow(draft_id: str, request: Request, body: BindWorkflow):
    live_store = workflow_file_store()
    from backend.app.api.v1.structure import _summary_json
    summary = service(request).bind_workflow(draft_id, body, file_store=live_store)
    return {'mode': 'toolbox', 'structure_id': summary.structure_id,
            'summary': _summary_json(summary, 'poscar'),
            'binding': summary.catalysis_binding, 'surface_policy': summary.surface_policy}


@router.get('/drafts', response_model=DraftListResponse)
def list_drafts(request: Request):
    return {'mode': 'toolbox', 'drafts': service(request).list()}


@router.post('/drafts', response_model=DraftResponse, response_model_exclude_unset=True)
def create_draft(request: Request, body: CreateDraft):
    return {'mode': 'toolbox', 'draft': service(request).create(body, resolve_structure=resolve_structure)}


@router.get('/drafts/{draft_id}', response_model=DraftResponse, response_model_exclude_unset=True)
def get_draft(draft_id: str, request: Request):
    return {'mode': 'toolbox', 'draft': service(request).get(draft_id)}


@router.patch('/drafts/{draft_id}', response_model=DraftResponse, response_model_exclude_unset=True)
def patch_draft(draft_id: str, request: Request, body: PatchDraft):
    return {'mode': 'toolbox', 'draft': service(request).patch(draft_id, body)}


@router.post('/drafts/{draft_id}/surfaces', response_model=DraftResponse, response_model_exclude_unset=True)
def build_surfaces(draft_id: str, request: Request, body: BuildSurfaces):
    return {'mode': 'toolbox', 'draft': service(request).build(draft_id, body)}


@router.post('/drafts/{draft_id}/constraints', response_model=DraftResponse, response_model_exclude_unset=True)
def set_constraints(draft_id: str, request: Request, body: SetConstraints):
    return {'mode': 'toolbox', 'draft': service(request).constraints(draft_id, body)}


@router.get('/drafts/{draft_id}/geometry')
def get_geometry(draft_id: str, request: Request, revision: int = Query(..., ge=1),
                 surface_id: str | None = Query(None, max_length=80),
                 candidate_id: str | None = Query(None, max_length=80)):
    return service(request).geometry(draft_id, revision, surface_id, candidate_id)


@router.get('/drafts/{draft_id}/export')
def export_draft(draft_id: str, request: Request, revision: int = Query(..., ge=1),
                 surface_id: str | None = Query(None, max_length=80)):
    content = service(request).export(draft_id, revision, surface_id)
    return Response(content=content, media_type='application/zip', headers={
        'Content-Disposition': f'attachment; filename="{draft_id}-r{revision}.zip"',
        'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})


@router.post('/drafts/{draft_id}/adsorbate', response_model=DraftResponse, response_model_exclude_unset=True)
def set_adsorbate(draft_id: str, request: Request, body: SetAdsorbate):
    return {'mode': 'toolbox', 'draft': service(request).set_adsorbate(draft_id, body)}


@router.post('/drafts/{draft_id}/adsorption/sites', response_model=DraftResponse, response_model_exclude_unset=True)
def find_adsorption_sites(draft_id: str, request: Request, body: FindAdsorptionSites):
    return {'mode': 'toolbox', 'draft': service(request).find_adsorption_sites(draft_id, body)}


@router.post('/drafts/{draft_id}/adsorption/candidates', response_model=DraftResponse, response_model_exclude_unset=True)
def build_adsorption_candidates(draft_id: str, request: Request, body: BuildAdsorptionCandidates):
    return {'mode': 'toolbox', 'draft': service(request).build_adsorption_candidates(draft_id, body)}


@router.patch('/drafts/{draft_id}/adsorption/selection', response_model=DraftResponse, response_model_exclude_unset=True)
def select_adsorption_candidates(draft_id: str, request: Request, body: SelectAdsorptionCandidates):
    return {'mode': 'toolbox', 'draft': service(request).select_adsorption_candidates(draft_id, body)}


@router.post('/drafts/{draft_id}/adsorption/export')
def export_adsorption_candidates(draft_id: str, request: Request, body: ExportAdsorptionCandidates):
    content = service(request).export_adsorption_candidates(draft_id, body)
    return Response(content=content, media_type='application/zip', headers={
        'Content-Disposition': f'attachment; filename="{draft_id}-adsorption-r{body.revision}.zip"',
        'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
