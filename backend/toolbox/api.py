"""HTTP contract shared by the Toolbox UI and optional AI service."""
from __future__ import annotations
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import APIRouter, FastAPI, Request, Query
from fastapi.responses import JSONResponse
from . import browse, consent, paths
from .config import ExecutionConfig, load_settings, save_settings
from .contracts import ToolboxError
from .service import ExecutionService, envelope

router = APIRouter(prefix='/toolbox', tags=['Toolbox execution'])

def service(request: Request) -> ExecutionService:
    return request.app.state.toolbox

async def error_handler(request, exc: ToolboxError):
    return JSONResponse(status_code=exc.status, content=envelope(ok=False, error=exc.payload()))

@router.get('/projects')
def projects(request: Request):
    return envelope(projects=service(request).store.list_projects())

@router.post('/projects')
def create_project(request: Request, payload: dict):
    name = payload.get('name')
    if not isinstance(name, str) or not name.strip():
        raise ToolboxError('INVALID_PROJECT', '项目名称不能为空')
    return envelope(project=service(request).store.create_project(name, str(payload.get('description') or '')))

@router.delete('/projects/{project_id}')
def delete_project(project_id: str, request: Request):
    svc = service(request)
    # Lock every task before removing the project, so monitors cannot resurrect it.
    from contextlib import ExitStack
    with svc._guard, ExitStack() as stack:
        for task in sorted(svc.store.list_tasks(project_id), key=lambda t: t['id']):
            stack.enter_context(consent.task_lock(project_id, task['id']))
        if not svc.store.delete_project(project_id):
            raise ToolboxError('PROJECT_NOT_FOUND', '项目不存在', 404)
    return envelope(deleted=True)

@router.get('/projects/{project_id}/tasks')
def tasks(project_id: str, request: Request):
    svc = service(request)
    if not svc.store.get_project(project_id):
        raise ToolboxError('PROJECT_NOT_FOUND', '项目不存在', 404)
    return envelope(tasks=[{k:v for k,v in t.items() if k != 'flow'} for t in svc.store.list_tasks(project_id)])

@router.post('/projects/{project_id}/tasks')
def create_task(project_id: str, request: Request, payload: dict):
    svc = service(request)
    with svc._guard:
        if not svc.store.get_project(project_id):
            raise ToolboxError('PROJECT_NOT_FOUND', '项目不存在', 404)
        allowed = {'title', 'goal', 'local_workspace', 'hpc_workspace'}
        if set(payload) - allowed or any(not isinstance(v, (str, type(None))) for v in payload.values()):
            raise ToolboxError('INVALID_TASK', '只接受任务标题、目标和工作区路径')
        task = svc.store.create_task(project_id, **payload)
    return envelope(task=task)

@router.patch('/projects/{project_id}/tasks/{task_id}')
def update_task(project_id: str, task_id: str, request: Request, payload: dict):
    svc = service(request)
    if not payload or set(payload) - {'title', 'goal', 'local_workspace', 'hpc_workspace'}:
        raise ToolboxError('INVALID_TASK_PATCH', '禁止直接修改执行状态；只接受任务元数据')
    if any(not isinstance(v, (str, type(None))) for v in payload.values()):
        raise ToolboxError('INVALID_TASK_PATCH', '任务元数据必须为字符串')
    with consent.task_lock(project_id, task_id):
        task = svc.require_task(project_id, task_id)
        if {'local_workspace', 'hpc_workspace'} & set(payload):
            if (task.get('flow') or {}).get('phase') == 'monitoring':
                raise ToolboxError('TASK_ACTIVE', '监控期间不能改变工作区', 409)
            raw = task.get('flow') or {}
            if any(j.get('submission_state') == 'unknown' or j.get('status') in {'submitted','queued','running','unknown'}
                   for j in (raw.get('plan') or {}).get('jobs', [])):
                raise ToolboxError('TASK_UNRESOLVED', '在途或结果不确定的作业不能改变工作区', 409)
            if raw:
                if 'local_workspace' in payload:
                    local = payload['local_workspace'] or ''
                    raw['local_dir'] = str(Path(local).expanduser().resolve()) if local else str(svc.root / 'workspace' / f'{project_id}__{task_id}')
                if 'hpc_workspace' in payload:
                    raw['hpc_dir'] = payload['hpc_workspace'] or ''
                raw.update(draft=[], artifacts={}, script_attestations={}, precheck={'ok':False,'issues':[]})
                for action in (raw.get('consent') or {}).get('actions', {}).values():
                    if action.get('state') in {'pending', 'approved'}:
                        action.update(state='expired', result='工作区已变更，必须重新确认')
                payload = {**payload, 'flow': raw}
        task = svc.store.update_task(project_id, task_id, **payload)
    task.pop('flow', None)
    return envelope(task=task)

