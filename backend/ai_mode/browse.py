"""本地 / 超算工作区目录浏览（M032 / M33 增强）——前端图形化点选。

能力：
- 只读列目录（本地 os.scandir / 超算 SFTP listdir_attr）。
- 过滤隐藏与系统目录：本地忽略点开头名、Windows 隐藏/系统属性及常见系统目录；
  超算忽略点开头目录与 lost+found 等无关目录。
- 本地对进不了的目录实地探测后跳过（不展示无权目录）。
- 新建文件夹：本地 mkdir / 超算 SFTP mkdir；仅命名校验，不做越权路径操作。

安全边界：
- 本模块只做目录列举与「用户显式」新建文件夹，不写不删已存在文件、不执行命令；
  出错只回简明提示，不携带敏感系统信息。
- 超算操作复用 M6 SSH 层（SFTP），路线与 Orchestrator 相同；密码只经凭据管理器。
- 返回前端的是目录树（路径文本），不进 LLM 上下文。
"""

from __future__ import annotations

import os
import posixpath
import subprocess
import sys
from pathlib import Path

from .config import AiModeConfig
from .ssh.errors import SSHError

# Windows 文件属性（FILE_ATTRIBUTE_HIDDEN / FILE_ATTRIBUTE_SYSTEM）
_FILE_ATTRIBUTE_HIDDEN = 0x2
_FILE_ATTRIBUTE_SYSTEM = 0x4

# 常见系统/占用目录名（小写）：即使属性不可读也一律不展示
_LOCAL_ALWAYS_HIDDEN = {
    "$recycle.bin",
    "recovery",
    "system volume information",
    "windows",
    "windows.old",
}

# 超算端常驻、与计算无关的目录（点开头目录另行过滤）
_HPC_SKIP_NAMES = {
    "core",
    "lost+found",
    "lost_found",
}

_ILLEGAL_WINDOWS_CHARS = set('<>:"|?*')


def local_roots() -> list[dict]:
    """本地图形化点选的起点：Windows 现有盘符 + 用户主目录。"""
    roots = []
    if os.name == "nt":
        for code in range(ord("A"), ord("Z") + 1):
            drive = f"{chr(code)}:\\"
            if os.path.exists(drive):
                roots.append({"name": drive, "is_dir": True})
    home = str(Path.home())
    if home not in {r["name"] for r in roots}:
        roots.append({"name": home, "is_dir": True})
    return roots


def _unavailable_dict(message: str) -> dict:
    return {"notice": message, "path": "", "parent": None,
            "exists": False, "is_dir": False, "entries": []}


def _hidden_local_entry(entry) -> bool:
    """本地条目是否视为隐藏：点开头名、Windows 隐藏/系统属性、常见系统目录。"""
    name = entry.name
    if name.startswith("."):
        return True
    if os.name == "nt":
        try:
            attr = entry.stat(follow_symlinks=False).st_file_attributes or 0
            if attr & (_FILE_ATTRIBUTE_HIDDEN | _FILE_ATTRIBUTE_SYSTEM):
                return True
        except OSError:
            pass
        if name.lower() in _LOCAL_ALWAYS_HIDDEN:
            return True
    return False


def _dir_readable(path: str) -> bool:
    """能否进入并列出目录内容（探测一次首条目即返回，不做深层扫描）。"""
    try:
        it = os.scandir(path)
        with it:
            next(it, None)
        return True
    except (PermissionError, OSError):
        return False


def _entry_size(entry) -> int | None:
    try:
        return int(entry.stat(follow_symlinks=False).st_size or 0)
    except OSError:
        return None


