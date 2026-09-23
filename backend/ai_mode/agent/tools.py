"""AI tool adapter; execution only happens across the Toolbox HTTP contract."""
import json
import re
from urllib.parse import quote

from backend.toolbox.client import ToolboxClient
from backend.toolbox.contracts import ToolboxError
from .tool_schema import tool_schema_text

_CONSENT_PENDING = '__CONSENT_PENDING__'
_LEGACY_TOOLS = frozenset({
    'get_state', 'ws_list', 'ws_read', 'mp_search', 'mp_import_poscar',
    'hpc_list', 'hpc_read', 'hpc_upload', 'stop_monitor', 'plan',
    'copy_inputs', 'propose_incar', 'generate_kpoints', 'precheck',
    'draft', 'submit', 'select_jobs', 'diagnose_job', 'retry_job',
})
_FILE_TOOLS = frozenset({'remote_inspect', 'remote_file_plan'})
_FIXED_TOOLS = frozenset({
    'remote_file_context', 'remote_file_status', 'remote_file_history',
    'remote_file_reconcile',
})
_ID = re.compile(r'[0-9a-f]{32}\Z')
_SECRET_KEYS = frozenset({
    'prepare_token', 'dispatch_nonce', 'nonce', 'password', 'secret',
    'api_key', 'private_key', 'ssh_config', 'credentials',
})


def _exact(args, allowed, required=()):
    return (isinstance(args, dict) and set(args) <= set(allowed)
            and set(required) <= set(args))


def _id(value):
    return isinstance(value, str) and bool(_ID.fullmatch(value))


def _safe(value, depth=0):
    """Bound untrusted Toolbox data before placing it in model context."""
    if depth > 5:
        return '…'
    if isinstance(value, dict):
        result = {str(k)[:80]: _safe(v, depth + 1)
                for k, v in list(value.items())[:60]
                if str(k).lower() not in _SECRET_KEYS}
        if len(value) > 60:
            result['_truncated_keys'] = len(value) - 60
        return result
    if isinstance(value, list):
        result = [_safe(v, depth + 1) for v in value[:120]]
        if len(value) > 120:
            result.append({'truncated': True, 'total_items': len(value)})
        return result
    if isinstance(value, str):
        return value[:3000] + ('…' if len(value) > 3000 else '')
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:200]


def _render(value):
    raw = json.dumps(_safe(value), ensure_ascii=False, default=str)
    if len(raw) > 12000:
        return json.dumps({
            'truncated': True, 'total_characters': len(raw), 'preview': raw[:10000],
            'hint': '结果过长；请缩小 limit 或按 cursor 分页重新查询，不要据此推断遗漏项状态',
        }, ensure_ascii=False)
    return raw


def _project_action(card):
    """Planner and SSE see only status and identity, never a file manifest."""
    if not isinstance(card, dict):
        return {}
    binding = card.get('binding') or {}
    receipt = card.get('receipt') or {}
    items = receipt.get('items') or []
    outcome = receipt.get('item_outcomes') or {}
    item_rows = []
    for item in items[:32]:
        if not isinstance(item, dict):
            continue
        row = {k: item[k] for k in ('item_id', 'state', 'bytes_processed', 'verification_level') if k in item}
        target = item.get('target_evidence')
        if isinstance(target, dict):
            row['target'] = {k: target[k] for k in ('canonical_path', 'size') if k in target}
        item_rows.append(row)
    return {k: card[k] for k in ('action_id', 'card_id', 'kind', 'state', 'summary', 'risk') if k in card} | {
        'binding': {k: binding[k] for k in ('job_key', 'attempt_id', 'scope_id', 'scope_version', 'manifest_digest') if k in binding},
        'receipt': {k: receipt[k] for k in ('phase', 'spent', 'held_unknown', 'released', 'leftovers', 'error') if k in receipt}
        | {'items': item_rows,
           'item_outcomes': {str(k): {field: v[field] for field in ('state', 'evidence') if field in v}
                             for k, v in list(outcome.items())[:32] if isinstance(v, dict)}},
    }


