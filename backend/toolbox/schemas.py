"""智能模式会话 schema（完全独立命名空间，不 import 工具箱任何代码）。

对齐 AI_MODE_MODULE_INTERFACES.md 核心数据结构：
- 会话文件 = 一次计算任务（同项目内任务上下文彼此独立）。
- 会话 = 超算计算目录（calc_dir）。
- 作业对象：job_id / slurm_job_id / 状态 / 所在步骤 / 已改次数 / 内容描述。
上下文快照约定见 AI_MODE_MODULE_INTERFACES.md §5.1（优先 C，重时降 B）。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class JobStatus(str, Enum):
    PLANNED = "planned"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    NOT_CONVERGED = "not_converged"
    CANCELLED = "cancelled"


class SnapshotPolicy(str, Enum):
    C = "C"   # 携带上下文原文（首选）
    B = "B"   # 项目+工序步+起止点+作业一览+最近对话小结


class Message(BaseModel):
    """会话中的一条历史消息（含闲聊）。"""

    role: Literal["user", "assistant", "tool", "system"]
    content: str
    created_at: str = Field(default_factory=_now_iso)


class RequirementSnapshot(BaseModel):
    """需求快照：用户原始目标 + 澄清后目标 + 本次覆盖范围。"""

    raw_goal: str
    clarified_goal: Optional[str] = None
    coverage: Optional[str] = None   # 本次覆盖工序：如 understand->report


class JobEntry(BaseModel):
    """计算作业记录（提交/监控引用的对象）。"""

    job_id: str = Field(default_factory=lambda: _new_id("job"))
    job_key: str                       # 规划键，如 "r1" / "dos"
    slurm_job_id: Optional[str] = None  # 超算作业号
    status: JobStatus = JobStatus.PLANNED
    step: str = ""                     # 所属工序步骤
    attempt_count: int = 0
    modify_count: int = 0              # 修改上限 2 次
    description: str = ""


class PlanStep(BaseModel):
    """规划中的作业单元（明确先后顺序约束）。"""

    job_key: str
    label: str
    step: str = "prepare_input"
    requires: list[str] = Field(default_factory=list)   # 前置（递进）须等其成功
    parallel_group: Optional[str] = None                 # 平行组标识


class PlanSnapshot(BaseModel):
    """作业规划快照：定死先后顺序（平行可并行，递进必须等前置成功）。"""

    goal: str = ""
    strategy: str = ""       # 平行/递进策略说明
    steps: list[PlanStep] = Field(default_factory=list)


class SnapshotState(BaseModel):
    """上下文快照状态：当前策略 + 最近摘要 + 占有率。"""

    policy: SnapshotPolicy = SnapshotPolicy.C
    last_summary: str = ""
    occupancy: float = 0.0           # 0~1，界面显示用


class Session(BaseModel):
    """一个会话文件 = 一次计算任务（同项目内彼此独立）。"""

    session_id: str = Field(default_factory=lambda: _new_id("sess"))
    project_id: str = ""
    title: str = ""
    calc_dir: str = ""                 # 会话绑定的超算计算目录
    local_workspace: str = ""          # 本地工作区（可复用）
    start_step: str = "understand"     # 本次起始工序
    end_step: str = "report"           # 本次结束工序
    current_step: str = "understand"
    duration: str = Field(default="full", description="full|segment")
    requirement: Optional[RequirementSnapshot] = None
    plan: Optional[PlanSnapshot] = None
    jobs: list[JobEntry] = Field(default_factory=list)
    messages: list[Message] = Field(default_factory=list)
    snapshot: SnapshotState = Field(default_factory=SnapshotState)
    report_draft: str = ""
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)

    def touch(self) -> None:
        self.updated_at = _now_iso()

    def append_message(self, role: Literal["user", "assistant", "tool", "system"],
                       content: str) -> Message:
        msg = Message(role=role, content=content)
        self.messages.append(msg)
        self.touch()
        return msg

    def job(self, job_key: str) -> Optional[JobEntry]:
        for j in self.jobs:
            if j.job_key == job_key:
                return j
        return None

    def to_dict(self) -> dict:
        return json.loads(self.model_dump_json())