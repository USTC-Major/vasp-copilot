"""Single computation identity and projections; never an execution queue."""
from __future__ import annotations

import copy
import hashlib
import json
import uuid

from .contracts import ToolboxError


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def normalize(flow):
    jobs = (flow.get("plan") or {}).get("jobs") or []
    if (not flow.get("computation_schema_version") and jobs
            and not any(j.get("precheck") or j.get("draft") for j in jobs)
            and (flow.get("precheck") or flow.get("draft"))):
        flow.setdefault("legacy_submission_evidence", {
            "authorizes_submission": False,
            "precheck": copy.deepcopy(flow.get("precheck")),
            "draft": copy.deepcopy(flow.get("draft")),
        })
    if jobs:
        flow["computation_schema_version"] = 1
    for job in jobs:
        if (not job.get("attempt_id") and not job.get("slurm_id")
                and not job.get("submission_state")
                and job.get("status", "draft") in {"draft", "waiting"}):
            job["attempt_id"] = uuid.uuid4().hex
    cons = flow.get("consent") or {}
    for action in (cons.get("actions") or {}).values():
        binding = action.get("binding") or {}
        if (action.get("kind") in {"submit", "script_attestation"}
                and action.get("state") in {"pending", "approved"}
                and (not binding.get("job_key") or not binding.get("attempt_id"))):
            action.update(state="expired", result="旧授权缺少单计算身份；请按 job/attempt 重新确认")
    if cons:
        cons["cards"] = {k: a for k, a in cons.get("actions", {}).items() if a.get("state") == "pending"}
    # Legacy global evidence stays audit-only. It cannot become fresh approval.
    if any(j.get("attempt_id") for j in jobs):
        flow["draft"] = [copy.deepcopy(j["draft"]) for j in jobs if j.get("draft")]
        flow["precheck"] = (copy.deepcopy(jobs[0].get("precheck") or {"ok": False, "issues": []})
                            if len(jobs) == 1 else {"ok": False, "issues": [], "per_job": True})
    if any(j.get("slurm_id") and j.get("status") in {"submitted", "queued", "running"} for j in jobs):
        flow["phase"] = "monitoring"
    return flow


def select_job(flow, args, *, preparing=True):
    jobs = (flow.get("plan") or {}).get("jobs") or []
    key, attempt = args.get("job_key"), args.get("attempt_id")
    if len(jobs) != 1 and (not key or not attempt):
        raise ToolboxError("JOB_REQUIRED", "多计算操作必须明确 job_key 和 attempt_id")
    job = next((j for j in jobs if j.get("key") == (key or (jobs[0].get("key") if jobs else None))), None)
    if job is None:
        raise ToolboxError("JOB_NOT_FOUND", "计算不存在", 404)
    if attempt and attempt != job.get("attempt_id"):
        raise ToolboxError("ATTEMPT_STALE", "计算尝试已变化，请刷新后重新确认", 409)
    if not attempt and job.get("attempt_history"):
        raise ToolboxError("ATTEMPT_STALE", "重试后的操作必须明确当前 attempt_id", 409)
    if preparing:
        if job.get("submission_state") in {"unknown", "executing"} or job.get("status") == "unknown":
            raise ToolboxError("SUBMISSION_UNKNOWN", "提交结果未知；不得重新提交", 409)
        if job.get("slurm_id") or job.get("submission_state") or job.get("status", "draft") not in {"draft", "waiting"}:
            raise ToolboxError("JOB_NOT_READY", "该计算不在待准备状态", 409)
    return job


def evidence(flow, job):
    by_key = {j["key"]: j for j in (flow.get("plan") or {}).get("jobs", [])}
    return {"job_key": job["key"], "attempt_id": job.get("attempt_id"),
            "requires": copy.deepcopy(job.get("requires") or []),
            "dependencies": [{"job_key": key, "attempt_id": by_key.get(key, {}).get("attempt_id"),
                              "status": by_key.get(key, {}).get("status"),
                              "slurm_id": by_key.get(key, {}).get("slurm_id")}
                             for key in job.get("requires") or []]}


def bind_precheck(flow, job, precheck):
    precheck["snapshot"].update(evidence(flow, job))
    precheck["digest"] = digest(precheck["snapshot"])
    precheck["attempt_id"] = job.get("attempt_id")
    job["precheck"] = precheck


def scope_valid(flow, action, *, active=True):
    binding = action.get("binding") or {}
    scope = ((flow.get("consent") or {}).get("computation_scopes") or {}).get(binding.get("scope_id")) or {}
    job = next((j for j in (flow.get("plan") or {}).get("jobs", []) if j.get("key") == binding.get("job_key")), {})
    from .consent import _expired
    expected_state = {"active"} if active else {"proposed", "active"}
    return (scope.get("state") in expected_state and not _expired(scope)
            and scope.get("version") == binding.get("scope_version")
            and scope.get("approval_mode") == "human" and scope.get("submit_limit") == 1
            and scope.get("allowed_operations") == ["submit"]
            and all(scope.get(k) == binding.get(k) for k in (
                "project_id", "task_id", "job_key", "attempt_id", "endpoint_digest", "precheck_digest"))
            and scope.get("draft") == binding.get("draft") == job.get("draft")
            and job.get("attempt_id") == binding.get("attempt_id")
            and (job.get("precheck") or {}).get("digest") == binding.get("precheck_digest")
            and not job.get("submission_state") and not job.get("slurm_id")
            and job.get("status", "draft") in {"draft", "waiting"})


def invalidate(flow, job_keys, reason):
    """Invalidate unclaimed authority only; executed/unknown history is immutable."""
    cons = flow.get("consent") or {}
    for action in cons.get("actions", {}).values():
        key = (action.get("binding") or {}).get("job_key")
        if action.get("state") in {"pending", "approved"} and (job_keys is None or key in job_keys or key is None):
            action.update(state="expired", result=reason)
    for scope in cons.get("computation_scopes", {}).values():
        if scope.get("state") in {"proposed", "active"} and (job_keys is None or scope.get("job_key") in job_keys):
            scope["state"] = "revoked"
