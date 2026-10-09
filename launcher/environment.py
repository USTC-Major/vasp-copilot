"""Prepare an owned, hash-locked runtime using an existing Windows Python.

Only the installer state directory is modified. This helper uses the standard
library until it invokes pip inside its own venv; it never installs into the
interpreter which launched it.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import stat
import struct
import subprocess
import sys
import uuid

MODULES = ("fastapi", "uvicorn", "multipart", "jinja2", "pydantic", "pydantic_settings",
           "httpx", "yaml", "numpy", "pymatgen.core", "openai", "paramiko", "keyring", "dotenv")
INDEX = "https://pypi.org/simple"


class PreparationError(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def platform_spec():
    try:
        spec = importlib.util.spec_from_file_location('launcher_python_support', Path(__file__).with_name('python_support.py'))
        support = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(support)
        compatible, reason = support.compatibility()
    except (OSError, ImportError):
        raise PreparationError("SUPPORT_POLICY_MISSING", "安装目录缺少 Python 兼容策略，请恢复完整应用目录。") from None
    if not compatible:
        raise PreparationError("UNSUPPORTED_PYTHON", reason)
    return "cp" + str(sys.version_info.major) + str(sys.version_info.minor)


def locked_versions(lock: Path):
    versions = {}
    for line in lock.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^\s]+) --hash=sha256:([a-f0-9]{64})", line)
        if not match:
            raise PreparationError("LOCK_INVALID", "运行依赖锁格式无效，请恢复完整安装目录。")
        name, version, _ = match.groups()
        if name in versions:
            raise PreparationError("LOCK_INVALID", "运行依赖锁包含重复项，请恢复完整安装目录。")
        versions[name] = version
    if not versions:
        raise PreparationError("LOCK_INVALID", "运行依赖锁为空，请恢复完整安装目录。")
    return versions


def child_environment(state: Path):
    # No inherited proxy URLs, pip indexes/config, Python paths, API keys or
    # credentials reach pip or its logs. PIP_CONFIG_FILE disables global/site
    # config too; --isolated additionally excludes user config and pip env flags.
    keep = {"SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "PATH", "PATHEXT", "TEMP", "TMP", "COMSPEC",
            "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS"}
    env = {k: v for k, v in os.environ.items() if k.upper() in keep}
    home = state / "installer-home"
    home.mkdir(parents=True, exist_ok=True)
    env.update(HOME=str(home), USERPROFILE=str(home), PIP_CONFIG_FILE=os.devnull,
               PYTHONUTF8="1", PYTHONNOUSERSITE="1")
    return env


def is_reparse(path: Path):
    try:
        return path.is_symlink() or bool(path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    except FileNotFoundError:
        return False


@contextmanager
def environment_lock(path: Path):
    import msvcrt
    if is_reparse(path):
        raise PreparationError("ENVIRONMENT_INVALID", "环境锁不是启动器所属的普通文件。")
    with path.open("a+b") as handle:
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise PreparationError("ENVIRONMENT_BUSY", "另一个启动器正在准备同一环境，请稍后重试。") from None
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def command(arguments, log, env):
    # Never propagate arbitrary exception text or inherited environment to UI.
    with log.open("ab") as stream:
        return subprocess.run(arguments, env=env, stdin=subprocess.DEVNULL,
                              stdout=stream, stderr=subprocess.STDOUT,
                              creationflags=subprocess.CREATE_NO_WINDOW).returncode


def probe(python: Path, expected, log, env):
    if not python.is_file():
        return False
    code = (
        "import importlib,importlib.metadata as m,platform,struct,sys\n"
        "assert sys.platform=='win32' and platform.python_implementation()=='CPython' and struct.calcsize('P')==8\n"
        "assert tuple(sys.version_info[:2])==" + repr(tuple(sys.version_info[:2])) + "\n"
        "expected=" + repr(expected) + "\n"
        "assert all(m.version(n)==v for n,v in expected.items())\n"
        "for name in " + repr(MODULES) + ":importlib.import_module(name)\n"
    )
    try:
        return command([str(python), "-I", "-X", "utf8", "-c", code], log, env) == 0
    except OSError:
        return False


def installation_error(log: Path):
    try:
        tail = log.read_bytes()[-256 * 1024:].decode("utf-8", errors="replace").lower()
    except OSError:
        tail = ""
    if any(word in tail for word in ("connectionerror", "connection broken", "connection refused", "proxyerror",
                                    "readtimeout", "connecttimeout", "timed out", "network is unreachable",
                                    "temporary failure", "certificate_verify_failed", "sslerror", "max retries exceeded")):
        return PreparationError("DEPENDENCY_NETWORK_FAILED", "无法从官方 PyPI 下载依赖，请检查网络和证书后重试；下载缓存会保留。")
    if "do not match the hashes" in tail:
        return PreparationError("DEPENDENCY_HASH_MISMATCH", "依赖文件未通过锁定哈希校验，未启用此环境；请检查本地安装日志。")
    return PreparationError("DEPENDENCY_INSTALL_FAILED", "依赖安装失败，请检查磁盘空间和本地安装日志后重试；下载缓存会保留。")


def prepare(args, progress, log):
    tag = platform_spec()
    lock = args.root / "backend" / ("requirements-win-" + tag + "-x64.lock")
    if not lock.is_file():
        raise PreparationError("LOCK_MISSING", "安装目录缺少对应 Python 版本的依赖锁，请恢复完整安装目录。")
    versions = locked_versions(lock)
    lock_hash = hashlib.sha256(lock.read_bytes()).hexdigest()
    identifier = "py" + tag[2:] + "-x64-" + lock_hash[:16]
    environments = args.state / "environments"
    environments.mkdir(parents=True, exist_ok=True)
    if is_reparse(environments) or environments.resolve().parent != args.state.resolve():
        raise PreparationError("ENVIRONMENT_INVALID", "环境父目录不能是符号链接或目录联接。")
    destination = environments / identifier
    if is_reparse(destination) or destination.resolve().parent != environments.resolve():
        raise PreparationError("ENVIRONMENT_INVALID", "环境目录不是启动器所属的独立目录。")
    python = destination / "Scripts" / "python.exe"
    marker = destination / ".complete.json"
    manifest = {"schema": 1, "environment_id": identifier, "lock_sha256": lock_hash,
                "python": tag, "architecture": "x64"}
    env = child_environment(args.state)
    with environment_lock(environments / (identifier + ".lock")):
        try:
            complete = json.loads(marker.read_text(encoding="utf-8")) == manifest
        except (OSError, ValueError):
            complete = False
        if complete:
            progress("verify", "正在核验已准备的独立环境…")
            if probe(python, versions, log, env):
                return {"ok": True, "python": str(python), "environment_id": identifier}
        if (os.environ.get("VASP_INSTALLER_PROXY_CONFIGURED") == "1"
                or any(value.strip() for key, value in os.environ.items()
                       if key.upper() in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"})):
            raise PreparationError("PROXY_UNSUPPORTED", "自动准备暂不支持继承代理配置；请关闭本次启动进程继承的代理后重试，或在可直连官方 PyPI 的网络准备。代理地址和凭据不会写入日志。")
        # Remove success evidence before any repair or installation. Recreate
        # incomplete environments in place: pip metadata alone cannot detect
        # missing package files after cancellation. Other lock IDs stay intact.
        marker.unlink(missing_ok=True)
        progress("create", "正在创建独立 Python 环境，原 Python 环境不会改变…")
        try:
            import venv  # noqa: F401 - explicit standard-library availability check
        except ImportError:
            raise PreparationError("VENV_UNAVAILABLE", "所选 Python 缺少 venv，请选择完整的 CPython 安装。") from None
        try:
            # -I ignores PYTHONUTF8. Explicit UTF-8 also covers redirected logs
            # containing Chinese paths on Windows installations with a Western locale.
            created = command([sys.executable, "-I", "-X", "utf8", "-m", "venv", "--clear", str(destination)], log, env)
        except OSError:
            created = 1
        if created != 0 or not python.is_file():
            raise PreparationError("VENV_UNAVAILABLE", "无法创建独立环境，请检查 Python 的 venv/ensurepip、磁盘空间和目录权限。")
        progress("install", "正在从官方 PyPI 安装已锁定依赖；首次准备可能需要数分钟…")
        if command([str(python), "-I", "-X", "utf8", "-m", "pip", "--version"], log, env) != 0:
            raise PreparationError("PIP_UNAVAILABLE", "独立环境缺少 pip，请检查所选 Python 的 ensurepip。")
        installed = command([str(python), "-I", "-X", "utf8", "-m", "pip", "--isolated", "--disable-pip-version-check",
            "install", "--no-input", "--no-user", "--index-url", INDEX, "--require-hashes",
            "--only-binary=:all:", "--cache-dir", str(args.state / "pip-cache"), "-r", str(lock)], log, env)
        if installed != 0:
            raise installation_error(log)
        progress("verify", "安装完成，正在核验版本与运行模块…")
        if not probe(python, versions, log, env):
            raise PreparationError("ENVIRONMENT_INVALID", "依赖安装后核验失败，未将此环境标记为可用；请查看本地日志并重试。")
        atomic_json(marker, manifest)
        return {"ok": True, "python": str(python), "environment_id": identifier}


def absolute(value):
    path = Path(value)
    if not path.is_absolute():
        raise argparse.ArgumentTypeError("must be an absolute path")
    return path.resolve()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("root", "state", "result-file", "progress-file"):
        parser.add_argument("--" + name, type=absolute, required=True)
    args = parser.parse_args(argv)
    args.state.mkdir(parents=True, exist_ok=True)
    logs = args.state / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    log = logs / ("environment-" + uuid.uuid4().hex + ".local.log")
    log.touch()
    def progress(stage, message):
        atomic_json(args.progress_file, {"stage": stage, "message": message, "log_path": str(log)})
    def failure(code, message):
        return {"ok": False, "code": code, "message": message, "log_path": str(log)}
    atomic_json(args.result_file, failure("PREPARATION_INTERRUPTED", "环境准备未完成，可安全重试。"))
    progress("validate", "正在检查已有 Python 与运行依赖锁…")
    try:
        result = prepare(args, progress, log)
    except PreparationError as exc:
        result = failure(exc.code, exc.message)
    except (OSError, ValueError, subprocess.SubprocessError):
        result = failure("ENVIRONMENT_PREPARATION_FAILED", "独立环境准备失败，请检查目录权限、磁盘空间和本地日志后重试。")
    atomic_json(args.result_file, result)
    progress("ready" if result["ok"] else "error", "独立环境已就绪。" if result["ok"] else result["message"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
