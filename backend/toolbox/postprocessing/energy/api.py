"""Energy-only API; bounded raw uploads and revision-protected confirmation."""
import json
import threading
from typing import Literal

from fastapi import APIRouter, Request, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response, JSONResponse
from fastapi.routing import APIRoute
from starlette.concurrency import run_in_threadpool

from ..store import fail
from . import schemas
from .store import EnergyStore, MAX_FILE
from ...contracts import ToolboxError
from .errors import located


class EnergyRoute(APIRoute):
    """Keep validation failures usable by the card's inline error UI."""
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def handle(request):
            try:
                return await handler(request)
            except RequestValidationError as exc:
                try:
                    body = await request.json()
                except (ValueError, TypeError):
                    body = {}
                if not isinstance(body, dict):
                    body = {}
                card_body = body.get('card')
                if not isinstance(card_body, dict):
                    card_body = {}
                errors = []
                card_id = request.path_params.get('card_id') or card_body.get('id')
                if not isinstance(card_id, str):
                    card_id = None
                for issue in exc.errors():
                    location = list(issue['loc'])
                    if location and location[0] in {'body', 'query', 'path'}:
                        location.pop(0)
                    sample_id = None
                    if location[:1] == ['card']:
                        location.pop(0)
                    if len(location) > 1 and location[0] in {'targets', 'samples'} and isinstance(location[1], int):
                        rows = card_body.get('targets', []) if location[0] == 'targets' else body.get('samples', [])
                        if isinstance(rows, list) and location[1] < len(rows) and isinstance(rows[location[1]], dict):
                            sample_id = rows[location[1]].get('sample_id')
                            if not isinstance(sample_id, str):
                                sample_id = None
                            if sample_id:
                                location[1] = sample_id
                    errors.append({'card_id': card_id, 'sample_id': sample_id,
                                   'field': '.'.join(map(str, location)) or 'card',
                                   'code': 'ENERGY_INVALID_REQUEST', 'message': issue['msg']})
                return JSONResponse(status_code=422, content={'error': {
                    'code': 'ENERGY_INVALID_REQUEST', 'message': '请求字段无效，请修正对应字段',
                    'retryable': False, 'field_errors': errors}})
            except ToolboxError as exc:
                field = 'expected_revision' if exc.code == 'ENERGY_REVISION_CONFLICT' else 'card'
                raise located(exc, request.path_params.get('card_id'), field=field) from exc
        return handle


router = APIRouter(prefix='/postprocessing/energy', tags=['Energy post-processing'], route_class=EnergyRoute)
_creation_lock = threading.Lock()


def store(request):
    with _creation_lock:
        if not hasattr(request.app.state, 'energy'):
            request.app.state.energy = EnergyStore(request.app.state.toolbox.root)
        return request.app.state.energy


def sources(request):
    svc = store(request)
    with _creation_lock:
        if svc.task_sources is None:
            from .task_sources import EnergyTaskSources
            svc.task_sources = EnergyTaskSources(request.app.state.toolbox, svc)
        return svc.task_sources


def response(doc):
    return {'mode': 'toolbox', 'collection': doc}


async def csv_bytes(request):
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > 1024 * 1024:
            fail('CSV最多1 MiB', 'ENERGY_TOO_LARGE', 413)
        data.extend(chunk)
    return bytes(data)


@router.get('/csv-template')
def csv_template(energy_basis: schemas.EnergyBasis = 'sigma_to_zero_ev'):
    return Response(EnergyStore.csv_template().encode('utf-8-sig'), media_type='text/csv',
                    headers={'Content-Disposition': f'attachment; filename="energy-{energy_basis}-template.csv"'})


@router.get('/collections')
def listing(request: Request):
    return {'mode': 'toolbox', 'collections': store(request).list()}


@router.post('/collections', status_code=201)
def create(request: Request, body: schemas.Create):
    return response(store(request).create(body.title, body.analysis_kind, body.workflow))


@router.get('/collections/{ident}')
def detail(ident: str, request: Request):
    return response(store(request).read(ident))


@router.delete('/collections/{ident}')
def delete(ident: str, request: Request):
    store(request).delete(ident)
    return {'mode': 'toolbox', 'deleted': True}


