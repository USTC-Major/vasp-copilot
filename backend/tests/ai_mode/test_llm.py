"""LLM 客户端（M3）独立测试：FakeLLM、OpenAI 兼容（注入假 http）、工厂、连通测试。"""

import json

import httpx
import pytest

from ai_mode.config import AiModeConfig
from ai_mode.llm import (
    LLMBadRequestError,
    LLMError,
    LLMUnavailableError,
    build_client,
    known_providers,
    resolve_provider,

)
from ai_mode.llm.base import _strip_json_fences
from ai_mode.llm.fake import FakeLLM
from ai_mode.llm import test_connection as llm_test_connection
from ai_mode.llm.openai_compat import OpenAIClient


def cfg(**kw):
    data = dict(
        llm_provider=kw.pop("llm_provider", "auto"),
        llm_base_url=kw.pop("llm_base_url", ""),
        llm_api_key=kw.pop("llm_api_key", ""),
        llm_model=kw.pop("llm_model", "gpt-test"),
        llm_timeout_seconds=kw.pop("llm_timeout_seconds", 2),
        llm_max_retries=kw.pop("llm_max_retries", 1),
        llm_max_tokens=kw.pop("llm_max_tokens", 64),
        llm_temperature=kw.pop("llm_temperature", 0.2),
        enabled=kw.pop("enabled", False),
    )
    data.update(kw)
    return AiModeConfig(**data)


def test_fake_llm_rule_and_queue():
    fake = FakeLLM(rules={r"需求": "收到需求，是否开始计算？"})
    fake.enqueue("第二条回复")
    r = fake.complete([{"role": "user", "content": "我有需求"}])
    assert r.text == "收到需求，是否开始计算？"
    r2 = fake.complete([{"role": "user", "content": "随便"}])
    assert r2.text == "第二条回复"
    assert len(fake.calls) == 2


def test_fake_llm_no_reply_raises_unavailable():
    fake = FakeLLM()
    with pytest.raises(LLMUnavailableError):
        fake.complete([{"role": "user", "content": "hi"}])


def test_fake_complete_json_with_fences():
    fake = FakeLLM(rules={r".*": '```json\n{"job": "r1", "parallel": true}\n```'})
    data = fake.complete_json([{"role": "user", "content": "规划"}])
    assert data == {"job": "r1", "parallel": True}


def test_strip_json_fences():
    assert _strip_json_fences("```json\n{\"a\": 1}\n```") == '{"a": 1}'
    assert _strip_json_fences('prefix {"b":2} suffix') == '{"b":2}'
    assert _strip_json_fences('{"c": 3}') == '{"c": 3}'


def test_complete_json_bad_raises():
    fake = FakeLLM(rules={r".*": "不是 json 哦"})
    with pytest.raises(LLMBadRequestError):
        fake.complete_json([{"role": "user", "content": "x"}])


# ---------------- OpenAI 兼容（注入假 http） ----------------

class FakeResp:
    def __init__(self, status, payload):
        self.status_code = status
        self._body = payload
        self.text = payload if isinstance(payload, str) else json.dumps(payload,
                                                                        ensure_ascii=False)

    def json(self):
        if isinstance(self._body, str):
            return json.loads(self._body)
        return self._body


class FakeHttp:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def post(self, url, json=None, headers=None):
        assert self.responses, "response queue exhausted"
        self.requests.append((url, json, headers))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def openai_client(http, *, retries=1):
    return OpenAIClient(base_url="http://llm:8000/v1", api_key="sk-test",
                        model="gpt-test", timeout_seconds=2,
                        max_retries=retries, http=http)


def test_openai_complete_ok():
    body = {"choices": [{"message": {"content": "  你好  "}}],
            "usage": {"total_tokens": 4}}
    http = FakeHttp(FakeResp(200, body))
    res = openai_client(http).complete([{"role": "user", "content": "hi"}])
    assert res.text == "你好"
    url, payload, headers = http.requests[0]
    assert url.endswith("/chat/completions")
    assert headers["Authorization"] == "Bearer sk-test"
    assert payload["model"] == "gpt-test"


def test_openai_401_raises_bad_request():
    http = FakeHttp(FakeResp(401, "unauthorized"))
    with pytest.raises(LLMBadRequestError):
        openai_client(http).complete([{"role": "user", "content": "hi"}])


def test_openai_500_retries_then_unavailable():
    http = FakeHttp(FakeResp(500, "oops"), FakeResp(500, "oops"))
    with pytest.raises(Exception):
        openai_client(http).complete([{"role": "user", "content": "hi"}])
    assert len(http.requests) == 2  # 500 5xx 不重试（4xx/5xx 语义在 complete 内抛）


def test_openai_timeout_raises_unavailable():
    http = FakeHttp(httpx.ConnectTimeout("boom"), httpx.ConnectTimeout("boom"))
    with pytest.raises(LLMUnavailableError):
        openai_client(http).complete([{"role": "user", "content": "hi"}])


# ---------------- 工厂 / 解析 / 连通 ----------------

def test_resolve_provider_auto_no_key():
    assert resolve_provider(cfg()) == "fake"


def test_resolve_provider_auto_with_key():
    c = cfg(llm_base_url="http://llm:8000/v1", llm_api_key="sk-x")
    assert resolve_provider(c) == "openai"


def test_resolve_provider_explicit_and_unknown():
    assert resolve_provider(cfg(llm_provider="fake")) == "fake"
    with pytest.raises(LLMError):
        resolve_provider(cfg(llm_provider="bogus"))