def _project_detail(detail):
    flow = detail.get('flow') or {}
    jobs = []
    for job in flow.get('jobs', [])[:30]:
        if isinstance(job, dict):
            jobs.append({k: job[k] for k in ('key', 'job_key', 'label', 'attempt_id', 'current_attempt_id', 'status') if k in job})
    roots = []
    for root in detail.get('file_roots', [])[:30]:
        if isinstance(root, dict):
            roots.append({k: root[k] for k in ('root_id', 'version', 'requested_path', 'canonical_path', 'path') if k in root})
    scopes = []
    for scope in detail.get('file_scopes', [])[:30]:
        if isinstance(scope, dict):
            row = {k: scope[k] for k in ('scope_id', 'version', 'state', 'job_key', 'attempt_id', 'allowed_operations', 'expires_at', 'max_operations', 'max_total_bytes') if k in scope}
            row['root_bindings'] = [
                {k: binding[k] for k in ('root_id', 'version', 'destination_prefixes') if k in binding}
                for binding in (scope.get('root_bindings') or [])[:8] if isinstance(binding, dict)]
            sources = scope.get('source_bindings') or []
            row['source_paths'] = [s.get('requested_path') for s in sources[:32] if isinstance(s, dict)]
            scopes.append(row)
    return {'jobs': jobs, 'file_roots_version': detail.get('file_roots_version'),
            'file_roots': roots, 'file_scopes': scopes,
            'file_actions': [_project_action(a) for a in detail.get('file_actions', [])[:50]],
            'total_counts': {k: len(v) for k, v in (
                ('jobs', flow.get('jobs', [])), ('file_roots', detail.get('file_roots', [])),
                ('file_scopes', detail.get('file_scopes', [])), ('file_actions', detail.get('file_actions', [])))},
            'hint': '若总数大于显示数，请在同任务 Toolbox 核对完整状态'}


def _valid_file_args(name, args):
    if name == 'remote_file_context':
        return _exact(args, ())
    if name in {'remote_file_status', 'remote_file_reconcile'}:
        return _exact(args, ('action_id',), ('action_id',)) and _id(args['action_id'])
    if name == 'remote_file_history':
        return (_exact(args, ('limit', 'cursor'))
                and ('limit' not in args or type(args['limit']) is int and 1 <= args['limit'] <= 100)
                and ('cursor' not in args or isinstance(args['cursor'], str)
                     and args['cursor'].isdigit() and len(args['cursor']) <= 12))
    if name == 'remote_inspect':
        return (_exact(args, ('path', 'view', 'limit', 'cursor'), ('path', 'view'))
                and isinstance(args['path'], str) and args['path'].startswith('/')
                and len(args['path']) <= 4096 and isinstance(args['view'], str)
                and args['view'] in {'list', 'stat', 'text'}
                and ('limit' not in args or type(args['limit']) is int and 1 <= args['limit'] <= 500)
                and ('cursor' not in args or isinstance(args['cursor'], str) and len(args['cursor']) <= 512))
    if name == 'remote_file_plan':
        if not (_exact(args, ('scope_id', 'scope_version', 'job_key', 'attempt_id', 'idempotency_key', 'items'),
                       ('scope_id', 'scope_version', 'job_key', 'attempt_id', 'idempotency_key', 'items'))
                and _id(args['scope_id']) and type(args['scope_version']) is int and args['scope_version'] > 0
                and all(isinstance(args[k], str) and 0 < len(args[k]) <= 128
                        for k in ('job_key', 'attempt_id', 'idempotency_key'))
                and isinstance(args['items'], list) and 1 <= len(args['items']) <= 32):
            return False
        for item in args['items']:
            if not _exact(item, ('item_id', 'op', 'source', 'destination', 'text', 'encoding', 'on_conflict'),
                          ('item_id', 'op', 'destination', 'on_conflict')):
                return False
            if not isinstance(item['op'], str) or item['op'] not in {'copy', 'symlink', 'write_text', 'mkdir'} or item['on_conflict'] != 'fail':
                return False
            if not isinstance(item['item_id'], str) or not item['item_id'] or len(item['item_id']) > 128:
                return False
            if item.get('encoding', 'utf-8') != 'utf-8':
                return False
            dest = item['destination']
            if not (_exact(dest, ('root_id', 'relative_path'), ('root_id', 'relative_path'))
                    and _id(dest['root_id']) and isinstance(dest['relative_path'], str)):
                return False
            if item['op'] in {'copy', 'symlink'}:
                if not (_exact(item.get('source'), ('absolute_path',), ('absolute_path',))
                        and isinstance(item['source']['absolute_path'], str)
                        and item['source']['absolute_path'].startswith('/') and 'text' not in item):
                    return False
            elif item['op'] == 'write_text':
                if 'source' in item or not isinstance(item.get('text'), str):
                    return False
            elif 'source' in item or 'text' in item:
                return False
        return True
    return False


