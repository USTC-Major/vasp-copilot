"""Chat-only persistence; execution state belongs exclusively to Toolbox."""
import copy
import threading
from datetime import datetime, timezone
from pathlib import Path
from backend.toolbox.owner import ProcessOwner
from backend.toolbox.storage import atomic_json, read_object, import_legacy, validate_chat

class ChatStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.owner = ProcessOwner(self.root, 'chat').acquire()
        self._lock = threading.RLock()
        self._closed = False
        self.path = self.root / 'chat_store.json'
        try:
            self.data = read_object(self.path) if self.path.is_file() else import_legacy(self.root, 'chat')
            validate_chat(self.data)
            atomic_json(self.path, self.data)
        except BaseException:
            self.owner.close()
            raise

    def close(self):
        with self._lock:
            self._closed = True
            self.owner.close()

    def _ensure_open(self):
        if self._closed:
            raise RuntimeError('ChatStore is closed')

    def messages(self, project_id, task_id):
        with self._lock:
            return copy.deepcopy(self.data.get('messages', {}).get(f'{project_id}:{task_id}', []))

    def append(self, project_id, task_id, role, content, thinking=''):
        with self._lock:
            self._ensure_open()
            msg = {'role': role, 'content': content, 'at': datetime.now(timezone.utc).isoformat()}
            if thinking:
                msg['thinking'] = thinking
            self.data.setdefault('messages', {}).setdefault(f'{project_id}:{task_id}', []).append(msg)
            atomic_json(self.path, self.data)
            return dict(msg)

    def metadata(self, project_id, task_id):
        with self._lock:
            return copy.deepcopy(self.data['task_metadata'].get(f'{project_id}:{task_id}', {}))

    def update(self, project_id, task_id, fields):
        with self._lock:
            self._ensure_open()
            self.data['task_metadata'].setdefault(f'{project_id}:{task_id}', {}).update(copy.deepcopy(fields))
            atomic_json(self.path, self.data)

    def context(self, project_id=None, task_id=None):
        with self._lock:
            rows = self.messages(project_id, task_id) if task_id else [m for group in self.data.get('messages', {}).values() for m in group]
            used = min(65536, sum(max(1, round(len((str(m.get('content', '')) + str(m.get('thinking', ''))).encode('utf-8')) / 4)) for m in rows))
            return {'ratio': round(used / 65536, 4), 'used': used, 'capacity': 65536}
