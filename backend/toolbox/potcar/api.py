"""Local library metadata and explicitly confirmed artifact download routes."""
from fastapi import APIRouter, Query, Request
from fastapi.responses import Response

router = APIRouter(prefix='/potcar', tags=['Local POTCAR libraries'])


def call(request, method, *args, **kwargs):
    from ..service import envelope
    from ..contracts import ToolboxError
    service = request.app.state.toolbox
    if service.potcar is None:
        raise service.potcar_error or ToolboxError('POTCAR_UNAVAILABLE', '本地赝势库服务暂不可用', 503, True)
    try:
        return envelope(**getattr(service.potcar, method)(*args, **kwargs))
    except (OSError, ValueError, TypeError, KeyError):
        raise ToolboxError('POTCAR_STORE_INVALID', '本地赝势库状态无法读取或保存；原文件保留，请检查状态目录', 503, True) from None


@router.post('/discover')
def discover(request: Request, payload: dict):
    return call(request, 'discover', payload)


@router.get('/libraries')
def libraries(request: Request):
    return call(request, 'list_libraries')


@router.post('/libraries')
def register(request: Request, payload: dict):
    return call(request, 'register', payload)


@router.get('/libraries/{library_id}')
def library(library_id: str, request: Request):
    return call(request, 'detail', library_id)


@router.patch('/libraries/{library_id}')
def patch(library_id: str, request: Request, payload: dict):
    return call(request, 'patch', library_id, payload)


@router.put('/default-library')
def default(request: Request, payload: dict):
    return call(request, 'set_default', payload)


@router.post('/libraries/{library_id}/relink')
def relink(library_id: str, request: Request, payload: dict):
    return call(request, 'relink', library_id, payload)


@router.post('/libraries/{library_id}/replace-source')
def replace(library_id: str, request: Request, payload: dict):
    return call(request, 'relink', library_id, payload, replace=True)


@router.delete('/libraries/{library_id}')
def delete(library_id: str, request: Request, expected_revision: int = Query(..., ge=0)):
    return call(request, 'delete', library_id, expected_revision)


@router.post('/libraries/{library_id}/scans')
def start_scan(library_id: str, request: Request, payload: dict):
    return call(request, 'start_scan', library_id, payload)


@router.get('/scans/{scan_id}')
def scan(scan_id: str, request: Request):
    return call(request, 'scan', scan_id)


@router.post('/scans/{scan_id}/cancel')
def cancel(scan_id: str, request: Request):
    return call(request, 'cancel', scan_id)


@router.get('/libraries/{library_id}/datasets')
def datasets(library_id: str, request: Request, element: str | None = None,
             status: str | None = None, cursor: str | None = None, limit: int = Query(50, ge=1, le=100)):
    return call(request, 'datasets', library_id, element=element, status=status, cursor=cursor, limit=limit)


@router.post('/previews')
def preview(request: Request, payload: dict):
    return call(request, 'preview', payload)


@router.post('/artifacts')
def generate(request: Request, payload: dict):
    return call(request, 'generate', payload)


@router.get('/artifacts/{artifact_id}')
def artifact(artifact_id: str, request: Request):
    return call(request, 'artifact', artifact_id)


@router.get('/artifacts/{artifact_id}/download')
def download(artifact_id: str, request: Request):
    result = call(request, 'download_bytes', artifact_id)
    return Response(content=result['raw'], media_type='application/octet-stream',
                    headers={'Content-Disposition': 'attachment; filename="POTCAR"', 'Cache-Control': 'no-store'})
