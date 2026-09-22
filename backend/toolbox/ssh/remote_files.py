"""Owner-only fixed helper transport; intentionally not registered as a tool/API."""
from __future__ import annotations

import copy
import datetime
import importlib.resources
import json
import posixpath
import shlex
import time
import uuid
from contextlib import nullcontext

from . import file_helper as protocol
from .errors import SSHError


class RemoteFileError(SSHError):
    def __init__(self, code, message, *, stage="validate", dispatched=False, published=False, leftovers=None):
        super().__init__(message)
        self.code, self.stage, self.dispatched, self.published = code, stage, dispatched, published
        self.retryable = False
        self.leftovers = leftovers or []


def _local_error(exc):
    if isinstance(exc, RemoteFileError):
        return exc
    if isinstance(exc, protocol.FileError):
        return RemoteFileError(exc.code, str(exc), stage=exc.stage, published=exc.published, leftovers=exc.leftovers)
    return RemoteFileError("INVALID_FILE_REQUEST", "Invalid file request")


def helper_command():
    """The only shell string: product source only, never request data."""
    source = importlib.resources.files(__package__).joinpath("file_helper.py").read_text(encoding="utf-8")
    return "python3 -I -u -c " + shlex.quote(source)


def _remaining(expires_at):
    try:
        expiry = datetime.datetime.fromisoformat(expires_at)
        if expiry.tzinfo is None:
            raise ValueError()
        remaining = (expiry - datetime.datetime.now(datetime.timezone.utc)).total_seconds()
        if not 0 < remaining <= 86400:
            raise ValueError()
        return remaining
    except (ValueError, TypeError):
        raise RemoteFileError("SCOPE_EXPIRED", "Owner file permission is expired or outside the supported lifetime")


