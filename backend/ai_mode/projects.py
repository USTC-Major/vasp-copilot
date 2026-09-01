"""项目 / 计算任务 / 对话消息持久化商店（真实后端数据源）。

背景：M12 前端界面曾对接 MSW 演示数据后端（仅供测试/演示），真实 FastAPI
后端一直没有项目列表/任务/消息路由，导致浏览器打开「智能模式」直接 404。
本模块补上真实数据源：
- 数据落盘 <VASP_AI_HOME>/ai_store.json（默认 ~/.vasp-ai，可用 VASP_AI_HOME 覆盖）。
- 全新环境首启为空结构，不注入任何演示作业。
- 安全红线：本模块从不接触 LLM/MP 密钥与 SSH 密码。
"""

from __future__ import annotations

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

logger = logging.getLogger("ai_mode.projects")

DATA_FILE = "ai_store.json"
CONTEXT_CAPACITY = 65536


def _now_iso(offset_ms: int = 0) -> str:
    base = datetime.now(timezone.utc)
    return base.replace(microsecond=0).isoformat().replace("+00:00", "Z")

def _empty_data() -> dict[str, Any]:
    return {
        "projects": [],
        "tasks": [],
        "messages": {},
        "waiting": [],
        "context": {"ratio": 0.0, "used": 0, "capacity": 65536},
    }

