"""Single-process chat runs outlive HTTP subscribers, never their explicit stop.

This is not a durable job queue: process restart reports interruption and never
replays tools. Final messages are persisted before terminal SSE events. A slow
subscriber gets an explicit reconnect error instead of silently losing events.
"""

from __future__ import annotations

import logging
import queue
import threading
import uuid
from collections.abc import Callable, Iterable

logger = logging.getLogger("ai_mode.streaming")
_LOCK = threading.RLock()
_RUNS: dict[tuple[str, str], "ChatRun"] = {}
ACTIVE_STOPS: dict[tuple[str, str], bool] = {}
_INTERRUPTED = "生成意外中断；已保留收到的内容。请检查任务状态后重试，系统不会自动重放操作。"


class GenerationBusy(Exception):
    """The task already has a producer, even if its browser disconnected."""


def generation_status(store, pid: str, tid: str) -> dict:
    with _LOCK:
        run = _RUNS.get((pid, tid))
        if run is not None:
            return {"running": True, "run_id": run.run_id,
                    "state": "stopping" if run.should_stop() else "running"}
        previous = store.generation_metadata(pid, tid)
        # A previous process may have died without its finally block. Do not
        # pretend it is still running, or silently restart an external action.
        if previous.get("state") in {"running", "stopping"}:
            previous = {**previous, "state": "interrupted", "message": _INTERRUPTED}
            store.update_generation(pid, tid, previous)
        return {**previous, "running": False}


def request_stop(pid: str, tid: str) -> bool:
    with _LOCK:
        key = (pid, tid)
        if key not in ACTIVE_STOPS:
            return False
        ACTIVE_STOPS[key] = True
        return True


class ChatRun:
    def __init__(self, store, pid: str, tid: str, *, capacity: int = 256):
        self.store, self.pid, self.tid = store, pid, tid
        self.key = (pid, tid)
        self.run_id = uuid.uuid4().hex
        self.events: queue.Queue = queue.Queue(maxsize=max(capacity, 2))
        self.detached = threading.Event()
        self.finished = threading.Event()
        self.answer = ""
        self._publish_lock = threading.Lock()
        with _LOCK:
            if self.key in _RUNS:
                raise GenerationBusy()
            _RUNS[self.key] = self
            ACTIVE_STOPS[self.key] = False
            try:
                store.update_generation(pid, tid, {"run_id": self.run_id,
                                                      "state": "running"})
            except Exception:
                _RUNS.pop(self.key, None)
                ACTIVE_STOPS.pop(self.key, None)
                raise

    def should_stop(self) -> bool:
        with _LOCK:
            return bool(ACTIVE_STOPS.get(self.key)) if _RUNS.get(self.key) is self else True

    def detach(self) -> None:
        # Network loss is not user consent to stop or restart an action.
        with self._publish_lock:
            self.detached.set()

    def publish(self, event: dict) -> None:
        with self._publish_lock:
            if self.detached.is_set():
                return
            try:
                self.events.put_nowait(event)
            except queue.Full:
                self.detached.set()
                while True:
                    try:
                        self.events.get_nowait()
                    except queue.Empty:
                        break
                self.events.put_nowait({"type": "error", "code": "STREAM_OVERFLOW",
                                       "message": "连接接收过慢，请刷新读取任务状态；后台生成仍在继续。"})
                self.events.put_nowait({"type": "_end"})

    def finish(self, answer: str, thinking: str = "", *, state: str = "done") -> None:
        self.answer = answer
        try:
            if answer.strip():
                self.store.append_message(self.pid, self.tid, "assistant",
                                          answer.strip(), thinking=thinking.strip())
            self.store.update_generation(self.pid, self.tid,
                                         {"run_id": self.run_id, "state": state})
        finally:
            with _LOCK:
                if _RUNS.get(self.key) is self:
                    _RUNS.pop(self.key, None)
                    ACTIVE_STOPS.pop(self.key, None)
            self.finished.set()

    def produce(self, factory: Callable[[], Iterable[dict]]) -> None:
        chunks, thinking, errors = [], [], []
        terminal = None
        state = "done"
        try:
            for event in factory():
                event = dict(event)
                kind = event.get("type")
                if kind == "thinking":
                    thinking.append(str(event.get("text") or ""))
                elif kind == "answer":
                    chunks.append(str(event.get("text") or ""))
                elif kind == "error":
                    errors.append(str(event.get("message") or "生成失败"))
                    state = "error"
                elif kind in {"done", "stopped"}:
                    terminal = event
                    if kind == "stopped":
                        state = "stopped"
                    # The generator has a terminal contract; never execute more
                    # tools after it, and persist before exposing completion.
                    break
                self.publish(event)
            if terminal is None:
                if self.should_stop():
                    terminal = {"type": "stopped", "answer": "".join(chunks)}
                    state = "stopped"
                else:
                    raise RuntimeError("producer ended without terminal event")
        except Exception as exc:
            # Do not leak provider exceptions (which may contain keys/URLs).
            logger.warning("Chat run %s interrupted (%s)", self.run_id, type(exc).__name__)
            state = "error"
            errors.append(_INTERRUPTED)
            self.publish({"type": "error", "code": "GENERATION_INTERRUPTED", "message": _INTERRUPTED})
        finally:
            base = str((terminal or {}).get("answer") or "".join(chunks)).strip()
            for message in errors:
                if message and message not in base:
                    base = (base + "\n\n" + message).strip()
            if state == "stopped" and not base:
                base = "已停止生成。"
            try:
                self.finish(base, "".join(thinking), state=state)
                self.publish({"type": "stopped" if state == "stopped" else "done", "answer": base})
            except Exception as exc:
                logger.error("Chat persistence failed (%s)", type(exc).__name__)
                self.publish({"type": "error", "code": "GENERATION_PERSIST_FAILED",
                              "message": "生成记录保存失败；请检查磁盘状态，不要重复提交作业。"})
            finally:
                self.publish({"type": "_end"})