class _Wire:
    def __init__(self, manager, *, expected_endpoint=None, scheduler_target=None):
        self.manager, self.expected_endpoint, self.scheduler_target = manager, expected_endpoint, scheduler_target
        self.channel = None
        self.buffer = bytearray()
        self.stderr_bytes = 0
        self.dispatched = False
        try:
            self.transport = manager.connect().get_transport()
            self.channel = self.transport.open_session(timeout=manager.connect_timeout)
            self.channel.settimeout(0.2)
            self.channel.exec_command(helper_command())
            self.check_endpoint()
        except RemoteFileError:
            self.close()
            raise
        except Exception:
            self.close()
            raise RemoteFileError("REMOTE_CAPABILITY_UNAVAILABLE", "Fixed helper could not be started", stage="connect")

    def check_endpoint(self):
        if self.expected_endpoint is None:
            return
        try:
            same = (self.manager.connected and self.manager._client.get_transport() is self.transport
                    and self.manager.file_endpoint(self.scheduler_target, connect=False) == self.expected_endpoint)
        except Exception:
            same = False
        if not same:
            raise RemoteFileError("ENDPOINT_CHANGED", "Channel endpoint differs from observed identity", stage="dispatch",
                                  dispatched=self.dispatched, published="unknown" if self.dispatched else False)

    def send(self, request):
        self.check_endpoint()
        data = protocol.canonical(request) + b"\n"
        if len(data) > protocol.MAX_FRAME:
            raise RemoteFileError("INVALID_FILE_REQUEST", "Request frame exceeds byte limit")
        # An exception from send may still mean a prefix/full frame was sent.
        self.dispatched = True
        deadline = time.monotonic() + 0.2
        if not callable(getattr(self.channel, "send", None)):
            self.channel.sendall(data)  # Minimal in-memory channel fixtures only.
            return
        while data:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("File control send deadline")
            self.channel.settimeout(remaining)
            count = self.channel.send(data)
            if not isinstance(count, int) or count <= 0:
                raise EOFError()
            data = data[count:]
        self.channel.settimeout(0.2)

    def receive(self, *, timeout=60, writing=False, should_cancel=None):
        deadline = time.monotonic() + timeout
        aborted = False
        try:
            while b"\n" not in self.buffer:
                if should_cancel and should_cancel() and not aborted:
                    self.send({"op": "abort"})
                    aborted = True
                    deadline = min(deadline, time.monotonic() + 2)
                if time.monotonic() >= deadline:
                    raise TimeoutError()
                while self.channel.recv_stderr_ready():
                    self.stderr_bytes += len(self.channel.recv_stderr(4096))
                    if self.stderr_bytes > 8192:
                        raise ValueError("helper stderr limit")
                if self.channel.recv_ready():
                    data = self.channel.recv(4096)
                    if not data:
                        raise EOFError()
                    self.buffer.extend(data)
                    if len(self.buffer) > protocol.MAX_REPLY + 1:
                        raise ValueError("helper reply limit")
                elif self.channel.exit_status_ready():
                    raise EOFError()
                else:
                    time.sleep(0.01)
            raw, _, rest = self.buffer.partition(b"\n")
            self.buffer = bytearray(rest)
            response = json.loads(raw.decode("utf-8"))
            if not isinstance(response, dict) or type(response.get("ok")) is not bool:
                raise ValueError("helper envelope")
            if not response["ok"]:
                error = response.get("error") or {}
                raise RemoteFileError(error.get("code", "PROTOCOL_ERROR"), str(error.get("message", "Helper failed")),
                                      stage=error.get("stage", "helper"), dispatched=self.dispatched,
                                      published=error.get("published", "unknown" if writing else False), leftovers=error.get("leftovers"))
            if set(response) != {"ok", "data"} or not isinstance(response["data"], dict):
                raise ValueError("helper response")
            if aborted:
                data = response["data"]
                if data.get("state") == "prepared":
                    data = self.receive(timeout=max(0, deadline - time.monotonic()), writing=True)
                raise RemoteFileError("ACTION_ABORTED", "Owner cancelled preparation", stage="prepare", dispatched=True,
                                      published=False, leftovers=data.get("leftovers", []))
            return response["data"]
        except RemoteFileError:
            raise
        except Exception:
            raise RemoteFileError("ACTION_UNKNOWN" if writing else "PROTOCOL_ERROR",
                                  "Helper reply is unavailable, malformed, truncated or late; no write replay",
                                  stage="receive", dispatched=self.dispatched, published="unknown" if writing else False)

    def call(self, request, *, timeout=60, writing=False, should_cancel=None, dispatch_guard=None, request_factory=None):
        try:
            with dispatch_guard() if dispatch_guard else nullcontext():
                self.send(request_factory() if request_factory else request)
            return self.receive(timeout=timeout, writing=writing, should_cancel=should_cancel)
        except RemoteFileError:
            raise
        except Exception:
            raise RemoteFileError("ACTION_UNKNOWN" if writing and self.dispatched else "REMOTE_CAPABILITY_UNAVAILABLE",
                                  "Helper dispatch failed; no write replay", stage="dispatch", dispatched=self.dispatched,
                                  published="unknown" if writing and self.dispatched else False)

    def close(self):
        if self.channel is not None:
            self.channel.close()
            self.channel = None


