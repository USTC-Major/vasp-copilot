"""风险分级规则（M5→M47）：把工具请求映射成 (风险, 裁决, 原因, 卡片, 所需提权)。

判定口径（对齐用户确认的权限矩阵，本地与超算同一套逻辑）：
- 红线（提权/系统级/磁盘/数据外发）-> DENY（HIGH），即使申请提权也拒绝。
- 凭据/密钥路径（~/.ssh、~/.aws 等）-> DENY（HIGH），拒绝理由点名具体路径。
- 系统敏感路径：本地读写一律 DENY（硬边界）；远端写 DENY、远端只读（探活/查看）ALLOW。
- 高风险但可提权（仅递归删除 rm -r/-rf、网络下载 curl/wget）-> HOLD（弹卡=申请提权），
  同意后以「提权模式」执行，且只在作用域内有效。
- 常见命令（cd/grep/pwd/echo/mv/chmod、解释器、安装/编译等）默认 ALLOW。
- 目录外写/删 -> HOLD（弹卡，可提权）；目录外只读 -> ALLOW（只读，LOW）。
- 目录内读/常规命令 -> ALLOW（LOW）；目录内写 -> ALLOW（MEDIUM，写白名单兜底）。
- 提交作业类（submit_job 等）-> HOLD（HIGH），永不放行；真实提交仅经用户确认后的确定性入口。
"""
from __future__ import annotations

import posixpath
from typing import Any

from ai_mode.exec.errors import ExecutionPolicyViolation
from ai_mode.exec.policy import (
    WRITE_COMMANDS,
    classify_command_shape,
    credential_hit,
    is_sensitive_path,
    remote_path_token_bounds,
)
from ai_mode.llm.base import ToolRequest

from .models import ConsentCard, RiskLevel, VerdictKind

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
        return _classify_hpc_write_script(args)
    if name in COMMAND_TOOL_NAMES:
        return _classify_command(name, args, cwd=cwd)
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
    return (RiskLevel.MEDIUM, VerdictKind.HOLD,
            f"未知工具 {name} 需用户确认", None, frozenset())


def _rel_path_ok(text: str) -> bool:
    """词法级相对路径校验：非空、无盘符/绝对/越界段（不做 IO）。"""
    if not text or text in (".", ".."):
        return False
    norm = posixpath.normpath(text)
    return not (norm.startswith("/") or norm == ".."
                or norm.startswith("../"))


def _classify_hpc_upload(args: dict[str, Any]):
    """hpc_upload（本地工作区 -> hpc_dir 的 SFTP 上传）分级。

    路径词法非法 -> DENY；合法 -> HOLD（弹卡授权，同意后重放同参即放行）。
    真正的文件存在性/大小/越界检查由工具执行体在做（含重放时）。
    """
    src = str(args.get("source") or "").strip().replace("\\", "/")
    dest = str(args.get("dest") or "").strip().replace("\\", "/")
    if not _rel_path_ok(src):
        return (RiskLevel.HIGH, VerdictKind.DENY,
                "非法 source（仅允许本地工作区内的相对路径）", None,
                frozenset())
    if dest and not _rel_path_ok(dest):
        return (RiskLevel.HIGH, VerdictKind.DENY,
                "非法 dest（仅允许超算工作区内的相对路径）", None,
                frozenset())
    return (RiskLevel.MEDIUM, VerdictKind.HOLD,
            "把本地工作区文件上传到超算工作区需你授权"
            "（SFTP 只写 hpc_dir 内）", None, frozenset())


def _classify_hpc_write_script(args: dict[str, Any]):
    """hpc_write_script（AI 起草提交脚本写入超算）分级（M51）。

    用户政策（2026-08-31）：超算上允许 AI 写 *.sh，但必须逐次弹卡经用户同意；
    本地仍绝不写 *.sh（write_input 拒收不变）。filename 非法 -> DENY。
    """
    filename = str(args.get("filename") or "").strip().replace("\\", "/")
    if "/" in filename or not filename.lower().endswith(".sh"):
        return (RiskLevel.HIGH, VerdictKind.DENY,
                "只允许在超算作业目录写 *.sh 文件（filename 非法；"
                "本地绝不写 *.sh）", None, frozenset())
    return (RiskLevel.HIGH, VerdictKind.HOLD,
            "在超算上生成提交脚本（*.sh）属高危操作，必须你逐次授权后写入",
            None, frozenset())


