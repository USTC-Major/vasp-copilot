"""Authoritative project and task execution store, written only by the Toolbox owner."""

from __future__ import annotations

import copy
import json
import logging
import os
import secrets
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from . import paths

logger = logging.getLogger("toolbox.projects")

DATA_FILE = "execution_store.json"
CONTEXT_CAPACITY = 65536


def _now_iso(offset_ms: int = 0) -> str:
    base = datetime.now(timezone.utc)
    return base.replace(microsecond=0).isoformat().replace("+00:00", "Z")

def _empty_data():
    return {"schema_version": 1, "projects": [], "tasks": [], "waiting": [], "events": []}


class ProjectStore:
    """Owner 内的 execution_store.json；聊天另库保存。"""

    def __init__(self, root: Optional[Path] = None):
        if root is None:
            root = paths.home_dir()
        self.root = Path(root).expanduser()
        self.root.mkdir(parents=True, exist_ok=True)
        self._path = self.root / DATA_FILE
        self._lock = threading.RLock()
        self._data = self._load()

    # ---- 落盘 ----
    def _load(self) -> dict:
        from .storage import read_object, import_legacy, recover_actions, validate_execution
        data = read_object(self._path) if self._path.is_file() else import_legacy(self.root, 'execution')
        validate_execution(data)
        recover_actions(data)
        self._flush_unlocked(data)
        return data

    def _flush_unlocked(self, data):
        from .storage import atomic_json
        atomic_json(self._path, data)

    def _commit(self) -> None:
        self._flush_unlocked(self._data)

    # ---- 项目 ----
    def list_projects(self) -> list[dict]:
        with self._lock:
            projects = [copy.deepcopy(p) for p in self._data.get("projects", [])]
        projects.sort(key=lambda p: p.get("updated_at") or p.get("created_at") or "",
                      reverse=True)
        return projects

    def list_recent_history(self, limit: int = 10) -> list[dict[str, Any]]:
        """Read-only, presentation-safe project/task history snapshot.

        This path intentionally does not decorate tasks, synchronize execution
        backends, or persist anything.  Workspace paths, goals, messages, and
        credentials are never included.
        """
        with self._lock:
            projects = [copy.deepcopy(p) for p in self._data.get("projects", [])]
            tasks = [copy.deepcopy(t) for t in self._data.get("tasks", [])]
        project_names = {
            str(project.get("id") or ""): str(project.get("name") or "未命名项目")[:80]
            for project in projects
        }
        rows: list[dict[str, Any]] = []
        projects_with_tasks = {
            str(task.get("project_id") or "") for task in tasks
        }
        for task in tasks:
            project_id = str(task.get("project_id") or "")
            task_id = str(task.get("id") or "")
            if not project_id or not task_id:
                continue
            flow = task.get("flow") if isinstance(task.get("flow"), dict) else {}
            execution = str(flow.get("execution_mode") or "None")
            if execution not in {"Fake", "Real", "None"}:
                execution = "None"
            rows.append({
                "id": f"{project_id}:{task_id}",
                "kind": "ai_task",
                "project_id": project_id,
                "task_id": task_id,
                "title": str(task.get("title") or "未命名任务")[:80],
                "project_name": project_names.get(project_id, "未命名项目"),
                "status": str(task.get("status") or "idle")[:40],
                "execution_mode": execution,
                "updated_at": str(task.get("updated_at") or ""),
            })
        for project in projects:
            project_id = str(project.get("id") or "")
            if not project_id or project_id in projects_with_tasks:
                continue
            rows.append({
                "id": project_id,
                "kind": "ai_project",
                "project_id": project_id,
                "title": str(project.get("name") or "未命名项目")[:80],
                "status": "project",
                "updated_at": str(project.get("updated_at")
                                  or project.get("created_at") or ""),
            })
        rows.sort(key=lambda row: str(row.get("updated_at") or ""), reverse=True)
        return rows[:max(0, limit)]

    def get_project(self, project_id: str) -> Optional[dict]:
        with self._lock:
            for p in self._data.get("projects", []):
                if p["id"] == project_id:
                    return copy.deepcopy(p)
        return None

    def create_project(self, name: str, description: str = "") -> dict:
        with self._lock:
            now = _now_iso()
            project = {
                "id": f"prj_{secrets.token_hex(3)}",
                "name": (name or "未命名项目").strip()[:80],
                "description": (description or "").strip()[:200] or None,
                "created_at": now,
                "updated_at": now,
                "job_count": 0,
                "context_ratio": 0.0,
            }
            self._data.setdefault("projects", []).append(project)
            self._commit()
            return dict(project)

    def delete_project(self, project_id: str) -> bool:
        with self._lock:
            projects = self._data.get("projects", [])
            before = len(projects)
            self._data["projects"] = [p for p in projects
                                      if p["id"] != project_id]
            tasks = self._data.get("tasks", [])
            self._data["tasks"] = [t for t in tasks
                                   if t.get("project_id") != project_id]
            deleted = len(self._data["projects"]) < before
            if deleted:
                self._commit()
            return deleted

    # ---- 计算任务 ----
    def list_tasks(self, project_id: str) -> list[dict]:
        with self._lock:
            tasks = [copy.deepcopy(t) for t in self._data.get("tasks", [])
                     if t.get("project_id") == project_id]
        tasks.sort(key=lambda t: t.get("updated_at") or "", reverse=True)
        return tasks



    def get_task(self, project_id: str, task_id: str) -> Optional[dict]:
        with self._lock:
            for t in self._data.get("tasks", []):
                if t.get("project_id") == project_id and t.get("id") == task_id:
                    return copy.deepcopy(t)
        return None

    def create_task(self, project_id: str, title: str = "", goal: str = "",
                    local_workspace: str = "", hpc_workspace: str = "") -> dict:
        with self._lock:
            goal = (goal or "").strip()
            title = (title or "").strip() or goal[:60] or "新计算任务"
            now = _now_iso()
            task_id = f"tsk_{secrets.token_hex(3)}"
            task = {
                "id": task_id,
                "project_id": project_id,
                "title": title[:80],
                "goal": goal,
                "local_workspace": (local_workspace or "").strip() or None,
                "hpc_workspace": (hpc_workspace or "").strip() or None,
                "status": "idle",
                "updated_at": now,
            }
            self._data.setdefault("tasks", []).append(task)
            for p in self._data.get("projects", []):
                if p.get("id") == project_id:
                    count = sum(1 for t in self._data.get("tasks", [])
                                if t.get("project_id") == project_id)
                    p["job_count"] = count
                    p["updated_at"] = now
            self._commit()
            return dict(task)

    # ---- 对话消息 ----
    def monitoring_tasks(self) -> list[tuple[str, str]]:
        """M55：扫描所有处于 monitoring 阶段的任务（后台监控线程用）。"""
        with self._lock:
            out: list[tuple[str, str]] = []
            for t in self._data.get("tasks", []):
                flow = t.get("flow") or {}
                if isinstance(flow, dict) and flow.get("phase") == "monitoring":
                    out.append((t.get("project_id") or "", t.get("id") or ""))
            return out



    def update_task(self, project_id: str, task_id: str, **fields) -> Optional[dict]:
        """就地更新任务字段（如 pending_flow 等扩展态），返回更新后的任务。"""
        with self._lock:
            now = _now_iso()
            target = None
            for t in self._data.get("tasks", []):
                if (t.get("project_id") == project_id and t.get("id") == task_id):
                    target = t
                    break
            if target is None:
                return None
            for key, value in fields.items():
                if key in ("id", "project_id"):
                    continue
                if value is None:
                    target.pop(key, None)
                else:
                    target[key] = copy.deepcopy(value)
            target["updated_at"] = now
            for p in self._data.get("projects", []):
                if p.get("id") == project_id:
                    p["updated_at"] = now
            self._commit()
            return copy.deepcopy(target)

    def delete_task(self, project_id: str, task_id: str) -> dict | None:
        """删除一个计算任务：任务记录、其对话消息、等待队列中该任务，并更新项目任务数。"""
        with self._lock:
            tasks = self._data.get("tasks", [])
            target = None
            kept = []
            for t in tasks:
                if t.get("project_id") == project_id and t.get("id") == task_id:
                    target = t
                else:
                    kept.append(t)
            if target is None:
                return None
            self._data["tasks"] = kept
            waiting = self._data.get("waiting", [])
            self._data["waiting"] = [w for w in waiting
                                     if str(w.get("task_id") or "").strip() != task_id]
            for p in self._data.get("projects", []):
                if p.get("id") == project_id:
                    count = sum(1 for t in kept if t.get("project_id") == project_id)
                    p["job_count"] = count
                    p["updated_at"] = _now_iso()
            self._commit()
            return copy.deepcopy(target)

    # ---- 上下文 / 等待队列 ----


    def list_waiting(self) -> tuple[list[dict], int]:
        with self._lock:
            waiting = [dict(w) for w in self._data.get("waiting", [])]
            return waiting, len(waiting)


    def append_event(self, project_id, task_id, kind, message):
        with self._lock:
            events = self._data.setdefault('events', [])
            event = {'id': (events[-1]['id'] + 1 if events else 1),
                     'project_id': project_id, 'task_id': task_id,
                     'kind': kind, 'at': _now_iso(), 'message': str(message)}
            events.append(event)
            self._commit()
            return dict(event)

    def list_events(self, project_id, task_id, after=0):
        with self._lock:
            return copy.deepcopy([e for e in self._data.get('events', [])
                    if e['project_id'] == project_id and e['task_id'] == task_id and e['id'] > after])


_store: Optional[ProjectStore] = None
_store_lock = threading.RLock()


def get_project_store() -> ProjectStore:
    """进程内复用单例，避免并发实例互相覆盖落盘文件。"""
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = ProjectStore()
    return _store
