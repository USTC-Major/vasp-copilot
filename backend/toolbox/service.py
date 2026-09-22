"""The sole execution owner; both human and AI callers use this service."""
from __future__ import annotations
import copy
import threading
from pathlib import Path
from .config import load_settings
from .contracts import ToolboxError, ToolFailure
from .owner import ProcessOwner
from .projects import ProjectStore
from .commands import ToolExecutor
from .orchestrator import Orchestrator
from .monitor import MonitorLoop
from . import consent

def envelope(**data):
    return {'mode': 'toolbox', **data}

class ExecutionService:
    def __init__(self, root: Path, *, settings_loader=load_settings, orch_factory=None, monitor_enabled=True, file_factory=None):
        self.root = Path(root)
        self.settings_loader = settings_loader
        self.orch_factory = orch_factory
        self.monitor_enabled = monitor_enabled
        self.file_factory = file_factory
        self.files = None
        self._backend_mode = None
        self.owner = ProcessOwner(root)
        self.store = None
        self._guard = threading.RLock()
        self.monitor = MonitorLoop(settings_loader=settings_loader,
            orch_factory=lambda p, t, cfg: self.orchestrator())

    def start(self):
        self.owner.acquire()
        try:
            self.store = ProjectStore(self.root)
            from .file_actions import FileActions
            self.files = FileActions(self, self.file_factory)
            self.files.start()
            if self.monitor_enabled:
                self.monitor.start(self.store)
        except BaseException:
            if self.files:
                self.files.close()
            self.monitor.stop()
            self.owner.close()
            raise
        return self

    def close(self):
        if self.files:
            self.files.close()
        self.monitor.stop()
        self.owner.close()

    def orchestrator(self):
        orch = self.orch_factory() if self.orch_factory else Orchestrator.from_settings(self.settings_loader())
        self._backend_mode = getattr(orch, 'execution_mode', getattr(getattr(orch, 'hpc', None), 'execution_mode', 'None'))
        return orch

    def backend_mode(self):
        # Determine availability without opening SSH connections on a GET.
        if self.orch_factory:
            return self._backend_mode or 'None'
        cfg = self.settings_loader()
        return 'Real' if cfg.ssh_host and cfg.ssh_username else 'None'

    def require_task(self, project_id, task_id):
        task = self.store.get_task(project_id, task_id)
        if task is None:
            raise ToolboxError('TASK_NOT_FOUND', '计算任务不存在或已删除', 404)
        return task

    def executor(self, project_id, task_id):
        self.require_task(project_id, task_id)
        return ToolExecutor(store=self.store, project_id=project_id, task_id=task_id,
                            cfg=self.settings_loader(), orch_factory=self.orchestrator)

    def detail(self, project_id, task_id):
        task = self.require_task(project_id, task_id)
        raw = copy.deepcopy(task.get('flow') or {})
        plan = raw.get('plan') or {}
        cfg = self.settings_loader()
        monitor = {'state': 'monitoring' if raw.get('phase') == 'monitoring' else 'idle',
                   'interval_seconds': cfg.poll_interval_seconds, 'remote_cancelled': False,
                   **raw.get('monitor', {})}
        flow = {**raw, 'execution_mode': raw.get('execution_mode', 'None'),
                'phase': raw.get('phase', ''), 'goal': raw.get('goal') or task.get('goal', ''),
                'strategy': plan.get('strategy', ''), 'jobs': plan.get('jobs', []),
                'local_dir': raw.get('local_dir', ''), 'hpc_dir': raw.get('hpc_dir', ''),
                'waiting': raw.get('waiting', []), 'report': raw.get('report', ''),
                'precheck': raw.get('precheck') or {'ok': False, 'issues': []},
                'draft': raw.get('draft', []), 'artifacts': raw.get('artifacts', {})}
        for job in flow['jobs']:
            job['attempts'] = copy.deepcopy(job.get('attempt_history', []))
        # Cards are a separate projection. No hidden file content in task detail.
        flow.pop('consent', None)
        cards = [copy.deepcopy(a) for a in (raw.get('consent') or {}).get('actions', {}).values()
                 if a.get('state') == 'pending']
        task.pop('flow', None)
        from .file_actions import public, summary
        file_scopes = [s for s in (raw.get('consent') or {}).get('computation_scopes', {}).values() if s.get('kind') == 'file']
        file_actions = [a for a in (raw.get('consent') or {}).get('actions', {}).values() if a.get('kind') == 'remote_file']
        active_states = {'pending','approved','executing','unknown'}
        active_files = [a for a in file_actions if a.get('state') in active_states]
        terminal_files = sorted((a for a in file_actions if a.get('state') not in active_states),
                                key=lambda a:(a.get('created_at',''),a['action_id']),reverse=True)[:20]
        return envelope(task_id=task_id, task=task, flow=flow, consents=[summary(a) if a.get('kind')=='remote_file' else public(a) for a in cards],
                        file_roots=raw.get('file_roots', []), file_roots_version=raw.get('file_roots_version', 0),
                        file_scopes=public(file_scopes), file_actions=[summary(a) for a in active_files+terminal_files],
                        events=self.store.list_events(project_id, task_id), monitor=monitor,
                        backend_mode=self.backend_mode())

    def execute(self, project_id, task_id, name, args):
        if not isinstance(name, str) or not isinstance(args, dict):
            raise ToolboxError('INVALID_TOOL_REQUEST', '工具需要name字符串和args对象')
        name = name.strip().lower()
        if name in {'remote_file_plan', 'remote_inspect'}:
            from .file_actions import public, file_error
            try:
                value = (self.files.plan(project_id, task_id, args) if name == 'remote_file_plan'
                         else self.files.inspect(project_id, task_id, args))
                return envelope(ok=True, error=None, data=public(value), pending=public(value) if name == 'remote_file_plan' else None)
            except Exception as exc:
                raise file_error(exc) from exc
        with consent.task_lock(project_id, task_id):
            if self.files and name in {'plan', 'select_jobs', 'retry_job'}:
                self.files.assert_mutable(project_id,task_id,unresolved=True)
            elif self.files and name not in {'get_state','hpc_list','hpc_read','diagnose_job','ws_list','ws_read','report'}:
                self.files.assert_mutable(project_id,task_id)
            executor = self.executor(project_id, task_id)
            name = name.strip().lower()
            pending = None
            error = None
            try:
                if name == 'report':
                    raw = self.require_task(project_id, task_id).get('flow') or {}
                    if not raw:
                        raise ToolboxError('REPORT_NOT_READY', '任务尚无执行记录')
                    result = self.orchestrator().finalize_report(self.store, project_id, task_id, raw)
                    raw['report'] = result
                    self.store.update_task(project_id, task_id, flow=raw)
                else:
                    method = executor._LLM_TOOL_METHODS.get(name)
                    if not method or name in executor._DISABLED_LLM_TOOLS:
                        raise ToolboxError('AI_TOOL_NOT_ALLOWED', f'未允许的工具：{name}')
                    result = getattr(executor, method)(args)
                    if isinstance(result, ToolFailure):
                        error = {'code': result.code, 'message': str(result), 'retryable': False}
                    raw = self.require_task(project_id, task_id).get('flow') or {}
                    if name in {'precheck', 'draft', 'submit'} and not error:
                        from .computation import select_job
                        selected = select_job(raw, args)
                        if not (selected.get('precheck') or {}).get('ok'):
                            error = {'code': 'PRECHECK_BLOCKED', 'message': str(result), 'retryable': False}
                        elif name in {'draft', 'submit'} and selected.get('draft'):
                            if raw.get('execution_mode') == 'None':
                                error = {'code': 'HPC_BACKEND_UNAVAILABLE', 'message': '未配置HPC执行后端；未提交', 'retryable': False}
                            else:
                                pending = consent.spawn_submit_card(self.store, project_id, task_id,
                                    selected['key'], selected['attempt_id'])
            except consent.PendingConsentError as exc:
                pending = consent.get_card(self.store, project_id, task_id, exc.card_id)
                result = '请人工确认当前绑定操作；确认前未执行。'
            except ToolboxError as exc:
                if exc.code not in {"JOB_REQUIRED", "JOB_NOT_FOUND", "ATTEMPT_STALE", "JOB_NOT_READY", "SUBMISSION_UNKNOWN", "DEPENDENCY_NOT_READY", "PRECHECK_BLOCKED"}:
                    raise
                error = exc.payload()
                result = str(exc)
            except Exception as exc:
                # Do not expose credentials/upstream exception bodies.
                raise ToolboxError('TOOL_EXECUTION_FAILED', f'工具执行失败：{type(exc).__name__}', 400) from exc
            self.store.append_event(project_id, task_id, 'tool.' + name, str(result))
            return envelope(task_id=task_id, ok=error is None, error=error,
                            result=str(result), pending=pending,
                            flow=self.detail(project_id, task_id)['flow'])

    def resolve(self, project_id, task_id, card_id, approved, note='', scope_confirmation=None):
        if not isinstance(approved, bool):
            raise ToolboxError('INVALID_CONSENT', 'approved必须是布尔值')
        with consent.task_lock(project_id, task_id):
            self.require_task(project_id, task_id)
            card = consent.get_card(self.store, project_id, task_id, card_id)
            if card is None:
                raise ToolboxError('CARD_NOT_FOUND', '授权卡不存在', 404)
            if card.get('kind') == 'remote_file':
                from .file_actions import public, file_error
                try:
                    result = self.files.approve(project_id,task_id,card_id,approved,note,scope_confirmation)
                    return envelope(ok=True,error=None,data=public(result),card=public(result),replayed=card['state']!='pending')
                except Exception as exc:
                    raise file_error(exc) from exc
            if self.files:
                self.files.assert_mutable(project_id,task_id)
            replayed = card['state'] != 'pending'
            if card['state'] == 'pending':
                if card.get('kind') == 'submit':
                    from .submission import perform_submit
                    try:
                        perform_submit(self.store, project_id, task_id, card_id, approved,
                                       note, orch=self.orchestrator())
                    except Exception:
                        pass  # The existing submit transaction persists unknown before returning.
                else:
                    consent.resolve_card(self.store, project_id, task_id, card_id,
                                         approved=approved, note=note)
                    if approved:
                        self.executor(project_id, task_id).execute_action(card_id)
            card = consent.get_card(self.store, project_id, task_id, card_id)
            ok = card['state'] in {'executed', 'rejected'}
            result = card.get('result') or ('已拒绝；未执行' if card['state'] == 'rejected' else card['state'])
            error = None if ok else {'code': 'CONSENT_' + card['state'].upper(),
                                    'message': result, 'retryable': False}
            self.store.append_event(project_id, task_id, 'consent.' + card['state'], result)
            return envelope(task_id=task_id, ok=ok, error=error, card=card, replayed=replayed,
                            result=result, flow=self.detail(project_id, task_id)['flow'])
