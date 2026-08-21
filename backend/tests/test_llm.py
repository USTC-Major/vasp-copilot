from __future__ import annotations

import json

import httpx
import pytest

from app.core.config import LlmConfig, Settings
from app.llm import get_explainer, reset_explainer, set_explainer
from app.llm import prompts
from app.llm.base import StubExplainer
from app.llm.openai_provider import OpenAiExplainer
from app.schemas.result import DiagnosisResult, NextStep, Provenance
from app.schemas.status import DiagnosisStatus, ModeKind


def _result(missing=(), allowed=True):
    return DiagnosisResult(
        diagnosis_id="diag_x",
        diagnosis_status=DiagnosisStatus.SUCCEEDED,
        summary="demo",
        missing_evidence=list(missing),
        next_step=NextStep(allowed=allowed, reason="ok"),
        provenance=Provenance(mode=ModeKind.RULE_BASED),
    )


def test_insufficient_line_fixed_phrase():
    line = prompts.insufficient_line(["OUTCAR"])
    assert line.startswith(prompts.INSUFFICIENT_PREFIX)
    assert "OUTCAR" in line


def test_explain_messages_roles():
    msgs = prompts.build_explain_messages(_result())
    assert len(msgs) == 2
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"
    assert "基于我提供的结构化诊断结果" in msgs[0]["content"]


def test_chat_messages_include_question():
    msgs = prompts.build_chat_messages(_result(), "为什么")
    assert "为什么" in msgs[1]["content"]


def test_provider_none_when_disabled():
    reset_explainer()
    assert get_explainer(Settings()) is None


def test_provider_returns_injected():
    reset_explainer()
    stub = StubExplainer("hi")
    set_explainer(stub)
    try:
        assert get_explainer() is stub
    finally:
        reset_explainer()


def test_openai_fake_transport_ok():
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [
            {"message": {"content": "answer"}}]})

    cfg = LlmConfig(enabled=True, api_key="k", base_url="https://x/v1",
                    model="m", temperature=0.1, max_tokens=4)
    ex = OpenAiExplainer(cfg, client=httpx.Client(
        transport=httpx.MockTransport(handler)))
    assert ex.explain(_result()) == "answer"
    assert captured["url"].endswith("/chat/completions")
    assert captured["auth"] == "Bearer k"
    assert captured["body"]["model"] == "m"


def test_openai_retries_then_raises():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        raise RuntimeError("boom")

    cfg = LlmConfig(enabled=True, api_key="k", base_url="https://x/v1")
    ex = OpenAiExplainer(cfg, retries=1, client=httpx.Client(
        transport=httpx.MockTransport(handler)))
    with pytest.raises(RuntimeError):
        ex.explain(_result())
    assert calls["n"] == 2