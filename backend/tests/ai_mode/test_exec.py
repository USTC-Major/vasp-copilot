"""M4 受限执行器测试：黑名单/越界/写白名单（纯策略）+ 真实受限执行（ls/mkdir/重定向/截断/超时/未找到）。"""

import os
import shutil
from pathlib import Path

import pytest

from ai_mode.exec import (
    ExecutionPolicyViolation,
    check_path_in_bounds,
    parse_command,
    run_command,
    validate_command_text,
)


def _tool_available(name: str) -> bool:
    roots = []
    for var in ("ProgramFiles", "ProgramW6432"):
        base = os.environ.get(var)
        if base:
            roots.append(Path(base) / "Git" / "usr" / "bin")
    candidates = [root / f"{name}.exe" for root in roots] + [Path(shutil.which(name))] \
        if shutil.which(name) else [root / f"{name}.exe" for root in roots]
    return any(bool(c and c.is_file()) for c in candidates)


_HAS_CORE = all(_tool_available(n) for n in ("pwd", "ls", "mkdir", "cat", "grep", "seq"))
_HAS_SLEEP = _tool_available("sleep")


def _mk_calc(tmp_path) -> Path:
    d = tmp_path / "calc"
    d.mkdir()
    (d / "INCAR").write_text("ENCUT=500\nISMEAR=0\n", encoding="utf-8")
    (d / "POSCAR").write_text("sample poscar\n", encoding="utf-8")
    return d


# ---------------- 解析 ----------------

def test_parse_basic_and_redirect():
    argv, spec = parse_command("ls -la > out.txt")
    assert argv == ["ls", "-la"]
    assert spec.stdout == "out.txt"
    assert spec.stdout_append is False


def test_parse_append_and_stderr():
    argv, spec = parse_command("grep TOTEN OUTCAR >> log.txt")
    assert argv == ["grep", "TOTEN", "OUTCAR"]
    assert spec.stdout == "log.txt"
    assert spec.stdout_append is True
    _, spec2 = parse_command("ls 2> err.txt")
    assert spec2.stderr == "err.txt"


def test_parse_keeps_quoted_windows_path():
    argv, _ = parse_command('cat "D:\\calc dir with space\\INCAR"')
    assert argv == ["cat", "D:\\calc dir with space\\INCAR"]


def test_parse_rejects_input_redirect():
    with pytest.raises(ExecutionPolicyViolation):
        parse_command("cat < input.txt")


# ---------------- 黑名单 / 越界 / 写白名单 ----------------

@pytest.mark.parametrize("cmd", [
    "rm -rf .",
    "rm -r out",
    "sudo apt install vasp",
    "curl -o leak http://x",
    "wget -O x http://y",
    "scp a b",
    "dd if=/dev/zero of=x",
])
def test_dangerous_blacklist(cmd, tmp_path):
    with pytest.raises(ExecutionPolicyViolation, match="危险命令黑名单"):
        validate_command_text(cmd, cwd=_mk_calc(tmp_path))


def test_common_commands_allowed(tmp_path):
    d = _mk_calc(tmp_path)
    for cmd in ("cd ..", "pwd", "echo hello > out.txt", "grep E INCAR",
                "mv a b", "chmod 700 run.sh", "python run.py",
                "bash -c echo hi", "rm OUTCAR",
                "powershell -Command Get-Help"):
        argv, _ = validate_command_text(cmd, cwd=d)
        assert argv, cmd


def test_escape_parent_and_absolute_denied(tmp_path):
    d = _mk_calc(tmp_path)
    with pytest.raises(ExecutionPolicyViolation, match="越出计算目录|敏感路径"):
        validate_command_text("cat '..\\..\\..\\Windows\\System32\\drivers\\etc\\hosts'", cwd=d)
    with pytest.raises(ExecutionPolicyViolation, match="越出计算目录|敏感路径"):
        validate_command_text("cat C:\\Windows\\win.ini", cwd=d)


def test_sensitive_path_wording(tmp_path):
    root = tmp_path / "calc"
    root.mkdir()
    with pytest.raises(ExecutionPolicyViolation, match="敏感路径"):
        check_path_in_bounds("C:\\Windows\\System32\\drivers\\etc\\hosts", root)


def test_write_whitelist_denies_outside(tmp_path):
    d = _mk_calc(tmp_path)
    with pytest.raises(ExecutionPolicyViolation, match="写白名单"):
        validate_command_text("ls > D:\\outside.txt", cwd=d)
    with pytest.raises(ExecutionPolicyViolation, match="写白名单"):
        validate_command_text("mkdir ..\\evil", cwd=d)


def test_write_whitelist_allows_inside(tmp_path):
    d = _mk_calc(tmp_path)
    validate_command_text("mkdir -p sub/level2", cwd=d)
    validate_command_text("ls > listing.txt", cwd=d)
    validate_command_text("touch newfile.txt", cwd=d)


# ---------------- 真实执行 ----------------

@pytest.mark.skipif(not _HAS_CORE, reason="需要 Git for Windows 提供 pwd/ls/mkdir/cat/grep/seq")
def test_exec_list_and_cat(tmp_path):
    d = _mk_calc(tmp_path)
    r = run_command("ls", cwd=d)
    assert r.ok, r.error
    assert "INCAR" in r.stdout
    r2 = run_command("cat INCAR", cwd=d)
    assert r2.ok, r2.error
    assert "ENCUT=500" in r2.stdout


@pytest.mark.skipif(not _HAS_CORE, reason="需要 Git for Windows 提供 mkdir/seq")
def test_exec_mkdir_and_redirect(tmp_path):
    d = _mk_calc(tmp_path)
    r = run_command("mkdir -p sub/deep", cwd=d)
    assert r.ok, r.error
    assert (d / "sub" / "deep").is_dir()
    r2 = run_command("seq 1 3 > nums.txt", cwd=d)
    assert r2.ok, r2.error
    content = (d / "nums.txt").read_text(encoding="utf-8")
    assert "3" in content


@pytest.mark.skipif(not _HAS_CORE, reason="需要 Git for Windows 提供 seq")
def test_exec_truncation(tmp_path):
    d = _mk_calc(tmp_path)
    r = run_command("seq 1 500", cwd=d, max_output_chars=300)
    assert r.ok, r.error
    assert r.truncated
    assert len(r.stdout) <= 400


def test_exec_command_not_found(tmp_path):
    r = run_command("definitelynotacmd987", cwd=_mk_calc(tmp_path))
    assert not r.ok
    assert "命令不存在" in r.error


@pytest.mark.skipif(not _HAS_SLEEP, reason="需要 Git for Windows 提供 sleep")
def test_exec_timeout(tmp_path):
    d = _mk_calc(tmp_path)
    r = run_command("sleep 30", cwd=d, timeout_seconds=1)
    assert r.timed_out
    assert not r.ok


def test_denied_via_run_command(tmp_path):
    d = _mk_calc(tmp_path)
    r = run_command("rm -rf .", cwd=d)
    assert not r.ok
    assert "黑名单" in r.error
    r2 = run_command("cat '..\\..\\..\\Windows\\System32\\drivers\\etc\\hosts'", cwd=d)
    assert not r2.ok
    assert ("越出计算目录" in r2.error) or ("敏感路径" in r2.error)


def test_missing_calc_dir(tmp_path):
    r = run_command("ls", cwd=tmp_path / "nothere")
    assert not r.ok
    assert "计算目录不存在" in r.error