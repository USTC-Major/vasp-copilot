"""Persistent, single-use consent actions for AI-mode mutations.

Consent is an action state machine, not a reusable permission grant. Every
card binds one exact operation payload with a SHA-256 digest and a short
expiry. Approval only advances that action to ``approved``; an executor must
atomically claim it before performing the bound operation.
"""
from __future__ import annotations

import hashlib
import json
import threading
import uuid
from datetime import datetime, timedelta, timezone

__all__ = [
    "PendingConsentError", "consent_of", "list_cards", "get_card",
    "save_card", "resolve_card", "claim_action", "finish_action",
    "grants_of", "denials_of", "card_payload", "spawn_submit_card",
    "task_lock",
]

_ACTIONS_KEY = "actions"
_CARDS_KEY = "cards"  # compatibility projection; never authoritative
_LOCKS: dict[tuple[str, str], threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).replace(microsecond=0).isoformat()


def task_lock(project_id: str, task_id: str) -> threading.RLock:
    """Return the shared re-entrant lock for all state changes on one task."""
    key = (project_id, task_id)
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


_lock_for = task_lock


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _binding_hash(binding: dict) -> str:
    return hashlib.sha256(_canonical(binding)).hexdigest()


def _expired(action: dict) -> bool:
    raw = str(action.get("expires_at") or "")
    try:
        return datetime.fromisoformat(raw) <= _now()
    except (TypeError, ValueError):
        return True


def _valid_binding(action: dict) -> bool:
    binding = action.get("binding")
    return (isinstance(binding, dict)
            and action.get("binding_hash") == _binding_hash(binding))


def consent_of(flow: dict) -> dict:
    cons = flow.get("consent")
    if not isinstance(cons, dict):
        cons = {}
    cons.setdefault(_ACTIONS_KEY, {})
    cons.setdefault(_CARDS_KEY, {})
    # Old batch grants are deliberately discarded. They must never authorize
    # a new or replayed operation.
    cons["grants"] = []
    cons["denials"] = []
    return cons


def _sync_cards(cons: dict) -> None:
    cons[_CARDS_KEY] = {
        aid: action for aid, action in cons[_ACTIONS_KEY].items()
        if action.get("state") == "pending"
    }


def _save_flow(store, project_id: str, task_id: str, flow: dict,
               cons: dict) -> None:
    _sync_cards(cons)
    flow["consent"] = cons
    flow["updated_at"] = _iso()
    task = store.get_task(project_id, task_id) or {}
    store.update_task(project_id, task_id, flow=dict(flow),
                      status=flow.get("status") or task.get("status", "planned"))


def _load(store, project_id: str, task_id: str) -> tuple[dict, dict]:
    flow = (store.get_task(project_id, task_id) or {}).get("flow") or {}
    flow = dict(flow)
    return flow, consent_of(flow)


def card_payload(*, tool: str, args: dict, risk: str, reason: str,
                 batch_key: str, kind: str, summary: str,
                 options: list[str] | None = None,
                 binding: dict | None = None,
                 expires_seconds: int = 600) -> dict:
    """Construct a hash-bound action and its user-visible card payload."""
    action_id = uuid.uuid4().hex
    created = _now()
    expires_at = _iso(created + timedelta(seconds=max(1, expires_seconds)))
    exact = dict(binding or {
        "operation": tool,
        "args": dict(args or {}),
        "kind": kind,
    })
    exact["action_id"] = action_id
    exact["expires_at"] = expires_at
    return {
        "action_id": action_id,
        "card_id": action_id,
        "tool": tool,
        "args": dict(args or {}),
        "risk": risk,
        "reason": reason,
        "options": list(options or ["同意本次", "拒绝"]),
        "batch_key": batch_key,  # display/dedup hint only; never a grant key
        "kind": kind,
        "summary": summary,
        "execution_mode": str(exact.get("execution_mode") or "None"),
        "state": "pending",
        "binding": exact,
        "binding_hash": _binding_hash(exact),
        "created_at": _iso(created),
        "expires_at": expires_at,
    }


