"""Risk classification for the narrow AI-mode capability surface.

Free-form local/remote commands and AI-authored scripts are permanently
denied. Known read operations are low risk, deterministic mutations require a
single-use consent action, and scheduler submission remains behind its own
exact confirmation boundary.
"""
from __future__ import annotations

import posixpath
from typing import Any

from ai_mode.llm.base import ToolRequest

from .models import RiskLevel, VerdictKind

__all__ = ["classify", "classify_hpc_command", "COMMAND_TOOL_NAMES",
           "SUBMIT_TOOL_NAMES", "LOW_RISK_TOOLS", "MEDIUM_RISK_TOOLS",
           "PERMIT_HOLD", "PERMIT_OUT_OF_BOUNDS_WRITE"]

COMMAND_TOOL_NAMES = ("execute_command", "exec", "run_exec", "hpc_exec",
                      "hpc_execute")
SUBMIT_TOOL_NAMES = ("submit_job", "confirm_submit", "sbatch")

LOW_RISK_TOOLS = frozenset({"read_file", "list_files"})
MEDIUM_RISK_TOOLS = frozenset({"write_file", "mkdir", "touch"})

#: 提权模式标记（授权后执行器据此放行）。
PERMIT_HOLD = "hold"
PERMIT_OUT_OF_BOUNDS_WRITE = "out_of_bounds_write"


def classify(tool: ToolRequest, *, cwd):
    """把一个工具请求分级。

    返回 (风险, 裁决, 原因, 卡片|None, 所需提权 frozenset)。
    """
    name = (tool.name or "").strip().lower()
    args = tool.args or {}
    if name == "hpc_upload":
        return _classify_hpc_upload(args)
    if name == "hpc_write_script":
        return (RiskLevel.HIGH, VerdictKind.DENY,
                "AI_TOOL_NOT_ALLOWED: AI 不得生成或写入提交脚本",
                None, frozenset())
    if name in COMMAND_TOOL_NAMES:
        return (RiskLevel.HIGH, VerdictKind.DENY,
                "AI_FREEFORM_EXEC_DISABLED: 任意命令执行已禁用",
                None, frozenset())
    if name in SUBMIT_TOOL_NAMES:
        return (RiskLevel.HIGH, VerdictKind.HOLD,
                "提交作业须用户确认（弹卡）；真实提交仅经系统确定性入口",
                None, frozenset())
    if name in LOW_RISK_TOOLS:
        return (RiskLevel.LOW, VerdictKind.ALLOW, "低风险只读工具，放行",
                None, frozenset())
    if name in MEDIUM_RISK_TOOLS:
        return (RiskLevel.MEDIUM, VerdictKind.HOLD,
                f"工具 {name} 需用户确认", None, frozenset())
    return (RiskLevel.HIGH, VerdictKind.DENY,
            f"AI_TOOL_NOT_ALLOWED: 未允许的工具 {name}", None, frozenset())


def _rel_path_ok(text: str) -> bool:
    """词法级相对路径校验：非空、无盘符/绝对/越界段（不做 IO）。"""
    if not text or text in (".", ".."):
        return False
    norm = posixpath.normpath(text)
    return not (norm.startswith("/") or norm == ".."
                or norm.startswith("../"))


def _classify_hpc_upload(args: dict[str, Any]):
    """hpc_upload（本地工作区 -> hpc_dir 的 SFTP 上传）分级。

    只接受登记 artifact ID 与可选规划作业 key，不接受模型提供任意源/目标路径。
    调用方须创建并执行精确绑定的单次 action；原始 LLM 请求永不重放。
    """
    artifact_id = str(args.get("artifact_id") or "").strip()
    job_key = str(args.get("job_key") or "").strip().replace("\\", "/")
    if not artifact_id.startswith("art_") or len(artifact_id) > 128:
        return (RiskLevel.HIGH, VerdictKind.DENY,
                "上传只接受已登记 artifact_id", None,
                frozenset())
    if job_key and not _rel_path_ok(job_key):
        return (RiskLevel.HIGH, VerdictKind.DENY,
                "非法 job_key（仅允许规划内相对作业路径）", None,
                frozenset())
    return (RiskLevel.MEDIUM, VerdictKind.HOLD,
            "把本地工作区文件上传到超算工作区需你授权"
            "（SFTP 只写 hpc_dir 内）", None, frozenset())


def classify_hpc_command(command: str, *, hpc_root: str
                         ) -> tuple[RiskLevel, VerdictKind, str, frozenset[str]]:
    """Compatibility classifier: every remote free-form command is denied."""
    del command, hpc_root
    return (RiskLevel.HIGH, VerdictKind.DENY,
            "AI_FREEFORM_EXEC_DISABLED: 任意远程命令执行已禁用",
            frozenset())
