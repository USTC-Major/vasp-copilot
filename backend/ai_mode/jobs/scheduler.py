"""作业配额与调度（M7）。

对齐 WORKFLOW.md v14 §9：
- 空位 = max_jobs（全局，默认 20）− 账号实时「排队 + 运行中」作业总数。
- 一律以 ``squeue -u <账号>`` 实况为准，不建影子台账；账号现有在跑作业无论来源
  （智能体/工具箱/用户手敲）全部计占用。
- 有空位 → 走统一提交动作；没空位 → 进「等待空位」本地队列，按确认先后补提。
- 补提仍走统一提交动作；提交须用户确认（M5 授权门）——本层通过注入 submitter
  完成分工，SSH 与提交器均可注入 fake 便于离线测试。
"""

from __future__ import annotations

from typing import Callable

from .state import Job, JobStatus, TERMINAL

__all__ = ["Scheduler", "parse_slurm_output", "make_submit_callback"]


def _offline_run(command: str) -> tuple[int, str, str]:
    """离线默认 runner：不执行任何远端命令，返回空输出（exit 0）。"""
    return 0, "", ""


def parse_slurm_output(stdout: str) -> tuple[int, int]:
    """粗略解析 ``squeue``-like 标准输出，返回 (排队数, 运行数)。

    - 跳过表头（含 ``JOBID`` / ``JOB ID`` / ``ST`` 的行；多行表头亦兼容）。
    - 每行看状态列：PD/PENDING → 排队；RUN/RUNNING/R → 运行；其余（CG 等）不计。
    - 支持 CRLF / 首行已为数据 / 空输出等情形；无数据行时返回 (0,0)。
    """
    pending = 0
    running = 0
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        tokens = line.upper().split()
        if any(t in ("JOBID", "JOB ID", "ST") for t in tokens):
            continue  # 表头
        if "PENDING" in tokens or "PD" in tokens:
            pending += 1
        elif any(t in ("R", "RUN", "RUNNING") for t in tokens):
            running += 1
    return pending, running


def make_submit_callback(submitter):
    """语义化包装：把一个可调用变成「统一提交回调」，供 :class:`Scheduler` 注入。

    「只生成不执行」场景：传入只记录/打印命令构造器并返回 None 的回调即可（不真正提交）。
    返回整数的回调视为真实提交成功并回填 slurm_id（M9 模板实现）。
    """
    return submitter


class Scheduler:
    """全局作业配额调度器。

    :param max_jobs: 账号「排队+运行中」作业数上限（全局生效，默认 20）。
    :param account: 超算账号，用于组装 ``squeue -u <account>``。
    :param run: 执行远端命令的可调用，签名同 SSHManager.run：
        ``(command) -> (exit_code, stdout, stderr)``；测试注入内存 fake，
        不填则视为离线（占用按 0，即空位 = 上限）。
    :param submitter: 统一提交调度器的可调用对象。真实实现 M9 模板成型；
        本层只保证「有空位才调用」且失败保留队列顺序。
    :param poll_interval_seconds: 轮询频率（可设项），供监控层节奏使用。
    """

    def __init__(self, *, max_jobs: int = 20, account: str = "",
                 run: Callable[[str], tuple[int, str, str]] | None = None,
                 submitter: Callable[[Job], str | None] | None = None,
                 poll_interval_seconds: int = 60):
        self.max_jobs = max(1, int(max_jobs or 20))
        self.account = (account or "").strip()
        self._run = run or _offline_run
        self._submitter = submitter
        self.poll_interval_seconds = max(1, int(poll_interval_seconds or 60))
        self._waiting: list[Job] = []
        self._inflight: dict[str, Job] = {}
        self._usage: tuple[int, int] = (0, 0)

    # ---------- 实况 ----------
    def _query_usage(self) -> tuple[int, int]:
        """实时问超算：当前「排队 + 运行中」占用。查询失败一律按 (0,0)。"""
        cmd = f"squeue -u {self.account}".rstrip()
        try:
            code, out, _err = self._run(cmd)
        except Exception:
            return 0, 0
        if code != 0:
            return 0, 0
        return parse_slurm_output(out)

    def sync(self) -> "dict":
        """查询一次实况并刷新内部占用快照，返回 ``snapshot()``。"""
        self._usage = self._query_usage()
        return self.snapshot()

    def snapshot(self) -> dict:
        """当前实况快照（pending/running/occupied/free/waiting）。"""
        pending, running = self._usage
        occupied = pending + running
        return {
            "pending": pending,
            "running": running,
            "occupied": occupied,
            "max_jobs": self.max_jobs,
            "free": max(0, self.max_jobs - occupied),
            "waiting": len(self._waiting),
        }

    @property
    def free_slots(self) -> int:
        """剩余空位 = 上限 −（排队 + 运行中）。"""
        return max(0, self.max_jobs - self._usage[0] - self._usage[1])

    # ---------- 调度 ----------
    def arrange(self, jobs: list[Job]) -> tuple[list[Job], list[Job]]:
        """新作业逐个安排：有空位 → 立即提交；无空位 → 依序进等待队列。

        返回 ``(已提交, 已排队)``。本批内以本地占位防超限，不重复查询。
        终态作业跳过，不计排队。
        """
        self.sync()
        submitted: list[Job] = []
        enqueued: list[Job] = []
        for job in jobs:
            if job is None or job.status in TERMINAL:
                continue
            if self.free_slots > 0:
                self.submit(job)
                submitted.append(job)
            else:
                self.enqueue(job)
                enqueued.append(job)
        return submitted, enqueued

    def enqueue(self, job: Job) -> None:
        """加入「等待空位」本地队列（只记谁在等、按确认先后）。"""
        if job not in self._waiting:
            self._waiting.append(job)

    def submit(self, job: Job) -> str | None:
        """统一提交动作。仅在有空位时调用（外层授权门已放行）。"""
        if job.status in TERMINAL:
            return None
        slurm_id = self._call_submitter(job)
        try:
            job.transition(JobStatus.SUBMITTED)
        except ValueError:
            pass
        self._inflight[job.job_id] = job
        self._usage = (self._usage[0] + 1, self._usage[1])  # 本地占位防同批超限
        return slurm_id

    def _call_submitter(self, job: Job) -> str | None:
        if self._submitter is None:
            return None
        value = self._submitter(job)
        if value is None:
            return None
        try:
            slurm_id = int(str(value).strip())
        except (TypeError, ValueError):
            return None
        job.slurm_id = slurm_id
        return str(slurm_id)

    # ---------- 回填 ----------
    def backfill(self) -> list[Job]:
        """超算有空位时按确认先后补提等待队列，尽量当轮补齐空位。

        提交异常时把该作业放回队首并中止本次回填（保留确认顺序）。
        返回本次补提成功的作业列表。
        """
        if not self._waiting:
            return []
        self.sync()
        submitted: list[Job] = []
        while self.free_slots > 0 and self._waiting:
            job = self._waiting.pop(0)
            try:
                self.submit(job)
            except Exception:
                self._waiting.insert(0, job)
                break
            submitted.append(job)
        return submitted
