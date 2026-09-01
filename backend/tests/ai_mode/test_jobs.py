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


def test_free_slots_from_runner():
    runner = FakeRunner(jobs={"1": "PD", "2": "R"})
    s = Scheduler(max_jobs=5, account="user", run=runner, submitter=None)
    snap = s.sync()
    assert snap["pending"] == 1 and snap["running"] == 1
    assert snap["occupied"] == 2 and snap["free"] == 3
    assert s.free_slots == 3
    assert "squeue -u user" in runner.commands[-1]


def test_arrange_submit_when_free():
    runner = FakeRunner(jobs={})
    sub = RecordingSubmitter()
    s = Scheduler(max_jobs=2, account="user", run=runner, submitter=sub)
    submitted, enqueued = s.arrange([make_job("a"), make_job("b"), make_job("c")])
    assert [j.job_id for j in submitted] == ["a", "b"]
    assert [j.job_id for j in enqueued] == ["c"]
    assert sub.called == ["a", "b"]
    assert s.snapshot()["waiting"] == 1


def test_arrange_sees_existing_remote_jobs():
    runner = FakeRunner(jobs={"101": "R", "102": "PD"})
    s = Scheduler(max_jobs=3, account="user", run=runner, submitter=None)
    submitted, enqueued = s.arrange([make_job("new1"), make_job("new2")])
    assert [j.job_id for j in submitted] == ["new1"]
    assert [j.job_id for j in enqueued] == ["new2"]


def test_backfill_preserves_order():
    runner = FakeRunner(jobs={"101": "R", "102": "PD"})
    sub = RecordingSubmitter()
    s = Scheduler(max_jobs=2, account="u", run=runner, submitter=sub)
    s.arrange([make_job("z1"), make_job("z2"), make_job("z3")])
    assert s.snapshot()["waiting"] == 3
    runner.jobs = {"102": "PD"}  # 101 完成，空出一个空位
    backfilled = s.backfill()
    assert [j.job_id for j in backfilled] == ["z1"]
    assert s.snapshot()["waiting"] == 2


def test_backfill_fills_all_slots():
    runner = FakeRunner(jobs={})
    sub = RecordingSubmitter()
    s = Scheduler(max_jobs=3, account="u", run=runner, submitter=sub)
    s.arrange([make_job("a"), make_job("b"), make_job("c"), make_job("d")])
    assert s.snapshot()["waiting"] == 1
    runner.jobs = {"101": "R"}  # 真实占用 1，留 2 空位
    backfilled = s.backfill()
    assert [j.job_id for j in backfilled] == ["d"]
    assert s.snapshot()["waiting"] == 0


def test_backfill_stops_when_no_slots():
    runner = FakeRunner(jobs={"1": "R", "2": "PD"})
    s = Scheduler(max_jobs=1, account="u", run=runner, submitter=None)
    s.arrange([make_job("a"), make_job("b"), make_job("c")])
    assert s.snapshot()["waiting"] == 3
    assert s.backfill() == []
    assert s.snapshot()["waiting"] == 3


def test_query_failure_treated_as_no_occupancy():
    runner = FakeRunner(jobs={"1": "R"})
    runner.exit_code = 1
    s = Scheduler(max_jobs=5, account="u", run=runner, submitter=None)
    snap = s.sync()
    assert snap["pending"] == 0 and snap["running"] == 0
    assert s.free_slots == 5


def test_submit_sets_slurm_id_and_status():
    runner = FakeRunner(jobs={})
    sub = RecordingSubmitter(["42"])
    s = Scheduler(max_jobs=1, account="u", run=runner, submitter=sub)
    job = make_job("a")
    submitted, _ = s.arrange([job])
    assert submitted == [job]
    assert job.status == JobStatus.SUBMITTED
    assert job.slurm_id == 42


def test_submit_without_submitter_is_dry_run():
    s = Scheduler(max_jobs=2, account="u", run=FakeRunner(jobs={}))
    job = make_job("a")
    submitted, enqueued = s.arrange([job])
    assert submitted == [job] and enqueued == []
    assert job.status == JobStatus.SUBMITTED
    assert job.slurm_id is None


def test_enqueue_dedup():
    s = Scheduler(max_jobs=1, account="u", run=FakeRunner(jobs={}))
    job = make_job("a")
    s.enqueue(job)
    s.enqueue(job)
    assert len(s._waiting) == 1


def test_arrange_skips_terminal_jobs():
    s = Scheduler(max_jobs=2, account="u", run=FakeRunner(jobs={}))
    done = make_job("done", status=JobStatus.RUNNING)
    done.transition(JobStatus.COMPLETED)
    job = make_job("a")
    submitted, enqueued = s.arrange([done, job])
    assert submitted == [job] and enqueued == []


def test_backfill_failure_preserves_queue_head():
    def failing_submitter(job):
        raise RuntimeError("boom")
    s = Scheduler(max_jobs=2, account="u", run=FakeRunner(jobs={}),
                  submitter=failing_submitter)
    s.enqueue(make_job("a"))
    s.enqueue(make_job("b"))
    assert s.backfill() == []
    assert [j.job_id for j in s._waiting] == ["a", "b"]


def test_poll_interval_config():
    s = Scheduler(max_jobs=1, account="u", run=FakeRunner(), poll_interval_seconds=120)
    assert s.poll_interval_seconds == 120
    assert s.max_jobs == 1
