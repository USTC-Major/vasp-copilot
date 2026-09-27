"""Optional AI chat service. Execution and monitoring belong to the Toolbox HTTP owner."""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import re
import threading
from contextlib import asynccontextmanager
from urllib.parse import quote

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .config import load_settings
from .gate import is_ai_mode_enabled
from .settings.global_api import (
    mask_config as _mask_settings,
    persist as _persist_settings,
    check_connection as _run_settings_test,
    update_from_patch as _apply_settings_patch,
    update_secret as _update_secret,
    secret_status as _secret_status,
    writable_fields as _writable_fields,
)
from .settings.project import (
    ProjectSettingsError,
    ProjectSettingsStore,
)
from . import chat as _chat
from .consent import get_card as _get_consent_card
from .consent import list_cards as _list_consent_cards
from .consent import task_lock as _task_state_lock
from .agent.runner import _stream_card

from .projects import get_project_store as _get_project_store
from .storage import ensure_layout
from .streaming import ACTIVE_STOPS as _ACTIVE_STOPS
from .streaming import ChatRun, GenerationBusy, generation_status, request_stop


def _generation_running(store, project_id: str, task_id: str) -> bool:
    """对话轮是否正在进行；取不到状态时按"没有在跑"处理（回执就落库留痕）。"""
    try:
        return bool(generation_status(store, project_id, task_id).get('running'))
    except Exception:  # noqa: BLE001 - 测试替身/异常时退回旧行为
        return False


def _auto_wake_state(store, project_id: str, task_id: str) -> dict:
    try:
        return dict((store.generation_metadata(project_id, task_id) or {}).get("auto_wake") or {})
    except Exception:  # noqa: BLE001
        return {}


def _set_auto_wake_stopped(store, project_id: str, task_id: str, stopped: bool) -> None:
    """停止/继续的开关：停止后后台不再自动唤醒该任务；用户再说话时清掉。"""
    try:
        state = _auto_wake_state(store, project_id, task_id)
        state["stopped"] = bool(stopped)
        store.update_generation(project_id, task_id, {"auto_wake": state})
    except Exception:  # noqa: BLE001
        return


def _clear_auto_wake_stop(store, project_id: str, task_id: str) -> None:
    """用户又发消息＝要接着跑：清掉停止标记，允许后台事件再次自动唤醒。"""
    _set_auto_wake_stopped(store, project_id, task_id, False)


def stop_everything(store, project_id: str, task_id: str) -> dict:
    """一个按钮停到底：停 AI 生成 + 停计算流程（监控/下游自动准备）+ 停后台自动唤醒。

    返回给前端的信息里带上"仍在超算上运行的作业号"，用于提醒用户是否需要 scancel。
    """
    stopped_generation = bool(request_stop(project_id, task_id))
    flow_note = ""
    running_jobs: list[str] = []
    try:
        detail = store.client.request('GET', store.client.task_path(project_id, task_id) + '/detail')
        for job in ((detail or {}).get('flow') or {}).get('jobs') or []:
            if str(job.get('status') or '') in {'submitted', 'queued', 'running', 'unknown'}:
                running_jobs.append(f"{job.get('key')}（作业号 {job.get('slurm_id') or '未知'}）")
    except Exception:  # noqa: BLE001 - 详情读不到不影响停止
        pass
    try:
        result = store.client.request(
            'POST', store.client.task_path(project_id, task_id) + '/tools',
            json={'name': 'stop_monitor', 'args': {}})
        flow_note = str((result or {}).get('result') or '')
    except Exception as exc:  # noqa: BLE001 - 流程已经在终态时停止会失败，不算错误
        flow_note = f"（流程层面：{type(exc).__name__}）"
    _set_auto_wake_stopped(store, project_id, task_id, True)
    reminder = ["已按你的要求停止：AI 不再继续输出，后台监控与自动准备也停下了，"
                "不会再自动弹卡或自动唤醒。"]
    if running_jobs:
        reminder.append("注意：这些作业**已经在超算上运行**，我无法替你取消——"
                        "如需取消请在超算上执行 `scancel <作业号>`："
                        + "、".join(running_jobs) + "。")
    else:
        reminder.append("当前没有在超算上运行的作业。")
    reminder.append("说一句「继续」我就从当前进度接着做（已跑完的结果会保留，"
                    "不会重复提交已完成的作业）。")
    text = "\n".join(reminder)
    try:
        store.append_message(project_id, task_id, role='assistant', content=text)
    except Exception:  # noqa: BLE001
        pass
    return {"stopped_generation": stopped_generation, "running_jobs": running_jobs,
            "flow_note": flow_note, "message": text}

