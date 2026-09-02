"""LLM 客户端抽象基类与通用数据类型（M3）。

- 客户端拿不到钥匙（SSH 密码/MP key），不做任何执行。
- 每次调用拿着「上下文快照 + 最新消息」，返回文本或结构化结果。
- LLM 不可用 -> 抛 LLMUnavailableError，由上层转成「整体瘫痪」提示。
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterator, Literal

from .errors import LLMError, LLMBadRequestError, LLMUnavailableError

__all__ = [
    "LLMClient",
    "ToolRequest",
    "CompletionResult",
    "Message",
    "LLMError",
    "LLMBadRequestError",
    "LLMUnavailableError",
]

Message = dict[str, str]          # {"role": ..., "content": ...}


@dataclass
class ToolRequest:
    """结构化工具调用请求（接口 §2；M5 门卫消费）。"""

    name: str
    args: dict[str, Any] = field(default_factory=dict)
    rationale: str = ""


@dataclass
class CompletionResult:
    """一次补全的结果。"""

    text: str
    tool_requests: list[ToolRequest] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    raw: object = None


class LLMClient(ABC):
    """LLM 客户端接口。OpenAI 兼容与 FakeLLM 都实现它。"""

    name: str = ""
    description: str = ""

    @abstractmethod
    def complete(self, messages: list[Message], *, max_tokens: int | None = None,
                 temperature: float | None = None) -> CompletionResult:
        """完成一次对话（文本）。"""

    def complete_json(self, messages: list[Message], **kwargs) -> dict:
        """请求结构化 JSON（含 markdown 围栏/前后缀兜底）。"""
        result = self.complete(messages, **kwargs)
        text = result.text.strip()
        try:
            return json.loads(_strip_json_fences(text))
        except json.JSONDecodeError as exc:
            raise LLMBadRequestError(
                f"{self.name} 返回内容不是合法 JSON: {text[:200]}"
            ) from exc
    def stream(self, messages, *, max_tokens=None, temperature=None):
        """增量输出迭代器：每次 yield 一个 dict，含 type 与增量文本。

        type: "thinking"（模型推理，可选）或 "answer"（正文）。
        text 为增量，前端累积显示即可。默认实现一次性返回完整正文，
        兼容不提供流式的提供方；子类可覆写为真实逐 token 流。
        """
        result = self.complete(messages, max_tokens=max_tokens,
                               temperature=temperature)
        yield {"type": "answer", "text": result.text}

    @property
    def usable(self) -> bool:
        return True   # 子类覆写（如 openai 需要 base_url+api_key）


def _strip_json_fences(text: str) -> str:
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    # 只保留最外层 { } 包围区间，容忍说明性前后缀
    first = text.find("{")
    last = text.rfind("}")
    if first != -1 and last != -1 and last > first:
        text = text[first:last + 1]
    return text