class RemoteFiles:
    def __init__(self, manager, *, scheduler_target):
        self.manager = manager
        self.scheduler_target = copy.deepcopy(scheduler_target)

    def endpoint(self):
        return self.manager.file_endpoint(self.scheduler_target)

    def _read(self, request, *, expected_endpoint=None):
        endpoint = self.endpoint()
        if expected_endpoint is not None and endpoint != expected_endpoint:
            raise RemoteFileError("ENDPOINT_CHANGED", "SSH endpoint changed")
        wire = _Wire(self.manager, expected_endpoint=endpoint, scheduler_target=self.scheduler_target)
        try:
            result = wire.call(request)
            wire.check_endpoint()
            return result
        finally:
            wire.close()

    def probe(self):
        return self._read({"op": "probe"})

    def inspect_root(self, path, *, root_id=None, version=1):
        try:
            protocol.absolute(path)
            protocol.integer(version, 1)
            key = root_id or uuid.uuid4().hex
            protocol.require(isinstance(key, str) and 0 < len(key) <= 128)
            endpoint = self.endpoint()
            return {**self._read({"op": "root", "path": path}, expected_endpoint=endpoint),
                    "root_id": key, "version": version, "endpoint_digest": endpoint["endpoint_digest"]}
        except (protocol.FileError, ValueError, TypeError) as exc:
            raise _local_error(exc)

    def inspect(self, path, view="stat", *, provenance=None, limit=200, cursor=None, expected_evidence=None, expected_endpoint=None):
        try:
            protocol.absolute(path)
            protocol.require(view in {"stat", "list", "text"})
            protocol.integer(limit, 1, 500)
            endpoint = self.endpoint()
            if expected_endpoint is not None and endpoint != expected_endpoint:
                raise RemoteFileError('ENDPOINT_CHANGED', 'Inspection endpoint changed')
            result = self._read({"op": "inspect", "path": path, "view": view,
                               "provenance": provenance, "endpoint_digest": endpoint["endpoint_digest"],
                               "limit": limit, "cursor": cursor, "expected_evidence": expected_evidence}, expected_endpoint=endpoint)
            return {**result, 'endpoint_digest': endpoint['endpoint_digest']}
        except (protocol.FileError, ValueError, TypeError) as exc:
            raise _local_error(exc)

    def plan(self, context, roots, items, *, budgets):
        """Observe exact items. No directory, receipt, temporary file or target writes."""
        try:
            allowed_context = {"action_id", "project_id", "task_id", "job_key", "attempt_id", "scope_id", "scope_version", "expires_at", "source_provenance"}
            protocol.require(isinstance(context, dict) and set(context) <= allowed_context)
            protocol.require(set(budgets) == {"max_operations", "max_total_bytes"})
            protocol.integer(budgets["max_operations"], 1, 32)
            protocol.integer(budgets["max_total_bytes"])
            protocol.require(isinstance(items, list) and 0 < len(items) <= budgets["max_operations"])
            _remaining(context["expires_at"])
            action_id = context.get("action_id") or uuid.uuid4().hex
            protocol.names(action_id)
            endpoint = self.endpoint()
            protocol.require(endpoint["host_key"]["verification"] == "known_hosts", "ENDPOINT_CHANGED", "Verified host identity required")
            by_root = {root["root_id"]: copy.deepcopy(root) for root in roots}
            protocol.require(by_root and len(by_root) == len(roots))
            for root in by_root.values():
                protocol.require(root["endpoint_digest"] == endpoint["endpoint_digest"], "ENDPOINT_CHANGED", "Root endpoint changed")
            result, mkdirs, seen = [], {}, set()
            for raw in items:
                protocol.require(isinstance(raw, dict) and set(raw) <= {"item_id", "op", "source", "destination", "text", "encoding", "on_conflict"})
                item_id, op = raw["item_id"], raw["op"]
                derived = protocol.names(action_id, item_id)
                protocol.require(item_id not in seen and op in {"copy", "symlink", "write_text", "mkdir"})
                seen.add(item_id)
                protocol.require(raw.get("on_conflict", "fail") == "fail" and raw.get("encoding", "utf-8") == "utf-8")
                destination = raw["destination"]
                protocol.require(set(destination) == {"root_id", "relative_path"})
                protocol.relative(destination["relative_path"])
                root = by_root[destination["root_id"]]
                dest = self._read({"op": "destination", "root": root, "path": destination["relative_path"]}, expected_endpoint=endpoint)
                if dest["missing_components"]:
                    parent_key = (root["root_id"], posixpath.dirname(destination["relative_path"]))
                    protocol.require(parent_key in mkdirs, "SOURCE_NOT_FOUND", "Missing parent requires a preceding explicit mkdir item")
                    dest["parent_item_id"] = mkdirs[parent_key]
                source, text, provenance = None, None, None
                label = protocol.content_class(destination["relative_path"])
                if op in {"copy", "symlink"}:
                    protocol.require(set(raw.get("source") or {}) == {"absolute_path"} and "text" not in raw)
                    path = raw["source"]["absolute_path"]
                    protocol.absolute(path)
                    provenance = (context.get("source_provenance") or {}).get(item_id)
                    protocol.require(isinstance(provenance, dict), "CONTENT_READ_DENIED", "Owner must resolve source provenance")
                    source = self._read({"op": "source", "path": path, "provenance": provenance,
                                         "expected_evidence": provenance.get("expected_evidence"),
                                         "endpoint_digest": endpoint["endpoint_digest"]}, expected_endpoint=endpoint)
                    label = protocol.strict_class(label, source["content_class"])
                    size = source["size"] if op == "copy" else len(source["canonical_path"].encode("utf-8"))
                elif op == "write_text":
                    text = raw.get("text")
                    protocol.require("source" not in raw and isinstance(text, str) and len(text.encode("utf-8")) <= protocol.TEXT_LIMIT)
                    protocol.require(label not in {"potcar", "large_vasp", "credential"}, "CONTENT_READ_DENIED", "Restricted content cannot be created as text")
                    size = len(text.encode("utf-8"))
                else:
                    protocol.require("source" not in raw and "text" not in raw)
                    size = 0
                    mkdirs[(root["root_id"], destination["relative_path"])] = item_id
                if dest["target_exists"]:
                    same = source and protocol.identity_matches(dest["target_exists"], source)
                    raise RemoteFileError("SOURCE_EQUALS_DESTINATION" if same else "DESTINATION_CONFLICT", "Destination already exists")
                result.append({"item_id": item_id, "op": op, "source": source, "destination": dest, "text": text,
                               "mode": None if op == "symlink" else 0o700 if op == "mkdir" else 0o600,
                               "on_conflict": "fail", "content_class": label, "names": derived, "bytes": size,
                               "source_provenance": provenance})
            manifest = {"protocol_version": protocol.PROTOCOL, "policy_version": protocol.POLICY,
                        "action_id": action_id, **{k: context[k] for k in ("project_id", "task_id", "job_key", "attempt_id", "scope_id", "scope_version", "expires_at")},
                        "endpoint": endpoint, "roots": list(by_root.values()), **budgets, "items": result}
            manifest["manifest_digest"] = protocol.digest(manifest)
            protocol.validated_manifest(manifest)
            return manifest
        except (KeyError, TypeError, ValueError, protocol.FileError) as exc:
            raise _local_error(exc)

    def begin(self, manifest, dispatch_context, *, dispatch_guard=None):
        try:
            protocol.validated_manifest(manifest)
            fields = ("action_id", "manifest_digest", "project_id", "task_id", "job_key", "attempt_id", "scope_id", "scope_version")
            protocol.require(isinstance(dispatch_context, dict) and set(dispatch_context) == set(fields) | {"binding_hash", "dispatch_nonce"})
            protocol.require(all(dispatch_context[k] == manifest[k] for k in fields))
            protocol.require(isinstance(dispatch_context["binding_hash"], str) and len(dispatch_context["binding_hash"]) == 64)
            protocol.names(dispatch_context["dispatch_nonce"])
            if self.endpoint() != manifest["endpoint"]:
                raise RemoteFileError("ENDPOINT_CHANGED", "Current endpoint differs from approved manifest")
            return FileSession(self, manifest, dispatch_guard=dispatch_guard)
        except (KeyError, ValueError, TypeError, protocol.FileError) as exc:
            raise _local_error(exc)

    def reconcile(self, manifest, receipt_locator=None, *, item_id=None):
        try:
            protocol.validated_manifest(manifest)
            protocol.require(receipt_locator is None, "INVALID_FILE_REQUEST", "Reconciliation uses only manifest-derived receipt paths")
            request = {"op": "reconcile", "manifest": manifest}
            if item_id is not None:
                request["item_id"] = item_id
            return self._read(request, expected_endpoint=manifest["endpoint"])
        except (KeyError, ValueError, TypeError, protocol.FileError) as exc:
            raise _local_error(exc)