logger = logging.getLogger("ai_mode")

APP_TITLE = "VASP-Copilot 智能模式"
APP_VERSION = "0.3.0"




def _mask(config) -> dict:
    """只返回非私有信息的配置汇总；密钥一律掩码。"""
    return {
        "enabled": config.enabled,
        "data_dir": str(config.data_dir),
        "max_jobs": config.max_jobs,
        "poll_interval_seconds": config.poll_interval_seconds,
        "billing_estimate_enabled": config.billing_estimate_enabled,
        "llm": {
            "base_url": config.llm_base_url,
            "model": config.llm_model,
            "timeout_seconds": config.llm_timeout_seconds,
            "max_retries": config.llm_max_retries,
            "max_tokens": config.llm_max_tokens,
            "temperature": config.llm_temperature,
            "api_key": "<redacted>" if config.llm_api_key else "",
        },
        "ssh": {
            "name": config.ssh_name,
            "host": config.ssh_host,
            "port": config.ssh_port,
            "username": config.ssh_username,
        },
        "materials_project": {"api_key": "<redacted>" if config.mp_api_key else ""},
    }


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    ensure_layout()
    from .projects import close_project_store
    from .auto_wake import AutoWakeLoop
    wake_loop = AutoWakeLoop()
    if is_ai_mode_enabled():
        store = _get_project_store()  # Acquire chat ownership at startup, without network I/O.
        # 事件驱动自动唤醒：作业完成/卡片结果/失败时自己继续，用户只点确认卡。
        wake_loop.start(store)
    try:
        yield
    finally:
        wake_loop.stop()
        close_project_store()


