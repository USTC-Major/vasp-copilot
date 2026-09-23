"""The production OpenAI-compatible path is exercised only with local HTTP stubs."""

from __future__ import annotations

import copy
import hashlib
import hmac
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.ai_mode.config import AiModeConfig
from backend.ai_mode.reviewer import ReviewError, review_request
from backend.ai_mode.server import create_ai_mode_app

from .conftest import SECRET, canonical, plan, setup_scope


APPROVE = {
    "decision": "approve",
    "reason": "mechanical file preparation only",
    "checks": {
        "scope_match": True,
        "manifest_match": True,
        "ordinary_file_only": True,
        "no_scientific_claim": True,
        "no_execution": True,
    },
}


def _owner_payload(api):
    root, scope = setup_scope(api)
    activate = api.client.post(
        api.path(f"/computation-scopes/{scope['scope_id']}/activate"),
        json={"expected_version": 1, "approval_mode": "reviewer"},
    )
    assert activate.status_code == 200, activate.text
    plan(api, root, scope)
    assert api.transport.entered.wait(2)
    return copy.deepcopy(api.transport.requests[0])


def _configured(*, key: str = "offline-test-api-key") -> AiModeConfig:
    return AiModeConfig(
        enabled=True,
        llm_provider="openai",
        llm_base_url="https://llm.fixture.invalid/v1",
        llm_api_key=key,
        llm_model="offline-reviewer",
        llm_max_retries=4,  # reviewer must override this to zero
        llm_timeout_seconds=30,
    )


def test_real_adapter_uses_one_isolated_completion_and_signs_exact_decision(review_api):
    payload = _owner_payload(review_api)
    sent = []

    def respond(request: httpx.Request):
        sent.append(request)
        body = json.loads(request.content)
        assert request.url == "https://llm.fixture.invalid/v1/chat/completions"
        assert body["model"] == "offline-reviewer"
        assert "tools" not in body and "tool_choice" not in body
        assert [message["role"] for message in body["messages"]] == ["system", "user"]
        assert "nonce" not in json.dumps(body["messages"], ensure_ascii=False)
        assert SECRET not in json.dumps(body["messages"], ensure_ascii=False)
        return httpx.Response(200, json={
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(APPROVE)}}],
            "usage": {"prompt_tokens": 8, "completion_tokens": 8},
        })

    http = httpx.Client(transport=httpx.MockTransport(respond))
    answer = review_request(payload, secret=SECRET, settings_loader=_configured, http=http)
    assert len(sent) == 1
    assert answer["decision"] == APPROVE
    signed = {key: answer[key] for key in ("protocol_version", "challenge", "decision")}
    assert hmac.compare_digest(answer["signature"], hmac.new(SECRET.encode(), canonical(signed), hashlib.sha256).hexdigest())


def test_missing_key_never_falls_to_fake_or_calls_provider(review_api):
    payload = _owner_payload(review_api)
    calls = []
    http = httpx.Client(transport=httpx.MockTransport(lambda request: calls.append(request) or httpx.Response(500)))
    with pytest.raises(ReviewError) as raised:
        review_request(payload, secret=SECRET, settings_loader=lambda: _configured(key=""), http=http)
    assert raised.value.code == "REVIEWER_MODEL_UNAVAILABLE"
    assert calls == []
    http.close()


@pytest.mark.parametrize("response", [
    {"choices": [{"finish_reason": "length", "message": {"content": json.dumps(APPROVE)}}]},
    {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(APPROVE), "tool_calls": [{"function": {"name": "approve"}}]}}]},
    {"choices": [{"finish_reason": "stop", "message": {"content": '{"decision":"approve","decision":"reject","reason":"x","checks":{}}'}}]},
])
def test_truncated_tool_call_or_duplicate_key_cannot_produce_signed_approval(review_api, response):
    payload = _owner_payload(review_api)
    http = httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=response)))
    with pytest.raises(ReviewError) as raised:
        review_request(payload, secret=SECRET, settings_loader=_configured, http=http)
    assert raised.value.code == "REVIEWER_FORMAT"


def test_internal_route_rejects_missing_service_credential_before_model(monkeypatch, tmp_path):
    monkeypatch.setenv("VASP_AI_HOME", str(tmp_path / "ai-home"))
    monkeypatch.setenv("ENABLE_AI_MODE", "false")
    monkeypatch.setenv("VASP_REVIEWER_ENABLED", "true")
    monkeypatch.setenv("VASP_REVIEWER_SHARED_SECRET", SECRET)
    with TestClient(create_ai_mode_app()) as client:
        response = client.post("/ai/internal/reviewer/review", json={})
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "REVIEWER_FORBIDDEN"
        openapi = client.get("/ai/v1/openapi.json")
        assert "/ai/internal/reviewer/review" not in openapi.json()["paths"]
