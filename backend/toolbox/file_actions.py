"""Human file actions owned by the existing ExecutionService/ProjectStore."""

from __future__ import annotations

import copy
import datetime as dt
import posixpath
import threading
import time
import uuid
import hashlib
from collections import deque
from contextlib import contextmanager, nullcontext

from . import consent
from .contracts import ToolboxError
from . import file_evidence as evidence
from .ssh import file_helper as h
from .ssh.remote_files import RemoteFiles, RemoteFileError, _remaining


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def check(
    condition, code="INVALID_FILE_REQUEST", message="文件请求不符合合同", status=400
):
    if not condition:
        raise ToolboxError(code, message, status)


def exact(payload, allowed, required=None):
    check(
        isinstance(payload, dict)
        and not set(payload) - set(allowed)
        and set(required if required is not None else allowed) <= set(payload)
    )


def public(value):
    if isinstance(value, dict):
        if value.get("kind") == "remote_file":
            allowed = {"kind", "tool", "args", "risk", "reason", "batch_key", "summary",
                       "action_id", "card_id", "expires_at", "created_at", "state",
                       "binding", "binding_hash", "receipt", "result", "resolved_at", "note",
                       "executing_at", "finished_at", "review"}
            return {k: public(v) for k, v in value.items() if k in allowed}
        if set(value) & {"protocol_version", "requested_at", "reviewer_model"} and "reason_code" in value:
            allowed = {"protocol_version", "state", "requested_at", "finished_at", "decision",
                       "reason_code", "reason", "checks", "reviewer_model", "decided_by"}
            return {k: public(v) for k, v in value.items() if k in allowed}
        return {
            k: public(v)
            for k, v in value.items()
            if k not in {"prepare_token", "dispatch_nonce", "nonce"}
        }
    if isinstance(value, list):
        return [public(v) for v in value]
    return value


def summary(value):
    value = public(copy.deepcopy(value))
    if isinstance(value, dict):
        for item in (value.get("binding") or {}).get("manifest", {}).get("items", []):
            if isinstance(item.get("text"), str):
                item["text_sha256"] = hashlib.sha256(
                    item["text"].encode("utf-8")
                ).hexdigest()
                item.pop("text")
    return value


def file_error(exc):
    if isinstance(exc, ToolboxError):
        return exc
    if isinstance(exc, (RemoteFileError, h.FileError)):
        status = (
            403
            if exc.code == "CONTENT_READ_DENIED"
            else (
                404
                if exc.code == "SOURCE_NOT_FOUND"
                else 503 if exc.code == "REMOTE_CAPABILITY_UNAVAILABLE" else 409
            )
        )
        return ToolboxError(exc.code, str(exc), status)
    return ToolboxError(
        "INVALID_FILE_REQUEST", "文件请求失败：" + type(exc).__name__, 400
    )


