"""受限执行器子包（M4）：本地受限命令执行原语（黑名单/越界/写白名单拦截后运行）。

- 不 import 工具箱；执行器只翻译执行，判断属于 M5 门卫。
- run_command 返回 ExecResult（成功与否 + 摘要 + 输出片段）。
"""

from .errors import ExecError, ExecutionPolicyViolation
from .policy import (
    RedirectSpec,
    DANGEROUS_COMMANDS,
    WRITE_COMMANDS,
    parse_command,
    check_path_in_bounds,
    validate_command,
    validate_command_text,
)
from .runner import ExecResult, run_command

__all__ = [
    "ExecError",
    "ExecutionPolicyViolation",
    "RedirectSpec",
    "DANGEROUS_COMMANDS",
    "WRITE_COMMANDS",
    "parse_command",
    "check_path_in_bounds",
    "validate_command",
    "validate_command_text",
    "ExecResult",
    "run_command",
]