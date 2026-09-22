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
        if action.get("kind") == "submit":
            from .computation import scope_valid
            if approved and not scope_valid(flow, action, active=False):
                action.update(state="failed", result="SCOPE_STALE: 单计算范围或尝试已变化，未提交")
                _save_flow(store, project_id, task_id, flow, cons)
                return {"approved": False, "conflict": True, "card_id": card_id, "state": "failed"}
            scope = cons.get("computation_scopes", {}).get((action.get("binding") or {}).get("scope_id"))
            if scope:
                scope["state"] = "active" if approved else "revoked"
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


def spawn_submit_card(store, project_id: str, task_id: str,
                      job_key: str | None = None, attempt_id: str | None = None) -> dict:
    from .computation import select_job, digest
    from .contracts import ToolboxError
    with task_lock(project_id, task_id):
        flow, cons = _load(store, project_id, task_id)
        job = select_job(flow, {"job_key": job_key, "attempt_id": attempt_id})
        precheck = job.get("precheck") or {}
        if (not precheck.get("ok") or not precheck.get("hard")
                or precheck.get("attempt_id") != job.get("attempt_id")
                or not job.get("draft") or len(str(precheck.get("digest") or "")) != 64):
            raise ToolboxError("PRECHECK_BLOCKED", "必须先完成当前计算的硬预检与草稿")
        by_key = {j["key"]: j for j in (flow.get("plan") or {}).get("jobs", [])}
        if any(by_key.get(key, {}).get("status") != "completed" for key in job.get("requires") or []):
            raise ToolboxError("DEPENDENCY_NOT_READY", "依赖未完成；完成后需单独重新确认", 409)
        target = (precheck.get("snapshot") or {}).get("scheduler_target") or {}
        binding = {
            "operation": "submit", "project_id": project_id, "task_id": task_id,
            "job_key": job["key"], "attempt_id": job["attempt_id"],
            "execution_kind": "paracloud_cbatch" if target.get("scheduler") == "paracloud" else "slurm_sbatch",
            "remote_root": str(flow.get("hpc_dir") or flow.get("local_dir") or "").strip(),
            "draft": job["draft"], "execution_mode": str(flow.get("execution_mode") or "None"),
            "precheck_digest": precheck["digest"],
            "endpoint_digest": digest({"scheduler_target": target, "host_key_evidence": "unknown"}),
            "policy_version": "single-job-human-v1",
        }
        batch_key = "submit|" + digest(binding)
        for action in cons["actions"].values():
            if action.get("state") == "pending" and action.get("batch_key") == batch_key and not _expired(action):
                from .computation import scope_valid
                if scope_valid(flow, action, active=False):
                    return dict(action)
        scope_id = uuid.uuid4().hex
        binding.update(scope_id=scope_id, scope_version=1)
        payload = card_payload(
            tool="confirm_submit", args={"job_key": job["key"], "attempt_id": job["attempt_id"]},
            risk="high", reason="仅批准本计算当前尝试的一次提交；用户需审阅脚本及资源，系统未证明全部副作用。",
            batch_key=batch_key, kind="submit",
            summary=f"提交计算 {job['key']} / attempt {job['attempt_id']}？\n目录：`{job['draft']['dir']}`\n命令：{job['draft']['submit_cmd']}\nSHA-256：{job['draft']['script_sha256']}\n仅此计算一次，不包含后继或重试。",
            options=["确认提交", "取消"], binding=binding)
        cons.setdefault("computation_scopes", {})[scope_id] = {
            **{key: binding[key] for key in ("project_id", "task_id", "job_key", "attempt_id", "endpoint_digest", "precheck_digest", "draft")},
            "version": 1, "approval_mode": "human", "allowed_operations": ["submit"],
            "submit_limit": 1, "expires_at": payload["expires_at"], "state": "proposed",
            "host_key_evidence": "unknown",
        }
        cons["actions"][payload["action_id"]] = payload
        _save_flow(store, project_id, task_id, flow, cons)
        return dict(payload)
