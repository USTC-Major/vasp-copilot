"""智能模式独立开关测试。"""

import pytest

from ai_mode.gate import DEFAULT_VALUE, ENV_NAME, is_ai_mode_enabled


def test_default_disabled(monkeypatch):
    monkeypatch.delenv(ENV_NAME, raising=False)
    assert DEFAULT_VALUE == "false"
    assert is_ai_mode_enabled() is False


@pytest.mark.parametrize("raw", ["1", "true", "on", "yes", " True ", "YES", "y"])
def test_truthy_values(monkeypatch, raw):
    monkeypatch.setenv(ENV_NAME, raw)
    assert is_ai_mode_enabled() is True


@pytest.mark.parametrize("raw", ["0", "false", "off", "no", "NO", ""])
def test_falsy_values(monkeypatch, raw):
    monkeypatch.setenv(ENV_NAME, raw)
    assert is_ai_mode_enabled() is False


def test_invalid_value_raises(monkeypatch):
    monkeypatch.setenv(ENV_NAME, "maybe")
    with pytest.raises(ValueError):
        is_ai_mode_enabled()