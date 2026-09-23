"""Owner-only download of fixed, bounded results from an exact submitted attempt."""
from __future__ import annotations

import copy
import posixpath
import re
import threading

from . import consent
from .computation import digest
from .contracts import ToolboxError
from .scheduler_profile import target_binding
from .ssh.file_helper import RESULT_NAMES
from .ssh.errors import SSHError
from .ssh.remote_files import RemoteFileError

_TERMINAL = frozenset({"completed", "failed", "not_converged"})
_SLOTS = threading.BoundedSemaphore(2)


def _snapshot(svc, project_id: str, task_id: str, job_key: str, attempt_id: str, name: str):
    if name not in RESULT_NAMES:
        raise ToolboxError("RESULT_NAME_DENIED", "只可下载指定的三种结果文件", 400)
    if not job_key or not attempt_id:
        raise ToolboxError("RESULT_IDENTITY_REQUIRED", "必须指定当前作业与尝试", 400)
    task = svc.require_task(project_id, task_id)
    flow = task.get("flow") or {}
    matches = [job for job in ((flow.get("plan") or {}).get("jobs") or []) if job.get("key") == job_key]
    if len(matches) != 1 or matches[0].get("attempt_id") != attempt_id:
        raise ToolboxError("RESULT_ATTEMPT_NOT_FOUND", "当前计算尝试不存在", 404)
    job = matches[0]
    action_id = job.get("submission_action_id")
    action = ((flow.get("consent") or {}).get("actions") or {}).get(action_id)
    draft = job.get("draft")
    if job.get("status") not in _TERMINAL or job.get("submission_state") != "submitted":
        raise ToolboxError("RESULT_NOT_TERMINAL", "仅可取回已提交且终态的计算结果", 409)
    if not job.get("slurm_id") or not isinstance(draft, dict) or not isinstance(action, dict):
        raise ToolboxError("RESULT_ORIGIN_UNVERIFIED", "缺少本次提交的持久身份", 409)
    binding = action.get("binding")
    if (action.get("kind") != "submit" or action.get("state") != "executed"
            or not isinstance(binding, dict) or action.get("binding_hash") != digest(binding)
            or binding.get("operation") != "submit" or binding.get("project_id") != project_id
            or binding.get("task_id") != task_id or binding.get("job_key") != job_key
            or binding.get("attempt_id") != attempt_id or binding.get("draft") != draft
            or draft.get("job_key") != job_key or draft.get("attempt_id") != attempt_id):
        raise ToolboxError("RESULT_ORIGIN_UNVERIFIED", "提交记录与当前尝试不一致", 409)
    root, directory = binding.get("remote_root"), draft.get("dir")
    if (not isinstance(root, str) or not isinstance(directory, str)
            or not root.startswith("/") or not directory.startswith("/")
            or posixpath.normpath(root) != root or posixpath.normpath(directory) != directory
            or any(part in {"", ".", ".."} for part in root.split("/")[1:])
            or any(part in {"", ".", ".."} for part in directory.split("/")[1:])
            or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_./-]{0,127}", job_key)
            or any(part in {"", ".", ".."} for part in job_key.split("/"))
            or directory not in {root, root.rstrip("/") + "/" + job_key}):
        raise ToolboxError("RESULT_ORIGIN_UNVERIFIED", "提交目录证据不符合受限作业路径", 409)
    target = target_binding(svc.settings_loader())
    if (job.get("scheduler_target") != target
            or (draft.get("scheduler_target") is not None and draft.get("scheduler_target") != target)
            or ((job.get("precheck") or {}).get("snapshot") or {}).get("scheduler_target") != target
            or binding.get("endpoint_digest") != digest({"scheduler_target": target, "host_key_evidence": "unknown"})):
        raise ToolboxError("RESULT_ENDPOINT_CHANGED", "SSH或调度目标与提交记录不同", 409)
    return {"job": copy.deepcopy(job), "action": copy.deepcopy(action), "directory": directory, "target": target}


def download_result(svc, project_id: str, task_id: str, job_key: str, attempt_id: str, name: str):
    with consent.task_lock(project_id, task_id):
        before = _snapshot(svc, project_id, task_id, job_key, attempt_id, name)
    if not _SLOTS.acquire(blocking=False):
        raise ToolboxError("RESULT_BUSY", "结果下载正在处理，请稍后重试", 503, retryable=True)
    try:
        try:
            with svc.files.remote() as files:
                if files.scheduler_target != {**before["target"],
                        "identity_file": before["target"]["identity_file"] or None,
                        "known_hosts_path": before["target"]["known_hosts_path"] or None}:
                    raise ToolboxError("RESULT_ENDPOINT_CHANGED", "SSH目标已变化", 409)
                data, sha256 = files.read_result(before["directory"], name)
        except RemoteFileError as exc:
            status = {"SOURCE_NOT_FOUND": 404, "RESULT_TOO_LARGE": 413,
                      "CONTENT_READ_DENIED": 403, "PATH_SYMLINK_ESCAPE": 403,
                      "REMOTE_CAPABILITY_UNAVAILABLE": 503, "REMOTE_IO_ERROR": 503,
                      "RESULT_TIMEOUT": 503, "PROTOCOL_ERROR": 503}.get(exc.code, 409)
            raise ToolboxError(exc.code, "结果读取失败：" + exc.code, status) from exc
        except SSHError as exc:
            raise ToolboxError("RESULT_TRANSPORT_UNAVAILABLE", "远端结果连接不可用", 503,
                               retryable=True) from exc
        with consent.task_lock(project_id, task_id):
            after = _snapshot(svc, project_id, task_id, job_key, attempt_id, name)
            if after != before:
                raise ToolboxError("RESULT_ATTEMPT_CHANGED", "任务状态在下载期间发生变化", 409)
        return data, sha256
    finally:
        _SLOTS.release()