def browse_local(path: str) -> dict:
    """列本地目录（隐藏/系统/进不去的目录已过滤）；path 为空返回起点。"""
    path = (path or "").strip()
    if not path:
        return {"path": "", "parent": None, "exists": True, "is_dir": True,
                "roots": local_roots(), "entries": []}
    raw = Path(path).expanduser()
    if not raw.is_absolute():
        raw = Path.home() / raw
    target = str(raw)
    entries = []
    try:
        with os.scandir(target) as it:
            for e in it:
                if _hidden_local_entry(e):
                    continue
                try:
                    is_dir = e.is_dir()
                except OSError:
                    is_dir = False
                if is_dir and not _dir_readable(e.path):
                    continue
                entries.append({"name": e.name, "is_dir": is_dir,
                                "size": _entry_size(e)})
    except (FileNotFoundError, NotADirectoryError):
        return _unavailable_dict("目录不存在或不是文件夹")
    except PermissionError:
        return _unavailable_dict("无权限访问该目录")
    except OSError as exc:
        return _unavailable_dict(f"无法访问该目录（{exc.__class__.__name__}）")
    return {
        "path": target,
        "parent": str(raw.parent) if str(raw) != str(raw.parent) else None,
        "exists": raw.exists(),
        "is_dir": raw.is_dir(),
        "entries": sorted(entries,
                          key=lambda e: (not e["is_dir"], e["name"].lower())),
    }


def hpc_roots(ssh=None) -> list[dict]:
    """超算起点：根目录 /；若可探测登录主目录（pwd）则一并列出。"""
    roots = [{"name": "/", "is_dir": True}]
    if ssh is not None:
        try:
            _code, out, _err = ssh.run("pwd")
            home = ((out or "").strip().splitlines() or [""])[0]
            if home and home != "/" and not any(r["name"] == home
                                                for r in roots):
                roots.append({"name": home, "is_dir": True})
        except Exception:  # noqa: BLE001 - 探测失败不阻塞浏览
            pass
    return roots


def _remote_parent(path: str) -> str:
    parent = posixpath.dirname(path)
    return parent if parent and parent != path else None


def _hidden_hpc_name(name: str) -> bool:
    return name.startswith(".") or name.lower() in _HPC_SKIP_NAMES


def browse_hpc(ssh, path: str) -> dict:
    """列远端目录（过滤隐藏/无关目录）；path 为空返回起点。"""
    path = (path or "").strip()
    if not path:
        return {"path": "", "parent": None, "exists": True, "is_dir": True,
                "roots": hpc_roots(ssh), "entries": []}
    try:
        attrs = ssh.list_dir_info(path)
    except SSHError as exc:
        return _unavailable_dict(str(exc) or "无法列出该目录")
    except Exception as exc:  # noqa: BLE001
        return _unavailable_dict(f"无法列出该目录（{exc.__class__.__name__}）")
    exists = True
    try:
        exists = ssh.stat(path) is not None
    except Exception:  # noqa: BLE001
        exists = False
    entries = [e for e in attrs
               if not _hidden_hpc_name(str(e.get("name", "")))]
    return {
        "path": path,
        "parent": _remote_parent(path),
        "exists": exists,
        "is_dir": True,
        "entries": sorted(entries,
                          key=lambda e: (not e.get("is_dir", False),
                                         str(e.get("name", "")).lower())),
    }


def validate_new_name(name) -> tuple[bool, str]:
    """校验新建文件夹名；返回 (ok, 错误信息)。"""
    name = (name or "").strip()
    if not name:
        return False, "文件夹名不能为空"
    if len(name) > 120:
        return False, "文件夹名过长（超过 120 字符）"
    if "/" in name or "\\" in name or name in (".", ".."):
        return False, "文件夹名不能包含路径分隔符"
    if any(ord(ch) < 32 for ch in name):
        return False, "文件夹名包含非法控制字符"
    if os.name == "nt":
        if any(ch in _ILLEGAL_WINDOWS_CHARS for ch in name):
            return False, "文件夹名包含非法字符（<>:\"/|?*）"
        if name.endswith((" ", ".")):
            return False, "Windows 文件夹名不能以空格或点结尾"
    return True, ""