@router.delete('/projects/{project_id}/tasks/{task_id}')
def delete_task(project_id: str, task_id: str, request: Request):
    svc = service(request)
    with consent.task_lock(project_id, task_id):
        svc.require_task(project_id, task_id)
        svc.store.delete_task(project_id, task_id)
    return envelope(deleted=True, task_id=task_id)

@router.get('/projects/{project_id}/tasks/{task_id}/detail')
def detail(project_id: str, task_id: str, request: Request):
    return service(request).detail(project_id, task_id)

@router.get('/projects/{project_id}/tasks/{task_id}/events')
def events(project_id: str, task_id: str, request: Request, after: int = Query(0, ge=0)):
    svc = service(request)
    svc.require_task(project_id, task_id)
    rows = svc.store.list_events(project_id, task_id, after)
    return envelope(events=rows, cursor=rows[-1]['id'] if rows else after)

@router.post('/projects/{project_id}/tasks/{task_id}/tools')
def tools(project_id: str, task_id: str, request: Request, payload: dict):
    return service(request).execute(project_id, task_id, payload.get('name'), payload.get('args', {}))

@router.get('/projects/{project_id}/tasks/{task_id}/consents')
def cards(project_id: str, task_id: str, request: Request):
    svc = service(request)
    svc.require_task(project_id, task_id)
    return envelope(cards=consent.list_cards(svc.store, project_id, task_id))

@router.get('/projects/{project_id}/tasks/{task_id}/consents/{card_id}')
def card(project_id: str, task_id: str, card_id: str, request: Request):
    svc = service(request)
    svc.require_task(project_id, task_id)
    result = consent.get_card(svc.store, project_id, task_id, card_id)
    if result is None:
        raise ToolboxError('CARD_NOT_FOUND', '授权卡不存在', 404)
    return envelope(card=result)

@router.post('/projects/{project_id}/tasks/{task_id}/consents/{card_id}')
def resolve(project_id: str, task_id: str, card_id: str, request: Request, payload: dict):
    return service(request).resolve(project_id, task_id, card_id, payload.get('approved'), str(payload.get('note') or '')[:500])

@router.get('/recent-history')
def history(request: Request, limit: int = Query(10, ge=1, le=20)):
    return envelope(items=service(request).store.list_recent_history(limit))

@router.get('/jobs/waiting')
def waiting(request: Request):
    items, total = service(request).store.list_waiting()
    return envelope(jobs=items, total=total)

def settings_payload(svc):
    cfg = svc.settings_loader()
    return envelope(settings={
        'max_jobs': cfg.max_jobs, 'poll_interval_seconds': cfg.poll_interval_seconds,
        'ssh': {key: getattr(cfg, 'ssh_' + key) for key in
                ['name', 'host', 'port', 'username', 'known_hosts_path', 'identity_file']} | {'scheduler_backend': cfg.scheduler_backend},
        'materials_project': {'configured': bool(cfg.mp_api_key)},
    }, backend_mode=svc.backend_mode())

@router.get('/settings')
def settings(request: Request):
    return settings_payload(service(request))

@router.put('/settings')
def update_settings(request: Request, payload: dict):
    svc = service(request)
    allowed = set(ExecutionConfig.model_fields) - {'data_dir', 'mp_api_key'}
    if set(payload) - allowed:
        raise ToolboxError('INVALID_SETTINGS', '未知或禁止设置字段')
    with svc._guard:
        try:
            cfg = ExecutionConfig(**(svc.settings_loader().model_dump() | payload))
            if not 10 <= cfg.poll_interval_seconds <= 3600 or cfg.max_jobs < 1 or not 1 <= cfg.ssh_port <= 65535:
                raise ValueError('range')
        except Exception:
            raise ToolboxError('INVALID_SETTINGS', '请检查端口、作业上限和轮询间隔（10–3600秒）') from None
        save_settings(cfg, svc.root / 'toolbox_config.json')
    return settings_payload(svc)

@router.post('/settings/secrets/{kind}')
def secret(kind: str, request: Request, payload: dict):
    svc = service(request)
    value = payload.get('value')
    if not isinstance(value, str):
        raise ToolboxError('INVALID_SECRET', 'value必须为字符串；空字符串清除')
    with svc._guard:
        cfg = svc.settings_loader()
        if kind == 'ssh':
            if not cfg.ssh_host or not cfg.ssh_username:
                raise ToolboxError('SSH_UNCONFIGURED', '请先设置SSH主机和用户名')
            from .ssh.credentials import KeyringCredentialStore
            credentials = KeyringCredentialStore()
            if value:
                credentials.set_password(cfg.ssh_host, cfg.ssh_username, value)
            else:
                credentials.delete_password(cfg.ssh_host, cfg.ssh_username)
        elif kind == 'mp':
            import os
            if os.environ.get('TOOLBOX_MP_API_KEY') or os.environ.get('AI_MODE_MP_API_KEY'):
                raise ToolboxError('SECRET_ENV_MANAGED', 'MP密钥由环境变量管理，不能通过页面修改')
            cfg.mp_api_key = value
            save_settings(cfg, svc.root / 'toolbox_config.json')
        else:
            raise ToolboxError('INVALID_SECRET_KIND', '不支持的凭据类型')
    return envelope(configured=bool(value))

