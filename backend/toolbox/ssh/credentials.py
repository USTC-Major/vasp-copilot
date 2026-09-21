"""超算凭据管理（M6）：SSH 密码只存系统凭据管理器，绝不落盘/进 LLM 上下文。

对齐全栈约束（安全边界 §2）：
- 默认后端 = keyring（Windows 上即 Windows 凭据管理器）。
- 密码生命周期：设置页写入；仅连接池建连时读取一次，立刻丢弃本地引用。
- 密钥/密码永不打日志、永不写入为此 LLM 上下文、produce 不进项目文件。
- 可注入后端（MemoryCredentialStore 供测试/离线演示），生产默认 KeyringCredentialStore。

账号身份由 (service_name, account_name) 唯一标识；服务名统一 "vasp-ai-agent"。
密码明文不出本模块边界（至多传入 paramiko connect 的 password 参数）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from .errors import SSHConfigError

__all__ = [
    "CredentialStore",
    "KeyringCredentialStore",
    "MemoryCredentialStore",
    "SERVICE_NAME",
    "account_key",
    "SSHCredentials",
]

SERVICE_NAME = "vasp-ai-agent"


def account_key(host: str, username: str) -> str:
    """把 (host, username) 映射为凭据管理器里的用户键。"""
    return f"ssh://{username}@{host}"


class CredentialStore(ABC):
    """凭据存取接口。实现必须保证不当紧。"""

    @abstractmethod
    def get_password(self, host: str, username: str) -> str | None:
        """返回已存密码；未存则 None。"""

    @abstractmethod
    def set_password(self, host: str, username: str, password: str) -> None:
        """保存/覆盖密码。"""

    @abstractmethod
    def delete_password(self, host: str, username: str) -> None:
        """删除某账号密码（不存在也幂等）。"""


class KeyringCredentialStore(CredentialStore):
    """keyring 后端。延迟 import keyring，避免未装库/无桌面的环境炸导入。

    注意：keyring 在无桌面（CI）下可能 fallback 到文件后端并弹警告，本项目
    「纯 Windows 本地 app」场景使用正常；测试一律注入 MemoryCredentialStore，
    不触碰真实凭据。"""

    def __init__(self) -> None:
        self._store = None

    def _store_or_raise(self):
        if self._store is None:
            import keyring  # 延迟导入，失败时在 get 时已可明确报错

            self._store = keyring
        return self._store

    def get_password(self, host: str, username: str) -> str | None:
        store = self._store_or_raise()
        return store.get_password(SERVICE_NAME, account_key(host, username))

    def set_password(self, host: str, username: str, password: str) -> None:
        store = self._store_or_raise()
        store.set_password(SERVICE_NAME, account_key(host, username), password)

    def delete_password(self, host: str, username: str) -> None:
        store = self._store_or_raise()
        try:
            store.delete_password(SERVICE_NAME, account_key(host, username))
        except Exception:  # keyring 各后端对“不存在”行为不一，统一幂等
            pass


class MemoryCredentialStore(CredentialStore):
    """进程内内存版——供测试/离线演示。重启即失效，不落持久层。"""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    def get_password(self, host: str, username: str) -> str | None:
        return self._data.get(account_key(host, username))

    def set_password(self, host: str, username: str, password: str) -> None:
        self._data[account_key(host, username)] = password

    def delete_password(self, host: str, username: str) -> None:
        key = account_key(host, username)
        if key in self._data:
            del self._data[key]


@dataclass
class SSHCredentials:
    """某账号的连接身份（连接时使用）。密码字段仅供建连瞬读，用完即弃。"""

    host: str
    port: int = 22
    username: str = ""
    password: str = ""

    @property
    def key(self) -> str:
        return account_key(self.host, self.username)
