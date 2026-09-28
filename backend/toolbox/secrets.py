"""本地密钥存储：LLM / MP key 与 SSH 密码一样只进系统凭据管理器。

- 后端默认 keyring（Windows 上即 Windows 凭据管理器），服务名与 SSH 凭据一致
  （``vasp-ai-agent``），账号键用 ``secret://<name>``，与 ``ssh://user@host`` 不冲突。
- 每次读取后端，其他应用实例的替换和删除立即可见。
- 无 keyring 时仍可读取旧配置；后端操作失败明确报错，原配置保留。
"""
from __future__ import annotations

from typing import Optional, Protocol

SERVICE_NAME = "vasp-ai-agent"

#: 对外只暴露逻辑名，账号键集中在这里，避免各处硬编码。
ACCOUNTS = {
    "llm_api_key": "secret://llm_api_key",
    "mp_api_key": "secret://mp_api_key",
}

# Shared with every configuration root using this credential namespace. This
# is ownership/migration state, not a successful-clear receipt: the actual
# credential must still be read, and every mutation must be verified.
MANAGED_ACCOUNTS = {name: f"secret-state://{name}" for name in ACCOUNTS}
_MANAGED_VERSION = "managed-v1"


class SecretStorageError(ValueError):
    """Credential operation failed; messages never include secret values."""


class BackendUnavailable(Exception):
    """No supported system credential provider is configured."""


class SecretBackend(Protocol):
    def get(self, account: str) -> Optional[str]: ...
    def set(self, account: str, value: str) -> None: ...
    def delete(self, account: str) -> None: ...


class KeyringBackend:
    """生产后端：系统凭据管理器（延迟导入，缺库时在调用点明确失败）。"""

    def __init__(self) -> None:
        import keyring

        self._keyring = keyring
        # keyring may be installed in a headless image while its fail backend
        # has no credential provider. This is absence, not a failed read.
        if keyring.get_keyring().priority <= 0:
            raise BackendUnavailable()

    def get(self, account: str) -> Optional[str]:
        return self._keyring.get_password(SERVICE_NAME, account)

    def set(self, account: str, value: str) -> None:
        self._keyring.set_password(SERVICE_NAME, account, value)

    def delete(self, account: str) -> None:
        if self.get(account) is not None:
            self._keyring.delete_password(SERVICE_NAME, account)


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
_backend_checked = False


def configure_backend(backend: Optional[SecretBackend]) -> None:
    """注入后端（测试用）；传 None 表示恢复默认的 keyring 后端。"""
    global _backend, _backend_checked
    _backend = backend
    _backend_checked = backend is not None


def clear_cache() -> None:
    """Compatibility hook: secret values are no longer cached."""


def _get_backend() -> Optional[SecretBackend]:
    global _backend, _backend_checked
    if _backend_checked:
        return _backend
    _backend_checked = True
    try:
        _backend = KeyringBackend()
    except (ImportError, BackendUnavailable):
        _backend = None
    except Exception as exc:
        _backend_checked = False
        raise SecretStorageError('系统凭据后端初始化失败') from exc
    return _backend


def available() -> bool:
    return _get_backend() is not None


def get_secret(name: str) -> Optional[str]:
    """不存在或未安装后端返回 None；已安装后端读取失败不能伪装成空值。"""
    account = ACCOUNTS.get(name)
    if account is None:
        raise KeyError(f"未知密钥名: {name}")
    backend = _get_backend()
    value: Optional[str] = None
    if backend is not None:
        try:
            value = backend.get(account)
        except Exception as exc:
            raise SecretStorageError("凭据后端读取失败，请检查本机凭据服务") from exc
    return value


def secret_state(name: str) -> tuple[Optional[str], bool]:
    """Return current value and whether historical plaintext is still eligible."""
    if name not in ACCOUNTS:
        raise KeyError(f"未知密钥名: {name}")
    backend = _get_backend()
    if backend is None:
        return None, True
    try:
        state = backend.get(MANAGED_ACCOUNTS[name])
        if state not in {None, _MANAGED_VERSION}:
            raise SecretStorageError("凭据迁移状态无法识别，请检查本机凭据服务")
        value = backend.get(ACCOUNTS[name])
    except SecretStorageError:
        raise
    except Exception as exc:
        raise SecretStorageError("凭据后端读取失败，请检查本机凭据服务") from exc
    return value, state is None and not value


def resolve_secret(name: str, legacy_value: str) -> str:
    value, legacy_allowed = secret_state(name)
    return value or (legacy_value if legacy_allowed else "")


def _mark_managed(backend: SecretBackend, name: str) -> None:
    account = MANAGED_ACCOUNTS[name]
    if backend.get(account) != _MANAGED_VERSION:
        backend.set(account, _MANAGED_VERSION)
    if backend.get(account) != _MANAGED_VERSION:
        raise SecretStorageError("凭据后端未确认迁移状态，未完成变更")


def set_secret(name: str, value: str) -> bool:
    """写入并复读确认；后端失败或未实际写入时明确报错。"""
    account = ACCOUNTS.get(name)
    if account is None:
        raise KeyError(f"未知密钥名: {name}")
    backend = _get_backend()
    if backend is None or not value:
        raise SecretStorageError("凭据后端不可用或密钥为空，未保存")
    try:
        backend.set(account, value)
        if backend.get(account) != value:
            raise SecretStorageError("凭据后端未确认保存，未完成替换")
        _mark_managed(backend, name)
    except SecretStorageError:
        raise
    except Exception as exc:
        raise SecretStorageError("凭据后端保存失败，未完成替换") from exc
    return True


def delete_secret(name: str) -> None:
    account = ACCOUNTS.get(name)
    if account is None:
        raise KeyError(f"未知密钥名: {name}")
    backend = _get_backend()
    if backend is None:
        raise SecretStorageError("凭据后端不可用，无法确认清除")
    try:
        # Establish ownership before deleting. A failed delete leaves its real
        # value visible; no other root may subsequently migrate stale plaintext.
        _mark_managed(backend, name)
        if backend.get(account) is not None:
            backend.delete(account)
        if backend.get(account) is not None:
            raise SecretStorageError("凭据后端未确认删除，未完成清除")
    except SecretStorageError:
        raise
    except Exception as exc:
        raise SecretStorageError("凭据后端删除失败，未完成清除") from exc


def secret_value_for_file(name: str, current: str, previous: str, env_value: str) -> str:
    """返回该密钥字段应写进配置文件的取值。

    普通保存只迁移未入库的旧密钥，不覆盖已有凭据。显式替换/清除先完成
    后端操作再保存模型；空 current 不会复活 previous。环境值从不入库或落盘。
    迁移失败抛异常，调用方尚未写文件，旧文件保持原样。
    """
    candidate = previous if env_value else current
    stored, legacy_allowed = secret_state(name)
    if stored or not legacy_allowed:
        return ""
    if not candidate:
        return ""
    set_secret(name, candidate)
    return ""
