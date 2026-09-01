"""M11++ 全局设置：掩码汇总 + 可设字段校验 + 真连通测试派发（后端支撑）。

- 私人信息（MP/LLM/SSH 密码）只存本地（LLM key 落 config.json、SSH 密码走系统
  凭据管理器），绝不上传、不进回包。
- 对外回包一律走 mask_config()，密钥只出现 <redacted>；另设「只读状态」接口
  返回是否已配置的布尔态，以及「点眼睛」时按需提取原文的 reveal 接口（仅前端本地展示）。
- 真连通：llm 走 M3 工厂（auto->openai 时真正 ping）；mp 用最小 GET 验证 key 有效性；
  ssh 用 M6 SSHManager 建连 + whoami（读系统凭据管理器密码）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Optional

from ..config import AiModeConfig, save_settings

MASK = "<redacted>"
SSH_PASSWORD_KEY = "ssh_password"   # 仅内部保留，永不回包
#: 仅凭据管理器处理、不落 model 的字段（写路由单独提取）。
SECRET_FIELDS = {SSH_PASSWORD_KEY}

#: 设置页可写字段：key -> 校验函数（返回 None=合法，否则返回错误描述字符串）。
def _int_min1(value) -> Optional[str]:
    try:
        if int(value) >= 1:
            return None
    except (TypeError, ValueError):
        pass
    return "需为 >=1 的整数"


def _int_nonneg(value) -> Optional[str]:
    try:
        if int(value) >= 0:
            return None
    except (TypeError, ValueError):
        pass
    return "需为 >=0 的整数"


def _bool(value) -> Optional[str]:
    return None if isinstance(value, bool) else "需为布尔值"


def _provider(value) -> Optional[str]:
    return None if str(value).strip().lower() in {"fake", "openai", "auto"} else "未知 LLM 提供方"


def _port(value) -> Optional[str]:
    try:
        if 1 <= int(value) <= 65535:
            return None
    except (TypeError, ValueError):
        pass
    return "端口需在 1-65535"


def _str(value, maxlen: int = 256) -> Optional[str]:
    if not isinstance(value, str):
        return "需为字符串"
    if len(value) > maxlen:
        return "长度超限"
    return None


SETTABLE_FIELDS: dict[str, Callable[[object], Optional[str]]] = {
    "max_jobs": _int_min1,
    "poll_interval_seconds": _int_min1,
    "billing_estimate_enabled": _bool,
    "llm_provider": _provider,
    "llm_base_url": _str,
    "llm_api_key": _str,
    "llm_model": _str,
    "llm_timeout_seconds": _int_min1,
    "llm_max_retries": _int_nonneg,
    "llm_max_tokens": _int_min1,
    "llm_temperature": lambda v: None if isinstance(v, float) or isinstance(v, int) else "需为数值",
    "llm_enable_thinking": _bool,
    "ssh_name": _str,
    "ssh_host": _str,
    "ssh_port": _port,
    "ssh_username": _str,
    "mp_api_key": _str,
}


_BOOL_FIELDS = {"billing_estimate_enabled", "llm_enable_thinking"}

_INT_FIELDS = {"max_jobs", "poll_interval_seconds",
               "llm_timeout_seconds", "llm_max_retries", "llm_max_tokens"}


def _coerce(key: str, raw) -> object:
    """把写回值按字段类型转成规范类型，避免下游因字符串/数值混用出错。"""
    if key in _INT_FIELDS:
        return int(raw)
    if key == "llm_temperature":
        return float(raw)
    if key in _BOOL_FIELDS:
        return bool(raw)
    return str(raw)


def mask_config(config: AiModeConfig) -> dict:
    """把配置汇总结成可安全对外展示的字典（密钥一律掩码）。"""
    return {
        "enabled": config.enabled,
        "data_dir": str(config.data_dir),
        "max_jobs": config.max_jobs,
        "poll_interval_seconds": config.poll_interval_seconds,
        "billing_estimate_enabled": config.billing_estimate_enabled,
        "llm": {
            "base_url": config.llm_base_url,
            "model": config.llm_model,
            "provider": config.llm_provider,
            "api_key": MASK if config.llm_api_key else "",
            "enable_thinking": config.llm_enable_thinking,
        },
        "ssh": {
            "name": config.ssh_name,
            "host": config.ssh_host,
            "port": config.ssh_port,
            "username": config.ssh_username,
        },
        "materials_project": {"api_key": MASK if config.mp_api_key else ""},
    }


def update_from_patch(config: AiModeConfig,
                      patch: Mapping) -> AiModeConfig:
    """按白名单字段合并更新配置（不落盘）。

    ssh_password 属 SECRET_FIELDS：接受但不写进 model（由写路由经凭据管理器处理）。

    :raises ValueError: 含未知字段或字段值不合法。
    """
    if not isinstance(patch, Mapping):
        raise ValueError("设置必须为对象")
    unknown = [k for k in patch
               if k not in SETTABLE_FIELDS and k not in SECRET_FIELDS]
    if unknown:
        raise ValueError(f"未知设置字段: {', '.join(sorted(unknown))}")
    errors = []
    data = config.model_dump(mode="json")
    for key, raw in patch.items():
        if key in SECRET_FIELDS:
            continue   # 由写路由单独处理（凭据管理器）
        issue = SETTABLE_FIELDS[key](raw)
        if issue:
            errors.append(f"{key}: {issue}")
            continue
        data[key] = _coerce(key, raw)
    if errors:
        raise ValueError("; ".join(errors))
    return AiModeConfig(**data)


def persist(config: AiModeConfig, config_path=None) -> None:
    """把配置写入本地私有文件（enabled 不落盘；密钥仅本地）。"""
    save_settings(config, config_path=config_path)


def writable_fields() -> list[str]:
    """设置页可写字段（含仅凭据管理器处理的 ssh_password）。"""
    return sorted(set(SETTABLE_FIELDS) | SECRET_FIELDS)


def _default_credential_store():
    from ..ssh.credentials import KeyringCredentialStore
    return KeyringCredentialStore()


def store_ssh_password(config: AiModeConfig, password, *,
                       credential_store=None) -> None:
    """把 SSH 密码写入系统凭据管理器（绝不落盘/回包）。空值=不修改。"""
    if password is None:
        return
    password = str(password)
    if not password:
        return
    if not config.ssh_host or not config.ssh_username:
        raise ValueError("保存 SSH 密码需先填写主机地址与用户名")
    store = credential_store or _default_credential_store()
    store.set_password(config.ssh_host, config.ssh_username, password)


def get_ssh_password(config: AiModeConfig, *,
                     credential_store=None) -> Optional[str]:
    """读回凭据管理器里的密码（仅「眼睛」点击时用于前端本地展示）。"""
    if not config.ssh_host or not config.ssh_username:
        return None
    store = credential_store or _default_credential_store()
    try:
        return store.get_password(config.ssh_host, config.ssh_username)
    except Exception:  # noqa: BLE001
        return None


def secret_status(config: AiModeConfig, *,
                  credential_store=None) -> dict:
    """返回密钥「是否已配置」的布尔态（不回明文、不泄密）。"""
    return {
        "llm": bool(config.llm_api_key),
        "mp": bool(config.mp_api_key),
        "ssh": get_ssh_password(config, credential_store=credential_store) is not None,
    }


def _llm_test(cfg: AiModeConfig) -> dict:
    from ..llm.factory import test_connection
    result = test_connection(cfg)
    if result.get("provider") == "fake":
        # 明确提示：此刻是假 LLM 离线可用，未连真实接口。
        result["message"] = "未配置真实接口（假 LLM 离线可用）"
    return result


def _mp_connect(cfg: AiModeConfig, *, timeout: float = 10.0) -> dict:
    """最小真实 MP 校验：get /materials/summary/?limit=1，看 key 是否有效。"""
    import httpx
    url = "https://api.materialsproject.org/materials/summary/"
    headers = {"X-API-KEY": cfg.mp_api_key, "Accept": "application/json"}
    try:
        resp = httpx.get(url, headers=headers, params={"limit": 1},
                         timeout=timeout, follow_redirects=True)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "provider": "mp",
                "message": f"MP 接口请求失败（网络不可达或被拦截）：{type(exc).__name__}"}
    if resp.status_code in (401, 403):
        return {"ok": False, "provider": "mp",
                "message": "MP API key 被拒绝（401/403），请检查后重试"}
    if resp.status_code >= 400:
        return {"ok": False, "provider": "mp",
                "message": f"MP API 返回 HTTP {resp.status_code}"}
    try:
        data = resp.json() if resp.content else {}
    except ValueError:
        data = {}
    docs = data.get("data") if isinstance(data, dict) else None
    n = len(docs) if isinstance(docs, list) else 0
    return {"ok": True, "provider": "mp",
            "message": f"MP API 连通成功（返回 {n} 条）"}


def _mp_test(cfg: AiModeConfig) -> dict:
    if not cfg.mp_api_key:
        return {"ok": False, "provider": "mp",
                "message": "未配置 Materials Project API key"}
    return _mp_connect(cfg)


def _ssh_test(cfg: AiModeConfig) -> dict:
    if not cfg.ssh_host:
        return {"ok": False, "provider": "ssh", "message": "未配置 SSH 主机"}
    if not cfg.ssh_username:
        return {"ok": False, "provider": "ssh", "message": "未配置 SSH 用户名"}
    from ..ssh.connection import SSHManager
    from ..ssh.credentials import KeyringCredentialStore
    try:
        manager = SSHManager(credentials=KeyringCredentialStore(),
                             connect_timeout=10)
        try:
            ok, msg = manager.test_connection(host=cfg.ssh_host,
                                              username=cfg.ssh_username,
                                              port=cfg.ssh_port or 22)
        finally:
            try:
                manager.close()
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "provider": "ssh",
                "message": f"SSH 测试出错：{type(exc).__name__}"}
    return {"ok": bool(ok), "provider": "ssh", "message": msg}


def _default_testers() -> dict:
    return {"llm": _llm_test, "mp": _mp_test, "ssh": _ssh_test}


def check_connection(provider: str, config: AiModeConfig, *,
                    testers: Optional[Mapping[str, Callable[[AiModeConfig], dict]]] = None) -> dict:
    """按 provider 派发连通测试；未识别的 provider 抛 ValueError。

    :param testers: 注入用（测试可传 fake），缺省内置（llm 真实工厂 / mp、ssh 真连通）。
    """
    name = str(provider).strip().lower()
    tests = dict(_default_testers())
    if testers:
        tests.update({str(k).lower(): v for k, v in testers.items()})
    if name not in tests:
        raise ValueError(f"未知设置 provider: {name}（可用: {sorted(tests)}）")
    return dict(tests[name](config))