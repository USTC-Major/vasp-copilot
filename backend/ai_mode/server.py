"""智能模式后端入口（完全独立，零依赖工具箱）。

启动（backend 目录下）：
  powershell -ExecutionPolicy Bypass -File .\run_ai.ps1
或直接：
  ../.venv/Scripts/python.exe -m uvicorn ai_mode.server:app --host 127.0.0.1 --port 8500

开关两态均能启动：
- ENABLE_AI_MODE=false（默认）：工具箱服务不加载本包；本进程显式启动后，
  只返回「未启用」禁用信封（503），用于演示/验证开关两态。
- ENABLE_AI_MODE=true：返回掩码后的配置汇总（密钥不出现）。

M1 阶段端点：根部 /、/ai/v1/ping、/ai/v1/config（掩码汇总）、/ai/v1/layout。
后续里程碑在此基础上挂真功能。
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .config import load_settings
from .gate import is_ai_mode_enabled
from .settings.global_api import (
    mask_config as _mask_settings,
    persist as _persist_settings,
    check_connection as _run_settings_test,
    update_from_patch as _apply_settings_patch,
    store_ssh_password as _store_ssh_password,
    get_ssh_password as _get_ssh_password,
    secret_status as _secret_status,
    writable_fields as _writable_fields,
)
from .settings.project import (
    ProjectSettingsError,
    ProjectSettingsStore,
)
from . import chat as _chat
from .consent import get_card as _get_consent_card
from .consent import resolve_card as _resolve_consent_card
from .projects import get_project_store as _get_project_store
from .storage import ensure_layout

logger = logging.getLogger("ai_mode")

#: 正在进行的流式生成任务停止标记（键=(project_id, task_id)）。
_ACTIVE_STOPS: dict[tuple[str, str], bool] = {}

APP_TITLE = "VASP-Doctor 智能模式"
APP_VERSION = "0.1.0-m1"


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
    # M55：后台监控线程——monitoring 任务按 poll_interval_seconds 自动推进
    if is_ai_mode_enabled():
        from .monitor import monitor_loop
        monitor_loop.start(store=_get_project_store())
    try:
        yield
    finally:
        try:
            from .monitor import monitor_loop
            monitor_loop.stop()
        except Exception:  # noqa: BLE001
            pass


def create_ai_mode_app() -> FastAPI:
    app = FastAPI(
        title=APP_TITLE,
        version=APP_VERSION,
        openapi_url="/ai/v1/openapi.json",
        lifespan=_lifespan,
    )

    @app.get("/")
    async def root() -> dict:
        return {"mode": "ai", "enabled": is_ai_mode_enabled(), "version": APP_VERSION}

    @app.get("/ai/v1/ping")
    async def ping() -> dict:
        return {"mode": "ai", "enabled": is_ai_mode_enabled(), "version": APP_VERSION}

    @app.get("/ai/v1/layout")
    async def layout() -> dict:
        dirs = ensure_layout()
        return {"home": str(dirs["sessions"].parent), "dirs": {
            name: str(path) for name, path in dirs.items()
        }}


    @app.get("/ai/v1/llm/status")
    async def llm_status():
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
    async def get_config(request: Request):
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
    async def get_settings():
        cfg = load_settings()
        resp = _require_enabled(cfg)
        if resp is not None:
            return resp
        return {"mode": "ai", "enabled": True, "settings": _mask_settings(cfg),
                "writable": _writable_fields()}

    @app.put("/ai/v1/settings")
    async def put_settings(payload: dict):
        cfg = load_settings()
        resp = _require_enabled(cfg)
        if resp is not None:
            return resp
        try:
            updated = _apply_settings_patch(cfg, payload)
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"mode": "ai", "error": {
                "code": "AI_MODE_BAD_SETTINGS", "message": str(exc),
                "retryable": False}})
        ssh_pw = payload.get("ssh_password") if isinstance(payload, dict) else None
        if ssh_pw is not None:
            try:
                _store_ssh_password(updated, ssh_pw)
            except ValueError as exc:
                return JSONResponse(status_code=400, content={"mode": "ai", "error": {
                    "code": "AI_MODE_BAD_SETTINGS", "message": str(exc),
                    "retryable": False}})
        _persist_settings(updated)
        return {"mode": "ai", "ok": True, "settings": _mask_settings(updated)}

    @app.post("/ai/v1/settings/test/{provider}")
    async def settings_test(provider: str):
        cfg = load_settings()
        resp = _require_enabled(cfg)
        if resp is not None:
            return resp
        try:
            result = _run_settings_test(provider, cfg)
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"mode": "ai", "error": {
                "code": "AI_MODE_UNKNOWN_PROVIDER", "message": str(exc),
                "retryable": False}})
        if result.get("ok"):
            return JSONResponse(status_code=200, content={
                "mode": "ai", "provider": result["provider"],
                "ok": True, "message": result["message"]})
        return JSONResponse(status_code=502, content={
            "mode": "ai", "provider": result["provider"], "ok": False,
            "error": {
                "code": "AI_MODE_PROVIDER_FAILED",
                "message": result["message"],
                "retryable": False,
            }})

    def _404_secret(message: str) -> JSONResponse:
        return JSONResponse(status_code=404, content={"mode": "ai", "error": {
            "code": "AI_MODE_SECRET_NOT_FOUND", "message": message,
            "retryable": False}})

    @app.get("/ai/v1/settings/secret-status")
    async def settings_secret_status():
        """密钥是否已配置（只回布尔态，不回明文）——设置页回显掩码用。"""
        cfg = load_settings()
        resp = _require_enabled(cfg)
        if resp is not None:
            return resp
        return {"mode": "ai", "enabled": True, "secrets": _secret_status(cfg)}

    @app.post("/ai/v1/settings/reveal")
    async def settings_reveal(payload: dict):
        """按需取回已存密钥原文（前端「点眼睛」才调用；仅本机展示，不写入日志）。"""
        cfg = load_settings()
        resp = _require_enabled(cfg)
        if resp is not None:
            return resp
        kind = ""
        if isinstance(payload, dict):
            kind = str(payload.get("kind", "")).strip().lower()
        if kind == "llm":
            if not cfg.llm_api_key:
                return _404_secret("未配置 LLM API key")
            return {"mode": "ai", "kind": kind, "value": cfg.llm_api_key}
        if kind == "mp":
            if not cfg.mp_api_key:
                return _404_secret("未配置 Materials Project API key")
            return {"mode": "ai", "kind": kind, "value": cfg.mp_api_key}
        if kind == "ssh":
            value = _get_ssh_password(cfg)
            if not value:
                return _404_secret("未配置 SSH 密码")
            return {"mode": "ai", "kind": kind, "value": value}
        return JSONResponse(status_code=400, content={"mode": "ai", "error": {
            "code": "AI_MODE_UNKNOWN_SECRET", "message": "未知密钥类型（llm|mp|ssh）",
            "retryable": False}})
    @app.get("/ai/v1/projects/{project_id}/settings")
    async def get_project_settings(project_id: str):
        cfg = load_settings()
        resp = _require_enabled(cfg)
        if resp is not None:
            return resp
        store = ProjectSettingsStore()
        return {"mode": "ai", "project_id": project_id,
                "settings": store.load(project_id)}

    @app.put("/ai/v1/projects/{project_id}/settings")
    async def put_project_settings(project_id: str, payload: dict):
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
    async def delete_project_settings(project_id: str):
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
    async def list_projects():
        cfg = load_settings()
        resp = _require_enabled(cfg)
        if resp is not None:
            return resp
        store = _get_project_store()
        return {"mode": "ai", "enabled": True,
                "projects": store.list_projects()}

    @app.post("/ai/v1/projects")
    async def create_project(payload: dict):
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
    async def delete_project(project_id: str):
        cfg = load_settings()
        resp = _require_enabled(cfg)
        if resp is not None:
            return resp
        deleted = _get_project_store().delete_project(project_id)
        return {"mode": "ai", "deleted": deleted}

    @app.get("/ai/v1/projects/{project_id}/tasks")
    async def list_tasks(project_id: str):
        cfg = load_settings()
        resp = _require_enabled(cfg)
        if resp is not None:
            return resp
        store = _get_project_store()
        if store.get_project(project_id) is None:
            return _project_404("项目不存在或被删除")
        return {"mode": "ai", "tasks": store.list_tasks(project_id)}

    @app.post("/ai/v1/projects/{project_id}/tasks")
    async def create_task(project_id: str, payload: dict):
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
    async def get_task_detail_route(project_id: str, task_id: str):
        """任务详情：把 flow 概要（phase/作业列表/依赖/等待原因/预检/报告）
        裁剪后吐给前端进度页；未开始计算流程时 flow 为空对象。"""
        cfg = load_settings()
        resp = _require_enabled(cfg)
        if resp is not None:
            return resp
        store = _get_project_store()
        if store.get_project(project_id) is None:
            return _project_404("项目不存在或被删除")
        task = store.get_task(project_id, task_id)
        if task is None:
            return _project_404("计算任务不存在或被删除")
        flow = dict(task.get("flow") or {})
        plan = dict(flow.get("plan") or {})
        jobs = []
        for j in (plan.get("jobs") or []):
            if not isinstance(j, dict):
                continue
            jobs.append({
                "key": str(j.get("key") or ""),
                "label": str(j.get("label") or j.get("key") or ""),
                "kind": str(j.get("kind") or ""),
                "requires": [str(r) for r in (j.get("requires") or [])],
                "status": str(j.get("status") or "draft"),
                "slurm_id": j.get("slurm_id"),
                "description": str(j.get("description") or ""),
            })
        detail = {
            "phase": str(flow.get("phase") or ""),
            "goal": str(flow.get("goal") or task.get("goal") or ""),
            "strategy": str(plan.get("strategy") or ""),
            "local_dir": str(flow.get("local_dir") or ""),
            "hpc_dir": str(flow.get("hpc_dir") or ""),
            "waiting": [str(k) for k in (flow.get("waiting") or [])],
            "precheck": (flow.get("precheck")
                         if isinstance(flow.get("precheck"), dict)
                         else {"ok": True, "issues": []}),
            "report": str(flow.get("report") or ""),
            "jobs": jobs,
        }
        return {"mode": "ai", "task_id": task_id, "flow": detail}

    @app.patch("/ai/v1/projects/{project_id}/tasks/{task_id}")
    async def update_task_route(project_id: str, task_id: str, payload: dict):
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
        task = store.update_task(project_id, task_id, **fields)
        return {"mode": "ai", "task": task}

    @app.delete("/ai/v1/projects/{project_id}/tasks/{task_id}")
    async def delete_task_route(project_id: str, task_id: str):
        cfg = load_settings()
        resp = _require_enabled(cfg)
        if resp is not None:
            return resp
        store = _get_project_store()
        if store.get_project(project_id) is None:
            return _project_404("项目不存在或被删除")
        deleted = store.delete_task(project_id, task_id)
        if deleted is None:
            return _project_404("计算任务不存在或被删除")
        return {"mode": "ai", "deleted": True, "task_id": task_id}

    @app.get("/ai/v1/browse/local")
    async def browse_local_route(path: str = ""):
        cfg = load_settings()
        resp = _require_enabled(cfg)
        if resp is not None:
            return resp
        from .browse import browse_local as _browse_local
        return {"mode": "ai", "kind": "local", **_browse_local(path)}

    @app.get("/ai/v1/browse/hpc")
    async def browse_hpc_route(path: str = ""):
        cfg = load_settings()
        resp = _require_enabled(cfg)
        if resp is not None:
            return resp
        from . import browse as _browse_mod
        ssh = _browse_mod.create_hpc_ssh(cfg)
        if ssh is None:
            return JSONResponse(status_code=400, content={"mode": "ai", "error": {
                "code": "AI_MODE_HPC_UNCONFIGURED",
                "message": "未配置超算账号：请在设置页添加 SSH 账号后再浏览超算目录。",
                "retryable": False}})
        try:
            return {"mode": "ai", "kind": "hpc",
                    **_browse_mod.browse_hpc(ssh, path)}
        finally:
            try:
                ssh.close()
            except Exception:
                pass

    @app.post("/ai/v1/browse/local/mkdir")
    async def browse_local_mkdir(payload: dict):
        cfg = load_settings()
        resp = _require_enabled(cfg)
        if resp is not None:
            return resp
        from .browse import mkdir_local as _mkdir_local
        return {"mode": "ai", "kind": "local",
                **_mkdir_local(str(payload.get("path") or ""),
                               str(payload.get("name") or ""))}

    @app.post("/ai/v1/browse/hpc/mkdir")
    async def browse_hpc_mkdir(payload: dict):
        cfg = load_settings()
        resp = _require_enabled(cfg)
        if resp is not None:
            return resp
        from . import browse as _browse_mod
        ssh = _browse_mod.create_hpc_ssh(cfg)
        if ssh is None:
            return JSONResponse(status_code=400, content={"mode": "ai", "error": {
                "code": "AI_MODE_HPC_UNCONFIGURED",
                "message": "未配置超算账号：请在设置页添加 SSH 账号后再管理超算目录。",
                "retryable": False}})
        try:
            return {"mode": "ai", "kind": "hpc",
                    **_browse_mod.mkdir_hpc(ssh,
                                            str(payload.get("path") or ""),
                                            str(payload.get("name") or ""))}
        finally:
            try:
                ssh.close()
            except Exception:
                pass



    @app.post("/ai/v1/browse/local/pick")
    def browse_local_pick(payload: dict = None):
        cfg = load_settings()
        resp = _require_enabled(cfg)
        if resp is not None:
            return resp
        import os
        from .browse import BrowseDialogError as _BrowseDialogError
        from .browse import pick_local_directory as _pick
        initial = str((payload or {}).get("initial_dir") or "").strip()
        try:
            picked = _pick(initial or None)
        except _BrowseDialogError as exc:
            return {"mode": "ai", "kind": "local", "ok": False,
                    "path": "", "notice": f"无法弹出系统目录选择窗口：{exc}"}
        except Exception as exc:  # noqa: BLE001
            return {"mode": "ai", "kind": "local", "ok": False, "path": "",
                    "notice": f"无法弹出系统目录选择窗口（{exc.__class__.__name__}）"}
        picked = (picked or "").strip()
        if not picked:
            return {"mode": "ai", "kind": "local", "ok": False,
                    "path": "", "notice": "已取消选择"}
        if not os.path.isdir(picked):
            return {"mode": "ai", "kind": "local", "ok": False,
                    "path": picked, "notice": "所选路径无效或不是目录"}
        return {"mode": "ai", "kind": "local", "ok": True, "path": picked}

    @app.get("/ai/v1/projects/{project_id}/tasks/{task_id}/messages")
    async def list_messages(project_id: str, task_id: str):
        cfg = load_settings()
        resp = _require_enabled(cfg)
        if resp is not None:
            return resp
        if _get_project_store().get_task(project_id, task_id) is None:
            return _project_404("计算任务不存在或被删除")
        return {"mode": "ai",
                "messages": _get_project_store().list_messages(project_id, task_id)}

    @app.post("/ai/v1/projects/{project_id}/tasks/{task_id}/messages")
    async def send_message(project_id: str, task_id: str, payload: dict):
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
        store.append_message(project_id, task_id, "user", content)
        answer = await asyncio.to_thread(_chat.reply, store, project_id,
                                         task_id, content)
        store.append_message(project_id, task_id, "assistant", answer)
        return {"mode": "ai", "answer": answer}

    @app.post("/ai/v1/projects/{project_id}/tasks/{task_id}/messages/stream")
    async def send_message_stream(project_id: str, task_id: str,
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
        store.append_message(project_id, task_id, "user", content)
        stop_key = (project_id, task_id)
        _ACTIVE_STOPS[stop_key] = False

        def _should_stop() -> bool:
            return bool(_ACTIVE_STOPS.get(stop_key))

        # 生产/回复在后台线程运行，避免同步消费 reply_stream 阻塞事件循环，
        # 使「停止」「卡片同意/拒绝」等 POST 始终能立即打断当前运行。
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue(maxsize=256)

        def _put(event: dict) -> None:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, event)
            except BaseException:
                pass

        def _produce() -> None:
            final = ""
            thinking_parts: list[str] = []
            try:
                for ev in _chat.reply_stream(store, project_id, task_id,
                                             content, should_stop=_should_stop):
                    ev = dict(ev)
                    kind = ev.get("type")
                    if kind == "thinking":
                        thinking_parts.append(ev.get("text") or "")
                    elif kind in ("done", "stopped"):
                        final = ev.get("answer") or ""
                    elif kind == "error" and not final:
                        final = ev.get("message") or ""
                    _put(ev)
            finally:
                if final.strip():
                    try:
                        store.append_message(project_id, task_id, "assistant",
                                             final.strip(),
                                             thinking="".join(thinking_parts).strip())
                    except Exception:
                        pass
                _put({"type": "_end"})

        thread = threading.Thread(target=_produce, daemon=True)
        thread.start()

        async def _iter():
            try:
                while True:
                    try:
                        ev = await asyncio.wait_for(queue.get(), 20)
                    except asyncio.TimeoutError:
                        yield "data: : keepalive\n\n"
                        continue
                    if ev.get("type") == "_end":
                        break
                    yield "data: " + json.dumps(ev, ensure_ascii=False) + "\n\n"
            finally:
                # 客户端断开/自然结束都要停止后台线程并清理标记，
                # 避免残留影响下一次同 key 请求。
                _ACTIVE_STOPS[stop_key] = True
                thread.join(timeout=5)
                _ACTIVE_STOPS.pop(stop_key, None)

        return StreamingResponse(_iter(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache",
                                          "X-Accel-Buffering": "no"})


    @app.post("/ai/v1/projects/{project_id}/tasks/{task_id}/messages/stop")
    async def stop_message_stream(project_id: str, task_id: str):
        """停止指定任务正在进行的流式生成；无活跃运行返回 stopped=false。"""
        cfg = load_settings()
        resp = _require_enabled(cfg)
        if resp is not None:
            return resp
        store = _get_project_store()
        if store.get_task(project_id, task_id) is None:
            return _project_404("计算任务不存在或被删除")
        stop_key = (project_id, task_id)
        active = stop_key in _ACTIVE_STOPS
        if active:
            _ACTIVE_STOPS[stop_key] = True
        return {"mode": "ai", "stopped": active}

    @app.post("/ai/v1/projects/{project_id}/tasks/{task_id}/messages/consent")
    async def resolve_consent(project_id: str, task_id: str, payload: dict):
        """处理授权卡片：workspace 类写入 grants/denials；submit 类驱动真实提交/取消。"""
        cfg = load_settings()
        resp = _require_enabled(cfg)
        if resp is not None:
            return resp
        store = _get_project_store()
        if store.get_task(project_id, task_id) is None:
            return _project_404("计算任务不存在或被删除")
        card_id = str(payload.get("card_id") or "").strip()
        if not card_id:
            return _bad("缺少 card_id")
        approved = bool(payload.get("approved"))
        note = str(payload.get("note") or "")[:500]
        card = _get_consent_card(store, project_id, task_id, card_id)
        if card is None:
            return {"mode": "ai", "ok": False, "reason": "card_missing",
                    "message": "该授权卡片不存在或已处理，请重新发起。"}
        kind = card.get("kind") or "workspace"
        if kind == "submit":
            result = await asyncio.to_thread(
                _chat.perform_submit, store, project_id, task_id, card_id,
                approved, note)
            final = (result or "").strip()
            if final:
                store.append_message(project_id, task_id, "assistant", final)
            return {"mode": "ai", "ok": True, "kind": "submit",
                    "approved": approved, "result": final}
        resolved = _resolve_consent_card(store, project_id, task_id, card_id,
                                         approved=approved, note=note)
        return {"mode": "ai", "ok": True, "kind": kind,
                "approved": resolved.get("approved", approved),
                "result": ("已授权本批操作，后续同类操作将直接执行" if approved
                           else "已拒绝本批操作，后续同类操作不再弹卡")}

    @app.get("/ai/v1/projects/{project_id}/tasks/{task_id}/context")
    async def get_task_context(project_id: str, task_id: str):
        cfg = load_settings()
        resp = _require_enabled(cfg)
        if resp is not None:
            return resp
        store = _get_project_store()
        if store.get_task(project_id, task_id) is None:
            return _project_404("计算任务不存在或被删除")
        return store.task_context(project_id, task_id)

    @app.get("/ai/v1/context")
    async def get_context():
        cfg = load_settings()
        resp = _require_enabled(cfg)
        if resp is not None:
            return resp
        return _get_project_store().context()

    @app.get("/ai/v1/jobs/waiting")
    async def jobs_waiting():
        cfg = load_settings()
        resp = _require_enabled(cfg)
        if resp is not None:
            return resp
        waiting, count = _get_project_store().list_waiting()
        return {"mode": "ai", "waiting": waiting, "count": count}
    return app

#: uvicorn 入口：uvicorn ai_mode.server:app。创建对象无副作用（lifespan 才走 ensure_layout）。
app = create_ai_mode_app()

