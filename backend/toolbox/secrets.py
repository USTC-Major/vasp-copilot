"""本地密钥存储：LLM / MP key 与 SSH 密码一样只进系统凭据管理器。

- 后端默认 keyring（Windows 上即 Windows 凭据管理器），服务名与 SSH 凭据一致
  （``vasp-ai-agent``），账号键用 ``secret://<name>``，与 ``ssh://user@host`` 不冲突。
- 进程内缓存读取结果，写入/删除时失效，避免每个请求都读一次凭据管理器。
- 凭据后端不可用（无桌面 / CI / 无 keyring）时读取返回 None、写入返回 False；
  调用方据此回退到本地配置文件里的旧值，保证功能不因缺少凭据后端而中断，
  也不会因为“写不进去”而把密钥从磁盘上抹掉。
"""
from __future__ import annotations

from typing import Optional, Protocol

SERVICE_NAME = "vasp-ai-agent"

#: 对外只暴露逻辑名，账号键集中在这里，避免各处硬编码。
ACCOUNTS = {
    "llm_api_key": "secret://llm_api_key",
    "mp_api_key": "secret://mp_api_key",
}


class SecretBackend(Protocol):
    def get(self, account: str) -> Optional[str]: ...
    def set(self, account: str, value: str) -> None: ...
    def delete(self, account: str) -> None: ...


class KeyringBackend:
    """生产后端：系统凭据管理器（延迟导入，缺库时在调用点明确失败）。"""

    def __init__(self) -> None:
        import keyring

        self._keyring = keyring

    def get(self, account: str) -> Optional[str]:
        return self._keyring.get_password(SERVICE_NAME, account)

    def set(self, account: str, value: str) -> None:
        self._keyring.set_password(SERVICE_NAME, account, value)

    def delete(self, account: str) -> None:
        try:
            self._keyring.delete_password(SERVICE_NAME, account)
        except Exception:  # noqa: BLE001 - 各后端对“不存在”行为不一，统一幂等
            pass


class MemoryBackend:
    """测试 / 离线演示后端：进程内，重启即失效。"""

    def __init__(self, values: Optional[dict[str, str]] = None) -> None:
        self._values = dict(values or {})

    def get(self, account: str) -> Optional[str]:
        return self._values.get(account)

    def set(self, account: str, value: str) -> None:
        self._values[account] = value

    def delete(self, account: str) -> None:
        self._values.pop(account, None)


_backend: Optional[SecretBackend] = None
_cache: dict[str, Optional[str]] = {}
_backend_checked = False


def configure_backend(backend: Optional[SecretBackend]) -> None:
    """注入后端（测试用）；传 None 表示恢复默认的 keyring 后端。"""
    global _backend, _backend_checked
    _backend = backend
    _backend_checked = backend is not None
    _cache.clear()


def clear_cache() -> None:
    _cache.clear()
    global _backend_checked
    _backend_checked = False


def _get_backend() -> Optional[SecretBackend]:
    global _backend, _backend_checked
    if _backend_checked:
        return _backend
    _backend_checked = True
    try:
        _backend = KeyringBackend()
    except Exception:  # noqa: BLE001 - 无 keyring / 无桌面时明确降级
        _backend = None
    return _backend


def available() -> bool:
    return _get_backend() is not None


def get_secret(name: str) -> Optional[str]:
    """读取密钥；不存在或后端不可用返回 None（不抛异常，调用方回退文件值）。"""
    account = ACCOUNTS.get(name)
    if account is None:
        raise KeyError(f"未知密钥名: {name}")
    if name in _cache:
        return _cache[name]
    backend = _get_backend()
    value: Optional[str] = None
    if backend is not None:
        try:
            value = backend.get(account)
        except Exception:  # noqa: BLE001
            value = None
    _cache[name] = value
    return value


def set_secret(name: str, value: str) -> bool:
    """写入密钥；返回是否成功（False 时调用方必须保留原有落盘值）。"""
    account = ACCOUNTS.get(name)
    if account is None:
        raise KeyError(f"未知密钥名: {name}")
    backend = _get_backend()
    if backend is None or not value:
        return False
    try:
        backend.set(account, value)
    except Exception:  # noqa: BLE001
        return False
    _cache[name] = value
    return True


def delete_secret(name: str) -> None:
    account = ACCOUNTS.get(name)
    if account is None:
        raise KeyError(f"未知密钥名: {name}")
    backend = _get_backend()
    if backend is not None:
        try:
            backend.delete(account)
        except Exception:  # noqa: BLE001
            pass
    _cache[name] = None


def secret_value_for_file(name: str, current: str, previous: str, env_value: str) -> str:
    """返回该密钥字段应写进配置文件的取值。

    规则：配置文件里绝不留明文。优先把“用户自己的密钥”迁移进凭据管理器；
    环境变量提供的密钥只参与运行、不落盘；迁移失败时返回原文，避免丢密钥。
    """
    candidate = previous or (current if current and current != env_value else "")
    if not candidate:
        return ""
    return "" if set_secret(name, candidate) else candidate