class ToolExecutor:
    def __init__(self, *, store, project_id, task_id, client=None, cfg=None,
                 orch=None, orch_factory=None, should_stop=None):
        self.store, self.project_id, self.task_id = store, project_id, task_id
        self.client = client or getattr(store, 'client', None) or ToolboxClient()
        self.should_stop = should_stop

    def handle(self, name, args):
        if self.should_stop and self.should_stop():
            return '已停止生成；未发起新的工具请求'
        if not isinstance(name, str) or name not in _LEGACY_TOOLS | _FILE_TOOLS | _FIXED_TOOLS:
            return '[TOOL_FORBIDDEN] 未授权的工具名称'
        if not isinstance(args, dict):
            return '[INVALID_TOOL_ARGS] 工具参数必须是对象'
        if name in _FILE_TOOLS | _FIXED_TOOLS:
            try:
                valid = _valid_file_args(name, args)
            except (TypeError, ValueError, KeyError):
                valid = False
            if not valid:
                return '[INVALID_TOOL_ARGS] 文件工具参数不符合固定接口'
        try:
            if name in _FIXED_TOOLS:
                return self._fixed(name, args)
            result = self.client.tool(self.project_id, self.task_id, name, args)
        except ToolboxError as exc:
            return f'[{exc.code}] {exc}'
        if not isinstance(result, dict):
            return '[TOOLBOX_BAD_RESPONSE] 工具响应不是对象'
        if result.get('ok') is False:
            error = result.get('error') or {}
            return f"[{error.get('code', 'TOOL_FAILED')}] {error.get('message') or result.get('result', '')}"
        if name in _FILE_TOOLS and result.get('ok') is not True:
            return '[TOOLBOX_BAD_RESPONSE] 文件工具缺少明确成功标记'
        pending = result.get('pending')
        if name == 'remote_file_plan':
            if not isinstance(pending, dict) or not _id(pending.get('card_id')):
                return '[TOOLBOX_BAD_RESPONSE] 文件计划未返回有效确认卡；请查询状态'
            state = pending.get('state')
            if state == 'pending':
                return _CONSENT_PENDING + pending['card_id']
            if state in {'approved', 'executing', 'executed', 'rejected', 'expired', 'failed', 'unknown'}:
                return _render(_project_action(pending))
            return '[TOOLBOX_BAD_RESPONSE] 文件计划返回未知卡状态；请查询状态'
        if pending:
            return _CONSENT_PENDING + pending['card_id']
        if name == 'remote_inspect':
            if not isinstance(result.get('data'), dict):
                return '[TOOLBOX_BAD_RESPONSE] 远端查看缺少 data'
            return _render(result['data'])
        return result.get('result', '')

    def _fixed(self, name, args):
        path = self.client.task_path(self.project_id, self.task_id)
        if name == 'remote_file_context':
            value = self.client.request('GET', path + '/detail')
            if not isinstance(value, dict) or 'flow' not in value:
                return '[TOOLBOX_BAD_RESPONSE] 任务详情缺少 flow'
            return _render(_project_detail(value))
        action_id = args.get('action_id')
        if name == 'remote_file_status':
            value = self.client.request('GET', path + '/consents/' + quote(action_id, safe=''))
            card = value.get('card') if isinstance(value, dict) else None
            if not isinstance(card, dict) or card.get('kind') != 'remote_file':
                return '[TOOLBOX_BAD_RESPONSE] 查询结果不是文件动作卡'
            return _render(_project_action(card))
        if name == 'remote_file_history':
            value = self.client.request('GET', path + '/file-actions', params=args)
            data = value.get('data') if isinstance(value, dict) else None
            if not isinstance(data, dict) or not isinstance(data.get('active'), list) or not isinstance(data.get('actions'), list):
                return '[TOOLBOX_BAD_RESPONSE] 文件历史缺少有效 data'
            return _render({'active': [_project_action(a) for a in data['active'][:120]],
                            'actions': [_project_action(a) for a in data['actions'][:100]],
                            'total_active': len(data['active']), 'total_actions_in_page': len(data['actions']),
                            'active_truncated': len(data['active']) > 120,
                            'next_cursor': data.get('next_cursor'),
                            'hint': '若结果截断，请缩小 limit 或在 Toolbox 核对完整列表'})
        value = self.client.request('POST', path + '/file-actions/' + quote(action_id, safe='') + '/reconcile', json={})
        if not isinstance(value, dict) or value.get('ok') is not True or not isinstance(value.get('data'), dict):
            return '[TOOLBOX_BAD_RESPONSE] 核对未返回有效 data；请查询状态'
        return _render(_project_action(value['data']))

    def execute_action(self, action_id):
        if not _id(action_id):
            return '[INVALID_TOOL_ARGS] 无效确认卡 ID'
        result = self.client.request('GET', self.client.task_path(self.project_id, self.task_id) + '/consents/' + quote(action_id, safe=''))
        card = result['card']
        return card.get('result') or card['state']

    def phase(self):
        return (self.store.get_task(self.project_id, self.task_id).get('flow') or {}).get('phase', '')

    def auto_pump(self):
        detail = self.client.request('GET', self.client.task_path(self.project_id, self.task_id) + '/detail')
        return json.dumps({'flow': detail['flow'], 'monitor': detail['monitor']}, ensure_ascii=False)

    def hpc_snapshot(self):
        return self.handle('hpc_list', {'path': ''})