def create_ai_mode_app() -> FastAPI:
    app = FastAPI(
        title=APP_TITLE,
        version=APP_VERSION,
        openapi_url="/ai/v1/openapi.json",
        lifespan=_lifespan,
    )

    from backend.toolbox.contracts import ToolboxError
    async def error_handler(request, exc):
        payload = exc.payload()
        payload['code'] = {'PROJECT_NOT_FOUND': 'AI_MODE_PROJECT_NOT_FOUND', 'TASK_NOT_FOUND': 'AI_MODE_PROJECT_NOT_FOUND', 'SSH_UNCONFIGURED': 'AI_MODE_HPC_UNCONFIGURED', 'INVALID_SETTINGS': 'AI_MODE_BAD_SETTINGS'}.get(payload['code'], payload['code'])
        return JSONResponse(status_code=exc.status, content={'mode': 'ai', 'ok': False, 'error': payload})
    app.add_exception_handler(ToolboxError, error_handler)

    @app.post('/ai/internal/reviewer/review', include_in_schema=False)
    def internal_file_review(request: Request, payload: dict):
        import hmac
        import os
        from .reviewer import ReviewError, review_request
        secret = os.environ.get('VASP_REVIEWER_SHARED_SECRET', '')
        token = request.headers.get('authorization', '')
        if (os.environ.get('VASP_REVIEWER_ENABLED', '').lower() != 'true' or
            len(secret.encode('utf-8')) < 32 or
            not hmac.compare_digest(token.encode('utf-8'), ('Bearer ' + secret).encode('utf-8'))):
            return JSONResponse(status_code=403, content={'error': {'code': 'REVIEWER_FORBIDDEN'}})
        try:
            return review_request(payload, secret=secret)
        except ReviewError as exc:
            return JSONResponse(status_code=422, content={'error': {'code': exc.code}})
        except Exception:
            return JSONResponse(status_code=503, content={'error': {'code': 'REVIEWER_UNAVAILABLE'}})

    @app.get("/")
    def root() -> dict:
        return {"mode": "ai", "enabled": is_ai_mode_enabled(), "version": APP_VERSION}

    @app.get("/ai/v1/ping")
    def ping() -> dict:
        return {"mode": "ai", "enabled": is_ai_mode_enabled(), "version": APP_VERSION}

    @app.get("/ai/v1/layout")
    def layout() -> dict:
        dirs = ensure_layout()
        return {"home": str(dirs["sessions"].parent), "dirs": {
            name: str(path) for name, path in dirs.items()
        }}


    @app.get("/ai/v1/llm/status")
    def llm_status():
        """LLM 连通状态：不可用 = 智能模式整体瘫痪提示（安全边界）。"""
        cfg = load_settings()
        if not cfg.enabled:
            return JSONResponse(status_code=503, content={
                "mode": "ai",
                "error": {"code": "AI_MODE_DISABLED",
                          "message": "智能模式未启用（ENABLE_AI_MODE=false）。",
                          "retryable": False},
            })
        from .llm.factory import resolve_provider, test_connection
        try:
            _ = resolve_provider(cfg)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse(status_code=503, content={
                "mode": "ai",
                "error": {"code": "AI_MODE_LLM_UNAVAILABLE",
                          "message": f"LLM 配置无法解析: {exc}",
                          "retryable": True},
            })
        result = test_connection(cfg)
        if result["ok"]:
            return {"mode": "ai", "provider": result["provider"],
                    "ok": True, "message": result["message"]}
        return JSONResponse(status_code=503, content={
            "mode": "ai",
            "provider": result["provider"],
            "ok": False,
            "error": {
                "code": "AI_MODE_LLM_UNAVAILABLE",
                "message": result["message"]
                           + "（LLM 不可用 = 智能模式整体瘫痪，请等待恢复再操作）",
                "retryable": True,
            },
        })
    @app.get("/ai/v1/config")
    def get_config(request: Request):
        cfg = load_settings()
        if not cfg.enabled:
            return JSONResponse(status_code=503, content={
                "mode": "ai",
                "error": {
                    "code": "AI_MODE_DISABLED",
                    "message": "智能模式未启用（ENABLE_AI_MODE=false）。"
                               "请在设置中启用后再访问。",
                    "retryable": False,
                },
            })
        return {"mode": "ai", "enabled": True, "config": _mask(cfg)}

    def _disabled_envelope(message: str = "") -> JSONResponse:
        return JSONResponse(status_code=503, content={
            "mode": "ai",
            "error": {"code": "AI_MODE_DISABLED",
                      "message": message or "智能模式未启用（ENABLE_AI_MODE=false）。",
                      "retryable": False},
        })

    def _require_enabled(cfg) -> JSONResponse | None:
        return None if cfg.enabled else _disabled_envelope()

    @app.get("/ai/v1/settings")
    def get_settings():
        cfg = load_settings()
        resp = _require_enabled(cfg)
        if resp is not None:
            return resp
        settings = _mask_settings(cfg)
        remote = _get_project_store().client.request('GET', '/settings')['settings']
        settings.update(remote)
        settings['materials_project'] = {'api_key': '<redacted>' if remote['materials_project']['configured'] else ''}
        return {'mode': 'ai', 'enabled': True, 'settings': settings, 'writable': _writable_fields()}

    @app.put("/ai/v1/settings")
    def put_settings(payload: dict):
        cfg = load_settings()
        resp = _require_enabled(cfg)
        if resp is not None:
            return resp
        if {'llm_api_key', 'mp_api_key', 'ssh_password'} & set(payload):
            return JSONResponse(status_code=400, content={'mode': 'ai', 'error': {'code': 'AI_MODE_SECRET_WRITE_ONLY', 'message': '密钥只能通过专用接口替换或清除', 'retryable': False}})
        execution = {k:v for k,v in payload.items() if not k.startswith('llm_')}
        model = {k:v for k,v in payload.items() if k.startswith('llm_')}
        if execution:
            _get_project_store().client.request('PUT', '/settings', json=execution)
        if model:
            try:
                _persist_settings(_apply_settings_patch(cfg, model))
            except ValueError as exc:
                return _bad(str(exc))
        result = get_settings()
        if isinstance(result, dict):
            result['ok'] = True
        return result

    @app.post("/ai/v1/settings/test/{provider}")
    def settings_test(provider: str):
        cfg = load_settings()
        resp = _require_enabled(cfg)
        if resp is not None:
            return resp
        if provider in {'ssh', 'mp'}:
            return _get_project_store().client.request('POST', '/settings/test/' + provider)
        if provider != 'llm':
            return _bad('未知provider')
        result = _run_settings_test(provider, cfg)
        return {'mode': 'ai', **result}

    @app.get("/ai/v1/settings/secret-status")
    def settings_secret_status():
        cfg = load_settings()
        resp = _require_enabled(cfg)
        if resp is not None:
            return resp
        # 复用统一判定：环境变量 > 系统凭据管理器 > 本地配置文件。
        # 此前这里内联硬编码 local_config，密钥迁到凭据管理器后仍会显示错误来源。
        model = _secret_status(cfg)['llm']
        remote = _get_project_store().client.request('GET', '/settings/secret-status')['secrets']
        return {'mode': 'ai', 'enabled': True, 'secrets': {'llm': model, **remote}}

    @app.post("/ai/v1/settings/reveal")
    def settings_reveal(payload: dict):
        """Legacy endpoint is permanently disabled; secrets are non-revealable."""
        del payload
        cfg = load_settings()
        resp = _require_enabled(cfg)
        if resp is not None:
            return resp
        return JSONResponse(status_code=403, content={"mode": "ai", "error": {
            "code": "AI_SECRET_REVEAL_DISABLED",
            "message": "已保存密钥不可查看、复制或取回；只能整体替换或清除",
            "retryable": False}})

    @app.put("/ai/v1/settings/secrets/{kind}")
    def settings_update_secret(kind: str, payload: dict):
        cfg = load_settings()
        resp = _require_enabled(cfg)
        if resp is not None:
            return resp
        kind = kind.strip().lower()
        action = payload.get('action')
        if action not in {'replace', 'clear'}:
            return _bad('action必须为replace或clear')
        if kind in {'mp', 'ssh'}:
            value = '' if action == 'clear' else payload.get('value')
            if action == 'replace' and not value:
                return _bad('replace需要非空value')
            _get_project_store().client.request('POST', '/settings/secrets/' + kind, json={'value': value})
        elif kind == 'llm':
            try:
                _persist_settings(_update_secret(cfg, kind, action, payload.get('value')))
            except ValueError as exc:
                return _bad(str(exc))
        else:
            return _bad('未知密钥类型')
        statuses = settings_secret_status()
        status = statuses['secrets'][kind]
        return {'mode': 'ai', 'ok': True, 'kind': kind, **status, 'secret': status}
    @app.get("/ai/v1/projects/{project_id}/settings")
    def get_project_settings(project_id: str):
        cfg = load_settings()
        resp = _require_enabled(cfg)
        if resp is not None:
            return resp
        store = ProjectSettingsStore()
        return {"mode": "ai", "project_id": project_id,
                "settings": store.load(project_id)}

    @app.put("/ai/v1/projects/{project_id}/settings")
    def put_project_settings(project_id: str, payload: dict):
        cfg = load_settings()
        resp = _require_enabled(cfg)
        if resp is not None:
            return resp
        accuracy = payload.get("accuracy") if isinstance(payload, dict) else None
        if accuracy is None:
            return JSONResponse(status_code=400, content={"mode": "ai", "error": {
                "code": "AI_MODE_BAD_PROJECT_SETTINGS",
                "message": "缺少 accuracy 字段", "retryable": False}})
        try:
            stored = ProjectSettingsStore().save(project_id, accuracy)
        except ProjectSettingsError as exc:
            return JSONResponse(status_code=400, content={"mode": "ai", "error": {
                "code": "AI_MODE_BAD_PROJECT_SETTINGS", "message": str(exc),
                "retryable": False}})
        return {"mode": "ai", "ok": True, "settings": stored}

    @app.delete("/ai/v1/projects/{project_id}/settings")
    def delete_project_settings(project_id: str):
        cfg = load_settings()
        resp = _require_enabled(cfg)
        if resp is not None:
            return resp
        removed = ProjectSettingsStore().delete(project_id)
        return {"mode": "ai", "ok": True, "deleted": removed,
                "project_id": project_id}


    def _project_404(what: str) -> JSONResponse:
        return JSONResponse(status_code=404, content={"mode": "ai", "error": {
            "code": "AI_MODE_PROJECT_NOT_FOUND", "message": what, "retryable": False}})

    def _bad(what: str) -> JSONResponse:
        return JSONResponse(status_code=400, content={"mode": "ai", "error": {
            "code": "AI_MODE_BAD_REQUEST", "message": what, "retryable": False}})

    @app.get("/ai/v1/projects")
    def list_projects():
        cfg = load_settings()
        resp = _require_enabled(cfg)
        if resp is not None:
            return resp
        store = _get_project_store()
        return {"mode": "ai", "enabled": True,
                "projects": store.list_projects()}

    @app.get("/ai/v1/history/recent")
    def recent_history(limit: int = Query(default=10, ge=1, le=20)):
        """Return a read-only project/task summary without backend synchronization."""
        cfg = load_settings()
        resp = _require_enabled(cfg)
        if resp is not None:
            return resp
        return {
            "mode": "ai",
            "source": "ai",
            "retention": "persistent",
            "records": _get_project_store().list_recent_history(limit=limit),
        }

    @app.post("/ai/v1/projects")
    def create_project(payload: dict):
        cfg = load_settings()
        resp = _require_enabled(cfg)
        if resp is not None:
            return resp
        name = str(payload.get("name") or "")
        if not name.strip():
            return _bad("请填写项目名称")
        project = _get_project_store().create_project(
            name, str(payload.get("description") or ""))
        return {"mode": "ai", "project": project}

    @app.delete("/ai/v1/projects/{project_id}")
    def delete_project(project_id: str):
        cfg = load_settings()
        resp = _require_enabled(cfg)
        if resp is not None:
            return resp
        deleted = _get_project_store().delete_project(project_id)
        return {"mode": "ai", "deleted": deleted}

    @app.get("/ai/v1/projects/{project_id}/tasks")
    def list_tasks(project_id: str):
        resp = _require_enabled(load_settings())
        if resp is not None:
            return resp
        return {"mode": "ai", "tasks": _get_project_store().list_tasks(project_id)}

    @app.post("/ai/v1/projects/{project_id}/tasks")
    def create_task(project_id: str, payload: dict):
        cfg = load_settings()
        resp = _require_enabled(cfg)
        if resp is not None:
            return resp
        store = _get_project_store()
        if store.get_project(project_id) is None:
            return _project_404("项目不存在或被删除")
        task = store.create_task(
            project_id,
            title=str(payload.get("title") or ""),
            goal=str(payload.get("goal") or ""),
            local_workspace=str(payload.get("local_workspace") or ""),
            hpc_workspace=str(payload.get("hpc_workspace") or ""))
        return {"mode": "ai", "task": task}

    @app.get("/ai/v1/projects/{project_id}/tasks/{task_id}/detail")
    def get_task_detail_route(project_id: str, task_id: str):
        resp = _require_enabled(load_settings())
        if resp is not None:
            return resp
        return _get_project_store().client.request('GET', f'/projects/{project_id}/tasks/{task_id}/detail')

    @app.patch("/ai/v1/projects/{project_id}/tasks/{task_id}")
    def update_task_route(project_id: str, task_id: str, payload: dict):
        cfg = load_settings()
        resp = _require_enabled(cfg)
        if resp is not None:
            return resp
        store = _get_project_store()
        if store.get_project(project_id) is None:
            return _project_404("项目不存在或被删除")
        if store.get_task(project_id, task_id) is None:
            return _project_404("计算任务不存在或被删除")
        fields = {}
        title = str(payload.get("title") or "").strip()
        if "title" in payload:
            if not title:
                return _bad("任务标题不能为空")
            fields["title"] = title[:80]
        goal = str(payload.get("goal") or "").strip()
        if "goal" in payload:
            if not goal:
                return _bad("计算需求不能为空")
            fields["goal"] = goal
        if "local_workspace" in payload:
            fields["local_workspace"] = str(payload.get("local_workspace") or "").strip() or None
        if "hpc_workspace" in payload:
            fields["hpc_workspace"] = str(payload.get("hpc_workspace") or "").strip() or None
        if not fields:
            return _bad("没有可更新的字段（title/goal/local_workspace/hpc_workspace）")
        with _task_state_lock(project_id, task_id):
            task = store.update_task(project_id, task_id, **fields)
        return {"mode": "ai", "task": task}

    @app.delete("/ai/v1/projects/{project_id}/tasks/{task_id}")
    def delete_task_route(project_id: str, task_id: str):
        cfg = load_settings()
        resp = _require_enabled(cfg)
        if resp is not None:
            return resp
        store = _get_project_store()
        if store.get_project(project_id) is None:
            return _project_404("项目不存在或被删除")
        with _task_state_lock(project_id, task_id):
            deleted = store.delete_task(project_id, task_id)
        if deleted is None:
            return _project_404("计算任务不存在或被删除")
        return {"mode": "ai", "deleted": True, "task_id": task_id}

    @app.get("/ai/v1/browse/local")
    def browse_local_route(path: str = ""):
        resp = _require_enabled(load_settings())
        if resp is not None:
            return resp
        return _get_project_store().client.request('GET', '/browse/local', params={'path': path})

    @app.get("/ai/v1/browse/hpc")
    def browse_hpc_route(path: str = ""):
        resp = _require_enabled(load_settings())
        if resp is not None:
            return resp
        return _get_project_store().client.request('GET', '/browse/hpc', params={'path': path})

    @app.post("/ai/v1/browse/local/pick")
    def browse_local_pick(payload: dict = None):
        resp = _require_enabled(load_settings())
        if resp is not None:
            return resp
        return _get_project_store().client.request('POST', '/browse/local/pick', json=payload or {})

    @app.get("/ai/v1/projects/{project_id}/tasks/{task_id}/messages")
    def list_messages(project_id: str, task_id: str):
        cfg = load_settings()
        resp = _require_enabled(cfg)
        if resp is not None:
            return resp
        if _get_project_store().get_task(project_id, task_id) is None:
            return _project_404("计算任务不存在或被删除")
        # Read running state before messages: completion may happen between the
        # reads. Returning running=true with final messages causes one harmless
        # extra poll; running=false with pre-completion messages loses the final
        # result until a manual reload.
        generation = generation_status(_get_project_store(), project_id, task_id)
        return {"mode": "ai",
                "messages": _get_project_store().list_messages(project_id, task_id),
                "generation": generation,
                "pending_actions": [_stream_card(card) for card in
                                    _list_consent_cards(_get_project_store(), project_id, task_id)]}

    @app.post("/ai/v1/projects/{project_id}/tasks/{task_id}/messages")
    def send_message(project_id: str, task_id: str, payload: dict):
        cfg = load_settings()
        resp = _require_enabled(cfg)
        if resp is not None:
            return resp
        store = _get_project_store()
        if store.get_task(project_id, task_id) is None:
            return _project_404("计算任务不存在或被删除")
        content = str(payload.get("content") or "").strip()
        if not content:
            return _bad("消息内容不能为空")
        try:
            run = ChatRun(store, project_id, task_id)
        except GenerationBusy:
            return JSONResponse(status_code=409, content={"mode": "ai", "error": {
                "code": "GENERATION_RUNNING", "message": "该任务仍在生成，请等待或明确停止。"}})
        # Sync callers share the same producer lifetime and exclusion as SSE.
        # Cancellation of an HTTP request must not release its task early.
        try:
            store.append_message(project_id, task_id, "user", content)
        except Exception:
            run.finish("", state="error")
            raise
        _clear_auto_wake_stop(store, project_id, task_id)   # 用户又说话了→接着跑
        # 用户又说话了＝要这条链继续：清掉"已停止"标记，让后台事件能再次自动唤醒。
        _clear_auto_wake_stop(store, project_id, task_id)

        def _sync_events():
            answer = _chat.reply(store, project_id, task_id, content, should_stop=run.should_stop)
            yield {"type": "stopped" if run.should_stop() else "done", "answer": answer}

        run.detach()
        threading.Thread(target=run.produce, args=(_sync_events,), daemon=True).start()
        run.finished.wait()
        return {"mode": "ai", "answer": run.answer}

    @app.post("/ai/v1/projects/{project_id}/tasks/{task_id}/messages/stream")
    def send_message_stream(project_id: str, task_id: str,
                                  payload: dict):
        """SSE 流式发送：先落库用户消息，再流式输出（思考 -> 正文），完成后落 assistant 消息。"""
        cfg = load_settings()
        resp = _require_enabled(cfg)
        if resp is not None:
            return resp
        store = _get_project_store()
        if store.get_task(project_id, task_id) is None:
            return _project_404("计算任务不存在或被删除")
        content = str(payload.get("content") or "").strip()
        if not content:
            return _bad("消息内容不能为空")
        try:
            run = ChatRun(store, project_id, task_id)
        except GenerationBusy:
            return JSONResponse(status_code=409, content={"mode": "ai", "error": {
                "code": "GENERATION_RUNNING", "message": "该任务仍在生成，请等待或明确停止。"}})
        try:
            store.append_message(project_id, task_id, "user", content)
        except Exception:
            run.finish("", state="error")
            raise

        def _events():
            return _chat.reply_stream(store, project_id, task_id, content,
                                      should_stop=run.should_stop)

        threading.Thread(target=run.produce, args=(_events,), daemon=True).start()

        async def _iter():
            try:
                while True:
                    try:
                        ev = await asyncio.to_thread(run.events.get, True, 1)
                    except queue.Empty:
                        yield ": keepalive\n\n"
                        continue
                    if ev.get("type") == "_end":
                        break
                    yield "data: " + json.dumps(ev, ensure_ascii=False) + "\n\n"
            finally:
                run.detach()

        return StreamingResponse(_iter(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache",
                                          "X-Accel-Buffering": "no"})


    @app.post("/ai/v1/projects/{project_id}/tasks/{task_id}/messages/stop")
    def stop_message_stream(project_id: str, task_id: str):
        """停止指定任务正在进行的流式生成；无活跃运行返回 stopped=false。"""
        cfg = load_settings()
        resp = _require_enabled(cfg)
        if resp is not None:
            return resp
        store = _get_project_store()
        if store.get_task(project_id, task_id) is None:
            return _project_404("计算任务不存在或被删除")
        # 一个按钮停到底：对话生成 + 计算流程（监控/下游自动准备）+ 后台自动唤醒。
        result = stop_everything(store, project_id, task_id)
        return {"mode": "ai", "stopped": True, **result}

    @app.post("/ai/v1/projects/{project_id}/tasks/{task_id}/messages/consent")
    def resolve_consent(project_id: str, task_id: str, payload: dict):
        resp = _require_enabled(load_settings())
        if resp is not None:
            return resp
        card_id = str(payload.get('card_id') or '')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', card_id):
            return _bad('无效card_id')
        store = _get_project_store()
        card = _get_consent_card(store, project_id, task_id, card_id)
        if card is None:
            return _project_404('确认卡不存在')
        if card.get('kind') == 'remote_file':
            return JSONResponse(status_code=403, content={'mode': 'ai', 'error': {
                'code': 'REMOTE_FILE_REVIEW_REQUIRED',
                'message': '文件操作必须前往同一计算任务的 Toolbox 完整审阅卡批准或拒绝',
                'retryable': False}, 'card_id': card_id, 'kind': 'remote_file'})
        path = store.client.task_path(project_id, task_id) + '/consents/' + quote(card_id, safe='')
        result = store.client.request('POST', path, json={'approved': payload.get('approved'), 'note': payload.get('note', '')})
        # 回执只在"没有正在进行的对话轮"时才落成 assistant 消息：这时没有 AI 会替它
        # 总结，用户需要留痕（例如上一个进程死掉后恢复的卡片）。
        # 对话轮还在跑时，那张卡会被当轮 AI 收尾总结，不必再逐条刷"已把…复制到…"。
        if (result.get('result') and not result.get('replayed')
                and not _generation_running(store, project_id, task_id)):
            store.append_message(project_id, task_id, role='assistant', content=str(result['result']))
        return {**result, 'mode': 'ai', 'state': result['card']['state'], 'kind': result['card'].get('kind'), 'approved': payload.get('approved')}

    @app.get("/ai/v1/projects/{project_id}/tasks/{task_id}/context")
    def get_task_context(project_id: str, task_id: str):
        cfg = load_settings()
        resp = _require_enabled(cfg)
        if resp is not None:
            return resp
        store = _get_project_store()
        if store.get_task(project_id, task_id) is None:
            return _project_404("计算任务不存在或被删除")
        return store.task_context(project_id, task_id)

    @app.get("/ai/v1/context")
    def get_context():
        cfg = load_settings()
        resp = _require_enabled(cfg)
        if resp is not None:
            return resp
        return _get_project_store().context()

    @app.get("/ai/v1/jobs/waiting")
    def jobs_waiting():
        cfg = load_settings()
        resp = _require_enabled(cfg)
        if resp is not None:
            return resp
        waiting, count = _get_project_store().list_waiting()
        return {"mode": "ai", "waiting": waiting, "count": count}
    return app

#: uvicorn 入口：uvicorn ai_mode.server:app。创建对象无副作用（lifespan 才走 ensure_layout）。
app = create_ai_mode_app()