def _classify_command(name: str, args: dict[str, Any], *, cwd):
    command = args.get("command") or args.get("cmd")
    if not command:
        return (RiskLevel.HIGH, VerdictKind.DENY, "命令工具缺少 command 参数",
                None, frozenset())
    try:
        shape = classify_command_shape(str(command), cwd=cwd)
    except ExecutionPolicyViolation as exc:
        return (RiskLevel.HIGH, VerdictKind.DENY, f"策略拦截: {exc.reason}",
                None, frozenset())
    # 红线命令：即使申请提权也拒绝
    if shape.tier == "deny":
        return (RiskLevel.HIGH, VerdictKind.DENY,
                f"红线命令拒绝: {shape.prog} —— {shape.tier_reason}",
                None, frozenset())
    cred_tok = next((t for t in shape.all_path_tokens if credential_hit(t)), None)
    if cred_tok is not None:
        return (RiskLevel.HIGH, VerdictKind.DENY,
                f"命令触及凭据/密钥路径 {cred_tok}，已拒绝以保护凭据",
                None, frozenset())
    if shape.sensitive:
        sys_tok = next((t for t in shape.all_path_tokens if is_sensitive_path(t)),
                       None) or "系统敏感路径"
        return (RiskLevel.HIGH, VerdictKind.DENY,
                f"命令触及系统敏感路径 {sys_tok}，已拒绝", None, frozenset())
    # 高风险可提权：工作区内破坏性 / 解释器 / 安装 / 下载（弹卡）
    if shape.tier == "hold":
        return (RiskLevel.HIGH, VerdictKind.HOLD,
                f"高风险操作需你授权：{shape.prog}（{shape.tier_reason}）",
                None, frozenset({PERMIT_HOLD}))
    # 写目标越出作用域 -> 弹卡（目录外写/删，可提权）
    if shape.write_out_of_bounds:
        return (RiskLevel.MEDIUM, VerdictKind.HOLD,
                "写入/删除目标在指定工作区之外，需要你授权提权",
                None, frozenset({PERMIT_OUT_OF_BOUNDS_WRITE}))
    if shape.has_write:
        return (RiskLevel.MEDIUM, VerdictKind.ALLOW,
                "写操作在计算目录内（写白名单已兜底）", None, frozenset())
    return (RiskLevel.LOW, VerdictKind.ALLOW, "只读/低风险命令放行", None,
            frozenset())


def classify_hpc_command(command: str, *, hpc_root: str
                         ) -> tuple[RiskLevel, VerdictKind, str, frozenset[str]]:
    """超算远端命令分级（锚定 hpc_dir）。

    远端无本地可解析文件系统，用词法防御判断路径档位：
    - 红线 DENY；凭据/密钥路径 DENY（点名具体路径）；
    - 解释器/破坏/安装/下载 HOLD（弹卡）；
    - 系统敏感路径：写 DENY、只读（探活/查看）ALLOW；
    - 越出工作区：写 HOLD、只读 ALLOW；
    - 工作区内读写 ALLOW。
    返回 (风险, 裁决, 原因, 所需提权)。
    """
    cmd = str(command or "").strip()
    if not cmd:
        return RiskLevel.HIGH, VerdictKind.DENY, "缺少命令", frozenset()
    try:
        shape = classify_command_shape(cmd, cwd=hpc_root)
    except ExecutionPolicyViolation as exc:
        return RiskLevel.HIGH, VerdictKind.DENY, f"策略拦截: {exc.reason}", frozenset()
    if shape.tier == "deny":
        return RiskLevel.HIGH, VerdictKind.DENY, \
            f"红线命令拒绝: {shape.prog} —— {shape.tier_reason}", frozenset()
    cred_tok = next((t for t in shape.all_path_tokens if credential_hit(t)), None)
    if cred_tok is not None:
        return RiskLevel.HIGH, VerdictKind.DENY, \
            f"命令触及凭据/密钥路径 {cred_tok}，为保护凭据已拒绝", frozenset()
    if shape.tier == "hold":
        return RiskLevel.HIGH, VerdictKind.HOLD, \
            f"远端高风险操作需你授权：{shape.prog}", frozenset({PERMIT_HOLD})
    sys_tok = next((t for t in shape.all_path_tokens if is_sensitive_path(t)), None)
    if sys_tok is not None:
        if shape.has_write:
            return RiskLevel.HIGH, VerdictKind.DENY, \
                f"命令写入系统敏感路径 {sys_tok}，已拒绝", frozenset()
        return RiskLevel.LOW, VerdictKind.ALLOW, \
            "远端只读查看系统路径，放行（探活/状态查询）", frozenset()
    out_tok = next(
        (t for t in shape.all_path_tokens
         if remote_path_token_bounds(t, str(hpc_root)) == "out"), None)
    if out_tok is not None:
        if shape.has_write:
            return RiskLevel.MEDIUM, VerdictKind.HOLD, \
                "远端写目标在指定超算工作区之外，需要你授权提权", \
                frozenset({PERMIT_OUT_OF_BOUNDS_WRITE})
        return RiskLevel.LOW, VerdictKind.ALLOW, \
            "远端越出工作区的只读操作，放行", frozenset()
    if shape.has_write:
        return RiskLevel.MEDIUM, VerdictKind.ALLOW, "远端工作区内写操作放行", frozenset()
    return RiskLevel.LOW, VerdictKind.ALLOW, "远端只读/低风险命令放行", frozenset()