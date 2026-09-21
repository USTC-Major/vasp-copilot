"""M7 作业配额与调度测试：状态机 + squeue 解析 + 调度器（内存 fake，不碰网络）。"""

import pytest

from ai_mode.jobs import Job, JobStatus, Scheduler, parse_slurm_output
from ai_mode.jobs.state import TERMINAL, can_transition, normalize


# ---------------- 状态机 ----------------
_VALID = [
    ("draft", "waiting"),
    ("draft", "submitted"),
    ("waiting", "submitted"),
    ("waiting", "canceled"),
    ("submitted", "queued"),
    ("submitted", "running"),
    ("submitted", "failed"),
    ("submitted", "canceled"),
    ("queued", "running"),
    ("queued", "failed"),
    ("queued", "not_converged"),
    ("queued", "canceled"),
    ("running", "completed"),
    ("running", "failed"),
    ("running", "not_converged"),
    ("running", "canceled"),
    ("not_converged", "submitted"),
    ("not_converged", "failed"),
]
_INVALID = [
    ("waiting", "queued"),
    ("running", "submitted"),
    ("canceled", "submitted"),
    ("submitted", "submitted"),
    ("completed", "queued"),
    ("not_found", "submitted"),
]


@pytest.mark.parametrize("old_,new_", _VALID)
def test_valid_transition(old_, new_):
    job = Job(job_id=f"j-{old_}-{new_}", status=JobStatus(old_))
    assert can_transition(JobStatus(old_), JobStatus(new_))
    assert job.transition(JobStatus(new_)) == JobStatus(new_)


@pytest.mark.parametrize("old_,new_", _INVALID)
def test_invalid_transition(old_, new_):
    job = Job(job_id=f"j-{old_}-{new_}", status=JobStatus(old_))
    assert not can_transition(JobStatus(old_), JobStatus(new_))
    with pytest.raises(ValueError):
        normalize(JobStatus(old_), JobStatus(new_))
    with pytest.raises(ValueError):
        job.transition(JobStatus(new_))


def test_terminal_states_are_frozen():
    for st in TERMINAL:
        for target in JobStatus:
            if target is st:
                continue
            assert not can_transition(st, target)
        job = Job(job_id="x", status=st)
        assert job.is_terminal()


def test_unknown_status_raises():
    with pytest.raises(ValueError):
        normalize(JobStatus.DRAFT, "bogus")


# ---------------- squeue 解析 ----------------
def test_parse_empty():
    assert parse_slurm_output("") == (0, 0)
    assert parse_slurm_output("   \n \n") == (0, 0)


def test_parse_header_only():
    out = "JOBID PARTITION NAME USER ST TIME NODES NODELIST(REASON)"
    assert parse_slurm_output(out) == (0, 0)


def test_parse_mixed():
    out = "\n".join([
        "             JOBID PARTITION     NAME     USER ST       TIME  NODES NODELIST(REASON)",
        "             1010       gpu   relax.sh   user  PD       0:00      1 (Resources)",
        "             1011       gpu   md.sh      user   R       1:02      1 n1",
        "             1012       gpu   vc.sh      user  CG       2:00      1 n2",
    ])
    assert parse_slurm_output(out) == (1, 1)


def test_parse_lowercase_and_crlf():
    out = "jobid st name user state time\r\n" \
          "1 gpu x user pending 0:00\r\n" \
          "2 gpu y user running 0:01\r\n"
    assert parse_slurm_output(out) == (1, 1)


def test_parse_ignores_other_states():
    out = "\n".join(["JOBID ST TIME",
                     "10 CG 1:00",
                     "11 S  2:00"])
    assert parse_slurm_output(out) == (0, 0)
# ---------------- 调度器 ----------------
def make_job(job_id, status=JobStatus.DRAFT, order=0, **kw):
    return Job(job_id=job_id, status=status, order=order, **kw)


class FakeRunner:
    """内存版 run：把 jobs 渲染成 squeue 输出，供调度器解析。"""

    def __init__(self, jobs=None, exit_code=0):
        self.jobs = jobs or {}
        self.exit_code = exit_code
        self.commands = []

    def __call__(self, command, *, cwd=None, timeout=None):
        self.commands.append(command)
        if self.exit_code != 0:
            return self.exit_code, "", ""
        lines = ["JOBID PARTITION NAME USER ST TIME NODES NODELIST(REASON)"]
        for i, (jobid, st) in enumerate(sorted(self.jobs.items()), start=1):
            lines.append(f"{jobid} gpu jb{i} user {st} 0:0{i} 1 node{i}")
        return self.exit_code, "\n".join(lines), ""


class RecordingSubmitter:
    def __init__(self, slurm_ids=None):
        self.called = []
        self._ids = list(slurm_ids or [])

    def __call__(self, job):
        self.called.append(job.job_id)
        if self._ids:
            return self._ids.pop(0)
        return None


def test_legacy_scheduler_is_explicitly_retired():
    with pytest.raises(RuntimeError, match="retired"):
        Scheduler()
