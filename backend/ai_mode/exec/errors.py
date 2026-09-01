"""受限执行器错误（M4）：安全策略违反执行器直接回报，不继续往下走。"""

from __future__ import annotations


class ExecError(Exception):
    """执行器通用错误基类。"""


class ExecutionPolicyViolation(ExecError):
    """命令或参数触犯安全策略（危险黑名单 / 越界路径 / 写白名单）。

    :param reason: 人类可读的拒绝原因（会进执行结果报文与门卫提示）。
    :param command: 触发的原始命令串，便于追溯。
    """

    def __init__(self, reason: str, *, command: str | None = None):
        super().__init__(reason)
        self.reason = reason
        self.command = command