@router.post('/collections/{ident}/outcar', status_code=201)
async def upload(ident: str, request: Request, expected_revision: int = Query(ge=0), name: str = 'OUTCAR',
                 relative_path: str | None = None):
    if name != 'OUTCAR':
        fail('自动导入仅接受原始OUTCAR；显示名称可在确认表编辑', 'ENERGY_SOURCE_NAME_DENIED')
    svc = store(request)
    path, budget = await run_in_threadpool(svc.reserve, ident, expected_revision)
    try:
        length = request.headers.get('content-length')
        if length:
            try:
                if int(length) > budget:
                    fail('完整文件超过导入预算', 'ENERGY_TOO_LARGE', 413)
            except ValueError:
                fail('Content-Length无效', 'ENERGY_INVALID_REQUEST')
        size = 0
        with path.open('xb') as target:
            async for chunk in request.stream():
                size += len(chunk)
                if size > budget:
                    fail('单文件最多64 MiB，比较集最多128 MiB', 'ENERGY_TOO_LARGE', 413)
                await run_in_threadpool(target.write, chunk)
        return response(await run_in_threadpool(svc.import_path, ident, expected_revision, path, name, relative_path))
    finally:
        await run_in_threadpool(svc.release, path)


@router.post('/collections/{ident}/manual', status_code=201)
def manual(ident: str, request: Request, body: schemas.Manual):
    return response(store(request).manual(ident, body.expected_revision, body.model_dump(exclude={'expected_revision'})))


@router.post('/collections/{ident}/csv', status_code=201)
async def csv_import(ident: str, request: Request, expected_revision: int = Query(ge=0),
                     energy_basis: schemas.EnergyBasis | None = None):
    store(request).check_editable(ident, expected_revision)
    data = await csv_bytes(request)
    return response(await run_in_threadpool(store(request).csv_import, ident, expected_revision, data, energy_basis))


@router.post('/collections/{ident}/csv/preview')
async def csv_preview(ident: str, request: Request, expected_revision: int = Query(ge=0),
                      energy_basis: schemas.EnergyBasis | None = None):
    store(request).check_editable(ident, expected_revision)
    data = await csv_bytes(request)
    return {'mode': 'toolbox', 'preview': await run_in_threadpool(
        store(request).csv_preview, ident, expected_revision, data, energy_basis)}


@router.get('/collections/{ident}/samples.csv')
def samples_csv(ident: str, request: Request, energy_basis: schemas.EnergyBasis,
                value_source: Literal['original', 'effective'] = 'effective'):
    return Response(store(request).samples_csv(ident, energy_basis, value_source).encode('utf-8-sig'),
                    media_type='text/csv', headers={'Content-Disposition': f'attachment; filename="{ident}-samples-{value_source}.csv"'})


@router.put('/collections/{ident}/configuration')
def configure(ident: str, request: Request, body: schemas.Configuration):
    return response(store(request).configure(ident, body.model_dump()))


@router.post('/collections/{ident}/calculate')
def calculate(ident: str, request: Request, body: schemas.Revision):
    return response(store(request).calculate(ident, body.expected_revision))


@router.post('/collections/{ident}/lock')
def lock(ident: str, request: Request, body: schemas.Revision):
    return response(store(request).lock(ident, body.expected_revision))


@router.post('/collections/{ident}/unlock')
def unlock(ident: str, request: Request, body: schemas.Revision):
    return response(store(request).unlock(ident, body.expected_revision))


@router.post('/collections/{ident}/copy', status_code=201)
def copy_analysis(ident: str, request: Request, body: schemas.CopyAnalysis):
    return response(store(request).copy_analysis(ident, body.expected_revision, body.analysis_kind,
                                                body.title, body.group_id, body.workflow))


@router.post('/collections/{ident}/autofill')
def autofill(ident: str, request: Request, body: schemas.Revision):
    return response(store(request).autofill(ident, body.expected_revision))


@router.post('/collections/{ident}/samples/removal-preview')
def removal_preview(ident: str, request: Request, body: schemas.RemoveSamples):
    svc = store(request)
    if body.workflow == 'cards' or svc.read(ident).get('workflow') == 'cards':
        removal = svc.cards.removal_preview(ident, body.expected_revision, body.sample_ids, body.clear_all)
    else:
        removal = svc.removal_preview(ident, body.expected_revision, body.sample_ids, body.clear_all)
    return {'mode': 'toolbox', 'removal': removal}


@router.post('/collections/{ident}/samples/remove')
def remove_samples(ident: str, request: Request, body: schemas.RemoveSamples):
    svc = store(request)
    if body.workflow == 'cards' or svc.read(ident).get('workflow') == 'cards':
        doc, removal = svc.cards.remove_samples(ident, body.expected_revision, body.sample_ids, body.clear_all,
                                                body.preview_id, body.acknowledge_locked_cards)
    else:
        doc, removal = svc.remove_samples(ident, body.expected_revision, body.sample_ids, body.clear_all)
    return {**response(doc), 'removal': removal}


