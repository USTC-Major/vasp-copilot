"""Execution owner's single-use reviewer challenge and bounded background queue."""
from __future__ import annotations

import copy
import hashlib
import hmac
import os
import posixpath
import secrets
import threading
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone

from . import consent
from .file_actions import check, now
from .review_transport import PROTOCOL, call, settings, signature
from .ssh import file_helper as helper


_SCRIPT = {".sh", ".bash", ".zsh", ".ksh", ".py", ".pl", ".rb", ".ps1", ".bat", ".cmd"}
_CHECKS = {"scope_match", "manifest_match", "ordinary_file_only", "no_scientific_claim", "no_execution"}
_MESSAGES = {
    "REVIEWER_QUEUED": "文件计划已受理，等待独立 reviewer；尚未批准或执行。",
    "REVIEWER_REVIEWING": "独立 reviewer 正在核对文件操作；尚未批准或执行。",
    "REVIEWER_DISABLED": "独立 reviewer 未启用，请人工审核。",
    "REVIEWER_SECRET_MISSING": "独立 reviewer 服务密钥未配置，请人工审核。",
    "REVIEWER_URL_INVALID": "独立 reviewer 服务地址未配置或无效，请人工审核。",
    "REVIEWER_INELIGIBLE": "此文件计划需要人工审核正文、脚本或科学适用性。",
    "REVIEWER_QUEUE_FULL": "独立 reviewer 队列已满，请人工审核。",
    "REVIEWER_UNAVAILABLE": "独立 reviewer 暂不可用，请人工审核。",
    "REVIEWER_FORMAT": "独立 reviewer 响应无效，请人工审核。",
    "REVIEWER_BINDING_CHANGED": "文件授权绑定已变化，请重新建立范围或计划。",
    "REVIEWER_NEEDS_HUMAN": "此文件计划需要人工审核。",
    "REVIEWER_REJECTED": "独立 reviewer 拒绝此文件计划。",
    "REVIEWER_APPROVED": "独立 reviewer 已核对文件操作范围；正文和科学适用性未审核。",
    "REVIEWER_INTERRUPTED": "独立 reviewer 审查中断，请人工审核。",
}


def _name_unsafe(path):
    name = posixpath.basename(path).upper()
    return name in helper.TEXT_NAMES or posixpath.splitext(name.lower())[1] in _SCRIPT


def eligible(manifest):
    """Conservative owner check; no text or source body is opened here."""
    try:
        helper.validated_manifest(manifest)
        roots = {root["root_id"]: root for root in manifest["roots"]}
        for item in manifest["items"]:
            if item["op"] not in {"mkdir", "write_text", "copy"} or item["on_conflict"] != "fail":
                return False
            dest = item["destination"]
            root = roots[dest["root_id"]]
            paths = (dest["relative_path"], root["requested_path"], root["canonical_path"],
                     posixpath.join(root["canonical_path"], dest["relative_path"]))
            if any(_name_unsafe(path) for path in paths):
                return False
            if root.get("resolution_chain") or dest.get("target_exists"):
                return False
            if any(part.get("type") == "symlink" for part in dest.get("ancestors", [])):
                return False
            if item["op"] == "mkdir":
                if item["mode"] != 0o700:
                    return False
                continue
            if item["content_class"] not in {"normal_text", "unclassified_external"}:
                return False
            if item["op"] == "write_text":
                if item["bytes"] > helper.TEXT_LIMIT or item["mode"] != 0o600:
                    return False
            else:
                source = item["source"]
                if (not isinstance(source, dict) or source.get("type") != "file" or
                    source.get("resolution_chain") or
                    source.get("content_class") not in {"normal_text", "unclassified_external"} or
                    any(_name_unsafe(source[path]) for path in ("requested_path", "canonical_path"))):
                    return False
        return True
    except (KeyError, TypeError, ValueError, helper.FileError):
        return False


