from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path


class ChatHistoryStore:
    """单用户本地聊天记录持久化（data/chat_history.json，可选 TTL 过期）。"""

    MAX_MESSAGES = 200

    def __init__(self, path: Path, ttl_seconds: int = 7 * 24 * 3600) -> None:
        self._path = Path(path)
        self._ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._messages: list[dict] = []
        self._load()

    def _load(self) -> None:
        try:
            if not self._path.is_file():
                return
            data = json.loads(self._path.read_text(encoding='utf-8'))
            messages = data.get('messages')
            if not isinstance(messages, list):
                return
            now = datetime.now(timezone.utc)
            saved_at = data.get('saved_at')
            if self._ttl_seconds > 0 and isinstance(saved_at, str):
                try:
                    saved = datetime.fromisoformat(saved_at.replace('Z', '+00:00'))
                    if now - saved > timedelta(seconds=self._ttl_seconds):
                        return  # 过期历史不加载
                except ValueError:
                    pass
            self._messages = self._clean(messages)[-self.MAX_MESSAGES:]
        except Exception:  # noqa: BLE001 - 损坏/不可读不阻塞
            self._messages = []

    @staticmethod
    def _clean(messages) -> list:
        out = []
        for m in messages:
            if not isinstance(m, dict):
                continue
            role = m.get('role')
            content = m.get('content')
            if role in ('user', 'assistant') and isinstance(content, str) and content.strip():
                out.append({'role': role, 'content': content})
        return out

    def get(self) -> list[dict]:
        with self._lock:
            return list(self._messages)

    def save(self, messages: list[dict]) -> list[dict]:
        with self._lock:
            self._messages = self._clean(messages)[-self.MAX_MESSAGES:]
            self._persist()
            return list(self._messages)

    def clear(self) -> None:
        with self._lock:
            self._messages = []
        try:
            self._path.unlink(missing_ok=True)
        except OSError:
            pass

    def _persist(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                'saved_at': datetime.now(timezone.utc).isoformat(),
                'messages': self._messages,
            }
            self._path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + '\n',
                encoding='utf-8')
        except OSError:
            pass