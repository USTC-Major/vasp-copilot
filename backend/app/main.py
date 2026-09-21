from __future__ import annotations

import asyncio
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

# Bootstrap: BE-A modules import as ``backend.app.*`` (repo root on sys.path),
# while doctor modules import as ``app.*``. Insert repo root first so both
# trees resolve when launched via ``uvicorn app.main:app`` from backend/.


def _resolve_repo_root() -> Path:
    """定位包含 ``backend`` 顶层包的目录（仓库根）。

    本地布局 ``<repo>/backend/app/main.py`` 返回 ``<repo>``；
    容器布局 ``/repo/backend/app/main.py`` 返回 ``/repo``（见 Dockerfile）。
    向上逐级探测，避免硬编码父目录层数。
    """
    here = Path(__file__).resolve()
    for candidate in (here, here.parent, here.parent.parent,
                      here.parent.parent.parent):
        if (candidate / "backend" / "app").is_dir():
            return candidate
    return here.parent.parent.parent


_REPO_ROOT = str(_resolve_repo_root())
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api.v1.deps import settings, store, extractor
from .llm import runtime as llm_runtime
from .api.v1.diagnosis import router
from .api.v1.agent import router as agent_router
from .api.v1.workflows import router as workflows_router
from .api.v1.files import router as files_router
from .api.v1.structure import router as structure_router
from .api.v1.materials import router as materials_router
from .api.v1.llm import router as llm_router
from .api.v1.chat import router as chat_router
from .core.errors import AppError
from backend.app.core.errors import AppError as BackendAppError
from backend.app.recipes.errors import BeAError

_RUNS_CLEANUP_INTERVAL_SECONDS = 300.0


async def _ttl_cleanup_loop(runs_root: Path) -> None:
    while True:
        await asyncio.sleep(_RUNS_CLEANUP_INTERVAL_SECONDS)
        try:
            store.cleanup_expired()
            if settings.orphan_run_cleanup:
                store.sweep_orphaned_runs(runs_root)
        except Exception:  # noqa: BLE001 - 清理失败不得阻塞服务
            pass


@asynccontextmanager
async def lifespan(_app: FastAPI):
    from backend.toolbox.service import ExecutionService
    from backend.toolbox.paths import home_dir
    _app.state.toolbox = ExecutionService(home_dir()).start()
    task = None
    try:
        runs_root = Path(settings.data_dir) / "runs"
        llm_runtime.load(Path(settings.data_dir) / "llm_config.json")
        task = asyncio.create_task(_ttl_cleanup_loop(runs_root))
        yield
    finally:
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        _app.state.toolbox.close()


app = FastAPI(title=settings.app_name, version="0.2.5",
              openapi_url="/api/v1/openapi.json", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router, prefix="/api/v1")
app.include_router(agent_router, prefix="/api/v1")
app.include_router(workflows_router, prefix="/api/v1")
app.include_router(files_router, prefix="/api/v1")
app.include_router(structure_router, prefix="/api/v1")
app.include_router(materials_router, prefix="/api/v1")
app.include_router(llm_router, prefix="/api/v1")
app.include_router(chat_router, prefix="/api/v1")
from backend.toolbox.api import router as toolbox_router, error_handler as toolbox_error_handler
from backend.toolbox.contracts import ToolboxError
app.include_router(toolbox_router, prefix="/api/v1")
app.add_exception_handler(ToolboxError, toolbox_error_handler)


def _envelope(request: Request, code: str, message: str,
              retryable: bool, details=None) -> dict:
    rid = request.headers.get("X-Request-ID") or "req_" + uuid.uuid4().hex[:8]
    error: dict = {"code": code, "message": message, "retryable": retryable}
    if details not in (None, [], {}):
        error["details"] = details
    return {"request_id": rid, "error": error}


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.http_status,
        content=_envelope(request, exc.code, exc.message, exc.retryable,
                          getattr(exc, "details", None)),
    )


@app.exception_handler(BackendAppError)
async def backend_app_error_handler(request: Request,
                                    exc: BackendAppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.http_status,
        content=_envelope(request, exc.code, exc.message, exc.retryable,
                          getattr(exc, "details", None)),
    )


@app.exception_handler(BeAError)
async def be_a_error_handler(request: Request, exc: BeAError) -> JSONResponse:
    """将 BE-A 模块错误映射到统一封装（IR-01）。

    不可重试错误映射为 409（需要用户操作/确认）；可重试错误映射为 503。"""
    return JSONResponse(
        status_code=503 if exc.retryable else 409,
        content=_envelope(request, exc.code, exc.message, exc.retryable,
                          exc.details),
    )


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "app": settings.app_name}
