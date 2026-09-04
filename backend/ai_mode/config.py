"""智能模式配置加载（优先级：默认值 < 本地私有 config.json < 环境变量）。

- 默认值见 ``defaults()``：max_jobs=20、轮询 60s、LLM 接口默认空等。
- 本地私有文件 ~/.vasp-ai/config.json 存用户偏好与本地密钥（仅本地，不随项目上传）。
- 环境变量 AI_MODE_*（含开关 ENABLE_AI_MODE）最高优先级，便于容器/测试注入。
- SSH 密码不落本模型：M6 起接入系统凭据管理器；这里仅存连接资料。
- enabled 一律以独立开关 ENABLE_AI_MODE 为准（本模块在此固化，禁止被文件覆盖）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from pydantic import BaseModel, Field

from . import paths as _paths
from .gate import is_ai_mode_enabled

ENV_PREFIX = "AI_MODE_"

#: 环境变量名 -> 配置字段名（扁平映射，免嵌套魔法）。
_ENV_MAP: dict[str, str] = {
    f"{ENV_PREFIX}MAX_JOBS": "max_jobs",
    f"{ENV_PREFIX}POLL_INTERVAL_SECONDS": "poll_interval_seconds",
    f"{ENV_PREFIX}BILLING_ESTIMATE_ENABLED": "billing_estimate_enabled",
    f"{ENV_PREFIX}LLM_PROVIDER": "llm_provider",
    f"{ENV_PREFIX}LLM_BASE_URL": "llm_base_url",
    f"{ENV_PREFIX}LLM_API_KEY": "llm_api_key",
    f"{ENV_PREFIX}LLM_MODEL": "llm_model",
    f"{ENV_PREFIX}LLM_TIMEOUT_SECONDS": "llm_timeout_seconds",
    f"{ENV_PREFIX}LLM_MAX_RETRIES": "llm_max_retries",
    f"{ENV_PREFIX}LLM_MAX_TOKENS": "llm_max_tokens",
    f"{ENV_PREFIX}LLM_TEMPERATURE": "llm_temperature",
    f"{ENV_PREFIX}LLM_ENABLE_THINKING": "llm_enable_thinking",
    f"{ENV_PREFIX}SSH_NAME": "ssh_name",
    f"{ENV_PREFIX}SSH_HOST": "ssh_host",
    f"{ENV_PREFIX}SSH_PORT": "ssh_port",
    f"{ENV_PREFIX}SSH_USERNAME": "ssh_username",
    f"{ENV_PREFIX}SSH_KNOWN_HOSTS_PATH": "ssh_known_hosts_path",
    f"{ENV_PREFIX}MP_API_KEY": "mp_api_key",
}


class AiModeConfig(BaseModel):
    """智能模式全局配置。顶层键平铺（llm_/ssh_/mp_ 前缀），避免嵌套魔法。

    对齐总纲 §十：LLM、SSH、MP、作业数上限、轮询、计费开关等全局项。
    """

    enabled: bool = False
    data_dir: Path = Field(default_factory=_paths.home_dir)
    max_jobs: int = 20
    poll_interval_seconds: int = 60
    billing_estimate_enabled: bool = False

    llm_provider: str = "auto"   # fake|openai|auto（auto：有可用 key 走 openai，否则 fake）
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_timeout_seconds: int = 120
    llm_max_retries: int = 2
    llm_max_tokens: int = 1024
    llm_temperature: float = 0.2
    llm_enable_thinking: bool = False

    ssh_name: str = ""
    ssh_host: str = ""
    ssh_port: int = 22
    ssh_username: str = ""
    ssh_known_hosts_path: str = ""

    mp_api_key: str = ""


def defaults() -> dict[str, Any]:
    """返回默认配置字典（data_dir 动态计算自当前环境，测试可注入）。"""
    return AiModeConfig().model_dump(mode="json")


def _read_config_file(path: Path) -> dict[str, Any]:
    """读取本地私有配置文件；损坏/缺失一律返回空字典（降级默认）。"""
    if not path.is_file():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    known = set(defaults()) - {"enabled"}
    return {k: v for k, v in data.items() if k in known}


def _env_overrides(env: Mapping[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for var, key in _ENV_MAP.items():
        val = env.get(var)
        if val is not None and val != "":
            out[key] = val
    return out


def load_settings(
    *, env: Mapping[str, str] | None = None,
    config_path: Path | None = None,
) -> AiModeConfig:
    """按优先级合并三来源并返回配置对象（默认 < 文件 < 环境变量）。

    :param env: 环境变量映射；默认 os.environ。测试注入用。
    :param config_path: 私有配置文件路径；默认 paths.config_path()。
    """
    if env is None:
        env = os.environ
    base = defaults()
    if config_path is None:
        config_path = _paths.config_path()
    base.update(_read_config_file(config_path))
    base.update(_env_overrides(env))
    base["enabled"] = is_ai_mode_enabled(env)
    return AiModeConfig(**base)


def save_settings(config: AiModeConfig, config_path: Path | None = None) -> None:
    """把配置写入本地私有文件（enabled 不落盘，开关永远走环境变量）。"""
    if config_path is None:
        config_path = _paths.config_path()
    local_before = _read_config_file(config_path)
    payload = config.model_dump(mode="json")
    payload.pop("enabled", None)
    payload["data_dir"] = str(config.data_dir)
    payload.setdefault("llm_api_key", "")
    payload.setdefault("mp_api_key", "")
    # Environment secrets have higher runtime precedence but must never be
    # copied into the local config by an unrelated settings update. Preserve
    # any prior local value behind the environment override instead.
    for variable, field in ((f"{ENV_PREFIX}LLM_API_KEY", "llm_api_key"),
                            (f"{ENV_PREFIX}MP_API_KEY", "mp_api_key")):
        if os.environ.get(variable):
            payload[field] = str(local_before.get(field) or "")
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def execution_mode(hpc=None, *, explicit: str | None = None) -> str:
    """Classify the actual HPC execution backend, never the LLM provider.

    Production SSH managers are intrinsically ``Real``. Non-production
    adapters must opt in explicitly as ``Fake``; their class names are never
    inspected. No adapter is always ``None``.
    """
    declared = explicit if explicit is not None else getattr(hpc, "execution_mode", None)
    requested = str(declared or "None").strip().title()
    if requested not in {"Fake", "Real", "None"}:
        raise ValueError("execution mode must be Fake, Real, or None")
    if hpc is None:
        if requested != "None":
            raise ValueError("an execution backend is required for Fake/Real mode")
        return "None"
    from .ssh.connection import SSHManager
    if isinstance(hpc, SSHManager):
        if requested not in {"None", "Real"}:
            raise ValueError("SSHManager execution mode is always Real")
        return "Real"
    if requested == "Fake":
        return "Fake"
    raise ValueError("non-SSH execution adapters must explicitly declare Fake mode")
