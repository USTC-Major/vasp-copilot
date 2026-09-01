"""Fake LLM（M3 可插拔桩）：离线联调/测试用，不依赖真实 key。

- 规则匹配或回复队列出内容；未预设时抛 LLMUnavailableError（瘫痪语义）。
"""
from __future__ import annotations

import re
from collections import deque

from .base import LLMClient, Message, CompletionResult
from .errors import LLMUnavailableError


class FakeLLM(LLMClient):
    """可插拔假 LLM：支持正则规则匹配与回复队列两种模式。"""

    name = "fake"
    description = "离线假 LLM（不联网、不校验 key）"

    def __init__(self, rules: dict[str, str] | None = None,
                 queue: list[str] | None = None):
        self._rules = {re.compile(k): v for k, v in (rules or {}).items()}
        self._queue = deque(queue or [])
        self.calls: list[list[Message]] = []

    def on(self, pattern: str, reply: str) -> "FakeLLM":
        self._rules[re.compile(pattern)] = reply
        return self

    def enqueue(self, reply: str) -> "FakeLLM":
        self._queue.append(reply)
        return self

    def complete(self, messages: list[Message], *, max_tokens: int | None = None,
                 temperature: float | None = None) -> CompletionResult:
        self.calls.append(messages)
        user_text = "\n".join(m.get("content", "") for m in messages
                              if m.get("role") in ("user", "tool"))
        for pattern, reply in self._rules.items():
            if pattern.search(user_text):
                return CompletionResult(text=reply)
        if self._queue:
            return CompletionResult(text=self._queue.popleft())
        raise LLMUnavailableError("FakeLLM 未预设回复（无法完成任务）")

    def stream(self, messages, *, max_tokens=None, temperature=None):
        """8 字符分片输出完整正文（离线联调流式事件结构用）。"""
        result = self.complete(messages, max_tokens=max_tokens,
                               temperature=temperature)
        text = result.text or ""
        step = 8
        for i in range(0, len(text), step):
            yield {"type": "answer", "text": text[i:i + step]}