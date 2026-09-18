"""MP connection probes use current pagination without real credentials/network."""

import json

import httpx
import pytest

from ai_mode.config import AiModeConfig
from ai_mode.settings.global_api import check_connection


@pytest.mark.parametrize("status", [200, 401, 403, 400, 429, 500])
def test_mp_probe_uses_current_pagination_and_preserves_errors(monkeypatch, status):
    key = "unit-test-mp-key"
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(status, json={"data": [{"material_id": "mp-149"}]})

    monkeypatch.setattr(httpx, "get", fake_get)
    result = check_connection("mp", AiModeConfig(mp_api_key=key))

    assert len(calls) == 1
    url, kwargs = calls[0]
    assert url == "https://api.materialsproject.org/materials/summary/"
    assert kwargs["params"] == {"_limit": 1}
    assert "limit" not in kwargs["params"]
    assert "skip" not in kwargs["params"]
    assert kwargs["headers"]["X-API-KEY"] == key
    assert kwargs["timeout"] == 10.0
    assert kwargs["follow_redirects"] is True
    assert result["provider"] == "mp"
    assert result["ok"] is (status == 200)
    if status == 200:
        assert "返回 1 条" in result["message"]
    elif status in (401, 403):
        assert "key 被拒绝" in result["message"]
    else:
        assert f"HTTP {status}" in result["message"]
        assert "key 被拒绝" not in result["message"]
    assert key not in json.dumps(result)


def test_mp_probe_network_error_does_not_leak_credentials(monkeypatch):
    key = "unit-test-mp-key"

    def fail_get(*args, **kwargs):
        raise httpx.ConnectError(f"sensitive request: {key}")

    monkeypatch.setattr(httpx, "get", fail_get)
    result = check_connection("mp", AiModeConfig(mp_api_key=key))
    assert result["ok"] is False
    assert "ConnectError" in result["message"]
    assert key not in json.dumps(result)


def test_mp_probe_without_key_makes_no_request(monkeypatch):
    def unexpected_get(*args, **kwargs):
        pytest.fail("An unconfigured probe must not contact MP")

    monkeypatch.setattr(httpx, "get", unexpected_get)
    result = check_connection("mp", AiModeConfig(mp_api_key=""))
    assert result["ok"] is False
    assert "未配置" in result["message"]
