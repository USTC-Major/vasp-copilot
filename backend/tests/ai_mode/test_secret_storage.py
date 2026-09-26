"""密钥存储回归：LLM / MP 密钥进系统凭据管理器，配置文件不留明文且兼容旧值。"""
import json

import pytest

from ai_mode.config import load_settings, save_settings
from ai_mode.settings.global_api import secret_status, update_secret
from backend.toolbox import secrets
from backend.toolbox.config import ExecutionConfig
from backend.toolbox.config import load_settings as toolbox_load
from backend.toolbox.config import save_settings as toolbox_save


class FailingBackend:
    """模拟凭据管理器不可用（无桌面/CI）：写入必须失败且不吞掉用户密钥。"""

    def get(self, account):
        raise RuntimeError("credential store unavailable")

    def set(self, account, value):
        raise RuntimeError("credential store unavailable")

    def delete(self, account):
        raise RuntimeError("credential store unavailable")


def test_save_never_writes_plaintext_secret(tmp_path):
    path = tmp_path / "config.json"
    cfg = load_settings()
    save_settings(cfg.model_copy(update={"llm_api_key": "sk-plain"}), config_path=path)
    text = path.read_text(encoding="utf-8")
    assert "sk-plain" not in text
    assert json.loads(text)["llm_api_key"] == ""
    assert secrets.get_secret("llm_api_key") == "sk-plain"


def test_store_wins_over_legacy_file_value(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"llm_api_key": "legacy-file-value"}), encoding="utf-8")
    secrets.set_secret("llm_api_key", "from-credential-store")
    cfg = load_settings(config_path=path)
    assert cfg.llm_api_key == "from-credential-store"


def test_legacy_file_value_still_readable_when_store_empty(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"llm_api_key": "legacy-file-value"}), encoding="utf-8")
    assert load_settings(config_path=path).llm_api_key == "legacy-file-value"


def test_environment_still_beats_credential_store(tmp_path, monkeypatch):
    secrets.set_secret("llm_api_key", "from-credential-store")
    monkeypatch.setenv("AI_MODE_LLM_API_KEY", "from-environment")
    cfg = load_settings(config_path=tmp_path / "config.json")
    assert cfg.llm_api_key == "from-environment"
    assert secret_status(cfg)["llm"] == {
        "configured": True, "source": "environment", "manageable": False}


def test_secret_status_reports_credential_store(tmp_path):
    secrets.set_secret("mp_api_key", "mp-in-store")
    cfg = load_settings(config_path=tmp_path / "config.json")
    assert secret_status(cfg)["mp"] == {
        "configured": True, "source": "credential_store", "manageable": True}


def test_clear_secret_removes_it_from_store(tmp_path):
    secrets.set_secret("llm_api_key", "to-be-cleared")
    cfg = load_settings(config_path=tmp_path / "config.json")
    cleared = update_secret(cfg, "llm", "clear")
    assert cleared.llm_api_key == ""
    assert secrets.get_secret("llm_api_key") is None


def test_fallback_keeps_plaintext_when_store_unavailable(tmp_path):
    """凭据后端不可用时宁可保留原值，也不能把密钥弄丢。"""
    secrets.configure_backend(FailingBackend())
    try:
        path = tmp_path / "config.json"
        cfg = load_settings()
        save_settings(cfg.model_copy(update={"llm_api_key": "must-survive"}), config_path=path)
        assert json.loads(path.read_text(encoding="utf-8"))["llm_api_key"] == "must-survive"
    finally:
        secrets.configure_backend(secrets.MemoryBackend())


def test_toolbox_mp_key_migrates_to_store(tmp_path):
    path = tmp_path / "toolbox_config.json"
    toolbox_save(ExecutionConfig(mp_api_key="mp-plain"), config_path=path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["mp_api_key"] == ""
    assert "mp-plain" not in path.read_text(encoding="utf-8")
    assert toolbox_load(config_path=path).mp_api_key == "mp-plain"


def test_toolbox_env_mp_key_is_not_persisted(tmp_path, monkeypatch):
    monkeypatch.setenv("TOOLBOX_MP_API_KEY", "mp-from-env")
    path = tmp_path / "toolbox_config.json"
    toolbox_save(ExecutionConfig(mp_api_key="mp-from-env"), config_path=path)
    assert "mp-from-env" not in path.read_text(encoding="utf-8")
