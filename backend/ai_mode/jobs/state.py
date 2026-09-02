"""作业对象与状态机（M7）。对齐 WORKFLOW.md v14 §9。"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class JobStatus(str, Enum):
    DRAFT = "draft"
    WAITING = "waiting"
    SUBMITTED = "submitted"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    NOT_FOUND = "not_found"
    NOT_CONVERGED = "not_converged"
    CANCELED = "canceled"


_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.DRAFT: {JobStatus.WAITING, JobStatus.SUBMITTED},
    JobStatus.WAITING: {JobStatus.SUBMITTED, JobStatus.CANCELED},
    JobStatus.SUBMITTED: {JobStatus.QUEUED, JobStatus.RUNNING,
                          JobStatus.FAILED, JobStatus.CANCELED},
    JobStatus.QUEUED: {JobStatus.RUNNING, JobStatus.FAILED,
                       JobStatus.NOT_CONVERGED, JobStatus.CANCELED},
    JobStatus.RUNNING: {JobStatus.COMPLETED, JobStatus.FAILED,
                        JobStatus.NOT_CONVERGED, JobStatus.CANCELED},
    JobStatus.NOT_CONVERGED: {JobStatus.SUBMITTED, JobStatus.FAILED},
}
TERMINAL = frozenset({JobStatus.COMPLETED, JobStatus.FAILED,
                      JobStatus.CANCELED, JobStatus.NOT_FOUND})
for _s in TERMINAL:
    _TRANSITIONS[_s] = set()


def normalize(old, new):
    if isinstance(new, str):
        try:
            new = JobStatus(new)
        except ValueError:
            raise ValueError(f"未知状态: {new!r}")
    allowed = _TRANSITIONS.get(old, set())
    if new not in allowed:
        raise ValueError(f"非法迁移: {old.value} -> {new.value}")
    return new


def can_transition(old, new) -> bool:
    try:
        normalize(old, new)
        return True
    except ValueError:
        return False


@dataclass
class Job:
    job_id: str
    task_id: str = ""
    project_id: str = ""
    name: str = ""
    description: str = ""
    status: JobStatus = JobStatus.DRAFT
    slurm_id: int | None = None
    submit_cmd: str = ""
    order: int = 0
    retry_count: int = 0
    modified: int = 0
    workdir: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def transition(self, new) -> JobStatus:
        self.status = normalize(self.status, new)
        return self.status

    def is_terminal(self) -> bool:
        return self.status in TERMINAL

    def submit_cmd_clean(self) -> str:
        return self.submit_cmd.strip()