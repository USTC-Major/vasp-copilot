"""Independent S1-I-B review fixtures and CI marker registration."""

from __future__ import annotations

import os
import sys

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "posix_critical: real Linux helper subprocess and filesystem behavior; must run in CI",
    )


def linux_critical(test):
    """Mark a real POSIX test; Windows may skip, Linux CI must never do so."""

    return pytest.mark.posix_critical(
        pytest.mark.skipif(
            not sys.platform.startswith("linux"),
            reason="requires a real Linux filesystem; CI evidence must execute this test",
        )(test)
    )


@pytest.fixture
def required_linux_capabilities():
    """Fail, rather than skip, when the declared Linux capability floor is absent."""

    assert sys.platform.startswith("linux")
    assert os.name == "posix"
    assert hasattr(os, "O_NOFOLLOW")
    assert hasattr(os, "O_DIRECTORY")
    assert os.open in os.supports_dir_fd
    assert os.stat in os.supports_dir_fd
    assert os.unlink in os.supports_dir_fd
    return {
        "o_nofollow": os.O_NOFOLLOW,
        "o_directory": os.O_DIRECTORY,
    }
