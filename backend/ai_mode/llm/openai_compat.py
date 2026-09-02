"""OpenAI 兼容客户端（独立实现，走 httpx；不 import 工具箱代码）。

- base_url / api_key / model 都在 AiModeConfig（本地配置）。
- 注入 fake httpx.Client 可离线测试；不注入则用真实客户端。
- 超时/重试可配；5xx/429/传输错误按「LLM 不可用」整体瘫痪语义抛错。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

import httpx

from .base import LLMClient, Message, CompletionResult
from .errors import LLMBadRequestError, LLMUnavailableError

logger = logging.getLogger("ai_mode.llm.openai")

_RETRIABLE = {429, 500, 502, 503, 504}


class OpenAIClient(LLMClient):
    name = "openai"
    description = "OpenAI 兼容接口（base_url/api_key/model）"

    def __init__(self, *, base_url: str, api_key: str, model: str,
                 timeout_seconds: int = 120, max_retries: int = 2,
                 max_tokens: int = 1024, temperature: float = 0.2,
                 enable_thinking: bool = False,
                 http: Optional[httpx.Client] = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.enable_thinking = enable_thinking
        self._http = http or httpx.Client(timeout=timeout_seconds)

    @property
    def usable(self) -> bool:
        return bool(self.base_url and self.api_key)

    def close(self) -> None:
        self._http.close()

    def stream(self, messages, *, max_tokens=None, temperature=None):
        """真实增量流（SSE）：解析 delta.reasoning_content 与 content。

        type="thinking" 来自提供方 reasoning_content；无 reasoning 的模型
        只输出 answer 增量。注入 http 不支持 stream 时退回一次性 complete。
        流中途若发生超时/传输断开，统一转成 LLMUnavailableError，让上层回读
        离线文案而不是让 SSE 静默截断（修复 M42 回复中断）。
        """
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "max_tokens": max_tokens if max_tokens is not None else self.max_tokens,
            "temperature": self.temperature if temperature is None else temperature,
        }
        if self.enable_thinking:
            payload["thinking"] = {"type": "enabled"}
        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}",
                   "Content-Type": "application/json"}
        if not hasattr(self._http, "stream"):
            result = self.complete(messages, max_tokens=max_tokens,
                                   temperature=temperature)
            yield {"type": "answer", "text": result.text}
            return
        try:
            with self._http.stream("POST", url, json=payload,
                                   headers=headers) as resp:
                if resp.status_code == 401:
                    raise LLMBadRequestError("LLM 鉴权失败(401)：请检查 api_key")
                if resp.status_code in _RETRIABLE:
                    raise LLMUnavailableError(
                        f"LLM 服务端不可用({resp.status_code})")
                if resp.status_code >= 400:
                    raise LLMBadRequestError(
                        f"LLM 请求被拒({resp.status_code}): {resp.text[:300]}")
                for line in resp.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if raw == "[DONE]":
                        break
                    try:
                        obj = json.loads(raw)
                    except ValueError:
                        continue
                    choices = obj.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    reasoning = delta.get("reasoning_content")
                    content = delta.get("content")
                    if reasoning:
                        yield {"type": "thinking", "text": reasoning}
                    if content:
                        yield {"type": "answer", "text": content}
        except httpx.TimeoutException as exc:
            raise LLMUnavailableError(
                f"LLM 流式调用超时（{self.timeout_seconds}s）已断开"
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMUnavailableError(f"LLM 流式网络错误: {exc}") from exc

    # ---- 连通 ----
    def ping(self) -> tuple[bool, str]:
        """连通测试：一次极小请求。返回 (是否可用, 人类可读消息)。"""
        try:
            self.complete([{"role": "user", "content": "ping"}], max_tokens=1)
            return True, f"LLM 连通正常（{self.model}）"
        except LLMUnavailableError as exc:
            return False, str(exc)
        except LLMBadRequestError as exc:
            return False, str(exc)

    # ---- 请求核心 ----
    def _request(self, payload: dict[str, Any]) -> httpx.Response:
        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}",
                   "Content-Type": "application/json"}
        attempt = 0
        last_err: Exception | None = None
        while True:
            try:
                resp = self._http.post(url, json=payload, headers=headers)
                if resp.status_code in _RETRIABLE and attempt < self.max_retries:
                    attempt += 1
                    sleep = min(2 ** attempt, 10)
                    logger.warning("LLM 返回 %s，%.1fs 后重试（%d/%d）",
                                   resp.status_code, sleep, attempt,
                                   self.max_retries)
                    time.sleep(sleep)
                    continue
                return resp
            except httpx.TimeoutException as exc:
                last_err = exc
                if attempt < self.max_retries:
                    attempt += 1
                    time.sleep(min(2 ** attempt, 10))
                    continue
                raise LLMUnavailableError(
                    f"LLM 调用超时（{self.timeout_seconds}s，已重试 {attempt} 次）"
                ) from exc
            except httpx.HTTPError as exc:
                last_err = exc
                if attempt < self.max_retries:
                    attempt += 1
                    time.sleep(min(2 ** attempt, 10))
                    continue
                raise LLMUnavailableError(f"LLM 网络错误: {exc}") from exc
        # 不可达：重试已用尽

    def complete(self, messages: list[Message], *, max_tokens: int | None = None,
                 temperature: float | None = None) -> CompletionResult:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens if max_tokens is not None else self.max_tokens,
            "temperature": self.temperature if temperature is None else temperature,
        }
        if self.enable_thinking:
            payload["thinking"] = {"type": "enabled"}
        resp = self._request(payload)
        if resp.status_code == 401:
            raise LLMBadRequestError("LLM 鉴权失败(401)：请检查 api_key")
        if resp.status_code == 403:
            raise LLMBadRequestError("LLM 拒绝访问(403)：请检查权限/配额")
        if resp.status_code == 400:
            raise LLMBadRequestError(f"LLM 请求被拒(400): {resp.text[:300]}")
        if resp.status_code in _RETRIABLE:
            raise LLMUnavailableError(
                f"LLM 服务端不可用({resp.status_code}): {resp.text[:300]}")
        if resp.status_code >= 400:
            raise LLMBadRequestError(
                f"LLM 请求被拒({resp.status_code}): {resp.text[:300]}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise LLMUnavailableError("LLM 返回非 JSON 响应") from exc
        choices = data.get("choices") or []
        if not choices:
            raise LLMUnavailableError(f"LLM 返回空 choices: {str(data)[:200]}")
        msg = choices[0].get("message", {})
        text = (msg.get("content") or "").strip()
        return CompletionResult(text=text, usage=data.get("usage", {}), raw=data)