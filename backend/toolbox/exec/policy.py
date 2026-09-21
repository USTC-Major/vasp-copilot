"""受限执行器安全策略（M4）：危险黑名单 + 写白名单 + 路径越界拦截。

对齐 ``AI_MODE_SECURITY_BOUNDARY.md`` §5.2（已确认采纳）：
- 越界类：cd 出计算目录、操作系统路径、敏感路径（~/.ssh、凭据文件）。
- 破坏类：仅递归删除 rm -r/-rf 需弹卡授权；rm 普通删除、mv/chmod/chown 直接放行，mkfs/dd 底层写盘红线仍拒绝。
- 外带类：数据不出本机/超算目录边界（scp/rsync/curl/wget/邮件等）。
- 提权类：sudo/su/doas/pkexec 等。
- 解释器类：python/bash/sh/perl、安装器/编译器默认直接放行（敏感路径与越界写仍拦截）。
- 写操作白名单：写目标路径只允许在计算目录（cwd）内新增/覆盖本人文件。

执行模型：绝不经过 shell —— 命令先用 ``shlex(split,posix=False)`` 拆成 argv
独立运行；> / >> / 2> / 2>> 重定向由本模块解析并落地（安全版）；黑名单作用在
argv[0]；所有「看起来像路径」的参数都必须解析落在计算目录内。即使某可执行程序
本身有漏洞，也无法借 shell 元字符逃逸、也无法越出计算目录读写。
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path

from .errors import ExecutionPolicyViolation

__all__ = [
    "RedirectSpec",
    "CommandShape",
    "DANGEROUS_COMMANDS",
    "HOLD_COMMANDS",
    "DENY_COMMANDS",
    "WRITE_COMMANDS",
    "parse_command",
    "check_path_in_bounds",
    "validate_command",
    "validate_command_text",
    "classify_command_shape",
    "remote_path_token_bounds",
    "credential_hit",
    "is_sensitive_path",
]


#: 拒绝命令黑名单：命令字 -> 拒绝原因（安全边界 §5.2 展开；弹卡项见 _HOLD_PROGS）。
DANGEROUS_COMMANDS: dict[str, str] = {
    # 破坏类
    "rm": "递归删除 rm -r/-rf 属破坏性删除，需经用户授权后执行",
    "mkfs": "mkfs* 创建文件系统=底层破坏，不放行",
    "mkswap": "mkswap 创建交换分区，不放行",
    "fdisk": "fdisk 分区操作，不放行",
    "parted": "parted 分区操作，不放行",
    "dd": "dd 底层写盘，不放行",
    # 提权类
    "sudo": "sudo 提权，不放行",
    "su": "su 切换用户/提权，不放行",
    "doas": "doas 提权，不放行",
    "pkexec": "pkexec 提权执行，不放行",
    "runuser": "runuser 以其他用户运行，不放行",
    "run0": "run0 提权（systemd 类），不放行",
    # 系统控制
    "shutdown": "shutdown 关停系统，不放行",
    "reboot": "reboot 重启系统，不放行",
    "halt": "halt 停机，不放行",
    "poweroff": "poweroff 关机，不放行",
    "init": "init 切换运行级，不放行",
    "systemctl": "systemctl 控制系统服务，不放行",
    "service": "service 控制系统服务，不放行",
    "mount": "mount 挂载操作，不放行",
    "umount": "umount 卸载操作，不放行",
    "format": "format 格式化磁盘，不放行",
    "taskkill": "taskkill 结束进程，不放行",
    "tskill": "tskill 结束进程，不放行",
    "reg": "reg 修改注册表，不放行",
    "regedit": "regedit 修改注册表，不放行",
    # 外带类（数据不出边界）
    "scp": "scp 复制外带，不放行",
    "sftp": "sftp 文件外传，不放行",
    "rsync": "rsync 同步=数据外带，不放行",
    "curl": "curl 网络传输=数据外带，不放行",
    "wget": "wget 网络传输=数据外带，不放行",
    "mail": "mail 邮件外发=数据外带，不放行",
    "mailx": "mailx 邮件外发，不放行",
    "sendmail": "sendmail 邮件外发，不放行",
    "mutt": "mutt 邮件外发，不放行",
    "nc": "nc 网络直连，不放行",
    "ncat": "ncat 网络直连，不放行",
    "socat": "socat 网络转发，不放行",
    "telnet": "telnet 直连外部，不放行",
    "ftp": "ftp 外部传输，不放行",
    "lftp": "lftp 外部传输，不放行",
    "wput": "wput 文件外传，不放行",
    "tftp": "tftp 外部传输，不放行",
    # 常见解释器/安装器/编译器默认放行（敏感路径、越界写仍由策略拦截）
    # 其它系统管理
    "wmic": "wmic 系统管理，不放行",
    "sc": "sc 服务控制，不放行",
        "wsl": "wsl 进入其他系统，不放行",
}

#: 红线命令子集（即使申请提权也保持拒绝）：提权 / 系统级 / 磁盘 / 数据外发 / 固定 cwd。
_REDLINE_PROGS = frozenset({
    "sudo", "su", "doas", "pkexec", "runuser", "run0",
    "shutdown", "reboot", "halt", "poweroff", "init", "systemctl",
    "service", "mount", "umount", "format", "taskkill", "tskill",
    "reg", "regedit", "wmic", "sc", "wsl",
    "mkfs", "mkswap", "fdisk", "parted", "dd",
    "scp", "sftp", "rsync", "mail", "mailx", "sendmail", "mutt",
    "nc", "ncat", "socat", "telnet", "ftp", "lftp", "wput", "tftp",
})

#: 弹卡可提权命令子集：仅递归删除 rm -r/-rf、网络下载 curl/wget（其余直接放行）。
_HOLD_PROGS = frozenset({"rm", "curl", "wget"})

HOLD_COMMANDS: dict[str, str] = {k: DANGEROUS_COMMANDS[k]
                                 for k in _HOLD_PROGS
                                 if k in DANGEROUS_COMMANDS}
DENY_COMMANDS: dict[str, str] = {k: v
                                 for k, v in DANGEROUS_COMMANDS.items()
                                 if k not in HOLD_COMMANDS}


def _rm_is_recursive(argv: list[str] | None) -> bool:
    """rm 是否递归删除（-r/-R/-rf/--recursive 及合并短选项，到第一个非选项参数为止）。"""
    if not argv:
        return False
    for tok in argv[1:]:
        if not tok.startswith("-"):
            break
        body = tok.lstrip("-")
        if tok == "--":
            continue
        if "r" in body or "R" in body:
            return True
    return False


def _command_tier(prog: str, argv: list[str] | None = None) -> tuple[str, str]:
    """把命令字分成 deny（红线）/ hold（弹卡可提权）/ ok 三档，返回 (档, 原因)。"""
    if prog in DENY_COMMANDS or (prog.startswith("mkfs") and "mkfs" in DENY_COMMANDS):
        why = DENY_COMMANDS.get(prog) or DENY_COMMANDS.get("mkfs", "") or "红线命令"
        return "deny", why
    if prog in HOLD_COMMANDS:
        if prog == "rm" and not _rm_is_recursive(argv):
            return "ok", ""
        return "hold", HOLD_COMMANDS[prog]
    return "ok", ""

#: 写操作语义命令（写白名单的适用对象）：本身会写文件。
WRITE_COMMANDS: frozenset[str] = frozenset({
    "mkdir", "touch", "cp", "tee", "ln", "sed",
})

#: 凭据/密钥路径片段（~/.ssh、~/.aws 等；命中一律拒绝，拒绝理由中点名具体路径）。
_CREDENTIAL_PARTS: tuple[str, ...] = (
    "/.ssh", "\\.ssh",
    "/.gnupg", "\\.gnupg",
    "/.aws", "\\.aws",
    "/.azure", "\\.azure",
    "/.codex", "\\.codex",
    "/.config", "\\.config",
    "/.vasp-ai", "\\.vasp-ai",
)

#: 全部敏感路径片段（凭据 + 系统区；本地读写一律拒绝，远端只读可放行探活）。
_SENSITIVE_PARTS: tuple[str, ...] = _CREDENTIAL_PARTS + (
    "/etc", "/boot", "/proc", "/sys", "/dev", "/var", "/root", "\\windows",
    "\\windows\\system32", "\\program files",
)


@dataclass
class RedirectSpec:
    """命令重定向（安全版 > / >> / 2> / 2>>）。目标路径在写白名单内校验。"""

    stdout: str | None = None
    stdout_append: bool = False
    stderr: str | None = None
    stderr_append: bool = False


def _tokenize(command: str) -> list[str]:
    raw = shlex.split(command, posix=False)
    return [_strip_quotes(tok) for tok in raw]


def _strip_quotes(token: str) -> str:
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
        # 去掉成对的外层引号（posix=False 会原样保留它们）
        return token[1:-1]
    return token


def _fill_redirect(spec: RedirectSpec, slot: str, tokens: list[str], i: int,
                   *, append: bool) -> None:
    if i + 1 >= len(tokens):
        raise ExecutionPolicyViolation("重定向缺少目标文件")
    target = tokens[i + 1]
    if slot == "stdout":
        spec.stdout = target
        spec.stdout_append = append
    else:
        spec.stderr = target
        spec.stderr_append = append


def parse_command(command: str) -> tuple[list[str], RedirectSpec]:
    """把类 shell 命令拆成 (argv, RedirectSpec)。绝不经过真实 shell。"""
    spec = RedirectSpec()
    try:
        tokens = _tokenize(command)
    except ValueError as exc:
        raise ExecutionPolicyViolation(f"命令解析失败: {exc}", command=command) from exc
    if not tokens:
        raise ExecutionPolicyViolation("空命令", command=command)
    argv: list[str] = []
    i, n = 0, len(tokens)
    while i < n:
        low = tokens[i].lower()
        if low in (">", "1>"):
            _fill_redirect(spec, "stdout", tokens, i, append=False)
            i += 2
        elif low == ">>":
            _fill_redirect(spec, "stdout", tokens, i, append=True)
            i += 2
        elif low == "2>":
            _fill_redirect(spec, "stderr", tokens, i, append=False)
            i += 2
        elif low == "2>>":
            _fill_redirect(spec, "stderr", tokens, i, append=True)
            i += 2
        elif low == "2>&1":
            raise ExecutionPolicyViolation(
                "2>&1 错误合并暂不支持：请分开写 > 与 2>", command=command)
        elif low == "<":
            raise ExecutionPolicyViolation(
                "输入重定向 < 暂不支持（M4 只做计算或写计算目录文件）",
                command=command)
        else:
            argv.append(tokens[i])
            i += 1
    if spec.stdout and spec.stderr and spec.stdout == spec.stderr:
        raise ExecutionPolicyViolation("> 与 2> 不能指向同一文件")
    return argv, spec


def _looks_like_path(token: str) -> bool:
    if not token or token == "-":
        return False
    if token.startswith("-"):
        if "=" in token or token.startswith("--"):
            # 选项可能携带路径值：--out=xx 取等号后；--out xx 时 xx 作为下一参数单独判断
            if "=" in token:
                token = token.split("=", 1)[1]
            else:
                return False
        else:
            return False
    if token == ".":
        return False
    if token in ("..", "~"):
        return True
    if "/" in token or "\\" in token or token.startswith("~"):
        return True
    return False


def credential_hit(path: str) -> bool:
    """是否命中凭据/密钥路径片段（~/.ssh、~/.aws 等）；命中一律拒绝并在理由中点名。"""
    low = path.lower()
    return any(part.lower() in low for part in _CREDENTIAL_PARTS)


def is_sensitive_path(path: str) -> bool:
    """是否命中任一路径敏感片段（凭据或系统区）。"""
    low = path.lower()
    return any(part.lower() in low for part in _SENSITIVE_PARTS)


def _sensitive_hit(path: str) -> bool:
    return is_sensitive_path(path)


def check_path_in_bounds(path: str | Path, root: Path, *, write: bool = False) -> Path:
    """解析并校验路径必须落在计算目录 root 内，否则抛 ExecutionPolicyViolation。

    符号链接/相对路径经 resolve() 展开，防 ``..`` 与 link 逃逸；信任边界 = root。
    """
    p = Path(str(path)).expanduser()
    if not p.is_absolute():
        p = root / p
    try:
        resolved = p.resolve()
    except OSError as exc:
        raise ExecutionPolicyViolation(f"路径无法解析: {path}（{exc}）")
    try:
        root_resolved = root.resolve()
    except OSError as exc:
        raise ExecutionPolicyViolation(f"计算目录无法解析: {root}（{exc}）")
    try:
        resolved.relative_to(root_resolved)
    except ValueError:
        if _sensitive_hit(str(resolved)):
            raise ExecutionPolicyViolation(f"敏感路径不可达: {path}")
        label = "写白名单：目标路径越出计算目录" if write else "越出计算目录"
        raise ExecutionPolicyViolation(f"{label}: {path}（只能在 {root} 内操作）")
    return resolved


def write_targets(argv: list[str], redirects: RedirectSpec | None) -> list[str]:
    """提取需要写白名单校验的目标路径（重定向目标 + 写语义命令的路径参数）。"""
    out: list[str] = []
    if redirects is not None:
        if redirects.stdout:
            out.append(redirects.stdout)
        if redirects.stderr:
            out.append(redirects.stderr)
    if argv:
        prog = Path(argv[0]).name.lower()
        if prog in ("mkdir", "touch", "cp", "tee", "ln", "sed"):
            for tok in argv[1:]:
                if _looks_like_path(tok):
                    out.append(tok)
    return out


def _all_path_tokens(argv: list[str], redirects: RedirectSpec | None) -> list[str]:
    """收集命令里所有「看起来像路径」的 token（含重定向目标），供敏感路径检查。"""
    out: list[str] = []
    if redirects is not None:
        if redirects.stdout:
            out.append(redirects.stdout)
        if redirects.stderr:
            out.append(redirects.stderr)
    for tok in argv[1:]:
        if _looks_like_path(tok):
            out.append(tok)
    return out


@dataclass
class CommandShape:
    """命令结构化判定结果（authorize 层使用；不抛异常）。"""

    command: str
    argv: list[str]
    redirects: RedirectSpec | None
    prog: str
    tier: str                     # "deny" | "hold" | "ok"
    tier_reason: str
    sensitive: bool               # 任一 token 命中敏感路径（永远拒绝）
    write_out_of_bounds: bool     # 写目标越出计算目录（默认拒绝，授权后可写）
    has_write: bool
    all_path_tokens: list[str]
    write_targets: list[str]


def classify_command_shape(command: str, *, cwd: str | Path) -> CommandShape:
    """解析并判定一条命令的权限档位（供 authorize.rules 分级；不抛策略异常）。"""
    argv, redirects = parse_command(command)
    prog = Path(argv[0]).name.lower() if argv else ""
    tier, tier_reason = _command_tier(prog, argv)
    root = Path(cwd).expanduser()
    write_targets_raw = write_targets(argv, redirects)
    tokens = _all_path_tokens(argv, redirects)
    sensitive = any(_sensitive_hit(t) for t in tokens)
    write_out = False
    for target in write_targets_raw:
        try:
            check_path_in_bounds(target, root, write=True)
        except ExecutionPolicyViolation:
            write_out = True
    wrote = (redirects is not None
             and (redirects.stdout is not None or redirects.stderr is not None)) \
        or (prog in WRITE_COMMANDS) or bool(write_targets_raw)
    return CommandShape(command=command, argv=argv, redirects=redirects,
                        prog=prog, tier=tier, tier_reason=tier_reason,
                        sensitive=sensitive, write_out_of_bounds=write_out,
                        has_write=wrote,
                        all_path_tokens=tokens,
                        write_targets=write_targets_raw)


def remote_path_token_bounds(token: str, root: str) -> str:
    """超算远端路径的保守词典档位：in（工作区内）/ out（工作区外）/ sensitive。

    远端没有本地可解析文件系统，做防御性词法检查：拒绝 .. 、~、越界绝对路径、敏感片段。
    """
    if _sensitive_hit(token):
        return "sensitive"
    low = token.replace("\\", "/")
    if low in ("..", "/") or low.startswith("~") or low.startswith("/.."):
        return "out"
    if any(part == ".." for part in low.split("/")):
        return "out"
    rootn = str(root).replace("\\", "/").rstrip("/")
    if rootn and low.startswith("/"):
        if low == rootn or low.startswith(rootn + "/"):
            return "in"
        return "out"
    return "in"


def validate_command(argv: list[str], *, cwd: str | Path,
                     redirects: RedirectSpec | None = None,
                     command: str = "",
                     permit_hold: bool = False,
                     permit_out_of_bounds_write: bool = False) -> None:
    """校验 argv：红线条目 / 敏感路径 / 写白名单；违规抛异常。

    目录外「只读」放行；写目标必须落在计算目录内（permit_out_of_bounds_write
    仅在用户授权后放行目录外写入，敏感路径即使授权也拒绝）。
    """
    epic = command or " ".join(argv)
    if not argv:
        raise ExecutionPolicyViolation("空命令", command=epic)
    prog = Path(argv[0]).name.lower()
    tier, why = _command_tier(prog, argv)
    if tier == "deny":
        raise ExecutionPolicyViolation(
            f"危险命令黑名单: {prog} —— {why}", command=epic)
    if tier == "hold" and not permit_hold:
        raise ExecutionPolicyViolation(
            f"危险命令黑名单: {prog} —— {why}（需用户授权后方可执行）", command=epic)
    root = Path(cwd).expanduser()
    write_toks = set(write_targets(argv, redirects))
    for tok in _all_path_tokens(argv, redirects):
        if _sensitive_hit(tok):
            raise ExecutionPolicyViolation(f"敏感路径不可达: {tok}", command=epic)
    for target in write_toks:
        try:
            check_path_in_bounds(target, root, write=True)
        except ExecutionPolicyViolation as exc:
            if permit_out_of_bounds_write:
                continue
            raise exc
    # 只读命令对目录外读取放行（敏感路径已在上方拒绝）


def validate_command_text(command: str, *, cwd: str | Path) -> tuple[list[str], RedirectSpec]:
    """解析并校验整条命令（门卫复用）；违规抛 ExecutionPolicyViolation。"""
    argv, redirects = parse_command(command)
    validate_command(argv, cwd=cwd, redirects=redirects, command=command)
    return argv, redirects