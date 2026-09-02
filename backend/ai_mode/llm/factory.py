"""LLM 提供方工厂（M3）：可插拔注册 + 按配置构建。

- llm_provider: fake|openai|auto（auto：有可用 base_url+api_key 走 openai，否则 fake）。
- build_client(config) 唯一入口；上层只与 LLMClient 打交道。
"""
from __future__ import annotations

import logging
from typing import Callable

from .base import LLMClient
from .errors import LLMError
from .fake import FakeLLM
from ..config import AiModeConfig

logger = logging.getLogger("ai_mode.llm")
__test__ = False  # pytest: stop collecting top-level test_* functions
ProviderFactory = Callable[[AiModeConfig], LLMClient]
_registry: dict[str, ProviderFactory] = {}


def register_provider(name: str, factory: ProviderFactory) -> None:
    if name in _registry:
        logger.warning("LLM 提供方 %s 被重复注册", name)
    _registry[name] = factory


def known_providers() -> list[str]:
    return sorted(_registry)


def _fake_factory(_config: AiModeConfig) -> LLMClient:
    return FakeLLM()


def _openai_factory(config: AiModeConfig) -> LLMClient:
    from .openai_compat import OpenAIClient
    return OpenAIClient(
        base_url=config.llm_base_url,
        api_key=config.llm_api_key,
        model=config.llm_model,
        timeout_seconds=config.llm_timeout_seconds,
        max_retries=config.llm_max_retries,
        max_tokens=config.llm_max_tokens,
        temperature=config.llm_temperature,
        enable_thinking=config.llm_enable_thinking,
    )


register_provider("fake", _fake_factory)
register_provider("openai", _openai_factory)


def resolve_provider(config: AiModeConfig) -> str:
    requested = (config.llm_provider or "auto").strip().lower()
    if requested == "auto":
        return "openai" if (config.llm_base_url and config.llm_api_key) else "fake"
    if requested not in _registry:
        raise LLMError(f"未知 LLM 提供方: {requested}（可用: {known_providers()}）")
    return requested


def build_client(config: AiModeConfig, *, provider: str | None = None) -> LLMClient:
    name = resolve_provider(config) if provider is None else provider
    if name not in _registry:
        raise LLMError(f"未知 LLM 提供方: {name}（可用: {known_providers()}）")
    return _registry[name](config)


def test_connection(config: AiModeConfig) -> dict:
    """连通测试（设置页「测试连通」按钮用）。返回 {ok, provider, message}。"""
    name = resolve_provider(config)
    if name == "fake":
        client = FakeLLM()
        try:

            return {"ok": True, "provider": "fake",
                    "message": "假 LLM 离线可用（未配置真实 key）"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "provider": "fake", "message": str(exc)}
    client = _openai_factory(config)
    try:
        ok, msg = client.ping()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "provider": "openai", "message": str(exc)}
    close = getattr(client, "close", None)
    if close:
        close()
    return {"ok": ok, "provider": "openai", "message": msg}