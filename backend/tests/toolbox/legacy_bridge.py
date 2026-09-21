"""Tests-only bridge for old mixed core/agent fixtures; never used by production.

The fixture writes execution state directly for controlled fault setup, but all
agent client calls still exercise the new FastAPI router and execution service.
"""
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.toolbox.api import router, error_handler
from backend.toolbox.client import ToolboxClient
from backend.toolbox.config import ExecutionConfig
from backend.toolbox.contracts import ToolboxError
from backend.toolbox.projects import ProjectStore as ExecutionStore
from backend.toolbox.commands import ToolExecutor as CoreExecutor
from backend.toolbox.service import ExecutionService
from backend.ai_mode.chat_store import ChatStore
from backend.toolbox.storage import atomic_json, import_legacy, read_object
import threading

class ProjectStore(ExecutionStore):
    def __init__(self, root=None):
        super().__init__(root)
        # An explicitly ownerless chat fixture permits legacy reload assertions;
        # production ownership is independently verified by toolbox_review.
        self.chat = ChatStore.__new__(ChatStore)
        self.chat.root = self.root
        self.chat.path = self.root / 'chat_store.json'
        self.chat._lock = threading.RLock()
        self.chat._closed = False
        self.chat.data = read_object(self.chat.path) if self.chat.path.exists() else import_legacy(self.root, 'chat')
        self.chat.data.setdefault('task_metadata', {})
        atomic_json(self.chat.path, self.chat.data)
        self._service = ExecutionService(self.root, settings_loader=lambda: ExecutionConfig(data_dir=self.root), monitor_enabled=False)
        self._service.store = self
        app = FastAPI()
        app.state.toolbox = self._service
        app.include_router(router, prefix='/api/v1')
        app.add_exception_handler(ToolboxError, error_handler)
        self._testclient = TestClient(app)
        self.client = ToolboxClient(client=self._testclient)

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

class ToolExecutor(CoreExecutor):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if hasattr(self.store, '_service'):
            self.store._service.settings_loader = lambda: self.cfg
            self.store._service.orch_factory = self._ensure_orch

def make_executor(store, project_id, task_id, *, cfg, orch_factory, should_stop=None):
    return ToolExecutor(store=store, project_id=project_id, task_id=task_id,
                        cfg=cfg, orch_factory=orch_factory, should_stop=should_stop)
