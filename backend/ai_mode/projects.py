"""AI view: HTTP authoritative tasks plus independent local chat records."""
from pathlib import Path
import threading
from . import paths
from .chat_store import ChatStore
from backend.toolbox.client import ToolboxClient
from backend.toolbox.contracts import ToolboxError

class ProjectStore:
    def __init__(self, root=None, *, client=None, chat_root=None):
        self.root = Path(chat_root or root or paths.home_dir())
        self.client = client or ToolboxClient()
        self.chat = ChatStore(self.root)

    def close(self):
        self.chat.close()
        self.client.close()

    def list_projects(self):
        return self.client.request('GET', '/projects')['projects']

    def get_project(self, project_id):
        return next((p for p in self.list_projects() if p['id'] == project_id), None)

    def create_project(self, name, description=''):
        return self.client.request('POST', '/projects', json={'name': name, 'description': description})['project']

    def delete_project(self, project_id):
        try:
            return self.client.request('DELETE', '/projects/' + project_id)['deleted']
        except ToolboxError as exc:
            if exc.status == 404:
                return False
            raise

    def list_tasks(self, project_id):
        tasks = self.client.request('GET', '/projects/' + project_id + '/tasks')['tasks']
        for task in tasks:
            messages = self.list_messages(project_id, task['id'])
            if messages:
                task['last_message'] = str(messages[-1].get('content') or '')[:80]
            task['context_ratio'] = self.task_context(project_id, task['id'])['ratio']
        return tasks

    def get_task(self, project_id, task_id):
        try:
            detail = self.client.request('GET', self.client.task_path(project_id, task_id) + '/detail')
        except ToolboxError as exc:
            if exc.status == 404:
                return None
            raise
        flow = detail['flow']
        flow['plan'] = {'strategy': flow.get('strategy', ''), 'jobs': flow.get('jobs', [])}
        return {**detail['task'], 'flow': flow, **self.chat.metadata(project_id, task_id)}

    def create_task(self, project_id, title='', goal='', local_workspace='', hpc_workspace=''):
        return self.client.request('POST', '/projects/' + project_id + '/tasks', json={
            'title': title, 'goal': goal, 'local_workspace': local_workspace, 'hpc_workspace': hpc_workspace})['task']

    def update_task(self, project_id, task_id, **fields):
        chat_fields = {k: v for k, v in fields.items() if k == 'generation'}
        if chat_fields:
            self.chat.update(project_id, task_id, chat_fields)
        execution_fields = {k:v for k,v in fields.items() if k != 'generation'}
        if execution_fields:
            self.client.request('PATCH', self.client.task_path(project_id, task_id), json=execution_fields)
        return self.get_task(project_id, task_id)

    def delete_task(self, project_id, task_id):
        try:
            return self.client.request('DELETE', self.client.task_path(project_id, task_id))
        except ToolboxError as exc:
            if exc.status == 404:
                return None
            raise

    def generation_metadata(self, project_id, task_id):
        return self.chat.metadata(project_id, task_id).get('generation', {})

    def update_generation(self, project_id, task_id, generation):
        self.chat.update(project_id, task_id, {'generation': generation})

    def list_messages(self, project_id, task_id):
        return self.chat.messages(project_id, task_id)

    def append_message(self, project_id, task_id, role, content, thinking=''):
        return self.chat.append(project_id, task_id, role, content, thinking)

    def task_context(self, project_id, task_id):
        return self.chat.context(project_id, task_id)

    def context(self):
        return self.chat.context()

    def list_recent_history(self, limit=10):
        return self.client.request('GET', '/recent-history', params={'limit':limit})['items']

    def list_waiting(self):
        result = self.client.request('GET', '/jobs/waiting')
        return result['jobs'], result['total']

_store = None
_store_lock = threading.RLock()

def get_project_store():
    global _store
    with _store_lock:
        if _store is None:
            _store = ProjectStore()
        return _store

def close_project_store():
    global _store
    with _store_lock:
        if _store is not None:
            _store.close()
            _store = None