def save_card(store, project_id: str, task_id: str, flow: dict,
              payload: dict) -> dict:
    """Persist one immutable pending action; identical pending actions dedupe."""
    del flow  # always reload under the task lock to avoid stale-flow overwrite
    with _lock_for(project_id, task_id):
        current, cons = _load(store, project_id, task_id)
        if not _valid_binding(payload) or payload.get("state") != "pending":
            raise ValueError("invalid consent action binding")
        for action in cons[_ACTIONS_KEY].values():
            if action.get("state") == "pending" and _expired(action):
                action["state"] = "expired"
                action["resolved_at"] = _iso()
                action["result"] = "操作确认已过期，未执行"
                continue
            if (action.get("state") == "pending"
                    and action.get("batch_key")
                    and action.get("batch_key") == payload.get("batch_key")):
                return dict(action)
        cons[_ACTIONS_KEY][payload["action_id"]] = dict(payload)
        _save_flow(store, project_id, task_id, current, cons)
        return dict(payload)


def _expire_locked(store, project_id: str, task_id: str,
                   flow: dict, cons: dict, action: dict) -> bool:
    if action.get("state") in {"pending", "approved"} and _expired(action):
        action["state"] = "expired"
        action["resolved_at"] = _iso()
        action["result"] = "操作确认已过期，未执行"
        _save_flow(store, project_id, task_id, flow, cons)
        return True
    return False


def list_cards(store, project_id: str, task_id: str) -> list[dict]:
    with _lock_for(project_id, task_id):
        flow, cons = _load(store, project_id, task_id)
        changed = False
        for action in cons[_ACTIONS_KEY].values():
            if action.get("state") == "pending" and _expired(action):
                action["state"] = "expired"
                action["resolved_at"] = _iso()
                action["result"] = "操作确认已过期，未执行"
                changed = True
        if changed:
            _save_flow(store, project_id, task_id, flow, cons)
        return [dict(a) for a in cons[_ACTIONS_KEY].values()
                if a.get("state") == "pending"]


def get_card(store, project_id: str, task_id: str,
             card_id: str) -> dict | None:
    with _lock_for(project_id, task_id):
        flow, cons = _load(store, project_id, task_id)
        action = cons[_ACTIONS_KEY].get(card_id)
        if action is None:
            return None
        _expire_locked(store, project_id, task_id, flow, cons, action)
        return dict(action)


def grants_of(store, project_id: str, task_id: str) -> list[str]:
    """Compatibility API: reusable consent grants no longer exist."""
    return []


def denials_of(store, project_id: str, task_id: str) -> list[str]:
    """Compatibility API: decisions are recorded on individual actions."""
    return []


def resolve_card(store, project_id: str, task_id: str, card_id: str, *,
                 approved: bool, note: str = "") -> dict:
    """CAS a pending action to approved/rejected; terminal actions stay terminal."""
    with _lock_for(project_id, task_id):
        flow, cons = _load(store, project_id, task_id)
        action = cons[_ACTIONS_KEY].get(card_id)
        if action is None:
            return {"approved": approved, "missing": True, "card_id": card_id}
        if not _valid_binding(action):
            action["state"] = "failed"
            action["result"] = "确认绑定校验失败，未执行"
            _save_flow(store, project_id, task_id, flow, cons)
            return {"approved": False, "missing": False, "tampered": True,
                    "card_id": card_id, "state": "failed"}
        if _expire_locked(store, project_id, task_id, flow, cons, action):
            return {"approved": False, "missing": False, "expired": True,
                    "card_id": card_id, "state": "expired"}
        if action.get("state") != "pending":
            return {
                "approved": action.get("state") in {"approved", "executing", "executed"},
                "missing": False, "conflict": True, "card_id": card_id,
                "state": action.get("state"),
            }
        action["state"] = "approved" if approved else "rejected"
        action["resolved_at"] = _iso()
        action["note"] = str(note or "")[:500]
        _save_flow(store, project_id, task_id, flow, cons)
        return {"approved": approved, "missing": False, "card_id": card_id,
                "tool": action.get("tool", ""), "state": action["state"]}