def test_build_client_fake_and_openai():
    c = cfg(llm_provider="fake")
    client = build_client(c)
    assert client.name == "fake"
    c2 = cfg(llm_provider="openai", llm_base_url="http://x", llm_api_key="k")
    client2 = build_client(c2)
    assert client2.name == "openai"
    close = getattr(client2, "close", None)
    if close:
        close()


def test_known_providers_has_builtins():
    assert {"fake", "openai"} <= set(known_providers())


def test_connection_fake_ok():
    result = llm_test_connection(cfg())
    assert result["ok"] is True
    assert result["provider"] == "fake"
# ---------------- 流式（SSE 增量）----------------

class _Fs:
    def __init__(self, status, lines):
        self.status_code = status
        self._lines = list(lines)
        self.text = "".join(self._lines)

    def iter_lines(self):
        return iter(self._lines)


class _StreamCtx:
    def __init__(self, resp):
        self._resp = resp

    def __enter__(self):
        return self._resp

    def __exit__(self, *args):
        return False


class StreamHttp:
    def __init__(self, status, lines):
        self._stream_resp = _Fs(status, lines)
        self.requests = []

    def stream(self, method, url, **kwargs):
        self.requests.append((url, kwargs.get("json"),
                              kwargs.get("headers")))
        return _StreamCtx(self._stream_resp)


def test_fake_stream_chunks():
    fake = FakeLLM(rules={r".*": "0123456789abcdef"})
    chunks = list(fake.stream([{"role": "user", "content": "hi"}]))
    assert all(c["type"] == "answer" for c in chunks)
    assert "".join(c["text"] for c in chunks) == "0123456789abcdef"
    assert len(fake.calls) == 1


def test_openai_stream_reasoning_and_content():
    lines = [
        'data: {"choices":[{"delta":{"reasoning_content":"先"}}]}',
        'data: {"choices":[{"delta":{"reasoning_content":"读"}}]}',
        'data: {"choices":[{"delta":{"content":"我看到"}}]}',
        'data: {"choices":[{"delta":{"content":"工作区有 INCAR"}}]}',
        "data: [DONE]",
    ]
    http = StreamHttp(200, lines)
    chunks = list(openai_client(http).stream([{"role": "user",
                                               "content": "hi"}]))
    thinking = "".join(c["text"] for c in chunks if c["type"] == "thinking")
    answer = "".join(c["text"] for c in chunks if c["type"] == "answer")
    assert thinking == "先读"
    assert answer == "我看到工作区有 INCAR"
    _url, payload, _headers = http.requests[0]
    assert payload["stream"] is True
    assert payload["messages"][0]["content"] == "hi"


def test_openai_stream_error_status():
    http = StreamHttp(503, [])
    with pytest.raises(LLMUnavailableError):
        list(openai_client(http).stream([{"role": "user", "content": "hi"}]))


def test_openai_stream_fallback_to_complete():
    # FakeHttp 无 stream 方法 -> 走一次性 complete 兼容路径
    body = {"choices": [{"message": {"content": "你好"}}], "usage": {}}
    http = FakeHttp(FakeResp(200, body))
    chunks = list(openai_client(http).stream([{"role": "user", "content": "hi"}]))
    assert all(c["type"] == "answer" for c in chunks)
    assert "".join(c["text"] for c in chunks) == "你好"

def test_openai_thinking_payload_complete():
    body = {"choices": [{"message": {"content": "ok"}}], "usage": {}}
    http = FakeHttp(FakeResp(200, body))
    client = OpenAIClient(base_url="http://llm:8000/v1", api_key="sk-test",
                          model="gpt-test", enable_thinking=True, http=http)
    client.complete([{"role": "user", "content": "hi"}])
    _url, payload, _headers = http.requests[0]
    assert payload["thinking"] == {"type": "enabled"}


def test_openai_thinking_payload_stream():
    line = "data: " + json.dumps({"choices": [{"delta": {"content": "hi"}}]})
    http = StreamHttp(200, [line, "data: [DONE]"])
    client = OpenAIClient(base_url="http://llm:8000/v1", api_key="sk-test",
                          model="gpt-test", enable_thinking=True, http=http)
    list(client.stream([{"role": "user", "content": "hi"}]))
    _url, payload, _headers = http.requests[0]
    assert payload["thinking"] == {"type": "enabled"}


def test_openai_thinking_off_by_default():
    body = {"choices": [{"message": {"content": "ok"}}], "usage": {}}
    http = FakeHttp(FakeResp(200, body))
    openai_client(http).complete([{"role": "user", "content": "hi"}])
    _url, payload, _headers = http.requests[0]
    assert "thinking" not in payload
def test_openai_stream_midstream_timeout_raises_unavailable():
    # 复现 M42：流式中途抛 httpx 超时时，此前会裸抛 httpx 错误导致 SSE 静默断开、
    # 前端只剩部分正文；修复后应转成 LLMUnavailableError，让 runner 回读离线文案。
    lines = [
        'data: {"choices":[{"delta":{"content":"已生成部分"}}]}',
        'data: {"choices":[{"delta":{"content":"剩余内容"}}]}',
        "data: [DONE]",
    ]

    class _ReadTimeoutFs(_Fs):
        def iter_lines(self):
            yield self._lines[0]
            raise httpx.ReadTimeout("stream read timed out")

    http = StreamHttp(200, lines)
    http._stream_resp = _ReadTimeoutFs(200, lines)
    chunks_iter = openai_client(http).stream([{"role": "user", "content": "hi"}])
    first = next(chunks_iter)
    assert first["type"] == "answer" and first["text"] == "已生成部分"
    with pytest.raises(LLMUnavailableError) as ei:
        next(chunks_iter)
    assert "流式调用超时" in str(ei.value)