def visible_input(action, scope):
    manifest = action["binding"]["manifest"]
    def pick(value, keys):
        return {key: copy.deepcopy(value[key]) for key in keys if key in value}

    endpoint = manifest["endpoint"]
    endpoint_view = pick(endpoint, ("schema_version", "host", "port", "username",
                                    "endpoint_digest", "local_config_digest"))
    endpoint_view["host_key"] = pick(endpoint.get("host_key") or {},
                                      ("algorithm", "sha256", "verification"))
    endpoint_view["scheduler_target"] = pick(endpoint.get("scheduler_target") or {},
                                              ("scheduler",))
    visible = {key: copy.deepcopy(manifest[key]) for key in
               ("protocol_version", "policy_version", "action_id", "project_id", "task_id",
                "job_key", "attempt_id", "scope_id", "scope_version", "expires_at",
                "max_operations", "max_total_bytes", "manifest_digest")}
    visible["endpoint"] = endpoint_view
    visible["roots"] = [pick(root, ("root_id", "version", "requested_path", "canonical_path",
                                    "endpoint_digest", "identity", "resolution_chain"))
                        for root in manifest["roots"]]
    visible["items"] = []
    for item in manifest["items"]:
        entry = pick(item, ("item_id", "op", "mode", "on_conflict", "content_class", "bytes"))
        entry["source"] = (pick(item["source"],
                             ("requested_path", "canonical_path", "type", "size", "mtime_ns",
                              "ctime_ns", "mode", "device", "inode", "resolution_chain",
                              "content_class", "endpoint_digest", "sha256"))
                           if isinstance(item["source"], dict) else None)
        entry["destination"] = pick(item["destination"],
                                     ("root_id", "root_version", "relative_path", "parent_chain",
                                      "missing_components", "parent_item_id", "target_exists"))
        provenance = item["source_provenance"]
        entry["source_provenance"] = (pick(provenance, ("origin", "content_class", "receipt_id"))
                                      if isinstance(provenance, dict) else None)
        entry["content_not_provided"] = True
        if item["op"] == "write_text":
            entry["text_sha256"] = hashlib.sha256(item["text"].encode("utf-8")).hexdigest()
        visible["items"].append(entry)
    scope_view = {key: copy.deepcopy(scope[key]) for key in
                  ("scope_id", "version", "job_key", "attempt_id", "root_bindings",
                   "allowed_operations", "max_operations", "max_total_bytes", "expires_at")}
    scope_view["source_bindings"] = [pick(source,
        ("requested_path", "canonical_path", "type", "size", "mtime_ns", "ctime_ns",
         "mode", "device", "inode", "resolution_chain", "content_class",
         "endpoint_digest", "sha256")) for source in scope["source_bindings"]]
    return {"manifest": visible, "binding_hash": action["binding_hash"],
            "scope": scope_view,
            "purpose": "mechanical file preparation only; no body or scientific suitability review"}


