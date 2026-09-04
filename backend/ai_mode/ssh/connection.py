"""超算连接层（M6）：paramiko 建连、保持/回收、测试连通、执行与传输原语。

对齐接口文档 §1.5（钥匙保管员）与安全边界 §2：
- 密码只来自凭据管理器，本模块错误信息一律脱敏，绝不打密码/密钥；
  连接对象只交给「受限执行器」使用，LLM 拿不到。
- 一次只持有一个会话：账号切换=关旧开新（设置页切换：一次一个）。
- 原语：run（类 shell 命令）+ SFTP（list/stat/read/write/mkdir）。
- 测试注入 client_factory（默认 paramiko.SSHClient），生产路径不受影响。
"""

from __future__ import annotations

import hashlib
import shlex
import stat as stat_mode
import uuid
from pathlib import Path
from typing import Callable, Optional

from .credentials import CredentialStore, MemoryCredentialStore
from .errors import (
    SSHAuthError,
    SSHConnectError,
    SSHError,
    SSHExecuteError,
    SSHHostKeyMismatchError,
    SSHHostKeyUnknownError,
    SSHKnownHostsError,
    SSHSFTPError,
    SSHUnavailableError,
)

__all__ = ["SSHManager", "RemoteFileInfo"]


def _default_client_factory(known_hosts_path: str | None = None):
    """延迟 import paramiko，避免无 SSH 依赖环境直接炸导入。"""
    import paramiko

    class StrictRejectPolicy(paramiko.RejectPolicy):
        def missing_host_key(self, client, hostname, key):
            del client, key
            raise SSHHostKeyUnknownError(
                f"SSH 主机密钥未受信任：{hostname}；请先由用户核验并写入 known_hosts"
            )

    client = paramiko.SSHClient()
    try:
        client.load_system_host_keys()
        if known_hosts_path:
            trusted = Path(known_hosts_path).expanduser()
            if not trusted.is_file():
                raise SSHKnownHostsError("配置的 known_hosts 文件不存在或不可读")
            client.load_host_keys(str(trusted))
        else:
            trusted = Path.home() / ".ssh" / "known_hosts"
            if trusted.exists():
                if not trusted.is_file():
                    raise SSHKnownHostsError("默认 known_hosts 路径不是可读文件")
                client.load_host_keys(str(trusted))
    except SSHKnownHostsError:
        raise
    except Exception as exc:
        raise SSHKnownHostsError("无法读取 known_hosts 信任库") from exc
    client.set_missing_host_key_policy(StrictRejectPolicy())
    return client


def _guess_error_type(exc: BaseException) -> type[SSHError]:
    """按类名映射 paramiko/socket 异常为 SSHError 子类（弱依赖、免导入）。"""
    name = type(exc).__name__
    if "BadHostKey" in name or "HostKeyMismatch" in name:
        return SSHHostKeyMismatchError
    if "UnknownHostKey" in name or "HostKeyUnknown" in name:
        return SSHHostKeyUnknownError
    if "Authentication" in name or "Permission" in name or "password" in name.lower():
        return SSHAuthError
    if "timeout" in name.lower() or "Timeout" in name:
        return SSHConnectError
    return SSHConnectError