class FileSession:
    def __init__(self, files, manifest, *, dispatch_guard=None):
        self.files, self.manifest = files, copy.deepcopy(manifest)
        self.dispatch_guard = dispatch_guard
        self.wire = _Wire(files.manager, expected_endpoint=manifest["endpoint"], scheduler_target=files.scheduler_target)
        self.prepared, self.prepared_at, self.closed = None, None, False
        try:
            self.wire.call({"op": "begin", "manifest": self.manifest, "remaining_seconds": _remaining(manifest["expires_at"])}, writing=True,
                           dispatch_guard=(lambda: dispatch_guard("begin", None)) if dispatch_guard else None,
                           request_factory=lambda: {'op':'begin','manifest':self.manifest,'remaining_seconds':_remaining(manifest['expires_at'])})
        except BaseException:
            self.close()
            raise

    def _check(self):
        if self.closed:
            raise RemoteFileError("PROTOCOL_ERROR", "File session is closed")
        _remaining(self.manifest["expires_at"])
        # Do not reconnect an interrupted write session to a new transport.
        manager = self.files.manager
        try:
            unchanged = (manager.connected and manager._client.get_transport() is self.wire.transport
                         and manager.file_endpoint(self.files.scheduler_target, connect=False) == self.manifest["endpoint"])
        except SSHError:
            unchanged = False
        if not unchanged:
            raise RemoteFileError("ENDPOINT_CHANGED", "File session endpoint changed", dispatched=True, published="unknown")

    def prepare_next(self, *, should_cancel=None):
        self._check()
        if self.prepared is not None:
            raise RemoteFileError("PROTOCOL_ERROR", "Current item is already prepared")
        try:
            self.prepared = self.wire.call({"op": "prepare_next"}, timeout=_remaining(self.manifest["expires_at"]), writing=True,
                                          should_cancel=should_cancel)
            self.prepared_at = time.monotonic()
            return copy.deepcopy(self.prepared)
        except BaseException:
            self.close()
            raise

    def commit(self, permit):
        self._check()
        if self.prepared is None or time.monotonic() >= self.prepared_at + 30:
            raise RemoteFileError("SCOPE_EXPIRED", "Prepared item or commit token is expired")
        remaining = _remaining(permit.get("valid_until"))
        if remaining > 30:
            raise RemoteFileError("SCOPE_EXPIRED", "Commit permission exceeds its maximum lifetime")
        if datetime.datetime.fromisoformat(permit["valid_until"]) > datetime.datetime.fromisoformat(self.manifest["expires_at"]):
            raise RemoteFileError("SCOPE_EXPIRED", "Commit permission exceeds owner scope expiry")
        try:
            guard = getattr(self, "dispatch_guard", None)
            def frame():
                self._check()
                if time.monotonic() >= self.prepared_at + 30:
                    raise RemoteFileError('SCOPE_EXPIRED', 'Prepared token expired while waiting for owner')
                ttl = _remaining(permit.get('valid_until'))
                if ttl > 30 or datetime.datetime.fromisoformat(permit['valid_until']) > datetime.datetime.fromisoformat(self.manifest['expires_at']):
                    raise RemoteFileError('SCOPE_EXPIRED', 'Commit permission exceeds owner scope expiry')
                return {'op':'commit','permit':permit,'remaining_seconds':ttl}
            result = self.wire.call({"op": "commit", "permit": permit, "remaining_seconds": remaining}, writing=True, timeout=30,
                                   dispatch_guard=(lambda: guard("commit", permit["item_id"])) if guard else None, request_factory=frame)
            self.prepared = None
            return result
        except BaseException:
            self.close()
            raise

    def abort(self):
        if self.closed:
            return {"state": "closed", "published": "unknown", "leftovers": []}
        try:
            return self.wire.call({"op": "abort"}, writing=True)
        finally:
            self.close()

    def close(self):
        self.wire.close()
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