class Reviewer:
    def __init__(self, files, transport=None, *, queue_limit=64):
        self.files = files
        self.transport = transport
        self.queue_limit = queue_limit
        self.queue = deque()
        self.wake = threading.Event()
        self.stopping = threading.Event()
        self.thread = None
        self.run_id = uuid.uuid4().hex

    def start(self):
        self.thread = threading.Thread(target=self._loop, name="toolbox-reviewer", daemon=True)
        self.thread.start()

    def close(self):
        self.stopping.set()
        self.wake.set()
        store = self.files.store
        for project in store.list_projects():
            project_id = project["id"]
            for task in store.list_tasks(project_id):
                task_id = task["id"]
                with consent.task_lock(project_id, task_id), self.files.guard:
                    flow = self.files._flow(project_id, task_id)
                    ids = [action_id for action_id, action in
                           (flow.get("consent") or {}).get("actions", {}).items()
                           if action.get("kind") == "remote_file" and action.get("state") == "pending"
                           and (action.get("_review_private") or {}).get("status") == "issued"
                           and (action.get("_review_private") or {}).get("owner_run_id") == self.run_id]
                    for action_id in ids:
                        try:
                            self.files._save(project_id, task_id,
                                lambda current, aid=action_id: self._set_human(current, aid, "REVIEWER_INTERRUPTED"))
                        except Exception:
                            pass
        if self.thread is not None:
            self.thread.join(timeout=0.25)

    def status(self):
        _, code = settings()
        return {"configured": code == "READY", "reason_code": code}

    def schedule(self, project, task, action_id):
        files = self.files
        with consent.task_lock(project, task), files.guard:
            action = ((files._flow(project, task).get("consent") or {})
                      .get("actions", {}).get(action_id))
            check(action and action.get("kind") == "remote_file", "CARD_NOT_FOUND", "文件卡不存在", 404)
            if action.get("state") != "pending" or action.get("review"):
                return copy.deepcopy(action)
            binding = action["binding"]
            scope = files._scope(files._flow(project, task), binding["scope_id"],
                                 binding["scope_version"], active=True)
            check(scope.get("approval_mode", "human") == "reviewer", "SCOPE_STALE", "范围不是独立 reviewer 模式", 409)
            configuration, code = settings()
            if not eligible(binding["manifest"]):
                code = "REVIEWER_INELIGIBLE"
            elif code == "READY" and len(self.queue) >= self.queue_limit:
                code = "REVIEWER_QUEUE_FULL"
            if code != "READY":
                return files._save(project, task, lambda flow: self._set_human(flow, action_id, code))
            nonce = secrets.token_hex(32)
            expiry = min(datetime.fromisoformat(action["expires_at"]),
                         datetime.fromisoformat(scope["expires_at"]),
                         datetime.now(timezone.utc) + timedelta(seconds=120)).isoformat()
            challenge = {"challenge_id": uuid.uuid4().hex, "nonce": nonce,
                         "owner_run_id": self.run_id, "action_id": action_id,
                         "binding_hash": action["binding_hash"],
                         "manifest_digest": binding["manifest_digest"],
                         "scope_id": scope["scope_id"], "scope_version": scope["version"],
                         "project_id": project, "task_id": task,
                         "job_key": binding["job_key"], "attempt_id": binding["attempt_id"],
                         "endpoint_digest": binding["endpoint_digest"],
                         "policy_version": binding["policy_version"], "expires_at": expiry}
            review_input = visible_input(action, scope)
            if len(helper.canonical(review_input)) > helper.MAX_FRAME:
                return files._save(project, task, lambda flow: self._set_human(flow, action_id, "REVIEWER_INELIGIBLE"))
            def issue(flow):
                current = flow["consent"]["actions"][action_id]
                check(current["state"] == "pending" and not current.get("review"))
                current["review"] = self._review("queued", "REVIEWER_QUEUED", requested_at=now())
                current["_review_private"] = {key: value for key, value in challenge.items() if key != "nonce"}
                current["_review_private"].update(nonce_hash=hashlib.sha256(nonce.encode()).hexdigest(),
                                                  status="issued", consumed_at=None,
                                                  invalidated_reason=None, decision_digest=None)
                return current
            result = files._save(project, task, issue)
            self.queue.append((project, task, action_id, challenge, review_input, configuration))
            self.wake.set()
            return result

    @staticmethod
    def _review(state, code, *, requested_at=None, decision=None, checks=None, model=None,
                decided_by=None):
        return {"protocol_version": PROTOCOL, "state": state,
                "requested_at": requested_at, "finished_at": now() if state not in {"queued", "reviewing"} else None,
                "decision": decision, "reason_code": code, "reason": _MESSAGES[code],
                "checks": checks or {key: "unknown" for key in _CHECKS},
                "reviewer_model": model, "decided_by": decided_by}

    def _set_human(self, flow, action_id, code):
        action = flow["consent"]["actions"][action_id]
        if action["state"] == "pending":
            requested = (action.get("review") or {}).get("requested_at") or now()
            private = action.get("_review_private")
            if private and private.get("status") == "issued":
                private.update(status="invalidated", invalidated_reason=code)
            action["review"] = self._review("needs_human", code, requested_at=requested)
        return action

    def _loop(self):
        while not self.stopping.is_set():
            with self.files.guard:
                item = self.queue.popleft() if self.queue else None
                if item is None:
                    self.wake.clear()
            if item is None:
                self.wake.wait(0.1)
                continue
            project, task, action_id, challenge, review_input, configuration = item
            if self.stopping.is_set():
                break
            payload = {"protocol_version": PROTOCOL, "challenge": challenge,
                       "review_input": review_input}
            try:
                if not self._preflight(project, task, action_id, challenge):
                    continue
                if self.stopping.is_set():
                    break
                transport = self.transport or (lambda value: call(value, configuration=configuration))
                response = transport(payload)
                self._finish(project, task, action_id, challenge, response, configuration["secret"])
            except Exception:
                if not self.stopping.is_set():
                    try:
                        with consent.task_lock(project, task), self.files.guard:
                            self.files._save(project, task,
                                lambda flow: self._set_human(flow, action_id, "REVIEWER_UNAVAILABLE"))
                    except Exception:
                        pass

    def _preflight(self, project, task, action_id, challenge):
        with consent.task_lock(project, task), self.files.guard:
            if self.stopping.is_set():
                return False
            flow = self.files._flow(project, task)
            action = (flow.get("consent") or {}).get("actions", {}).get(action_id)
            if not action or action.get("state") != "pending":
                return False
            private = action.get("_review_private") or {}
            if (private.get("status") != "issued" or
                private.get("owner_run_id") != self.run_id or
                private.get("challenge_id") != challenge["challenge_id"] or
                private.get("nonce_hash") != hashlib.sha256(challenge["nonce"].encode()).hexdigest() or
                not consent._valid_binding(action) or
                action["binding_hash"] != challenge["binding_hash"] or
                datetime.fromisoformat(challenge["expires_at"]) <= datetime.now(timezone.utc)):
                self.files._save(project, task,
                    lambda current: self._set_human(current, action_id, "REVIEWER_BINDING_CHANGED"))
                return False
            try:
                scope = self.files._scope(flow, challenge["scope_id"],
                                          challenge["scope_version"], active=True)
                check(scope.get("approval_mode") == "reviewer")
                check(eligible(action["binding"]["manifest"]))
            except Exception:
                self.files._save(project, task,
                    lambda current: self._set_human(current, action_id, "REVIEWER_BINDING_CHANGED"))
                return False
            def mark(current):
                card = current["consent"]["actions"][action_id]
                card["review"] = self._review("reviewing", "REVIEWER_REVIEWING",
                                                requested_at=card["review"]["requested_at"])
                return card
            self.files._save(project, task, mark)
            return True

    def _finish(self, project, task, action_id, challenge, response, secret):
        if self.stopping.is_set():
            return
        valid = False
        decision = None
        if isinstance(response, dict) and set(response) == {"protocol_version", "challenge", "decision", "signature"}:
            decision = response["decision"]
            try:
                valid = (response["protocol_version"] == PROTOCOL and
                         response["challenge"] == challenge and
                         isinstance(decision, dict) and
                         hmac.compare_digest(response["signature"],
                             signature(secret, PROTOCOL, challenge, decision)))
            except (TypeError, ValueError):
                valid = False
        with consent.task_lock(project, task), self.files.guard:
            if self.stopping.is_set():
                return
            approved_now = False
            def update(flow):
                nonlocal approved_now
                action = flow["consent"]["actions"][action_id]
                private = action.get("_review_private") or {}
                if (action["state"] != "pending" or private.get("status") != "issued" or
                    private.get("owner_run_id") != self.run_id or
                    private.get("challenge_id") != challenge["challenge_id"] or
                    private.get("nonce_hash") != hashlib.sha256(challenge["nonce"].encode()).hexdigest()):
                    return action
                if (not valid or datetime.fromisoformat(challenge["expires_at"]) <= datetime.now(timezone.utc)
                    or not consent._valid_binding(action) or
                    action["binding_hash"] != challenge["binding_hash"] or
                    action["binding"]["manifest_digest"] != challenge["manifest_digest"]):
                    return self._set_human(flow, action_id, "REVIEWER_FORMAT")
                try:
                    scope = self.files._scope(flow, challenge["scope_id"], challenge["scope_version"], active=True)
                    check(scope.get("approval_mode") == "reviewer")
                    check(all(private.get(key) == value for key, value in challenge.items() if key != "nonce"))
                    check(action["binding"]["policy_version"] == challenge["policy_version"])
                    check(eligible(action["binding"]["manifest"]))
                except Exception:
                    return self._set_human(flow, action_id, "REVIEWER_BINDING_CHANGED")
                if (set(decision) != {"decision", "reason", "checks"} or
                    decision["decision"] not in {"approve", "reject", "needs_human"} or
                    not isinstance(decision["reason"], str) or not 0 < len(decision["reason"]) <= 500 or
                    not isinstance(decision["checks"], dict) or set(decision["checks"]) != _CHECKS or
                    any(type(value) is not bool and value != "unknown" for value in decision["checks"].values()) or
                    (decision["decision"] == "approve" and not all(value is True for value in decision["checks"].values()))):
                    return self._set_human(flow, action_id, "REVIEWER_FORMAT")
                choice = decision["decision"]
                if choice == "approve":
                    try:
                        self.files._global_write()
                        self.files._conflicts(action["binding"]["manifest"], exclude=action_id)
                        self.files._budget_available(flow, action, scope)
                    except Exception:
                        return self._set_human(flow, action_id, "REVIEWER_BINDING_CHANGED")
                    self.files._decide_in_flow(flow, action_id, True, "", None, reviewer=True)
                    approved_now = True
                elif choice == "reject":
                    self.files._decide_in_flow(flow, action_id, False, "", None, reviewer=True)
                private.update(status="consumed", consumed_at=now(),
                               decision_digest=helper.digest(decision))
                code = {"approve": "REVIEWER_APPROVED", "reject": "REVIEWER_REJECTED",
                        "needs_human": "REVIEWER_NEEDS_HUMAN"}[choice]
                action["review"] = self._review(
                    {"approve": "approved", "reject": "rejected", "needs_human": "needs_human"}[choice],
                    code, requested_at=action["review"]["requested_at"],
                    decision=choice, checks=decision["checks"], decided_by="reviewer")
                return action
            try:
                action = self.files._save(project, task, update)
            except Exception:
                self.files._save(project, task,
                    lambda flow: self._set_human(flow, action_id, "REVIEWER_BINDING_CHANGED"))
                return
            if approved_now and action["state"] == "approved":
                self.files._enqueue(project, task, action_id)
