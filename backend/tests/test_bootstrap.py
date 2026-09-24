from fastapi.testclient import TestClient

from app.api.v1.deps import settings
from app.main import app


def test_bootstrap_reflects_effective_backend_configuration(monkeypatch):
    monkeypatch.setattr(settings.feature_flags, "local_fake_hpc", False)
    monkeypatch.setattr(settings.feature_flags, "band_feature", True)
    monkeypatch.setattr(settings, "max_upload_bytes", 123 * 1024 * 1024)

    response = TestClient(app).get("/api/v1/bootstrap")

    assert response.status_code == 200
    flags = response.json()
    assert flags["ENABLE_FAKE_HPC"] is False
    assert flags["ENABLE_BAND_WORKFLOW"] is True
    assert flags["MAX_UPLOAD_SIZE_MB"] == 123
    assert flags["ENABLE_LLM"] == settings.feature_flags.llm_enabled
    assert flags["ENABLE_HPC_BRIDGE"] is False


def test_bootstrap_does_not_advertise_mock_only_hpc_when_config_default_is_true(monkeypatch):
    monkeypatch.setattr(settings.feature_flags, "local_fake_hpc", True)

    response = TestClient(app).get("/api/v1/bootstrap")

    assert response.status_code == 200
    assert response.json()["ENABLE_FAKE_HPC"] is False
