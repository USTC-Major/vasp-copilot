"""Legacy parser compatibility. Runtime scheduling belongs to Orchestrator."""
from __future__ import annotations

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


class Scheduler:
    def __init__(self, *args, **kwargs):
        raise RuntimeError('Legacy Scheduler retired; use the Toolbox execution service and task records')

def make_submit_callback(*args, **kwargs):
    raise RuntimeError('Legacy submit callbacks retired; use single-use Toolbox consent')