class FileActions:
    def __init__(self, service, file_factory=None, *, workers=2, queue_limit=64):
        self.service, self.store = service, service.store
        self.factory, self.worker_count, self.queue_limit = (
            file_factory,
            workers,
            queue_limit,
        )
        self.guard = threading.RLock()
        self.wake, self.stopping = threading.Event(), threading.Event()
        self.active, self.queue, self.threads, self.leases = {}, deque(), [], {}
        self.generation = 0
        self.closed = False
        self.owner_id = uuid.uuid4().hex
        self.reviewer = None

    def start(self):
        self.store.file_actions = self
        from .reviewer import Reviewer
        self.reviewer = Reviewer(self, self.service.reviewer_transport)
        self.reviewer.start()
        for n in range(self.worker_count):
            thread = threading.Thread(
                target=self._loop, name="toolbox-file-%s" % n, daemon=True
            )
            thread.start()
            self.threads.append(thread)
        return self

    @contextmanager
    def remote(self):
        files = self.factory() if self.factory else None
        if files is None:
            from .browse import create_hpc_ssh
            from .scheduler_profile import target_binding

            cfg = self.service.settings_loader()
            manager = create_hpc_ssh(cfg)
            check(
                manager is not None,
                "REMOTE_CAPABILITY_UNAVAILABLE",
                "未配置远端文件连接",
                503,
            )
            # Settings use empty paths whereas SSHManager stores None.
            target = target_binding(cfg)
            target["identity_file"] = target["identity_file"] or None
            target["known_hosts_path"] = target["known_hosts_path"] or None
            files = RemoteFiles(manager, scheduler_target=target)
        try:
            yield files
        finally:
            manager = getattr(files, "manager", None)
            if manager is not None:
                manager.close()

    def snapshot(self):
        return self.store.execution_snapshot()

    def _flow(self, project, task):
        return self.service.require_task(project, task).get("flow") or {}

    def _save(self, project, task, change):
        def apply(flow):
            value = change(flow)
            cons = flow.get("consent") or {}
            if cons:
                cons["cards"] = {
                    k: a
                    for k, a in cons.get("actions", {}).items()
                    if a.get("state") == "pending"
                }
            return value

        with self.guard:
            check(
                not self.closed,
                "FILE_ACTION_BUSY",
                "文件owner已关闭，旧请求不能再保存",
                409,
            )
            result = self.store.mutate_file_task(project, task, apply)
            self.generation += 1
            return result

    def _job(self, flow, args):
        check(
            isinstance(args.get("job_key"), str)
            and isinstance(args.get("attempt_id"), str),
            "JOB_REQUIRED",
            "文件操作必须明确job_key和attempt_id",
        )
        from .computation import select_job

        return select_job(flow, args)

    def _scope(self, flow, scope_id, version, *, active=False):
        h.integer(version, 1)
        scope = (flow.get("consent") or {}).get("computation_scopes", {}).get(scope_id)
        check(
            scope and scope.get("kind") == "file" and scope.get("version") == version,
            "SCOPE_STALE",
            "文件范围不存在或版本已变更",
            409,
        )
        check(scope.get("state") != "revoked", "SCOPE_REVOKED", "文件范围已撤销", 403)
        check(
            scope.get("state") in ({"active"} if active else {"active", "proposed"}),
            "SCOPE_STALE",
            "文件范围不再有效",
            409,
        )
        _remaining(scope["expires_at"])
        self._job(flow, scope)
        roots = {r["root_id"]: r for r in flow.get("file_roots", [])}
        for binding in scope["root_bindings"]:
            check(
                binding["root_id"] in roots
                and roots[binding["root_id"]]["version"] == binding["version"],
                "ROOT_CHANGED",
                "已批准根版本已变化",
                409,
            )
        return scope

    def _global_write(self):
        check(
            not evidence.legacy_unknown(self.snapshot()),
            "LEGACY_UPLOAD_UNKNOWN",
            "历史上传结果待核查且缺少可信主机身份；禁止新远端文件写，只读查询仍可用",
            409,
        )
        check(
            not self.stopping.is_set(),
            "FILE_ACTION_BUSY",
            "owner正在关闭，暂不接受文件写",
            409,
        )

    def busy(self, project, task, *, unresolved=False):
        flow = self._flow(project, task)
        return any(
            (a.get("kind") == "remote_file" or a.get("kind") == "hpc_upload")
            and a.get("state")
            in ({"executing", "unknown"} if unresolved else {"executing"})
            for a in (flow.get("consent") or {}).get("actions", {}).values()
        )

    def assert_mutable(self, project, task, *, unresolved=False):
        check(
            not self.busy(project, task, unresolved=unresolved),
            "FILE_ACTION_BUSY",
            "存在执行中或待核查文件动作；请先查看回执",
            409,
        )

    def set_roots(self, project, task, payload):
        exact(payload, {"expected_version", "roots"})
        h.integer(payload["expected_version"])
        check(isinstance(payload["roots"], list) and len(payload["roots"]) <= 8)
        with consent.task_lock(project, task), self.guard:
            flow = self._flow(project, task)
            check(
                flow.get("file_roots_version", 0) == payload["expected_version"],
                "ROOT_CHANGED",
                "根集合版本已变化",
                409,
            )
            old = {r["root_id"]: r for r in flow.get("file_roots", [])}
        result, seen = [], set()
        with self.remote() as files:
            for raw in payload["roots"]:
                exact(raw, {"root_id", "path"}, {"path"})
                root_id = raw.get("root_id") or uuid.uuid4().hex
                check(
                    root_id not in seen and (not raw.get("root_id") or root_id in old)
                )
                seen.add(root_id)
                observed = files.inspect_root(
                    raw["path"],
                    root_id=root_id,
                    version=old.get(root_id, {}).get("version", 0) + 1,
                )
                prior = old.get(root_id)
                if (
                    prior
                    and all(
                        observed[k] == prior[k]
                        for k in (
                            "requested_path",
                            "canonical_path",
                            "endpoint_digest",
                            "resolution_chain",
                        )
                    )
                    and h.identity_matches(observed["identity"], prior["identity"])
                    and len(observed["ancestors"]) == len(prior["ancestors"])
                    and all(
                        a["path"] == b["path"] and h.identity_matches(a, b)
                        for a, b in zip(observed["ancestors"], prior["ancestors"])
                    )
                ):
                    observed["version"] = prior["version"]
                check(
                    not any(
                        h.identity_matches(r["identity"], observed["identity"])
                        and r["endpoint_digest"] == observed["endpoint_digest"]
                        for r in result
                    ),
                    "ROOT_CHANGED",
                    "同一根目录不能重复登记别名",
                    409,
                )
                result.append({**observed, "selected_by_user_at": now()})
        with consent.task_lock(project, task), self.guard:

            def update(flow):
                check(
                    flow.get("file_roots_version", 0) == payload["expected_version"],
                    "ROOT_CHANGED",
                    "观察期间根集合已变化",
                    409,
                )
                flow["file_roots"], flow["file_roots_version"] = (
                    result,
                    payload["expected_version"] + 1,
                )
                current = {r["root_id"]: r for r in result}
                for scope in (
                    (flow.get("consent") or {}).get("computation_scopes", {}).values()
                ):
                    if scope.get("kind") == "file" and any(
                        b["root_id"] not in current
                        or current[b["root_id"]]["version"] != b["version"]
                        for b in scope["root_bindings"]
                    ):
                        self._revoke_in_flow(flow, scope, "用户可写根已变更")
                return {"version": flow["file_roots_version"], "roots": result}

            value = self._save(project, task, update)
            self._signal(project, task)
            return value

    def _observe(self, files, path):
        endpoint = files.endpoint()
        observed = files.inspect(path, "stat", expected_endpoint=endpoint)
        check(
            observed.get("endpoint_digest") == endpoint["endpoint_digest"],
            "ENDPOINT_CHANGED",
            "来源观察没有绑定当前端点身份",
            409,
        )
        check(
            observed.get("type") == "file",
            "CONTENT_READ_DENIED",
            "来源必须为普通文件",
            403,
        )
        with self.guard:
            provenance = evidence.provenance(self.snapshot(), endpoint, observed)
            generation = self.generation
        return observed, provenance, generation

    def inspect(self, project, task, args):
        self.service.require_task(project, task)
        exact(args, {"path", "view", "limit", "cursor"}, {"path", "view"})
        with self.remote() as files:
            if args["view"] != "text":
                return files.inspect(
                    args["path"],
                    args["view"],
                    limit=args.get("limit", 200),
                    cursor=args.get("cursor"),
                )
            observed, provenance, generation = self._observe(files, args["path"])
            value = files.inspect(
                args["path"], "text", provenance=provenance, expected_evidence=observed
            )
            with self.guard:
                check(
                    self.generation == generation,
                    "SOURCE_CHANGED",
                    "预览期间文件审计已变化，请重新查询",
                    409,
                )
            return value

    def create_scope(self, project, task, payload):
        fields = {
            "job_key",
            "attempt_id",
            "root_bindings",
            "allowed_operations",
            "source_paths",
            "max_operations",
            "max_total_bytes",
            "expires_at",
            "approval_mode",
        }
        exact(payload, fields)
        check(payload["approval_mode"] in {"human", "reviewer"})
        _remaining(payload["expires_at"])
        h.integer(payload["max_operations"], 1, 32)
        h.integer(payload["max_total_bytes"])
        check(
            isinstance(payload["allowed_operations"], list)
            and payload["allowed_operations"]
            and set(payload["allowed_operations"])
            <= {"copy", "symlink", "write_text", "mkdir"}
        )
        check(
            isinstance(payload["source_paths"], list)
            and len(payload["source_paths"]) <= 32
        )
        with consent.task_lock(project, task), self.guard:
            flow = self._flow(project, task)
            self._job(flow, payload)
            roots = {r["root_id"]: r for r in flow.get("file_roots", [])}
            bindings = copy.deepcopy(payload["root_bindings"])
            check(isinstance(bindings, list) and 0 < len(bindings) <= 8)
            for b in bindings:
                exact(b, {"root_id", "version", "destination_prefixes"})
                h.integer(b["version"], 1)
                check(
                    b["root_id"] in roots
                    and b["version"] == roots[b["root_id"]]["version"],
                    "ROOT_CHANGED",
                    "根版本不匹配",
                    409,
                )
                check(
                    isinstance(b["destination_prefixes"], list)
                    and 0 < len(b["destination_prefixes"]) <= 32
                )
                for prefix in b["destination_prefixes"]:
                    if prefix != "":
                        h.relative(prefix)
        with self.remote() as files:
            endpoint = files.endpoint()
            evidence.namespace(endpoint)
            check(
                all(
                    roots[b["root_id"]]["endpoint_digest"]
                    == endpoint["endpoint_digest"]
                    for b in bindings
                ),
                "ENDPOINT_CHANGED",
                "根不是当前连接身份",
                409,
            )
            sources = []
            generations = []
            for path in payload["source_paths"]:
                observed, provenance, generation = self._observe(files, path)
                generations.append(generation)
                observed["content_class"] = h.strict_class(
                    observed["content_class"],
                    provenance.get("content_class", "unclassified_external"),
                )
                sources.append(observed)
        scope_id = uuid.uuid4().hex
        scope = {k: copy.deepcopy(v) for k, v in payload.items() if k != "source_paths"}
        scope.update(
            scope_id=scope_id,
            kind="file",
            version=1,
            project_id=project,
            task_id=task,
            endpoint=endpoint,
            endpoint_digest=endpoint["endpoint_digest"],
            source_policy="exact_sources",
            source_bindings=sources,
            submit_limit=0,
            state="proposed",
            created_at=now(),
        )
        with consent.task_lock(project, task), self.guard:

            def update(flow):
                check(
                    all(g == self.generation for g in generations),
                    "SOURCE_CHANGED",
                    "来源分类观察期间审计变化，请重建范围",
                    409,
                )
                cons = flow.setdefault("consent", {})
                cons.setdefault("computation_scopes", {})[scope_id] = scope
                self._scope(flow, scope_id, 1)
                return scope

            return self._save(project, task, update)

    @staticmethod
    def _revoke_in_flow(flow, scope, reason):
        scope.update(state="revoked", revoked_at=now(), reason=str(reason)[:500])
        for action in (flow.get("consent") or {}).get("actions", {}).values():
            if (action.get("binding") or {}).get("scope_id") != scope["scope_id"]:
                continue
            private = action.get("_review_private")
            if private and private.get("status") == "issued":
                private.update(status="invalidated", invalidated_reason="SCOPE_REVOKED")
            if action["state"] in {"pending", "approved"}:
                action.update(state="expired", result=reason)
            elif action["state"] == "executing":
                action.setdefault("receipt", {}).update(
                    cancel_requested_at=now(), cancel_reason=reason
                )

    def _signal(self, project, task):
        worker = self.active.get((project, task))
        if worker:
            action = (
                (self._flow(project, task).get("consent") or {})
                .get("actions", {})
                .get(worker["action_id"], {})
            )
            if (action.get("receipt") or {}).get("cancel_requested_at"):
                worker["cancel"].set()
        self.wake.set()

    def revoke(self, project, task, scope_id, payload):
        exact(payload, {"expected_version", "reason"}, {"expected_version"})
        with consent.task_lock(project, task), self.guard:

            def update(flow):
                scope = (
                    (flow.get("consent") or {})
                    .get("computation_scopes", {})
                    .get(scope_id)
                )
                check(
                    scope
                    and scope.get("kind") == "file"
                    and scope["version"] == payload["expected_version"],
                    "SCOPE_STALE",
                    "范围版本不匹配",
                    409,
                )
                if scope["state"] != "revoked":
                    self._revoke_in_flow(
                        flow, scope, payload.get("reason") or "用户撤销文件范围"
                    )
                return scope

            value = self._save(project, task, update)
            self._signal(project, task)
            return value

    def activate(self, project, task, scope_id, payload):
        exact(payload, {"expected_version", "approval_mode"})
        check(payload["approval_mode"] == "reviewer", "SCOPE_STALE", "仅可显式启用独立 reviewer 范围", 409)
        with consent.task_lock(project, task), self.guard:
            def update(flow):
                scope = self._scope(flow, scope_id, payload["expected_version"])
                check(scope.get("approval_mode") == "reviewer", "SCOPE_STALE", "范围模式不匹配", 409)
                if scope["state"] == "proposed":
                    scope.update(state="active", activated_by="human", activated_at=now())
                return scope
            return self._save(project, task, update)

    def _dedup(self, flow, key, request_digest):
        for action in (flow.get("consent") or {}).get("actions", {}).values():
            binding = action.get("binding") or {}
            if (
                action.get("kind") == "remote_file"
                and binding.get("idempotency_key") == key
            ):
                check(
                    binding["request_digest"] == request_digest,
                    "IDEMPOTENCY_CONFLICT",
                    "同一幂等key对应不同文件请求",
                    409,
                )
                return action

    def plan(self, project, task, args):
        exact(
            args,
            {
                "scope_id",
                "scope_version",
                "job_key",
                "attempt_id",
                "idempotency_key",
                "items",
            },
        )
        key = args["idempotency_key"]
        check(isinstance(key, str) and 0 < len(key) <= 128)
        request_digest = h.digest(args)
        with consent.task_lock(project, task), self.guard:
            flow = self._flow(project, task)
            previous = self._dedup(flow, key, request_digest)
            if previous:
                return previous
            self._global_write()
            self._job(flow, args)
            scope = copy.deepcopy(
                self._scope(flow, args["scope_id"], args["scope_version"])
            )
            check(
                all(scope[k] == args[k] for k in ("job_key", "attempt_id")),
                "SCOPE_STALE",
                "范围计算身份不匹配",
                409,
            )
            roots = [
                r
                for r in flow["file_roots"]
                if r["root_id"] in {b["root_id"] for b in scope["root_bindings"]}
            ]
        items = args["items"]
        check(isinstance(items, list) and 0 < len(items) <= scope["max_operations"])
        bindings = {b["root_id"]: b for b in scope["root_bindings"]}
        owner_provenance, generations = {}, []
        with self.remote() as files:
            check(
                files.endpoint() == scope["endpoint"],
                "ENDPOINT_CHANGED",
                "范围端点配置已变化",
                409,
            )
            for item in items:
                check(
                    item.get("op") in scope["allowed_operations"],
                    "SCOPE_STALE",
                    "操作超出范围",
                    409,
                )
                dest = item.get("destination") or {}
                h.relative(dest.get("relative_path"))
                b = bindings.get(dest.get("root_id"))
                check(
                    b
                    and any(
                        p == "" or evidence.under(dest["relative_path"], p)
                        for p in b["destination_prefixes"]
                    ),
                    "PATH_OUTSIDE_ROOT",
                    "目标超出已批准前缀",
                    403,
                )
                if item["op"] in {"copy", "symlink"}:
                    observed, prov, generation = self._observe(
                        files, item["source"]["absolute_path"]
                    )
                    generations.append(generation)
                    check(
                        any(
                            all(
                                observed.get(k) == s.get(k)
                                for k in (
                                    "requested_path",
                                    "canonical_path",
                                    "endpoint_digest",
                                    "resolution_chain",
                                )
                            )
                            and h.identity_matches(observed, s, stable=True)
                            for s in scope["source_bindings"]
                        ),
                        "SOURCE_CHANGED",
                        "来源不属于范围或已变化",
                        409,
                    )
                    owner_provenance[item["item_id"]] = prov
            action_id = uuid.uuid4().hex
            manifest = files.plan(
                {
                    k: scope[k]
                    for k in (
                        "project_id",
                        "task_id",
                        "job_key",
                        "attempt_id",
                        "expires_at",
                    )
                }
                | {
                    "scope_id": scope["scope_id"],
                    "scope_version": scope["version"],
                    "action_id": action_id,
                    "source_provenance": owner_provenance,
                },
                roots,
                items,
                budgets={k: scope[k] for k in ("max_operations", "max_total_bytes")},
            )
        with consent.task_lock(project, task), self.guard:

            def update(flow):
                previous = self._dedup(flow, key, request_digest)
                if previous:
                    return previous
                check(
                    all(g == self.generation for g in generations),
                    "SOURCE_CHANGED",
                    "来源分类观察期间审计变化，请重新提案",
                    409,
                )
                self._scope(flow, args["scope_id"], args["scope_version"])
                self._global_write()
                self._conflicts(manifest)
                card = consent.card_payload(
                    tool="remote_file_plan",
                    args={},
                    risk=(
                        "high" if any(i["op"] == "symlink" for i in items) else "medium"
                    ),
                    reason="仅批准本manifest文件操作；文件完成不证明科学适用；链接可能回写源",
                    batch_key="",
                    kind="remote_file",
                    summary="人工精确文件计划",
                )
                card["action_id"] = card["card_id"] = action_id
                card["expires_at"] = min(
                    dt.datetime.fromisoformat(card["expires_at"]),
                    dt.datetime.fromisoformat(scope["expires_at"]),
                ).isoformat()
                binding = {
                    k: scope[k]
                    for k in (
                        "project_id",
                        "task_id",
                        "job_key",
                        "attempt_id",
                        "endpoint_digest",
                    )
                }
                binding.update(
                    action_id=action_id,
                    scope_id=scope["scope_id"],
                    scope_version=scope["version"],
                    manifest=manifest,
                    manifest_digest=manifest["manifest_digest"],
                    idempotency_key=key,
                    request_digest=request_digest,
                    policy_version=h.POLICY,
                    expires_at=card["expires_at"],
                )
                card.update(
                    binding=binding,
                    binding_hash=consent._binding_hash(binding),
                    receipt={"phase": "pending", "items": []},
                )
                flow.setdefault("consent", {}).setdefault("actions", {})[
                    action_id
                ] = card
                return card

            action = self._save(project, task, update)
            if action["action_id"] == action_id:
                current_scope = self._scope(self._flow(project, task), args["scope_id"], args["scope_version"])
                if current_scope.get("approval_mode") == "reviewer" and current_scope["state"] == "active":
                    action = self.reviewer.schedule(project, task, action_id)
            return action

    def _conflicts(self, manifest, *, exclude=None, protect_finished=False):
        held = list(
            evidence.occupied(
                self.snapshot(),
                manifest["endpoint"],
                exclude=exclude,
                protect_finished=protect_finished,
            )
        )
        for lease in self.leases.values():
            check(
                not lease.get("global"),
                "FILE_ACTION_BUSY",
                "旧目录操作正在执行，请稍后重试",
                409,
            )
            if evidence.namespace(lease["endpoint"]) == evidence.namespace(
                manifest["endpoint"]
            ):
                held += lease["targets"]
        check(
            not any(
                evidence.overlap(a, b) for a in evidence.targets(manifest) for b in held
            ),
            "DESTINATION_CONFLICT",
            "目标或其祖先有执行中/未知动作保留",
            409,
        )

    @staticmethod
    def _budget_available(flow, action, scope):
        manifest = action["binding"]["manifest"]
        used_ops = used_bytes = 0
        for other in flow["consent"]["actions"].values():
            if (other.get("binding") or {}).get("scope_id") != scope["scope_id"] or other is action:
                continue
            receipt = other.get("receipt") or {}
            for field in ("spent", "held_unknown", "reservation"):
                value = receipt.get(field) or {}
                if field == "reservation" and value.get("state") != "reserved":
                    continue
                used_ops += value.get("operations", 0)
                used_bytes += value.get("bytes", 0)
        amount = sum(item["bytes"] for item in manifest["items"])
        check(used_ops + len(manifest["items"]) <= scope["max_operations"]
              and used_bytes + amount <= scope["max_total_bytes"],
              "BUDGET_EXCEEDED", "范围剩余额度不足", 409)
        return amount

    def list(self, project, task, *, limit=20, cursor=None):
        h.integer(limit, 1, 100)
        values = [
            a
            for a in (self._flow(project, task).get("consent") or {})
            .get("actions", {})
            .values()
            if a.get("kind") == "remote_file"
        ]
        values.sort(
            key=lambda a: (a.get("created_at", ""), a["action_id"]), reverse=True
        )
        active_states = {"pending", "approved", "executing", "unknown"}
        active = [a for a in values if a.get("state") in active_states]
        values = [a for a in values if a.get("state") not in active_states]
        offset = int(cursor or 0)
        check(offset >= 0)
        return {
            "active": [summary(a) for a in active],
            "actions": [summary(a) for a in values[offset : offset + limit]],
            "next_cursor": (
                str(offset + limit) if offset + limit < len(values) else None
            ),
        }

    def approve(
        self, project, task, action_id, approved, note="", scope_confirmation=None
    ):
        with consent.task_lock(project, task), self.guard:
            current = (
                (self._flow(project, task).get("consent") or {})
                .get("actions", {})
                .get(action_id)
            )
            check(
                current and current.get("kind") == "remote_file",
                "CARD_NOT_FOUND",
                "文件卡不存在",
                404,
            )
            # Replays only return the durable result. In particular, they must
            # neither requeue approved work nor write after shutdown begins.
            if current["state"] != "pending":
                return copy.deepcopy(current)

            def update(flow):
                return self._decide_in_flow(flow, action_id, approved, note,
                                            scope_confirmation, reviewer=False)

            action = self._save(project, task, update)
            if action["state"] == "approved":
                self._enqueue(project, task, action_id)
            return action

    def _enqueue(self, project, task, action_id):
        key = (project, task, action_id)
        if key not in self.queue and self.active.get((project, task), {}).get("action_id") != action_id:
            self.queue.append(key)
        self.wake.set()

    def _decide_in_flow(self, flow, action_id, approved, note, scope_confirmation,
                        *, reviewer=False):
        """One store mutation owns both the decision and reviewer consumption."""
        action = (flow.get("consent") or {}).get("actions", {}).get(action_id)
        check(action and action.get("kind") == "remote_file", "CARD_NOT_FOUND", "文件卡不存在", 404)
        if action["state"] != "pending":
            return action
        check(consent._valid_binding(action), "SCOPE_STALE", "卡绑定校验失败", 409)
        check(not consent._expired(action), "SCOPE_EXPIRED", "文件卡已过期", 409)
        b = action["binding"]
        scope = None
        if approved or reviewer:
            scope = self._scope(flow, b["scope_id"], b["scope_version"])
            if reviewer:
                check(scope.get("approval_mode") == "reviewer" and scope["state"] == "active",
                      "SCOPE_STALE", "reviewer范围未激活", 409)
            elif scope.get("approval_mode", "human") == "reviewer" and scope["state"] == "proposed":
                check(False, "SCOPE_STALE", "reviewer范围须单独激活", 409)
        if approved:
            self._global_write()
            queued_count = sum(a.get("kind") == "remote_file" and a.get("state") == "approved"
                               for _, a in evidence.actions(self.snapshot()))
            check(queued_count < self.queue_limit, "FILE_QUEUE_FULL", "文件队列已满，尚未批准", 503)
            if scope["state"] == "proposed":
                check(scope_confirmation == {"scope_id": scope["scope_id"], "version": scope["version"]},
                      "SCOPE_STALE", "首次批准须确认所展示的范围版本", 409)
                scope.update(state="active", activated_at=now())
            action["state"] = "approved"
            action["receipt"]["phase"] = "queued"
        else:
            action["state"] = "rejected"
        if not reviewer:
            private = action.get("_review_private")
            if private and private.get("status") == "issued":
                private.update(status="invalidated", invalidated_reason="HUMAN_DECISION")
            if action.get("review"):
                action["review"].update(state="needs_human", decision=None,
                    reason_code="REVIEWER_NEEDS_HUMAN", reason="用户已人工决议。",
                    finished_at=now(), decided_by="human")
        action.update(resolved_at=now(), note=str(note)[:500])
        return action

    def _claim(self, project, task, action_id):
        # task -> file guard -> store: scan, budget reservation and durable
        # executing transition are one critical section across all tasks.
        with consent.task_lock(project, task), self.guard:
            if (project, task) in self.active:
                return None
            self._global_write()

            def update(flow):
                action = (flow.get("consent") or {}).get("actions", {}).get(action_id)
                if not action or action["state"] != "approved":
                    return None
                b = action["binding"]
                manifest = b["manifest"]
                check(
                    consent._valid_binding(action) and not consent._expired(action),
                    "SCOPE_EXPIRED",
                    "文件卡过期或绑定变化",
                    409,
                )
                scope = self._scope(
                    flow, b["scope_id"], b["scope_version"], active=True
                )
                check(
                    not any(
                        a.get("state") == "executing"
                        for a in flow["consent"]["actions"].values()
                    ),
                    "FILE_ACTION_BUSY",
                    "任务存在其他执行动作",
                    409,
                )
                self._conflicts(manifest, exclude=action_id)
                amount = self._budget_available(flow, action, scope)
                action.update(
                    state="executing",
                    executing_at=now(),
                    owner_run_id=self.owner_id,
                    dispatch_nonce=uuid.uuid4().hex,
                )
                action["receipt"].update(
                    phase="connecting",
                    reservation={
                        "operations": len(manifest["items"]),
                        "bytes": amount,
                        "state": "reserved",
                    },
                    dispatches=[],
                )
                return action

            action = self._save(project, task, update)
            if action:
                self.active[(project, task)] = {
                    "action_id": action_id,
                    "cancel": threading.Event(),
                    "session": None,
                    "thread": threading.current_thread(),
                }
            return action

    def _loop(self):
        while not self.stopping.is_set():
            key = None
            with self.guard:
                for candidate in self.queue:
                    if candidate[:2] not in self.active:
                        key = candidate
                        self.queue.remove(candidate)
                        break
                self.wake.clear()
            if key is None:
                self.wake.wait(0.1)
                continue
            try:
                action = self._claim(*key)
                if action:
                    self._run(*key, action)
                else:
                    with self.guard:
                        current = (
                            (self._flow(*key[:2]).get("consent") or {})
                            .get("actions", {})
                            .get(key[2], {})
                        )
                        if current.get("state") == "approved" and key not in self.queue:
                            self.queue.append(key)
            except Exception as exc:
                self._finish(*key, error=exc)
            finally:
                with self.guard:
                    if self.active.get(key[:2], {}).get("action_id") == key[2]:
                        self.active.pop(key[:2], None)
                    self.wake.set()

    def _update_action(self, project, task, action_id, change):
        with consent.task_lock(project, task), self.guard:

            def update(flow):
                action = flow["consent"]["actions"][action_id]
                return change(action)

            result = self._save(project, task, update)
            action = (
                (self._flow(project, task).get("consent") or {})
                .get("actions", {})
                .get(action_id, {})
            )
            self.store.append_event(
                project,
                task,
                "file.action",
                action_id
                + ": "
                + action.get("state", "")
                + "/"
                + (action.get("receipt") or {}).get("phase", ""),
            )
            return result

    @contextmanager
    def _dispatch(self, project, task, action_id, stage, item_id):
        # Hold locks through bounded send only; the adapter receives outside
        # this context. A durable marker can mean sent even after a crash.
        with self.service._guard, consent.task_lock(project, task), self.guard:
            self._global_write()

            def update(flow):
                action = flow["consent"]["actions"][action_id]
                check(
                    action["state"] == "executing",
                    "ACTION_UNKNOWN",
                    "文件动作不再执行",
                    409,
                )
                b = action["binding"]
                self._scope(flow, b["scope_id"], b["scope_version"], active=True)
                if self.factory is None:
                    from .scheduler_profile import target_binding

                    target = target_binding(self.service.settings_loader())
                    local_config = {
                        k: target.pop(k) or None
                        for k in ("identity_file", "known_hosts_path")
                    }
                    endpoint = b["manifest"]["endpoint"]
                    check(
                        target == endpoint["scheduler_target"]
                        and h.digest(local_config) == endpoint["local_config_digest"],
                        "ENDPOINT_CHANGED",
                        "运行期间连接或调度配置变化",
                        409,
                    )
                check(
                    not action["receipt"].get("cancel_requested_at"),
                    "SCOPE_REVOKED",
                    "文件范围已撤销",
                    403,
                )
                check(
                    consent._valid_binding(action), "SCOPE_STALE", "动作绑定已变更", 409
                )
                self._conflicts(b["manifest"], exclude=action_id)
                action["receipt"]["phase"] = (
                    "committing" if stage == "commit" else "connecting"
                )
                action["receipt"].setdefault("dispatches", []).append(
                    {
                        "stage": stage,
                        "item_id": item_id,
                        "commit_sent_at": now(),
                        "nonce": uuid.uuid4().hex,
                    }
                )
                return action

            self._save(project, task, update)
            yield

    def _run(self, project, task, action_id, action):
        b = action["binding"]
        manifest = b["manifest"]
        worker = self.active[(project, task)]
        session = None
        prepare_entered = set()
        try:
            with self.remote() as files:
                fields = (
                    "action_id",
                    "manifest_digest",
                    "project_id",
                    "task_id",
                    "job_key",
                    "attempt_id",
                    "scope_id",
                    "scope_version",
                )
                dispatch = {k: manifest[k] for k in fields}
                dispatch.update(
                    binding_hash=action["binding_hash"],
                    dispatch_nonce=action["dispatch_nonce"],
                )
                session = files.begin(
                    manifest,
                    dispatch,
                    dispatch_guard=lambda stage, item: self._dispatch(
                        project, task, action_id, stage, item
                    ),
                )
                worker["session"] = session
                for item in manifest["items"]:
                    check(
                        not worker["cancel"].is_set(),
                        "SCOPE_REVOKED",
                        "文件动作已取消",
                        403,
                    )
                    self._update_action(
                        project,
                        task,
                        action_id,
                        lambda a: a["receipt"].update(phase="preparing"),
                    )
                    prepare_entered.add(item["item_id"])
                    prepared = session.prepare_next(
                        should_cancel=worker["cancel"].is_set
                    )
                    # Preserve the temporary inode too, including its canonical path.
                    root = next(
                        r
                        for r in manifest["roots"]
                        if r["root_id"] == item["destination"]["root_id"]
                    )
                    if prepared.get("temporary_evidence"):
                        prepared["temporary_evidence"]["canonical_path"] = (
                            posixpath.join(
                                root["canonical_path"],
                                posixpath.dirname(item["destination"]["relative_path"]),
                                item["names"]["staging_name"],
                            )
                        )
                    self._update_action(
                        project,
                        task,
                        action_id,
                        lambda a: self._item(a, prepared, "prepared"),
                    )
                    check(
                        not worker["cancel"].is_set(),
                        "SCOPE_REVOKED",
                        "文件动作已取消",
                        403,
                    )
                    expiry = min(
                        dt.datetime.fromisoformat(manifest["expires_at"]),
                        dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=20),
                    )
                    permit = {
                        k: manifest[k]
                        for k in (
                            "action_id",
                            "manifest_digest",
                            "job_key",
                            "attempt_id",
                            "scope_id",
                            "scope_version",
                        )
                    }
                    permit.update(
                        item_id=item["item_id"],
                        endpoint_digest=manifest["endpoint"]["endpoint_digest"],
                        root_id=item["destination"]["root_id"],
                        root_version=item["destination"]["root_version"],
                        prepared_digest=prepared["prepared_digest"],
                        prepare_token=prepared["prepare_token"],
                        valid_until=expiry.isoformat(),
                    )
                    committed = session.commit(permit)
                    self._update_action(
                        project,
                        task,
                        action_id,
                        lambda a: self._item(a, committed, "committed"),
                    )
            self._finish(project, task, action_id)
        except Exception as exc:
            leftovers = []
            if session is not None:
                try:
                    leftovers = session.abort().get("leftovers", [])
                except Exception:
                    pass
            self._finish(
                project,
                task,
                action_id,
                error=exc,
                leftovers=leftovers,
                unstarted={item["item_id"] for item in manifest["items"]}
                - prepare_entered,
            )
        finally:
            if session is not None:
                session.close()

    @staticmethod
    def _item(action, receipt, phase):
        rows = action["receipt"]["items"]
        rows[:] = [r for r in rows if r["item_id"] != receipt["item_id"]]
        rows.append(copy.deepcopy(receipt))
        action["receipt"]["phase"] = phase

    def _finish(
        self, project, task, action_id, error=None, leftovers=None, unstarted=None
    ):
        def update(action):
            if action["state"] not in {"executing", "approved"}:
                return action
            receipt = action["receipt"]
            manifest = action["binding"]["manifest"]
            committed = {
                r["item_id"] for r in receipt["items"] if r.get("state") == "committed"
            }
            no_publication = (
                isinstance(error, RemoteFileError)
                and error.published is False
                and error.code != "ACTION_UNKNOWN"
            )
            uncertain = bool(
                error
                and not no_publication
                and (
                    getattr(error, "code", "") == "ACTION_UNKNOWN"
                    or getattr(error, "published", False) is True
                    or getattr(error, "published", False) == "unknown"
                    or any(
                        d["stage"] == "commit" and d["item_id"] not in committed
                        for d in receipt.get("dispatches", [])
                    )
                )
            )
            action["state"] = (
                "unknown" if uncertain else "failed" if error else "executed"
            )
            action["result"] = (
                str(file_error(error)) if error else "文件准备完成；尚未证明科学适用"
            )
            action["finished_at"] = now()
            receipt["phase"] = "finished"
            receipt["leftovers"] = list(
                dict.fromkeys((leftovers or []) + getattr(error, "leftovers", []))
            )
            receipt["error"] = file_error(error).payload() if error else None
            # Only a live sequential worker can attest that it never entered
            # prepare for a later item. Recovery never infers this from absence.
            all_items = manifest["items"]
            done = [i for i in all_items if i["item_id"] in committed]
            rest = [i for i in all_items if i["item_id"] not in committed]
            outcomes = receipt.setdefault("item_outcomes", {})
            for item in rest:
                if not uncertain or item["item_id"] in (unstarted or set()):
                    outcomes[item["item_id"]] = {
                        "state": "not_executed",
                        "evidence": (
                            "worker_stopped_before_prepare"
                            if item["item_id"] in (unstarted or set())
                            else "no_publication_confirmed"
                        ),
                    }
            released = [
                i
                for i in rest
                if outcomes.get(i["item_id"], {}).get("state") == "not_executed"
            ]
            held = [i for i in rest if i not in released]

            def amount(items):
                return {
                    "operations": len(items),
                    "bytes": sum(i["bytes"] for i in items),
                }

            receipt["spent"] = amount(done)
            receipt["held_unknown"] = amount(held)
            receipt["released"] = amount(released)
            receipt.setdefault("reservation", {})["state"] = "settled"
            return action

        try:
            self._update_action(project, task, action_id, update)
        except Exception:
            # A failed local save never permits another remote write. Recovery
            # sees the last durable executing record and retains its targets.
            self.stopping.set()

    def reconcile(self, project, task, action_id):
        with consent.task_lock(project, task), self.guard:
            action = copy.deepcopy(
                (self._flow(project, task).get("consent") or {})
                .get("actions", {})
                .get(action_id)
            )
            check(
                action and action.get("kind") == "remote_file",
                "CARD_NOT_FOUND",
                "文件动作不存在",
                404,
            )
            check(
                action["state"] == "unknown",
                "FILE_ACTION_BUSY",
                "只核对结果未知的动作",
                409,
            )
            binding_hash = action["binding_hash"]
            manifest = action["binding"]["manifest"]
        recovered = []
        released_ids = evidence.not_executed(action)
        with self.remote() as files:
            for item in manifest["items"]:
                if item["item_id"] in released_ids:
                    continue
                result = files.reconcile(manifest, item_id=item["item_id"])
                rows = result.get("items", [])
                check(
                    len(rows) == 1 and rows[0].get("item_id") == item["item_id"],
                    "ACTION_UNKNOWN",
                    "核对响应与文件条目不一致，保留未知结果",
                    409,
                )
                row = rows[0]
                if row.get("state") == "confirmed":
                    record = row.get("receipt") or {}
                    receipt_id = (
                        item["names"]["action_directory"]
                        + "/"
                        + item["names"]["committed_name"]
                    )
                    # B verifies remote bytes/identity; C validates the receipt
                    # belongs to the exact action before releasing its budget.
                    check(
                        record.get("action_id") == manifest["action_id"]
                        and record.get("manifest_digest") == manifest["manifest_digest"]
                        and record.get("item_id") == item["item_id"]
                        and record.get("state") == "committed"
                        and type(record.get("bytes_processed")) is int
                        and record["bytes_processed"] == item["bytes"]
                        and record.get("content_class") == item["content_class"]
                        and record.get("remote_receipt_id") == receipt_id
                        and all(
                            row.get(key) == record.get(key)
                            for key in ("remote_receipt_id", "content_class", "sha256")
                        ),
                        "ACTION_UNKNOWN",
                        "核对回执与批准动作不一致，保留未知结果和预算",
                        409,
                    )
                    recovered.append(record)

        def update(current):
            check(
                current["state"] == "unknown"
                and current["binding_hash"] == binding_hash,
                "SCOPE_STALE",
                "核对期间动作变化",
                409,
            )
            for receipt in recovered:
                self._item(current, receipt, "reconciled")
            committed = {
                r["item_id"]
                for r in current["receipt"]["items"]
                if r.get("state") == "committed"
            }
            done = [i for i in manifest["items"] if i["item_id"] in committed]
            released = [i for i in manifest["items"] if i["item_id"] in released_ids]
            rest = [
                i
                for i in manifest["items"]
                if i["item_id"] not in committed | released_ids
            ]
            current["receipt"]["spent"] = {
                "operations": len(done),
                "bytes": sum(i["bytes"] for i in done),
            }
            current["receipt"]["held_unknown"] = {
                "operations": len(rest),
                "bytes": sum(i["bytes"] for i in rest),
            }
            current["receipt"]["released"] = {
                "operations": len(released),
                "bytes": sum(i["bytes"] for i in released),
            }
            current["receipt"].setdefault("reservation", {})["state"] = "settled"
            if not rest:
                current.update(
                    state="failed" if released else "executed",
                    result=(
                        "只读核对确认已发布项目；其余已确认未执行，未重放"
                        if released
                        else "只读回执核对确认文件完成；未重新执行"
                    ),
                    finished_at=now(),
                )
            return current

        return self._update_action(project, task, action_id, update)

    def wait_idle(self, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.guard:
                if not self.active and not self.queue:
                    return True
            time.sleep(0.01)
        return False

    def has_audit(self):
        return any(
            a.get("kind") == "remote_file" for _, a in evidence.actions(self.snapshot())
        )

    def legacy_identity(self, root_path, relative_path, *, files=None):
        with self.guard:
            self._global_write()
        with self.remote() if files is None else nullcontext(files) as files:
            endpoint = files.endpoint()
            evidence.namespace(endpoint)
            root = files.inspect_root(root_path, root_id="legacy", version=1)
            destination = files._read(
                {"op": "destination", "root": root, "path": relative_path},
                expected_endpoint=endpoint,
            )
            manifest = {
                "endpoint": endpoint,
                "roots": [root],
                "items": [{"item_id": "legacy", "destination": destination}],
            }
            return {
                "endpoint": endpoint,
                "root": root,
                "destination": destination,
                "targets": evidence.targets(manifest),
            }

    def _legacy_files(self, hpc, cfg):
        # Observe the very manager that will write, not a second connection
        # created from possibly newer settings. Fake adapters are explicit too.
        if self.factory:
            files = self.factory()
            check(
                getattr(files, "manager", None) is hpc
                or getattr(files, "hpc", None) is hpc,
                "ENDPOINT_CHANGED",
                "文件观察与实际写入连接不一致",
                409,
            )
        else:
            from .scheduler_profile import target_binding

            target = target_binding(cfg)
            target["identity_file"] = target["identity_file"] or None
            target["known_hosts_path"] = target["known_hosts_path"] or None
            files = RemoteFiles(hpc, scheduler_target=target)
        return files

    @contextmanager
    def legacy_upload(self, project, task, binding, *, hpc, cfg):
        identity = binding.get("file_identity")
        check(identity, "ENDPOINT_CHANGED", "旧上传缺少可信主机身份，请重新提案", 409)
        files = self._legacy_files(hpc, cfg)
        actual = self.legacy_identity(
            binding["remote_root"], binding["remote_relative_path"], files=files
        )
        check(
            actual["endpoint"] == identity["endpoint"]
            and actual["root"]["canonical_path"] == identity["root"]["canonical_path"]
            and h.identity_matches(
                actual["root"]["identity"], identity["root"]["identity"]
            )
            and actual["targets"] == identity["targets"],
            "ROOT_CHANGED",
            "上传批准后目标或主机身份变化",
            409,
        )
        # Compare target snapshot too; the old upload is explicitly a replace.
        check(
            actual["destination"]["target_exists"]
            == identity["destination"]["target_exists"],
            "SOURCE_CHANGED",
            "上传目标在批准后变化",
            409,
        )
        lease_id = uuid.uuid4().hex
        with consent.task_lock(project, task), self.guard:
            self._global_write()
            held = list(
                evidence.occupied(
                    self.snapshot(),
                    actual["endpoint"],
                    protect_finished=True,
                    exclude=binding["action_id"],
                )
            )
            for lease in self.leases.values():
                check(
                    not lease.get("global"),
                    "FILE_ACTION_BUSY",
                    "旧目录操作正在执行",
                    409,
                )
                if evidence.namespace(lease["endpoint"]) == evidence.namespace(
                    actual["endpoint"]
                ):
                    held += lease["targets"]
            check(
                not any(
                    evidence.overlap(a, b) for a in actual["targets"] for b in held
                ),
                "DESTINATION_CONFLICT",
                "旧上传与文件动作或已登记产物冲突，请使用新文件计划",
                409,
            )
            self.leases[lease_id] = actual

        def before_write():
            with consent.task_lock(project, task), self.guard:
                self._global_write()
                self._update_action(
                    project,
                    task,
                    binding["action_id"],
                    lambda a: a.update(file_dispatch_at=now()),
                )

        try:
            yield before_write
        finally:
            with self.guard:
                self.leases.pop(lease_id, None)

    @contextmanager
    def legacy_mkdir(self, root_path, name, *, hpc, cfg):
        lease_id = uuid.uuid4().hex
        with self.guard:
            self._global_write()
            needs_guard = (
                self.has_audit()
                or bool(self.leases)
                or any(
                    a.get("kind") == "hpc_upload"
                    and a.get("state") in {"executing", "unknown"}
                    for _, a in evidence.actions(self.snapshot())
                )
            )
            if not needs_guard:
                self.leases[lease_id] = {"global": True}
        if not needs_guard:
            try:
                yield
            finally:
                with self.guard:
                    self.leases.pop(lease_id, None)
            return
        actual = self.legacy_identity(
            root_path, name, files=self._legacy_files(hpc, cfg)
        )
        with self.guard:
            self._global_write()
            check(
                not any(l.get("global") for l in self.leases.values()),
                "FILE_ACTION_BUSY",
                "其他旧目录操作正在执行",
                409,
            )
            snapshot = self.snapshot()
            held = list(
                evidence.occupied(snapshot, actual["endpoint"], protect_finished=True)
            )
            for lease in self.leases.values():
                if evidence.namespace(lease["endpoint"]) == evidence.namespace(
                    actual["endpoint"]
                ):
                    held.extend(lease["targets"])
            check(
                not any(
                    evidence.overlap(target, reserved)
                    for target in actual["targets"]
                    for reserved in held
                ),
                "DESTINATION_CONFLICT",
                "旧建目录入口与未决上传或活动文件目标交叠，请先核查原动作",
                409,
            )
            for _, action in evidence.actions(snapshot):
                manifest = (action.get("binding") or {}).get("manifest") or {}
                if (
                    action.get("kind") != "remote_file"
                    or not manifest
                    or evidence.namespace(manifest["endpoint"])
                    != evidence.namespace(actual["endpoint"])
                ):
                    continue
                for root in manifest["roots"]:
                    root_target = {
                        "path": root["canonical_path"],
                        "anchors": [
                            (root["identity"]["device"], root["identity"]["inode"], "")
                        ],
                    }
                    check(
                        not any(
                            evidence.overlap(t, root_target) for t in actual["targets"]
                        ),
                        "DESTINATION_CONFLICT",
                        "旧建目录入口与C文件审计根交叠，请改用精确文件计划",
                        409,
                    )
            self.leases[lease_id] = actual
        try:
            yield
        finally:
            with self.guard:
                self.leases.pop(lease_id, None)

    def close(self):
        with self.guard:
            if self.closed:
                return
        if self.reviewer:
            self.reviewer.close()
        self.stopping.set()
        self.wake.set()
        with self.guard:
            queued = list(self.queue)
            self.queue.clear()
            for worker in self.active.values():
                worker["cancel"].set()
        for project, task, action_id in queued:
            self._update_action(
                project,
                task,
                action_id,
                lambda a: (
                    a.update(state="expired", result="owner关闭；未恢复队列")
                    if a["state"] == "approved"
                    else None
                ),
            )
        deadline = time.monotonic() + 2.5
        for thread in self.threads:
            thread.join(max(0, deadline - time.monotonic()))
        with self.guard:
            sessions = [w["session"] for w in self.active.values() if w.get("session")]
        for session in sessions:
            session.close()
        for thread in self.threads:
            thread.join(3)
        check(
            not any(t.is_alive() for t in self.threads),
            "FILE_ACTION_BUSY",
            "文件worker未退出；保留owner锁，防止第二owner接管",
            409,
        )
        # A worker that had already popped an item may have requeued it while
        # shutdown drained the first snapshot. All threads have now stopped.
        with self.guard:
            queued = list(self.queue)
            self.queue.clear()
        for project, task, action_id in queued:
            self._update_action(
                project,
                task,
                action_id,
                lambda a: (
                    a.update(state="expired", result="owner关闭；未恢复队列")
                    if a["state"] == "approved"
                    else None
                ),
            )
        # Atomically seal every C persistence gate before ProcessOwner release.
        # A request still awaiting remote observation can return, but cannot save.
        with self.guard:
            self.closed = True
