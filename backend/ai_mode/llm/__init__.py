"""LLM 客户端子包（M3）：可插拔（fake/openai/auto），超时/重试/连通测试。

- build_client(config) 唯一入口。
- openai 客户端 usable 需 base_url+api_key；fake 永在。
- 不可用 = 整体瘫痪（LLMUnavailableError）。
"""
from .errors import LLMError, LLMBadRequestError, LLMUnavailableError
from .base import LLMClient, ToolRequest, CompletionResult
from .factory import (
    build_client,
    known_providers,
    register_provider,
    resolve_provider,
    test_connection,
)

__all__ = [
    "LLMClient", "ToolRequest", "CompletionResult",
    "LLMError", "LLMBadRequestError", "LLMUnavailableError",
    "build_client", "known_providers", "register_provider",
    "resolve_provider", "test_connection",
]