def claim_action(store, project_id: str, task_id: str,
                 action_id: str) -> dict | None:
    """Atomically claim exactly one approved, unexpired, untampered action."""
    with _lock_for(project_id, task_id):
        flow, cons = _load(store, project_id, task_id)
        action = cons[_ACTIONS_KEY].get(action_id)
        if action is None or action.get("state") != "approved":
            return None
        inflight = [other for key, other in cons[_ACTIONS_KEY].items()
                    if key != action_id and other.get("state") == "executing"]
        if inflight:
            for other in inflight:
                other["state"] = "unknown"
                other["finished_at"] = _iso()
                other["result"] = "上次执行被中断，结果未知；未自动重试"
            action["state"] = "failed"
            action["finished_at"] = _iso()
            action["result"] = "存在结果未知的先前操作；本次未执行"
            _save_flow(store, project_id, task_id, flow, cons)
            return None
        if not _valid_binding(action):
            action["state"] = "failed"
            action["result"] = "确认绑定校验失败，未执行"
            _save_flow(store, project_id, task_id, flow, cons)
            return None
        if _expire_locked(store, project_id, task_id, flow, cons, action):
            return None
        action["state"] = "executing"
        action["executing_at"] = _iso()
        _save_flow(store, project_id, task_id, flow, cons)
        return dict(action)


def finish_action(store, project_id: str, task_id: str, action_id: str, *,
                  state: str, result: str = "") -> dict | None:
    if state not in {"executed", "failed", "unknown"}:
        raise ValueError("invalid terminal action state")
    with _lock_for(project_id, task_id):
        flow, cons = _load(store, project_id, task_id)
        action = cons[_ACTIONS_KEY].get(action_id)
        if action is None or action.get("state") != "executing":
            return None
        action["state"] = state
        action["finished_at"] = _iso()
        action["result"] = str(result or "")[:2000]
        _save_flow(store, project_id, task_id, flow, cons)
        return dict(action)


class PendingConsentError(Exception):
    def __init__(self, card: dict):
        self.card = card
        self.card_id = card.get("card_id", "")
        self.batch_key = card.get("batch_key", "")
        self.tool = card.get("tool", "")
        super().__init__(card.get("reason") or "该操作需要你的授权")


def _stable_key(text: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_URL, str(text)).hex


def spawn_submit_card(store, project_id: str, task_id: str) -> dict:
    flow = (store.get_task(project_id, task_id) or {}).get("flow") or {}
    jobs = (flow.get("plan") or {}).get("jobs") or []
    active = [j for j in jobs if j.get("status") not in
              ("completed", "failed", "not_converged", "canceled",
               "skipped", "blocked", "unknown")]
    dir_by_job = {str(d["job_key"]): str(d["dir"])
                  for d in flow.get("draft") or []
                  if isinstance(d, dict) and d.get("job_key") and d.get("dir")}
    lines = "\n".join(
        f"- {j.get('key')}（{j.get('label') or j.get('key')}）"
        + (f"→ `{dir_by_job[j.get('key')]}`" if j.get("key") in dir_by_job else "")
        for j in active) or "（无待提交作业）"
    remote = str(flow.get("hpc_dir") or flow.get("local_dir") or "").strip()
    mode = str(flow.get("execution_mode") or "None")
    precheck = flow.get("precheck") or {}
    digest = str(precheck.get("digest") or "").lower()
    if (mode not in {"Fake", "Real", "None"}
            or not precheck.get("ok") or not precheck.get("hard")
            or len(digest) != 64
            or any(ch not in "0123456789abcdef" for ch in digest)):
        raise ValueError("[AI_PRECHECK_REQUIRED] 必须先完成当前 HPC 环境的不可变硬预检")
    binding = {
        "operation": "submit",
        "project_id": project_id,
        "task_id": task_id,
        "execution_kind": "slurm_sbatch",
        "remote_root": remote,
        "drafts": flow.get("draft") or [],
        "execution_mode": mode,
        "precheck_digest": digest,
    }
    payload = card_payload(
        tool="confirm_submit", args={}, risk="high",
        reason="这是真实的提交动作；确认只对当前绑定草稿生效。",
        batch_key=f"submit|{_stable_key(json.dumps(binding, sort_keys=True, ensure_ascii=False))}",
        kind="submit", summary=f"确认提交到超算工作区 `{remote}`？\n{lines}",
        options=["确认提交", "取消"], binding=binding,
    )
    return save_card(store, project_id, task_id, dict(flow), payload)
