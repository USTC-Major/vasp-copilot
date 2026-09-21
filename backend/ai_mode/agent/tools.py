"""AI tool adapter; execution only happens across the Toolbox HTTP contract."""
from backend.toolbox.client import ToolboxClient
from backend.toolbox.contracts import ToolboxError
from .tool_schema import tool_schema_text

_CONSENT_PENDING = '__CONSENT_PENDING__'

class ToolExecutor:
    def __init__(self, *, store, project_id, task_id, client=None, cfg=None,
                 orch=None, orch_factory=None, should_stop=None):
        self.store, self.project_id, self.task_id = store, project_id, task_id
        self.client = client or getattr(store, 'client', None) or ToolboxClient()
        self.should_stop = should_stop

    def handle(self, name, args):
        if self.should_stop and self.should_stop():
            return '已停止生成；未发起新的工具请求'
        try:
            result = self.client.tool(self.project_id, self.task_id, name, args)
        except ToolboxError as exc:
            return f'[{exc.code}] {exc}'
        pending = result.get('pending')
        if pending:
            return _CONSENT_PENDING + pending['card_id']
        if not result.get('ok', True):
            error = result.get('error') or {}
            return f"[{error.get('code', 'TOOL_FAILED')}] {error.get('message') or result.get('result', '')}"
        return result.get('result', '')

    def execute_action(self, action_id):
        result = self.client.request('GET', self.client.task_path(self.project_id, self.task_id) + '/consents/' + action_id)
        card = result['card']
        return card.get('result') or card['state']

    def phase(self):
        return (self.store.get_task(self.project_id, self.task_id).get('flow') or {}).get('phase', '')

    def auto_pump(self):
        detail = self.client.request('GET', self.client.task_path(self.project_id, self.task_id) + '/detail')
        import json
        return json.dumps({'flow': detail['flow'], 'monitor': detail['monitor']}, ensure_ascii=False)

    def hpc_snapshot(self):
        return self.handle('hpc_list', {'path': ''})
