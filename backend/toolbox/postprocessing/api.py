"""Local analysis API; raw request streaming avoids unbounded multipart spooling."""
import hashlib
import threading
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from .store import AnalysisStore, MAX_FILE, fail

router = APIRouter(prefix='/postprocessing', tags=['Local post-processing'])
_creation_lock = threading.Lock()


class Create(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['dos', 'band']
    title: str = Field(default='', max_length=120)


class AxisSettings(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    version: Literal['pp.axes.v1']
    x_min: float = Field(allow_inf_nan=False)
    x_max: float = Field(allow_inf_nan=False)
    x_interval: float = Field(gt=0, allow_inf_nan=False)
    y_min: float = Field(allow_inf_nan=False)
    y_max: float = Field(allow_inf_nan=False)
    y_interval: float = Field(gt=0, allow_inf_nan=False)


class ViewSettings(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    reference: Literal['raw', 'fermi', 'custom', 'legacy_absolute']
    reference_ev: float = Field(allow_inf_nan=False)
    mirror_down: bool
    atoms: list[int] = Field(max_length=10000)
    orbitals: list[str] = Field(max_length=50)
    band_start: int = Field(ge=1)
    band_end: int = Field(ge=1)
    axes: AxisSettings | None = None
    version: Literal['pp.view.v2'] | None = None
    energy_min_ev: float | None = Field(default=None, allow_inf_nan=False)
    energy_max_ev: float | None = Field(default=None, allow_inf_nan=False)
    elements: list[str] = Field(default_factory=list, max_length=118)
    projection_grouping: Literal['element', 'combined'] | None = None


class View(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: int = Field(ge=0, strict=True)
    view: ViewSettings


def store(request):
    with _creation_lock:
        if not hasattr(request.app.state, 'postprocessing'):
            request.app.state.postprocessing = AnalysisStore(request.app.state.toolbox.root)
        return request.app.state.postprocessing


@router.get('/datasets')
def listing(request: Request):
    return {'mode': 'toolbox', 'datasets': store(request).list()}


@router.post('/datasets', status_code=201)
def create(request: Request, body: Create):
    return {'mode': 'toolbox', 'dataset': store(request).create(body.kind, body.title)}


@router.get('/datasets/{ident}')
def detail(ident: str, request: Request):
    return {'mode': 'toolbox', 'dataset': store(request).read(ident)}


@router.put('/datasets/{ident}/files/{name}')
async def upload(ident: str, name: str, request: Request):
    svc = store(request)
    path, remaining = svc.reserve_upload(ident, name)
    size, digest = 0, hashlib.sha256()
    try:
        length = request.headers.get('content-length')
        if length and int(length) > min(MAX_FILE, remaining):
            fail('单文件最多 64 MiB，一批最多 128 MiB', 'PP_TOO_LARGE', 413)
        with path.open('xb') as target:
            async for chunk in request.stream():
                size += len(chunk)
                if size > min(MAX_FILE, remaining):
                    fail('单文件最多 64 MiB，一批最多 128 MiB', 'PP_TOO_LARGE', 413)
                target.write(chunk)
                digest.update(chunk)
        if not size:
            fail('不能导入空文件')
        return {'mode': 'toolbox', 'dataset': svc.finish_upload(ident, name, size, digest.hexdigest())}
    finally:
        path.unlink(missing_ok=True)
        with svc.guard:
            svc.uploads.discard(ident)


@router.post('/datasets/{ident}/analyses', status_code=202)
def analyze(ident: str, request: Request):
    return {'mode': 'toolbox', 'dataset': store(request).start(ident)}


@router.post('/datasets/{ident}/cancel')
def cancel(ident: str, request: Request):
    return {'mode': 'toolbox', 'dataset': store(request).cancel(ident)}


@router.delete('/datasets/{ident}')
def delete(ident: str, request: Request):
    store(request).delete(ident)
    return {'mode': 'toolbox', 'deleted': True}


@router.patch('/datasets/{ident}/view')
def view(ident: str, request: Request, body: View):
    return {'mode': 'toolbox', 'dataset': store(request).view(ident, body.expected_revision, body.view.model_dump(exclude_unset=True))}


@router.post('/datasets/{ident}/fit-axes')
def fit(ident: str, request: Request, body: View):
    return {'mode': 'toolbox', **store(request).fit(ident, body.expected_revision, body.view.model_dump(exclude_unset=True))}


@router.get('/datasets/{ident}/curves')
def curves(ident: str, request: Request):
    return {'mode': 'toolbox', **store(request).curves(ident)}


@router.get('/datasets/{ident}/export')
def export(ident: str, request: Request, format: Literal['csv', 'json'] = 'json'):
    svc = store(request)
    if format == 'csv':
        return Response(svc.csv(ident).encode('utf-8-sig'), media_type='text/csv',
                        headers={'Content-Disposition': f'attachment; filename="{ident}.csv"'})
    return {'mode': 'toolbox', **svc.curves(ident)}