class ProjectStore:
    """按 <VASP_AI_HOME>/ai_store.json 持久化的项目/任务/消息数据源。"""

    def __init__(self, root: Optional[Path] = None):
        if root is None:
            root = paths.home_dir()
        self.root = Path(root).expanduser()
        self.root.mkdir(parents=True, exist_ok=True)
        self._path = self.root / DATA_FILE
        self._lock = threading.Lock()
        self._data = self._load()

    # ---- 落盘 ----
    def _load(self) -> dict[str, Any]:
        if self._path.is_file():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except (json.JSONDecodeError, OSError):
                logger.warning("项目数据文件损坏，重置为空结构: %s", self._path)
        data = _empty_data()
        self._flush_unlocked(data)
        return data

    def _flush_unlocked(self, data: dict[str, Any]) -> None:
        payload = json.dumps(data, ensure_ascii=False, indent=2)
        fd, tmp_name = tempfile.mkstemp(dir=str(self.root),
                                        suffix=".tmp", prefix=".w_ais_")
        os.close(fd)
        tmp_path = Path(tmp_name)
        try:
            tmp_path.write_text(payload, encoding="utf-8")
            tmp_path.replace(self._path)
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

    def _commit(self) -> None:
        self._flush_unlocked(self._data)

    # ---- 项目 ----
    def list_projects(self) -> list[dict]:
        with self._lock:
            projects = [dict(p) for p in self._data.get("projects", [])]
        projects.sort(key=lambda p: p.get("updated_at") or p.get("created_at") or "",
                      reverse=True)
        return projects

    def get_project(self, project_id: str) -> Optional[dict]:
        with self._lock:
            for p in self._data.get("projects", []):
                if p["id"] == project_id:
                    return dict(p)
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
            messages = self._data.setdefault("messages", {})
            self._data["messages"] = {
                k: v for k, v in messages.items()
                if not k.startswith(project_id + ":")
            }
            deleted = len(self._data["projects"]) < before
            if deleted:
                self._commit()
            return deleted

    # ---- 计算任务 ----
    def list_tasks(self, project_id: str) -> list[dict]:
        with self._lock:
            tasks = [dict(t) for t in self._data.get("tasks", [])
                     if t.get("project_id") == project_id]
            self._decorate_tasks(project_id, tasks)
        tasks.sort(key=lambda t: t.get("updated_at") or "", reverse=True)
        return tasks

    def _decorate_tasks(self, project_id: str, tasks: list[dict]) -> None:
        """装饰任务列表：补最近一条消息摘要 + 上下文占比。"""
        messages = self._data.get("messages", {})
        for t in tasks:
            msgs = messages.get(f"{project_id}:{t.get('id')}", [])
            if msgs:
                last = msgs[-1]
                text = str(last.get("content") or "") or str(last.get("thinking") or "")
                text = text.replace("\r", " ").replace("\n", " ").strip()
                if text:
                    t["last_message"] = text[:80]
            used = 0
            for m in msgs:
                raw = str(m.get("content") or "")
                think = str(m.get("thinking") or "")
                if think:
                    raw += "\n[thinking] " + think
                used += max(1, round(len(raw.encode("utf-8", "replace")) / 4))
            used = min(used, CONTEXT_CAPACITY)
            t["context_ratio"] = 0.0 if used == 0 else round(used / CONTEXT_CAPACITY, 4)


    def get_task(self, project_id: str, task_id: str) -> Optional[dict]:
        with self._lock:
            for t in self._data.get("tasks", []):
                if t.get("project_id") == project_id and t.get("id") == task_id:
                    return dict(t)
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
            self._data.setdefault("messages", {})[f"{project_id}:{task_id}"] = []
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

    def list_messages(self, project_id: str, task_id: str) -> list[dict]:
        with self._lock:
            return [dict(m) for m in
                    self._data.get("messages", {}).get(f"{project_id}:{task_id}", [])]

    def append_message(self, project_id: str, task_id: str,
                       role: str, content: str,
                       thinking: str = "") -> dict:
        with self._lock:
            key = f"{project_id}:{task_id}"
            msgs = self._data.setdefault("messages", {}).setdefault(key, [])
            msg = {"role": role, "content": content, "at": _now_iso()}
            thinking = (thinking or "").strip()
            if thinking:
                msg["thinking"] = thinking
            msgs.append(msg)
            for t in self._data.get("tasks", []):
                if t.get("project_id") == project_id and t.get("id") == task_id:
                    t["updated_at"] = msg["at"]
            for p in self._data.get("projects", []):
                if p.get("id") == project_id:
                    p["updated_at"] = msg["at"]
            self._commit()
            return dict(msg)

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
                    target[key] = value
            target["updated_at"] = now
            for p in self._data.get("projects", []):
                if p.get("id") == project_id:
                    p["updated_at"] = now
            self._commit()
            return dict(target)

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
            self._data.setdefault("messages", {}).pop(f"{project_id}:{task_id}", None)
            waiting = self._data.get("waiting", [])
            self._data["waiting"] = [w for w in waiting
                                     if str(w.get("task_id") or "").strip() != task_id]
            for p in self._data.get("projects", []):
                if p.get("id") == project_id:
                    count = sum(1 for t in kept if t.get("project_id") == project_id)
                    p["job_count"] = count
                    p["updated_at"] = _now_iso()
            self._commit()
            return dict(target)

    # ---- 上下文 / 等待队列 ----
    def task_context(self, project_id: str, task_id: str) -> dict:
        """该任务的上下文占用（按已落库消息粗略估算，随对话增长）。"""
        with self._lock:
            msgs = list(self._data.get("messages", {}).get(
                f"{project_id}:{task_id}", []))
        used = 0
        for m in msgs:
            text = str(m.get("content") or "")
            think = str(m.get("thinking") or "")
            if think:
                text += "\n[thinking] " + think
            used += max(1, round(len(text.encode("utf-8", "replace")) / 4))
        used = min(used, CONTEXT_CAPACITY)
        ratio = 0.0 if used == 0 else round(used / CONTEXT_CAPACITY, 4)
        return {"ratio": ratio, "used": used,
                "capacity": CONTEXT_CAPACITY}

    def context(self) -> dict:
        used = 0
        with self._lock:
            messages = self._data.get("messages", {})
            for msgs in messages.values():
                for m in msgs:
                    text = str(m.get("content") or "")
                    think = str(m.get("thinking") or "")
                    if think:
                        text += "\n[thinking] " + think
                    used += max(1, round(len(text.encode("utf-8", "replace")) / 4))
        used = min(used, CONTEXT_CAPACITY)
        ratio = 0.0 if used == 0 else round(used / CONTEXT_CAPACITY, 4)
        return {"ratio": ratio, "used": used,
                "capacity": CONTEXT_CAPACITY}

    def list_waiting(self) -> tuple[list[dict], int]:
        with self._lock:
            waiting = [dict(w) for w in self._data.get("waiting", [])]
            return waiting, len(waiting)


_store: Optional[ProjectStore] = None
_store_lock = threading.Lock()


def get_project_store() -> ProjectStore:
    """进程内复用单例，避免并发实例互相覆盖落盘文件。"""
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = ProjectStore()
    return _store
