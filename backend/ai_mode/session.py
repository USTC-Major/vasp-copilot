"""会话与记忆存储（M2 唯一真源）。

- 每会话一个 JSON 文件：~/.vasp-ai/sessions/<session_id>.json。
- 启动列会话（供续接选择），按修改时间倒序，字段含需求快照/规划/作业/起止点/闲聊。
- 同一项目内、不同计算任务 = 不同 session_id，上下文彼此独立。
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from . import paths
from .schemas import Session, _new_id

logger = logging.getLogger("ai_mode.sessions")


class SessionStoreError(Exception):
    """会话存储底座错误。"""


class SessionNotFoundError(SessionStoreError, KeyError):
    """会话不存在。"""


class CorruptSessionError(SessionStoreError):
    """会话文件损坏或 schema 不兼容。"""


class SessionStore:
    """按会话 ID 管理 JSON 文件；单进程内核内使用（无常驻、无服务死锁）。"""

    def __init__(self, root: Optional[Path] = None):
        if root is None:
            root = paths.sessions_dir()
        self.root = Path(root).expanduser()
        self.root.mkdir(parents=True, exist_ok=True)

    # ---- 路径 ----
    def _path(self, session_id: str) -> Path:
        return self.root / f"{session_id}.json"

    # ---- CRUD ----
    def exists(self, session_id: str) -> bool:
        return self._path(session_id).is_file()

    def create(self, *, project_id: str = "", title: str = "",
               calc_dir: str = "", local_workspace: str = "",
               start_step: str = "understand", end_step: str = "report",
               session_id: Optional[str] = None) -> Session:
        session = Session(
            session_id=session_id or _new_id("sess"),
            project_id=project_id,
            title=title,
            calc_dir=calc_dir,
            local_workspace=local_workspace,
            start_step=start_step,
            end_step=end_step,
            current_step=start_step,
            duration="full" if (start_step, end_step) == ("understand", "report")
            else "segment",
        )
        self.save(session)
        return session

    def save(self, session: Session) -> None:
        """原子写（临时文件 + rename），避免半截文件。"""
        session.touch()
        payload = session.model_dump_json()
        tmp_path = None
        try:
            fd, tmp_name = tempfile.mkstemp(dir=str(self.root),
                                            suffix=".tmp", prefix=".w_")
            os.close(fd)
            tmp_path = Path(tmp_name)
            tmp_path.write_text(payload, encoding="utf-8")
            tmp_path.replace(self._path(session.session_id))
        finally:
            if tmp_path is not None and tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

    def load(self, session_id: str) -> Session:
        path = self._path(session_id)
        if not path.is_file():
            raise SessionNotFoundError(session_id)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return Session.model_validate(data)
        except (json.JSONDecodeError, ValidationError, OSError) as exc:
            raise CorruptSessionError(session_id) from exc

    def delete(self, session_id: str) -> None:
        self._path(session_id).unlink(missing_ok=True)

    # ---- 生命周期 ----
    def list_sessions(self, order: str = "updated") -> list[Session]:
        """列出全部会话（可排序）。order: updated|created。"""
        sessions: list[Session] = []
        for path in self.root.glob("*.json"):
            try:
                sessions.append(self.load(path.stem))
            except SessionStoreError:
                logger.warning("跳过损坏会话文件: %s", path.name)
        reverse = order == "updated"
        sessions.sort(key=lambda s: getattr(s, "updated_at" if reverse
                                            else "created_at", ""), reverse=reverse)
        return sessions

    def summaries(self) -> list[dict]:
        """启动列会话：简洁摘要字段（供 UI 续接列表）。"""
        out = []
        for path in sorted(self.root.glob("*.json"),
                           key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                s = self.load(path.stem)
            except SessionStoreError:
                continue
            snap = s.snapshot if s.snapshot is not None else None
            out.append({
                "session_id": s.session_id,
                "project_id": s.project_id,
                "title": s.title or path.stem,
                "current_step": s.current_step,
                "updated_at": s.updated_at,
                "summary": snap.last_summary if snap else None,
                "occupancy": snap.occupancy if snap else 0.0,
            })
        return out