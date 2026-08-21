from __future__ import annotations

import httpx

from ..core.config import LlmConfig
from ..schemas.result import DiagnosisResult
from . import prompts


class OpenAiExplainer:
    # OpenAI-compatible chat completions provider (MVP 5.4 timeout/retry/degrade).

    def __init__(self, cfg: LlmConfig, client=None,
                 retries: 'int | None' = None) -> None:
        self._cfg = cfg
        self._retries = retries if retries is not None else cfg.max_retries
        self._client = client if client is not None else httpx.Client(
            timeout=cfg.timeout_seconds)

    def _complete(self, messages: list) -> str:
        url = self._cfg.base_url.rstrip('/') + '/chat/completions'
        payload = {
            'model': self._cfg.model,
            'messages': messages,
            'temperature': self._cfg.temperature,
            'max_tokens': self._cfg.max_tokens,
        }
        headers = {'Authorization': 'Bearer ' + self._cfg.api_key,
                   'Content-Type': 'application/json'}
        last_exc = None
        for attempt in range(self._retries + 1):
            try:
                resp = self._client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                return (data.get('choices') or [{}])[0].get('message', {}).get(
                    'content') or ''
            except Exception as exc:
                last_exc = exc
        raise RuntimeError('LLM completion failed: %s' % (last_exc,))

    def complete(self, messages: list) -> str:
        # Raw completion used by the AgentOrchestrator tool-call resolution.
        return self._complete(messages)

    def explain(self, result: DiagnosisResult) -> str:
        return self._complete(prompts.build_explain_messages(result))

    def chat(self, result: DiagnosisResult, question: str) -> str:
        return self._complete(prompts.build_chat_messages(result, question))

    def chat_general(self, question: str, history: 'list | None' = None) -> str:
        return self._complete(prompts.build_general_chat_messages(question, history))

    def test(self) -> str:
        # 最小连通性检查（用于「模型设置」的「测试连接」）
        return self._complete([{'role': 'user', 'content': 'ping'}])