def _mkdir_result(ok: bool, *, path: str = "", notice: str = "") -> dict:
    return {"ok": ok, "path": path, "notice": notice}


def mkdir_local(path: str, name: str) -> dict:
    """在本地目录下新建文件夹（仅单层；名称名校验严格）。"""
    ok, reason = validate_new_name(name)
    if not ok:
        return _mkdir_result(False, notice=reason)
    path = (path or "").strip() or str(Path.home())
    try:
        target = Path(path) / name
        target.mkdir(exist_ok=True)
        return _mkdir_result(True, path=str(target))
    except PermissionError:
        return _mkdir_result(False, notice="无权限在该目录下新建文件夹")
    except OSError as exc:
        return _mkdir_result(False,
                             notice=f"新建文件夹失败（{exc.__class__.__name__}）")


def mkdir_hpc(ssh, path: str, name: str) -> dict:
    """在远端目录下新建文件夹（SFTP mkdir，仅单层）。"""
    ok, reason = validate_new_name(name)
    if not ok:
        return _mkdir_result(False, notice=reason)
    path = (path or "").strip()
    try:
        target = posixpath.join(path, name) if path else name
        ssh.mkdir(target)
        return _mkdir_result(True, path=target)
    except SSHError as exc:
        return _mkdir_result(False, notice=str(exc) or "新建文件夹失败")
    except Exception as exc:  # noqa: BLE001
        return _mkdir_result(False,
                             notice=f"新建文件夹失败（{exc.__class__.__name__}）")


def create_hpc_ssh(cfg: AiModeConfig):
    """按全局配置创建超算浏览用 SSHManager；未配置超算账号则返回 None。"""
    if not (cfg.ssh_host and cfg.ssh_username):
        return None
    from .ssh.connection import SSHManager
    from .ssh.credentials import KeyringCredentialStore

    manager = SSHManager(credentials=KeyringCredentialStore(),
                         connect_timeout=15)
    manager.switch(host=cfg.ssh_host, username=cfg.ssh_username,
                   port=cfg.ssh_port or 22)
    return manager

class BrowseDialogError(Exception):
    """系统原生目录选择弹窗不可用/失败。"""


# 子进程内运行的 Tk 原生目录选择程序（-X utf8 输出路径）。
_PICK_PROGRAM = r"""
import sys
import tkinter as tk
from tkinter import filedialog


def _main() -> int:
    initial = sys.argv[1] if len(sys.argv) > 1 else ""
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    root.lift()
    try:
        path = filedialog.askdirectory(
            title="选择本地工作区目录",
            mustexist=True,
            initialdir=(initial or None),
        )
    finally:
        try:
            root.destroy()
        except Exception:  # noqa: BLE001
            pass
    if path:
        sys.stdout.write(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
"""


def pick_local_directory(initial_dir: str | None = None) -> str:
    """弹出操作系统原生目录选择窗（tkinter），返回选中的绝对路径。

    取消返回空字符串；在子进程运行 Tk，避免 uvicorn 线程/消息循环限制。
    仅适用于本地工作区（本机桌面弹窗）；超算远端目录无本机弹窗，沿用 SSH 浏览。
    """
    try:
        import tkinter  # noqa: F401  # 提前探测 Tk 是否可用
    except Exception as exc:  # pragma: no cover - 与平台相关
        raise BrowseDialogError(
            f"当前环境不支持系统目录弹窗（{exc.__class__.__name__}）") from exc
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    cmd = [sys.executable, "-X", "utf8", "-c", _PICK_PROGRAM]
    if initial_dir:
        cmd.append(initial_dir)
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", env=env,
                              timeout=None, creationflags=creationflags)
    except OSError as exc:
        raise BrowseDialogError(
            f"无法启动系统目录弹窗（{exc.__class__.__name__}）") from exc
    return (proc.stdout or "").strip()
