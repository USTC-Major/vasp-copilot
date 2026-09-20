"""受限执行器（M4）：本地受限命令执行 + 输出截断 + 整洁回报。

安全：不经过 shell；危险黑名单 / 路径越界 / 写白名单由 :mod:`policy` 先拦截。
执行原语只做翻译与执行，不做授权判断（授权口径在 M5 门卫）。

返回给 LLM 的报文：成功与否 + 简短摘要（剪短给 LLM）+ 输出片段（按量截断）。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ExecutionPolicyViolation
from .policy import (RedirectSpec, check_path_in_bounds, parse_command,
                     validate_command)

__all__ = ["ExecResult", "run_command", "build_exec_env"]

DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_OUTPUT_CHARS = 6000
DEFAULT_MAX_RAW_BYTES = 200_000

_TRUNC_MARKER = "\n…（输出过长，仅保留前后片段；完整文本见文件/报告）\n"
_CWD_VALID = object()


@dataclass
class ExecResult:
    """执行结果报文（执行器 → LLM，接口 §2：成功与否 + 摘要 + 片段）。"""

    ok: bool = False
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    truncated: bool = False
    timed_out: bool = False
    interrupted: bool = False
    command: str = ""
    error: str = ""
    duration_ms: int = 0

    def summary(self, limit: int = 300) -> str:
        """给 LLM 的简报：状态一行 + 输出片段（截断折行显示）。"""
        head: list[str] = []
        if self.error:
            head.append(self.error)
        elif self.timed_out:
            head.append("命令超时被终止")
        elif self.ok:
            head.append(f"命令成功（exit={self.exit_code}）")
        else:
            head.append(f"命令失败（exit={self.exit_code}）")
        if self.truncated:
            head.append("输出已截断")
        combined = self.stdout.strip()
        if self.stderr.strip():
            combined = (combined + "\n[stderr]\n" + self.stderr.strip()).strip()
        head_text = "；".join(head)
        room = max(0, limit - len(head_text) - 4)
        snippet = " ".join(combined[:room].replace("\r", "").splitlines())
        if len(combined) > room:
            snippet += "…"
        return f"{head_text}\n输出：{snippet}"


def run_command(
    command: str,
    *,
    cwd: str | Path,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
    max_raw_bytes: int = DEFAULT_MAX_RAW_BYTES,
    env: dict[str, str] | None = None,
    extra_paths: list[str] | None = None,
    permit_hold: bool = False,
    permit_out_of_bounds_write: bool = False,
    should_stop: Any | None = None,
) -> ExecResult:
    """执行一条（已授权）命令。任何策略违规都返回 ok=False 报文，不抛异常。

    :param cwd: 计算目录（同时是路径越界/写白名单的根）。
    :param timeout_seconds: 超时后强杀并置 timed_out。
    :param max_output_chars: 返回给 LLM 的输出字符上限（超出前后截断）。
    :param max_raw_bytes: 解码前对输出字节的安全硬顶，防超大文件撑爆内存。
    """
    result = ExecResult(command=command)
    started = time.monotonic()
    try:
        argv, redirects = parse_command(command)
        validate_command(argv, cwd=cwd, redirects=redirects, command=command,
                         permit_hold=permit_hold,
                         permit_out_of_bounds_write=permit_out_of_bounds_write)
    except ExecutionPolicyViolation as exc:
        return _fail(result, exc.reason, started)

    cwd_path = Path(cwd).expanduser()
    if not cwd_path.is_dir():
        return _fail(result, f"计算目录不存在: {cwd}", started)

    # cd 属内建命令：执行器以固定工作目录运行，不改变 cwd；返回说明避免「命令不存在」。
    if os.path.basename(argv[0]).lower() == "cd":
        result.ok = True
        result.exit_code = 0
        result.stdout = (f"执行器以固定工作目录运行，cd 不改变目录；当前目录仍为 "
                         f"{cwd_path}")
        result.duration_ms = _duration_ms(started)
        return result

    proc_env = build_exec_env(env=env, extra_paths=extra_paths)
    exe = shutil.which(argv[0], path=proc_env["PATH"])
    if exe is None and ("/" not in argv[0] and "\\\\" not in argv[0]):
        return _fail(result, f"命令不存在（PATH 中找不到）: {argv[0]}", started)
    argv = [exe or argv[0]] + argv[1:]
    kwargs: dict[str, Any] = {
        "cwd": str(cwd_path),
        "env": proc_env,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    try:
        proc = subprocess.Popen(argv, **kwargs)
    except FileNotFoundError:
        return _fail(result, f"命令不存在（PATH 中找不到）: {argv[0]}", started)
    except PermissionError:
        return _fail(result, f"命令无执行权限: {argv[0]}", started)
    except OSError as exc:
        return _fail(result, f"执行失败: {argv[0]}（{exc}）", started)

    deadline = time.monotonic() + timeout_seconds
    while proc.poll() is None:
        if should_stop is not None and should_stop():
            _terminate_proc(proc)
            result.interrupted = True
            break
        if time.monotonic() >= deadline:
            _terminate_proc(proc)
            result.timed_out = True
            break
        time.sleep(0.05)
    try:
        out_bytes, err_bytes = proc.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        return _fail(result, f"命令清理超时: {argv[0]}", started)

    redir_err = _write_redirects(redirects, out_bytes, err_bytes, cwd_path)
    if redir_err:
        return _fail(result, redir_err, started)

    if result.interrupted:
        result.exit_code = proc.returncode
        result.error = "命令已被用户停止"
        result.stdout, tr_o = _truncate(_decode(out_bytes), max_output_chars)
        result.stderr, tr_e = _truncate(_decode(err_bytes), max_output_chars)
        result.truncated = tr_o or tr_e
        result.duration_ms = _duration_ms(started)
        return result

    out_c, clip_o = _clip_bytes(out_bytes, max_raw_bytes)
    err_c, clip_e = _clip_bytes(err_bytes, max_raw_bytes)
    out, tr_o = _truncate(_decode(out_c), max_output_chars)
    err, tr_e = _truncate(_decode(err_c), max_output_chars)
    result.ok = proc.returncode == 0
    result.exit_code = proc.returncode
    result.stdout = out
    result.stderr = err
    result.truncated = clip_o or clip_e or tr_o or tr_e
    result.duration_ms = _duration_ms(started)
    return result


def build_exec_env(*, env: dict[str, str] | None = None,
                   extra_paths: list[str] | None = None) -> dict[str, str]:
    """构造子进程环境。Windows 上自动补 Git/usr/bin，使 pwd/ls/cat/grep 等可用。"""
    merged = dict(os.environ)
    if env:
        merged.update(env)
    paths: list[str] = list(extra_paths or [])
    if os.name == "nt":
        paths.extend(_windows_tool_dirs())
    old = merged.get("PATH", "")
    if old:
        paths.append(old)
    merged["PATH"] = os.pathsep.join(paths)
    return merged


def _windows_tool_dirs() -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    candidates = []
    for var in ("ProgramFiles", "ProgramW6432"):
        base = os.environ.get(var)
        if base:
            candidates.append(Path(base) / "Git")
    candidates.append(Path(r"C:\Program Files") / "Git")
    for git_root in candidates:
        for sub in ("usr\\bin", "bin"):
            cand = git_root / sub
            try:
                if cand.is_dir() and str(cand) not in seen:
                    seen.add(str(cand))
                    out.append(str(cand))
            except OSError:
                continue
    return out


def _write_redirects(spec: RedirectSpec, stdout_bytes: bytes, stderr_bytes: bytes,
                     cwd_path: Path) -> str | None:
    """落地重定向（先再次写白名单校验，防 TOCTOU）。成功返回 None。"""
    try:
        if spec.stdout is not None:
            target = check_path_in_bounds(spec.stdout, cwd_path, write=True)
            target.parent.mkdir(parents=True, exist_ok=True)
            mode = "ab" if spec.stdout_append else "wb"
            with target.open(mode) as fh:
                fh.write(stdout_bytes)
        if spec.stderr is not None:
            target = check_path_in_bounds(spec.stderr, cwd_path, write=True)
            target.parent.mkdir(parents=True, exist_ok=True)
            mode = "ab" if spec.stderr_append else "wb"
            with target.open(mode) as fh:
                fh.write(stderr_bytes)
    except ExecutionPolicyViolation as exc:
        return str(exc)
    except OSError as exc:
        return f"写入重定向目标失败: {exc}"
    return None


def _decode(data: bytes) -> str:
    if not data:
        return ""
    for enc in ("utf-8", "gb18030"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def _clip_bytes(data: bytes, cap: int) -> tuple[bytes, bool]:
    if cap <= 0 or len(data) <= cap:
        return data, False
    return data[:cap], True


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    if limit <= 0:
        return "", bool(text)
    if len(text) <= limit:
        return text, False
    keep_head = int(limit * 0.6)
    keep_tail = max(0, limit - keep_head - len(_TRUNC_MARKER))
    head = text[:keep_head]
    tail = text[-keep_tail:] if keep_tail else ""
    return head + _TRUNC_MARKER + tail, True


def _fail(result: ExecResult, error: str, started: float) -> ExecResult:
    result.ok = False
    result.error = error
    result.duration_ms = _duration_ms(started)
    return result


def _duration_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _terminate_proc(proc: subprocess.Popen) -> None:
    """尽力终止子进程：先 terminate 再 wait 2s，仍未退出则 kill。"""
    try:
        proc.terminate()
    except OSError:
        pass
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