@router.post('/collections/{ident}/cards', status_code=201)
def create_card(ident: str, request: Request, body: schemas.CardConfiguration):
    return response(store(request).cards.create(ident, body.expected_revision, body.card.model_dump()))


@router.put('/collections/{ident}/cards/{card_id}')
def configure_card(ident: str, card_id: str, request: Request, body: schemas.CardConfiguration):
    return response(store(request).cards.configure(ident, body.expected_revision, card_id, body.card.model_dump()))


@router.post('/collections/{ident}/cards/{card_id}/copy', status_code=201)
def copy_card(ident: str, card_id: str, request: Request, body: schemas.CardCopy):
    return response(store(request).cards.copy(ident, body.expected_revision, card_id, body.name))


@router.delete('/collections/{ident}/cards/{card_id}')
def delete_card(ident: str, card_id: str, request: Request, body: schemas.Revision):
    return response(store(request).cards.delete(ident, body.expected_revision, card_id))


@router.post('/collections/{ident}/cards/{card_id}/lock')
def lock_card(ident: str, card_id: str, request: Request, body: schemas.CardLock):
    return response(store(request).cards.lock(ident, body.expected_revision, card_id, body.accepted_warnings))


@router.post('/collections/{ident}/cards/{card_id}/unlock')
def unlock_card(ident: str, card_id: str, request: Request, body: schemas.Revision):
    return response(store(request).cards.unlock(ident, body.expected_revision, card_id))


@router.post('/collections/{ident}/cards/{card_id}/calculate')
def calculate_card(ident: str, card_id: str, request: Request, body: schemas.Revision):
    return response(store(request).cards.calculate(ident, body.expected_revision, card_id))


@router.post('/collections/{ident}/cards/{card_id}/autofill')
def autofill_card(ident: str, card_id: str, request: Request, body: schemas.Revision):
    return response(store(request).cards.autofill(ident, body.expected_revision, card_id))


@router.post('/collections/{ident}/samples/change-preview')
def sample_change_preview(ident: str, request: Request, body: schemas.CardSamples):
    patches = [patch.model_dump(exclude_unset=True) for patch in body.samples]
    return {'mode': 'toolbox', 'impact': store(request).cards.change_preview(ident, body.expected_revision, patches, body.title)}


@router.put('/collections/{ident}/samples/configuration')
def configure_card_samples(ident: str, request: Request, body: schemas.CardSamples):
    patches = [patch.model_dump(exclude_unset=True) for patch in body.samples]
    return response(store(request).cards.configure_samples(ident, body.expected_revision, patches, body.title,
                                                           body.preview_id, body.acknowledge_locked_cards))


@router.get('/collections/{ident}/cards/{card_id}/export')
def export_card(ident: str, card_id: str, request: Request, format: Literal['json', 'csv'] = 'json'):
    svc = store(request).cards
    if format == 'csv':
        content, media = svc.csv(ident, card_id).encode('utf-8-sig'), 'text/csv'
    else:
        content = json.dumps({'mode': 'toolbox', 'collection': svc.export(ident, card_id)}, ensure_ascii=False, allow_nan=False).encode('utf-8')
        media = 'application/json'
    return Response(content, media_type=media, headers={'Content-Disposition': f'attachment; filename="{ident}-{card_id}.{format}"'})


@router.get('/collections/{ident}/export')
def export(ident: str, request: Request, format: Literal['json', 'csv'] = 'json'):
    svc = store(request)
    if format == 'csv':
        content = svc.csv(ident).encode('utf-8-sig')
        media = 'text/csv'
    else:
        content = json.dumps({'mode': 'toolbox', 'collection': svc.export(ident)}, ensure_ascii=False, allow_nan=False).encode('utf-8')
        media = 'application/json'
    return Response(content, media_type=media, headers={'Content-Disposition': f'attachment; filename="{ident}.{format}"'})


@router.post('/task-sources/preview')
def preview(request: Request, body: schemas.TaskPreview):
    return {'mode': 'toolbox', 'preview': sources(request).preview(body.model_dump())}


@router.post('/collections/{ident}/task-sources/import', status_code=201)
def import_task(ident: str, request: Request, body: schemas.TaskImport):
    return response(sources(request).import_source(ident, body.expected_revision, body.preview_id, body.name))


@router.post('/collections/{ident}/reuse', status_code=201)
def reuse(ident: str, request: Request, body: schemas.Reuse):
    return response(store(request).reuse(ident, body.expected_revision, body.source_collection_id, body.sample_id))
