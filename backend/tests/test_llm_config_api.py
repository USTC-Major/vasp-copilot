from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.v1.deps import settings
from app.llm import get_explainer, reset_explainer, runtime
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    # 把 llm_config 持久化路径隔离到临时目录，避免污染真实 data/
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    yield
    reset_explainer()
    runtime.set_active(None)


def _cfg_path() -> str:
    import pathlib
    return str(pathlib.Path(settings.data_dir) / "llm_config.json")


def test_get_config_env_defaults():
    r = client.get("/api/v1/llm/config")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["source"] == "env"
    assert data["base_url"]
    assert data["model"]
    assert data["api_key_set"] is False


def test_save_config_overrides_to_runtime():
    r = client.post("/api/v1/llm/config", json={
        "base_url": "http://127.0.0.1:8001/v1",
        "api_key": "sk-test-abc",
        "model": "my-local-model",
        "timeout_seconds": 120,
    })
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["source"] == "runtime"
    assert data["base_url"] == "http://127.0.0.1:8001/v1"
    assert data["model"] == "my-local-model"
    assert data["api_key_set"] is True
    assert "****" in data["api_key_masked"]
    assert runtime.get_active() is not None
    assert data["usable"] is True


def test_save_blank_key_keeps_existing():
    client.post("/api/v1/llm/config", json={
        "base_url": "http://127.0.0.1:8001/v1", "api_key": "sk-keep", "model": "m",
    })
    r = client.post("/api/v1/llm/config", json={
        "base_url": "http://127.0.0.1:8001/v1", "api_key": "", "model": "m2",
    })
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["api_key_set"] is True
    assert data["model"] == "m2"
    active = runtime.get_active()
    assert active is not None and active.api_key == "sk-keep"

def test_saved_config_restored_from_disk(tmp_path):
    client.post("/api/v1/llm/config", json={
        "base_url": "http://127.0.0.1:8001/v1",
        "api_key": "sk-persist",
        "model": "persisted-model",
    })
    assert tmp_path.joinpath("llm_config.json").is_file()
    runtime.set_active(None)
    runtime.load(tmp_path / "llm_config.json")
    cfg = runtime.get_active()
    assert cfg is not None and cfg.model == "persisted-model"


def test_reset_config_back_to_env():
    client.post("/api/v1/llm/config", json={
        "base_url": "http://127.0.0.1:8001/v1",
        "api_key": "sk-test-abc",
        "model": "my-local-model",
    })
    r = client.delete("/api/v1/llm/config")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["source"] == "env"
    assert runtime.get_active() is None


def test_get_explainer_prefers_runtime_cfg():
    client.post("/api/v1/llm/config", json={
        "base_url": "http://127.0.0.1:8001/v1",
        "api_key": "sk-test-abc",
        "model": "my-local-model",
    })
    explainer = get_explainer(settings)
    assert explainer is not None


def test_test_config_missing_key_reports_false():
    r = client.post("/api/v1/llm/config/test", json={
        "base_url": "http://127.0.0.1:8001/v1",
        "api_key": "",
        "model": "m",
    })
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["ok"] is False
    assert "API Key" in data["message"]


def test_test_config_success(monkeypatch):
    class FakeExplainer:
        def __init__(self, cfg):
            self.cfg = cfg

        def test(self) -> str:
            return "pong"

    monkeypatch.setattr("app.api.v1.llm.OpenAiExplainer", FakeExplainer)
    r = client.post("/api/v1/llm/config/test", json={
        "base_url": "http://127.0.0.1:8001/v1",
        "api_key": "sk-test-abc",
        "model": "m",
    })
    assert r.status_code == 200
    assert r.json()["data"]["ok"] is True


def test_test_config_failure(monkeypatch):
    class BoomExplainer:
        def __init__(self, cfg):
            pass

        def test(self) -> str:
            raise RuntimeError("boom")

    monkeypatch.setattr("app.api.v1.llm.OpenAiExplainer", BoomExplainer)
    r = client.post("/api/v1/llm/config/test", json={
        "base_url": "http://127.0.0.1:8001/v1",
        "api_key": "sk-test-abc",
        "model": "m",
    })
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["ok"] is False
    assert "boom" in data["message"]