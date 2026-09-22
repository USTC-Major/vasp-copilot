"""Atomic, non-destructive JSON persistence and read-only legacy migration."""
import copy
import hashlib
import json
import os
import tempfile
from pathlib import Path

def atomic_json(path: Path, value):
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.toolbox_', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)

def read_object(path: Path):
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError(f'Invalid store structure: {path.name}; file preserved')
    return data

def _records(value, field):
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise ValueError(f'Invalid store {field}: expected a list of objects; original preserved')


def _mapping(value, field):
    if not isinstance(value, dict):
        raise ValueError(f'Invalid store {field}: expected an object; original preserved')


def validate_chat(data):
    """Check only the containers used by chat reads/writes; retain unknown fields."""
    if data.get('schema_version') != 1:
        raise ValueError('Invalid chat schema_version; original preserved')
    _mapping(data.get('messages'), 'messages')
    for key, rows in data['messages'].items():
        _records(rows, 'messages.' + key)
    _mapping(data.get('task_metadata'), 'task_metadata')
    for metadata in data['task_metadata'].values():
        _mapping(metadata, 'task_metadata entry')
        if 'generation' in metadata:
            _mapping(metadata['generation'], 'generation')


def validate_execution(data):
    """Validate runtime containers before recovery or any persisted migration."""
    if data.get('schema_version') != 1:
        raise ValueError('Invalid execution schema_version; original preserved')
    for field in ('projects', 'tasks', 'waiting', 'events'):
        _records(data.get(field), field)
    for task in data['tasks']:
        flow = task.get('flow')
        if flow is None:
            continue
        _mapping(flow, 'flow')
        for field in ('plan', 'consent', 'monitor'):
            if field in flow:
                _mapping(flow[field], 'flow.' + field)
        plan = flow.get('plan', {})
        if 'jobs' in plan:
            _records(plan['jobs'], 'plan.jobs')
        actions = flow.get('consent', {}).get('actions', {})
        _mapping(actions, 'consent.actions')
        for action in actions.values():
            _mapping(action, 'consent action')


def import_legacy(root: Path, kind: str):
    source = root / 'ai_store.json'
    legacy = read_object(source) if source.is_file() else {}
    for key, expected in [('projects', list), ('tasks', list), ('messages', dict)]:
        if key in legacy and not isinstance(legacy[key], expected):
            raise ValueError(f'Invalid legacy {key}; original preserved')
    # Validate legacy containers before either destination is created.
    validate_execution({**legacy, 'schema_version': 1, 'projects': legacy.get('projects', []),
                        'tasks': legacy.get('tasks', []), 'waiting': legacy.get('waiting', []),
                        'events': legacy.get('events', [])})
    validate_chat({'schema_version': 1, 'messages': legacy.get('messages', {}),
                   'task_metadata': legacy.get('task_metadata', {})})
    for task in legacy.get('tasks', []):
        if 'generation' in task:
            _mapping(task['generation'], 'legacy task generation')
    metadata = {'source': 'ai_store.json', 'sha256': hashlib.sha256(source.read_bytes()).hexdigest()} if source.is_file() else None
    if kind == 'chat':
        result = {'schema_version': 1, 'messages': copy.deepcopy(legacy.get('messages', {})), 'context': copy.deepcopy(legacy.get('context', {})), 'task_metadata': {str(t.get('project_id')) + ':' + str(t.get('id')): {'generation': copy.deepcopy(t['generation'])} for t in legacy.get('tasks', []) if 'generation' in t}, 'migration': metadata}
        validate_chat(result)
        return result
    data = {key: copy.deepcopy(value) for key, value in legacy.items() if key not in {'messages', 'context'}}
    data.update(schema_version=1, events=[], migration=metadata)
    data.setdefault('projects', [])
    data.setdefault('tasks', [])
    data.setdefault('waiting', [])
    for task in data['tasks']:
        task.pop('generation', None)
    validate_execution(data)
    recover_actions(data, importing=True)
    return data

def recover_actions(data, *, importing=False):
    for task in data.get('tasks', []):
        flow = task.get('flow') or {}
        for action in (flow.get('consent') or {}).get('actions', {}).values():
            if action.get('kind') == 'hpc_upload' and not (action.get('binding') or {}).get('file_identity'):
                if action.get('state') in {'pending', 'approved'}:
                    action.update(state='expired', result='旧上传缺少可信主机与目标身份；请重新提案')
                elif action.get('state') in {'executing', 'unknown'}:
                    action['legacy_file_unknown'] = True
            if action.get('kind') == 'remote_file' and action.get('state') == 'approved':
                action.update(state='expired', result='服务中断：未恢复文件执行队列，请重新提案')
            if action.get('state') == 'executing':
                action.update(state='unknown', result='服务中断：操作结果待核实，禁止自动重放')
                if action.get('kind') == 'remote_file':
                    receipt=action.setdefault('receipt', {})
                    receipt['phase'] = 'interrupted'
                    manifest=(action.get('binding') or {}).get('manifest') or {}
                    done={r.get('item_id') for r in receipt.get('items',[]) if r.get('state')=='committed'}
                    released={key for key,value in receipt.get('item_outcomes',{}).items() if value.get('state')=='not_executed'} - done
                    items=manifest.get('items',[])
                    receipt['spent']={'operations':sum(i['item_id'] in done for i in items),'bytes':sum(i['bytes'] for i in items if i['item_id'] in done)}
                    receipt['held_unknown']={'operations':sum(i['item_id'] not in done|released for i in items),'bytes':sum(i['bytes'] for i in items if i['item_id'] not in done|released)}
                    receipt['released']={'operations':sum(i['item_id'] in released for i in items),'bytes':sum(i['bytes'] for i in items if i['item_id'] in released)}
                    receipt.setdefault('reservation',{})['state']='settled'
            elif importing and action.get('state') in {'pending', 'approved'}:
                action.update(state='expired', result='旧版授权已迁移为审计记录，请重新确认')
            if action.get('kind') == 'submit' and action.get('state') == 'unknown':
                # The action claim is saved before job dispatch. A crash in
                # that gap must not leave a fresh-looking attempt behind.
                # Also repair stores recovered by an earlier owner version.
                binding = action.get('binding') or {}
                if not binding.get('job_key') or not binding.get('attempt_id'):
                    continue
                scope = (flow.get('consent') or {}).get('computation_scopes', {}).get(binding.get('scope_id'))
                if scope and scope.get('attempt_id') == binding['attempt_id']:
                    scope['submit_limit'] = 0
                for job in (flow.get('plan') or {}).get('jobs', []):
                    if (job.get('key') != binding['job_key']
                            or job.get('attempt_id') != binding['attempt_id']):
                        continue
                    # A receipt already saved on the job remains authoritative;
                    # never replace a known job id or terminal evidence.
                    if (not job.get('slurm_id')
                            and job.get('submission_state') != 'submitted'
                            and (job.get('status', 'draft') in {'draft', 'waiting', 'unknown'}
                                 or job.get('submission_state') == 'executing')):
                        job.update(status='unknown', submission_state='unknown',
                                   submission_action_id=action.get('action_id'),
                                   submission_error='服务在提交确认后中断；结果待核实，禁止同一尝试再次提交')
                        flow['phase'] = 'blocked'
        if flow.get('phase') == 'monitoring':
            flow.setdefault('monitor', {}).update(state='recovering')
