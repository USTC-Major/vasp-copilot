"""Explicit key authentication stays local and retains strict host trust."""
from unittest.mock import Mock

import pytest

from ai_mode.config import AiModeConfig, load_settings, save_settings
from ai_mode.orchestrator import Orchestrator
from ai_mode.settings.global_api import mask_config, update_from_patch
from ai_mode.ssh.connection import SSHManager
from ai_mode.ssh.errors import SSHAuthError, SSHConnectError, SSHHostKeyUnknownError


def manager_for(identity, client=None, credentials=None):
    client = client or Mock()
    manager = SSHManager(identity_file=str(identity) if identity else None,
                         client_factory=lambda: client, credentials=credentials)
    manager.switch(host="example.invalid", username="demo", port=2222)
    return manager, client


def test_explicit_key_only_no_password_agent_or_discovery(tmp_path):
    identity = tmp_path / "demo_key"
    identity.write_text("TEST FIXTURE NOT A PRIVATE KEY")
    credentials = Mock()
    manager, client = manager_for(identity, credentials=credentials)
    manager.connect()
    kwargs = client.connect.call_args.kwargs
    assert kwargs["key_filename"] == str(identity)
    assert kwargs["password"] is None
    assert kwargs["allow_agent"] is False
    assert kwargs["look_for_keys"] is False
    assert kwargs["port"] == 2222
    credentials.get_password.assert_not_called()
    manager.close()


@pytest.mark.parametrize("kind", ["relative", "missing", "directory"])
def test_invalid_key_path_fails_before_connect(tmp_path, kind):
    identity = {"relative": "relative/key", "missing": tmp_path / "missing",
                "directory": tmp_path}[kind]
    manager, client = manager_for(identity)
    with pytest.raises(SSHAuthError):
        manager.connect()
    client.connect.assert_not_called()


def test_password_path_is_preserved_without_identity():
    credentials = Mock()
    credentials.get_password.return_value = "TEST_PASSWORD"
    manager, client = manager_for(None, credentials=credentials)
    manager.connect()
    assert client.connect.call_args.kwargs["password"] == "TEST_PASSWORD"
    assert "key_filename" not in client.connect.call_args.kwargs


@pytest.mark.parametrize("error", [ValueError("TEST_SECRET"), SSHHostKeyUnknownError("untrusted")])
def test_key_failure_closes_client_without_fallback(tmp_path, error):
    identity = tmp_path / "demo_key"
    identity.write_text("TEST FIXTURE")
    client = Mock()
    client.connect.side_effect = error
    manager, client = manager_for(identity, client)
    with pytest.raises((SSHConnectError, SSHHostKeyUnknownError)) as result:
        manager.connect()
    assert "TEST_SECRET" not in str(result.value)
    assert manager.connected is False
    assert client.connect.call_count == 1
    client.close.assert_called_once()


def test_identity_setting_roundtrip_and_orchestrator(tmp_path):
    path = str(tmp_path / "key")
    cfg = update_from_patch(AiModeConfig(), {
        "ssh_host": "example.invalid", "ssh_username": "demo",
        "ssh_identity_file": path, "ssh_known_hosts_path": str(tmp_path / "hosts"),
    })
    saved = tmp_path / "config.json"
    save_settings(cfg, saved)
    loaded = load_settings(env={}, config_path=saved)
    assert loaded.ssh_identity_file == path
    assert mask_config(loaded)["ssh"]["identity_file"] == path
    assert Orchestrator.from_settings(loaded).hpc.identity_file == path
    overridden = load_settings(env={"AI_MODE_SSH_IDENTITY_FILE": "other"}, config_path=saved)
    assert overridden.ssh_identity_file == "other"


def test_settings_test_passes_identity_and_trust_paths(monkeypatch, tmp_path):
    from backend.toolbox.settings import probe_ssh as _ssh_test
    factory = Mock()
    factory.return_value.test_connection.return_value = (True, "connected")
    monkeypatch.setattr("ai_mode.ssh.connection.SSHManager", factory)
    cfg = AiModeConfig(ssh_host="example.invalid", ssh_username="demo",
                       ssh_identity_file=str(tmp_path / "key"),
                       ssh_known_hosts_path=str(tmp_path / "hosts"))
    assert _ssh_test(cfg)["ok"] is True
    assert factory.call_args.kwargs["identity_file"] == cfg.ssh_identity_file
    assert factory.call_args.kwargs["known_hosts_path"] == cfg.ssh_known_hosts_path