@router.get('/settings/secret-status')
def secret_status(request: Request):
    import os
    cfg = service(request).settings_loader()
    mp_env = bool(os.environ.get('TOOLBOX_MP_API_KEY') or os.environ.get('AI_MODE_MP_API_KEY'))
    ssh_configured = False
    if cfg.ssh_host and cfg.ssh_username:
        from .ssh.credentials import KeyringCredentialStore
        try:
            ssh_configured = bool(KeyringCredentialStore().get_password(cfg.ssh_host, cfg.ssh_username))
        except Exception:
            pass
    return envelope(secrets={
        'mp': {'configured': bool(cfg.mp_api_key), 'source': 'environment' if mp_env else 'local_config' if cfg.mp_api_key else 'none', 'manageable': not mp_env},
        'ssh': {'configured': ssh_configured, 'source': 'credential_store' if ssh_configured else 'none', 'manageable': True}})

@router.post('/settings/test/mp')
def test_mp(request: Request):
    from .settings import probe_mp
    return envelope(**probe_mp(service(request).settings_loader()))

@router.post('/settings/test/ssh')
def test_ssh(request: Request):
    from .settings import probe_ssh
    return envelope(**probe_ssh(service(request).settings_loader()))

@router.get('/browse/{kind}')
def browse_directory(kind: str, request: Request, path: str = ''):
    if kind == 'local':
        return envelope(kind=kind, **browse.browse_local(path))
    if kind != 'hpc':
        raise ToolboxError('INVALID_BROWSE_KIND', '目录类型应为local或hpc')
    ssh = browse.create_hpc_ssh(service(request).settings_loader())
    if ssh is None:
        raise ToolboxError('SSH_UNCONFIGURED', '未配置SSH主机和用户名')
    try:
        return envelope(kind=kind, **browse.browse_hpc(ssh, path))
    finally:
        ssh.close()

@router.post('/browse/local/pick')
def pick_directory(payload: dict):
    try:
        selected = browse.pick_local_directory(payload.get('initial_dir') or None)
        if selected and not Path(selected).is_dir():
            return envelope(kind='local', ok=False, path='', notice='所选目录不存在')
        return envelope(kind='local', ok=bool(selected), path=selected or '', notice='' if selected else '已取消选择')
    except Exception as exc:
        return envelope(kind='local', ok=False, path='', notice='目录选择不可用：' + (str(exc) if isinstance(exc, browse.BrowseDialogError) else type(exc).__name__))

@router.post('/browse/{kind}/mkdir')
def mkdir(kind: str, request: Request, payload: dict):
    path, name = payload.get('path', ''), payload.get('name', '')
    if not isinstance(path, str) or not isinstance(name, str):
        raise ToolboxError('INVALID_DIRECTORY', '路径和目录名必须为字符串')
    if kind == 'local':
        result = browse.mkdir_local(path, name)
    elif kind == 'hpc':
        ssh = browse.create_hpc_ssh(service(request).settings_loader())
        if ssh is None:
            raise ToolboxError('SSH_UNCONFIGURED', '未配置SSH主机和用户名')
        try:
            result = browse.mkdir_hpc(ssh, path, name)
        finally:
            ssh.close()
    else:
        raise ToolboxError('INVALID_BROWSE_KIND', '目录类型应为local或hpc')
    if not result['ok']:
        raise ToolboxError('MKDIR_FAILED', result.get('notice', '创建目录失败'))
    return envelope(kind=kind, **result)

def create_toolbox_app(*, root: Path | None = None, settings_loader=None, orch_factory=None, monitor_enabled=True):
    root = Path(root) if root is not None else paths.home_dir()
    loader = settings_loader or (lambda: load_settings(config_path=root / 'toolbox_config.json'))
    @asynccontextmanager
    async def lifespan(app):
        app.state.toolbox = ExecutionService(root, settings_loader=loader,
            orch_factory=orch_factory, monitor_enabled=monitor_enabled).start()
        try:
            yield
        finally:
            app.state.toolbox.close()
    app = FastAPI(lifespan=lifespan)
    app.add_exception_handler(ToolboxError, error_handler)
    app.include_router(router, prefix='/api/v1')
    return app