class SSHManager:
    """一次一个账号的 SSH 连接管理。

    :param credentials: 凭据存储（默认 MemoryCredentialStore，生产请注入
        KeyringCredentialStore，或使用 from_config 工厂自动选择）。
    :param client_factory: 返回类 paramiko/System 的客户端对象工厂。
    :param connect_timeout: 秒。
    :param cmd_timeout: 默认。
    :param max_output_bytes: 单命令/sftp 读截断上限。
    """

    def __init__(self, *, credentials: CredentialStore | None = None,
                 client_factory=None, connect_timeout: int = 15,
                 cmd_timeout: int = 60,
                 max_output_bytes: int = 64 * 1024,
                 known_hosts_path: str | None = None):
        self.credentials = credentials or MemoryCredentialStore()
        self.client_factory = client_factory
        self.known_hosts_path = known_hosts_path
        self.connect_timeout = connect_timeout
        self.cmd_timeout = cmd_timeout
        self.max_output_bytes = max_output_bytes

        self._active: dict | None = None
        self._client = None
        self._sftp = None

    # ---------------- 账号（一次一个） ----------------

    def switch(self, *, host: str, username: str, password: str | None = None,
               port: int = 22) -> None:
        """设置页切账号：关闭旧连接并切换到新指纹。password 可选，给了会写入
        凭据管理器，不给则沿用已存凭据（没有就建连时报认证错）。"""
        if not host or not username:
            raise ValueError("host 与 username 必填")
        self.close()
        self._active = {"host": host, "username": username, "port": port}
        if password:
            self.credentials.set_password(host, username, password)

    def forget(self) -> None:
        """清空当前活动账号并断开连接（不删凭据）。"""
        self.close()
        self._active = None

    @property
    def active(self) -> dict | None:
        return self._active

    @property
    def connected(self) -> bool:
        return self._client is not None

    def close(self) -> None:
        """关闭会话（幂等）。"""
        self._close_sftp()
        if self._client is not None:
            try:
                self._client.close()
            finally:
                self._client = None

    def _close_sftp(self) -> None:
        if self._sftp is not None:
            try:
                self._sftp.close()
            except Exception:
                pass
            self._sftp = None

    # ---------------- 连接 ----------------

    def connect(self):
        """建立/复用连接；返回 client（供受限执行器使用）。
        保持的连接：调用方用完不主动 close，由 switch/close 统一回收。
        """
        active = self._active
        if active is None:
            raise SSHUnavailableError("未配置 SSH 账号：请先在设置页添加账号")
        if self._client is not None:
            if self._check_alive(self._client):
                return self._client
            self._cleanup_client(self._client)
            self._client = None
        client = (self.client_factory() if self.client_factory is not None
                  else _default_client_factory(self.known_hosts_path))
        password = self.credentials.get_password(active["host"], active["username"])
        try:
            client.connect(
                hostname=active["host"],
                port=active["port"],
                username=active["username"],
                password=password,
                timeout=self.connect_timeout,
                banner_timeout=self.connect_timeout,
                auth_timeout=self.connect_timeout,
                look_for_keys=False,
                allow_agent=False,
            )
        except SSHError:
            raise
        except Exception as exc:
            self._cleanup_client(client)
            error_type = _guess_error_type(exc)
            if error_type is SSHHostKeyMismatchError:
                message = "SSH 主机密钥与 known_hosts 不匹配；已在认证前拒绝连接"
            elif error_type is SSHHostKeyUnknownError:
                message = "SSH 主机密钥未知；已在认证前拒绝连接"
            else:
                message = "无法连接超算（已脱敏，请检查地址/端口/凭据）"
            raise error_type(message) from exc
        try:
            t = client.get_transport()
            if t is not None:
                t.set_keepalive(30)
        except Exception:
            pass
        self._client = client
        return client

    def _cleanup_client(self, client) -> None:
        try:
            client.close()
        except Exception:
            pass

    def _check_alive(self, client) -> bool:
        try:
            t = client.get_transport()
            return t is not None and t.is_active()
        except Exception:
            return False

    def test_connection(self, *, host: str | None = None,
                        username: str | None = None,
                        port: int | None = None,
                        password: str | None = None) -> tuple[bool, str]:
        """最小连通自检：建连 + whoami。返回 (ok, 摘要)。"""
        saved_active = self._active
        if host and username:
            self.switch(host=host, username=username, password=password, port=port or 22)
        try:
            self.connect()
        except SSHError as exc:
            return False, f"连接失败：{exc}"
        try:
            code, out, err = self.run("whoami")
            if code != 0:
                return False, f"连通但命令失败：{err.strip() or ('exit=' + str(code))}"
            return True, f"OK as {out.strip()}"
        except SSHError as exc:
            return False, f"连通但命令失败：{exc}"
        finally:
            self._active = saved_active
            if not (host and username):
                self.close()

    # ---------------- 命令（供受限执行器调用） ----------------

    def run(self, command: str, *, cwd: str | None = None,
            timeout: int | None = None) -> tuple[int, str, str]:
        """执行类 shell 命令，返回 (exit_code, stdout, stderr)（截断）。

        - cwd 未给时用远端当前目录（会话工作目录）。
        - 真正的命令审查在 M4 执行器做；这里是传输原语。
        - 输出按 max_output_bytes 截断防超长吃内存。
        """
        client = self.connect()
        full = command
        if cwd:
            full = f"cd -- {shlex.quote(cwd)} && {command}"
        timeout = timeout or self.cmd_timeout
        try:
            stdin, stdout, stderr = client.exec_command(full, timeout=timeout)
            out = _read_limited(stdout, self.max_output_bytes)
            err = _read_limited(stderr, self.max_output_bytes)
            exit_code = stdout.channel.recv_exit_status()
            return exit_code, out, err
        except Exception as exc:
            raise SSHExecuteError(f"执行失败（已脱敏）：{exc.__class__.__name__}") from exc

    # ---------------- SFTP 原语 ----------------

    def _get_sftp(self):
        if self._sftp is None:
            client = self.connect()
            self._sftp = client.open_sftp()
        return self._sftp

    def list_dir(self, remote: str) -> list[str]:
        try:
            return self._get_sftp().listdir(remote)
        except Exception as exc:
            raise SSHSFTPError(f"列目录失败: {remote!r}") from exc

    def list_dir_info(self, remote: str) -> list[dict]:
        """列目录并标注条目类型/大小（供前端图形化点选）。"""
        try:
            attrs = self._get_sftp().listdir_attr(remote)
        except Exception as exc:
            raise SSHSFTPError(f"列目录失败: {remote!r}") from exc
        entries = []
        for a in attrs:
            name = getattr(a, "filename", None) or getattr(a, "name", None)
            if not name:
                continue
            mode = int(getattr(a, "st_mode", 0) or 0)
            is_dir = bool(mode) and (mode & 0o170000) == 0o040000
            entries.append({
                "name": name,
                "is_dir": is_dir,
                "size": int(getattr(a, "st_size", 0) or 0),
            })
        entries.sort(key=lambda e: (not e["is_dir"], e["name"]))
        return entries

    def stat(self, remote: str) -> dict | None:
        try:
            st = self._get_sftp().stat(remote)
            mode = int(getattr(st, "st_mode", 0) or 0)
            return {"size": st.st_size, "mtime": getattr(st, "st_mtime", 0),
                    "is_file": stat_mode.S_ISREG(mode) if mode else None,
                    "is_dir": stat_mode.S_ISDIR(mode) if mode else None}
        except FileNotFoundError:
            return None
        except Exception as exc:
            raise SSHSFTPError(f"stat 失败: {remote!r}") from exc

    def read_file(self, remote: str, *, max_bytes: int | None = None) -> bytes:
        try:
            limit = max_bytes or self.max_output_bytes
            f = self._get_sftp().open(remote, "rb")
            with f:
                return f.read(limit)
        except Exception as exc:
            raise SSHSFTPError(f"读取失败: {remote!r}") from exc

    def sha256_file(self, remote: str) -> str:
        """Stream a remote file through SHA-256 without returning its contents."""
        try:
            digest = hashlib.sha256()
            f = self._get_sftp().open(remote, "rb")
            with f:
                while True:
                    chunk = f.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
            return digest.hexdigest()
        except Exception as exc:
            raise SSHSFTPError(f"哈希读取失败: {remote!r}") from exc

    def write_file(self, remote: str, data: bytes) -> int:
        try:
            f = self._get_sftp().open(remote, "wb")
            with f:
                n = f.write(data)
                f.flush()
            return n
        except Exception as exc:
            raise SSHSFTPError(f"写入失败: {remote!r}") from exc

    def atomic_write_file(self, remote: str, data: bytes, *,
                          expected_sha256: str) -> int:
        """Upload to a sibling temporary file, verify, then atomically rename."""
        expected = str(expected_sha256 or "").lower()
        if len(expected) != 64 or any(ch not in "0123456789abcdef" for ch in expected):
            raise SSHSFTPError("原子上传缺少有效 SHA-256")
        sftp = self._get_sftp()
        temporary = f"{remote}.vasp-doctor-{uuid.uuid4().hex}.tmp"
        try:
            with sftp.open(temporary, "wb") as handle:
                result = handle.write(data)
                written = len(data) if result is None else int(result)
                handle.flush()
            if written != len(data) or self.sha256_file(temporary) != expected:
                raise SSHSFTPError("远端临时文件哈希校验失败")
            rename = getattr(sftp, "posix_rename", None) or getattr(sftp, "rename", None)
            if rename is None:
                raise SSHSFTPError("SFTP 服务不支持原子 rename")
            rename(temporary, remote)
            if self.sha256_file(remote) != expected:
                raise SSHSFTPError("原子 rename 后远端哈希校验失败")
            return written
        except SSHSFTPError:
            raise
        except Exception as exc:
            raise SSHSFTPError(f"原子写入失败: {remote!r}") from exc
        finally:
            try:
                sftp.remove(temporary)
            except (FileNotFoundError, OSError):
                pass

    def mkdir(self, remote: str) -> None:
        try:
            self._get_sftp().mkdir(remote)
        except FileExistsError:
            return
        except Exception as exc:
            raise SSHSFTPError(f"创建目录失败: {remote!r}") from exc


def _read_limited(channel, limit: int) -> str:
    """读 channel 直到上限字节（拆块，防超长吃内存）。"""
    data = bytearray()
    while len(data) < limit:
        want = min(4096, limit - len(data))
        chunk = channel.read(want)
        if not chunk:
            break
        data += chunk
    return bytes(data).decode("utf-8", "replace")
