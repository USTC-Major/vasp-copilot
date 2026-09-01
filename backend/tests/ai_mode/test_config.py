"""配置加载测试：默认值 < 本地私有文件 < 环境变量。"""

import json

from ai_mode import paths
from ai_mode.config import (
    load_settings,
    save_settings,
)


def test_defaults_applied(monkeypatch, tmp_path):
    monkeypatch.setenv("VASP_AI_HOME", str(tmp_path))
    monkeypatch.setenv("ENABLE_AI_MODE", "false")
    cfg = load_settings()
    assert cfg.enabled is False
    assert cfg.max_jobs == 20
    assert cfg.poll_interval_seconds == 60
    assert cfg.llm_model == "gpt-4o-mini"
    assert cfg.data_dir == paths.home_dir()


def test_env_overrides_file_and_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("VASP_AI_HOME", str(tmp_path))
    monkeypatch.setenv("ENABLE_AI_MODE", "true")
    monkeypatch.setenv("AI_MODE_MAX_JOBS", "5")
    monkeypatch.setenv("AI_MODE_LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("AI_MODE_LLM_API_KEY", "sk-test")
    monkeypatch.setenv("AI_MODE_SSH_HOST", "login.hpc")
    cfg = load_settings()
    assert cfg.enabled is True
    assert cfg.max_jobs == 5
    assert cfg.llm_base_url == "http://localhost:11434/v1"
    assert cfg.llm_api_key == "sk-test"
    assert cfg.ssh_host == "login.hpc"


def test_config_file_below_env(monkeypatch, tmp_path):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps({"max_jobs": 3, "llm_model": "m-from-file",
                    "llm_api_key": "kfile", "ssh_host": "h-from-file"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("VASP_AI_HOME", str(tmp_path))
    monkeypatch.setenv("AI_MODE_MAX_JOBS", "7")
    cfg = load_settings(config_path=cfg_path)
    assert cfg.max_jobs == 7
    assert cfg.llm_model == "m-from-file"
    assert cfg.llm_api_key == "kfile"
    assert cfg.ssh_host == "h-from-file"


def test_save_round_trip(monkeypatch, tmp_path):
    monkeypatch.setenv("VASP_AI_HOME", str(tmp_path))
    monkeypatch.setenv("ENABLE_AI_MODE", "true")
    monkeypatch.setenv("AI_MODE_MAX_JOBS", "4")
    monkeypatch.setenv("AI_MODE_LLM_API_KEY", "sekret")
    saved = load_settings()
    cfg_path = tmp_path / "personal" / "config.json"
    save_settings(saved, config_path=cfg_path)
    loaded = load_settings(config_path=cfg_path, env={
        "ENABLE_AI_MODE": "false",
        "AI_MODE_MAX_JOBS": "99",
    })
    # enabled 永远来自开关（本次关）；env 优先于文件；未覆盖项走文件
    assert loaded.enabled is False
    assert loaded.max_jobs == 99
    assert loaded.llm_api_key == "sekret"


def test_enabled_never_persisted(monkeypatch, tmp_path):
    monkeypatch.setenv("VASP_AI_HOME", str(tmp_path))
    monkeypatch.setenv("ENABLE_AI_MODE", "true")
    cfg = load_settings()
    cfg_path = tmp_path / "config.json"
    save_settings(cfg, config_path=cfg_path)
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert "enabled" not in data