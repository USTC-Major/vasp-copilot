"""Offline contract tests for MP interpretation and strict search."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError as PydanticValidationError

from app.main import app as toolbox_app
from app.services import materials_project as mp_service
from app.services.materials_project import MaterialsProjectClient
from backend.ai_mode.config import AiModeConfig
from backend.ai_mode.llm.base import CompletionResult
from backend.ai_mode.llm.errors import LLMUnavailableError
from backend.ai_mode.llm.openai_compat import OpenAIClient
from backend.ai_mode import server as ai_server
from backend.ai_mode.llm import factory
from backend.materials_criteria import MaterialCriteria, formula_elements


@pytest.mark.parametrize("bad", [
    {}, {"elements": ["Xx"]}, {"formula": "Fe2(O3)"},
    {"band_gap": {"min": 3, "max": 1}},
    {"band_gap": {"min": "1"}}, {"band_gap": {"max": True}},
    {"band_gap": {"min": float("inf")}},
    {"elements": ["Fe"], "chemsys": "O-Ni"},
    {"formula": "Fe2O3", "chemsys": "Fe-S"},
    {"is_metal": True, "band_gap": {"min": 1}},
    {"ordering": "FM"}, {"energy_above_hull": {"max": 0.05}},
    {"elements_type": "="},
])
def test_strict_criteria_rejects_invalid_or_unsupported(bad):
    with pytest.raises(PydanticValidationError):
        MaterialCriteria.model_validate(bad)


def test_criteria_keeps_false_and_normalizes_chemsys():
    criteria = MaterialCriteria.model_validate({
        "elements": ["Fe", "O"], "chemsys": "O-Fe",
        "band_gap": {"min": 0, "max": 3}, "is_stable": False,
        "is_metal": False,
    }).to_mp()
    assert criteria == {
        "elements": ["Fe", "O"], "chemsys": "Fe-O",
        "band_gap": {"min": 0.0, "max": 3.0},
        "is_stable": False, "is_metal": False,
    }


def test_formula_detection_is_entire_query_only():
    assert formula_elements("Si") == {"Si"}
    assert formula_elements("NaCl") == {"Na", "Cl"}
    assert formula_elements("1eV") is None
    assert formula_elements("find NaCl") is None
    assert formula_elements("Fe2(O3)") is None


def test_mp_http_params_match_confirmed_criteria(monkeypatch):
    seen = []

    def respond(request: httpx.Request):
        seen.append(request)
        return httpx.Response(200, json={"data": []})

    http = httpx.Client(transport=httpx.MockTransport(respond))
    monkeypatch.setenv("TOOLBOX_MP_API_KEY", "test-mp")
    monkeypatch.setattr(mp_service, "MaterialsProjectClient", lambda api_key, base_url, timeout_seconds:
                        MaterialsProjectClient(api_key, base_url, timeout_seconds, client=http))
    with TestClient(toolbox_app) as client:
        client.app.state.toolbox.monitor.stop()
        body = {"query": "Fe2O3, only Fe and O, 1-3 eV, stable, not metal",
                "criteria": {"formula": "Fe2O3", "elements": ["Fe", "O"],
                             "chemsys": "O-Fe", "band_gap": {"min": 1, "max": 3},
                             "is_stable": True, "is_metal": False},
                "confirmed": True, "limit": 7}
        response = client.post("/api/v1/materials/search", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["criteria"]["chemsys"] == "Fe-O"
    assert len(seen) == 1
    params = seen[0].url.params
    assert seen[0].url.path == "/materials/summary/"
    assert seen[0].headers["X-API-KEY"] == "test-mp"
    assert {name: params[name] for name in ("formula", "elements", "chemsys", "band_gap_min", "band_gap_max", "is_stable", "is_metal", "_limit")} == {
        "formula": "Fe2O3", "elements": "Fe,O", "chemsys": "Fe-O", "band_gap_min": "1.0", "band_gap_max": "3.0",
        "is_stable": "true", "is_metal": "false", "_limit": "7",
    }
    assert "band_gap" not in params and "ordering" not in params


def test_search_rejects_uninterpreted_unconfirmed_and_unresolved(monkeypatch):
    with TestClient(toolbox_app) as client:
        client.app.state.toolbox.monitor.stop()
        cases = [
            ({"query": "1 eV 带隙的 Fe 氧化物"}, "MP_INTERPRETATION_REQUIRED"),
            ({"query": "not metal", "criteria": {"is_metal": False}}, "MP_CONFIRMATION_REQUIRED"),
            ({"query": "iron magnetic", "criteria": {"elements": ["Fe"]},
              "confirmed": True, "unresolved_conditions": ["magnetic"]}, "MP_UNRESOLVED_CONDITIONS"),
        ]
        for body, code in cases:
            result = client.post("/api/v1/materials/search", json=body)
            assert result.status_code == 422
            assert result.json()["error"]["code"] == code


class ModelClient:
    def __init__(self, result):
        self.result = result
        self.messages = None
        self.closed = False

    def complete(self, messages, **kwargs):
        self.messages = messages
        if isinstance(self.result, Exception):
            raise self.result
        return CompletionResult(text=self.result)

    def close(self):
        self.closed = True


@pytest.fixture
def ai_client(monkeypatch):
    monkeypatch.setenv("ENABLE_AI_MODE", "true")
    config = AiModeConfig(enabled=True, llm_provider="openai", llm_base_url="https://fake.invalid/v1",
                          llm_api_key="test-llm", llm_model="test-model")
    monkeypatch.setattr(ai_server, "load_settings", lambda **kwargs: config)
    model = ModelClient("")
    monkeypatch.setattr(factory, "build_client", lambda *args, **kwargs: model)
    return TestClient(ai_server.create_ai_mode_app()), config, model


def test_interpret_chinese_range_and_negation(ai_client):
    client, _, model = ai_client
    model.result = json.dumps({
        "criteria": {"elements": ["Fe", "O"], "band_gap": {"min": 1, "max": 3},
                     "is_stable": True, "is_metal": False},
        "interpreted_conditions": ["至少含 Fe 和 O", "带隙 1–3 eV", "MP 计算稳定", "非金属"],
        "unresolved_conditions": [], "warnings": [],
    })
    result = client.post("/ai/v1/materials/interpret", json={"query": "至少含 Fe、O，带隙 1-3 eV，稳定且非金属"})
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["status"] == "ready_for_confirmation"
    assert data["criteria"]["is_metal"] is False
    assert data["criteria"]["band_gap"] == {"min": 1.0, "max": 3.0}
    assert model.messages[0]["role"] == "system"
    assert model.messages[1]["role"] == "user"
    assert model.closed


def test_interpret_only_system_and_oxide_warning(ai_client):
    client, _, model = ai_client
    model.result = json.dumps({"criteria": {"chemsys": "O-Fe"},
                               "interpreted_conditions": ["only Fe and O"],
                               "unresolved_conditions": [], "warnings": []})
    only = client.post("/ai/v1/materials/interpret", json={"query": "Only Fe and O materials"})
    assert only.status_code == 200
    assert only.json()["criteria"] == {"chemsys": "Fe-O"}
    assert only.json()["status"] == "ready_for_confirmation"
    oxide = client.post("/ai/v1/materials/interpret", json={"query": "Fe oxides"})
    assert oxide.status_code == 200
    assert oxide.json()["status"] == "needs_clarification"
    assert "氧化物分类不能仅由含 O 元素筛选保证" in oxide.json()["unresolved_conditions"]


@pytest.mark.parametrize("query,expected_unresolved", [
    ("找氧化铁材料", "氧化物分类不能仅由含 O 元素筛选保证"),
    ("找二氧化钛", "氧化物分类不能仅由含 O 元素筛选保证"),
    ("Find Fe materials calculated with HSE03", "计算泛函或方法条件不可直接筛选"),
    ("Find Fe materials calculated with HSE06", "计算泛函或方法条件不可直接筛选"),
    ("Find Fe materials calculated with PBEsol", "计算泛函或方法条件不可直接筛选"),
    ("Find Fe materials calculated with PBE+U", "计算泛函或方法条件不可直接筛选"),
])
def test_common_unsupported_phrasings_cannot_be_silently_ready(ai_client, query, expected_unresolved):
    client, _, model = ai_client
    # A model that omitted the unsupported clause must not produce a ready search.
    model.result = json.dumps({"criteria": {"elements": ["Fe"]},
                               "interpreted_conditions": ["contains Fe"],
                               "unresolved_conditions": [], "warnings": []})
    result = client.post("/ai/v1/materials/interpret", json={"query": query})
    assert result.status_code == 200
    assert result.json()["status"] == "needs_clarification"
    assert expected_unresolved in result.json()["unresolved_conditions"]


def test_interpret_unsupported_only_needs_clarification(ai_client):
    client, _, model = ai_client
    model.result = json.dumps({"criteria": None, "interpreted_conditions": [],
                               "unresolved_conditions": ["ferromagnetic ordering"], "warnings": []})
    result = client.post("/ai/v1/materials/interpret", json={"query": "Find experimentally synthesized ferromagnets"})
    assert result.status_code == 200, result.text
    assert result.json()["criteria"] is None
    assert result.json()["status"] == "needs_clarification"
    assert "实验合成或验证条件不可直接筛选" in result.json()["unresolved_conditions"]


@pytest.mark.parametrize("model_result,expected", [
    ("{not-json", "AI_MODE_MATERIALS_INVALID_RESPONSE"),
    (json.dumps({"criteria": {"ordering": "FM"}, "interpreted_conditions": ["magnetic"],
                 "unresolved_conditions": [], "warnings": []}), "AI_MODE_MATERIALS_INVALID_RESPONSE"),
    (LLMUnavailableError("timeout with secret"), "AI_MODE_MATERIALS_LLM_UNAVAILABLE"),
])
def test_interpret_model_failures_are_explicit_and_redacted(ai_client, model_result, expected):
    client, _, model = ai_client
    model.result = model_result
    result = client.post("/ai/v1/materials/interpret", json={"query": "Find Fe compounds"})
    assert result.status_code in (502, 503)
    assert result.json()["error"]["code"] == expected
    assert "secret" not in result.text
    assert model.closed


def test_interpret_disabled_missing_and_fake(ai_client, monkeypatch):
    client, cfg, _ = ai_client
    monkeypatch.setenv("ENABLE_AI_MODE", "false")
    assert client.post("/ai/v1/materials/interpret", json={"query": "Fe"}).json()["error"]["code"] == "AI_MODE_DISABLED"
    monkeypatch.setenv("ENABLE_AI_MODE", "true")
    cfg.llm_api_key = ""
    assert client.post("/ai/v1/materials/interpret", json={"query": "Fe"}).json()["error"]["code"] == "AI_MODE_MATERIALS_LLM_NOT_CONFIGURED"
    cfg.llm_api_key = "test-llm"
    cfg.llm_provider = "fake"
    assert client.post("/ai/v1/materials/interpret", json={"query": "Fe"}).json()["error"]["code"] == "AI_MODE_MATERIALS_FAKE_PROVIDER"


def test_interpret_real_transport_timeout_is_distinct_and_client_closed(ai_client, monkeypatch):
    client, cfg, _ = ai_client
    cfg.llm_max_retries = 0
    http = httpx.Client(transport=httpx.MockTransport(lambda _req: (_ for _ in ()).throw(httpx.ReadTimeout("offline timeout"))))
    model = OpenAIClient(base_url=cfg.llm_base_url, api_key=cfg.llm_api_key,
                         model=cfg.llm_model, max_retries=0, http=http)
    monkeypatch.setattr(factory, "build_client", lambda *args, **kwargs: model)
    response = client.post("/ai/v1/materials/interpret", json={"query": "Find Fe"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "AI_MODE_MATERIALS_LLM_TIMEOUT"
    assert http.is_closed
    assert "offline timeout" not in response.text


def test_client_close_failure_does_not_mask_invalid_response(ai_client):
    client, _, model = ai_client
    model.result = "not json"
    model.close = lambda: (_ for _ in ()).throw(RuntimeError("private close detail"))
    result = client.post("/ai/v1/materials/interpret", json={"query": "Find Fe"})
    assert result.status_code == 502
    assert result.json()["error"]["code"] == "AI_MODE_MATERIALS_INVALID_RESPONSE"
    assert "private close detail" not in result.text
