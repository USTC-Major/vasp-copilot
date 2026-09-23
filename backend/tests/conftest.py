"""All local verification uses a private home, never the user's HPC/configuration."""
import os
import sys
from pathlib import Path
import pytest

BACKEND = Path(__file__).resolve().parents[1]
for path in (BACKEND.parent, BACKEND):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

@pytest.fixture(autouse=True)
def isolated_runtime_home(tmp_path, monkeypatch):
    monkeypatch.setenv('VASP_AI_HOME', str(tmp_path / 'runtime-home'))
    for key in list(os.environ):
        if key.startswith(('AI_MODE_SSH_', 'TOOLBOX_SSH_', 'AI_MODE_LLM_', 'TOOLBOX_MP_', 'AI_MODE_MP_', 'VASP_REVIEWER_')):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)

@pytest.fixture(autouse=True)
def isolated_toolbox_http_transport(tmp_path, monkeypatch):
    """No implicit client in any test may reach a user's listening 8000 service."""
    from contextlib import ExitStack
    from backend.toolbox.client import ToolboxClient
    original = ToolboxClient.__init__
    stack = ExitStack()
    transport = None

    def initialize(self, *, client=None, url=None):
        nonlocal transport
        if client is None and url is None:
            if transport is None:
                from fastapi.testclient import TestClient
                from backend.toolbox.api import create_toolbox_app
                transport = stack.enter_context(TestClient(create_toolbox_app(
                    root=tmp_path / 'isolated-http-owner', monitor_enabled=False)))
            client = transport
        original(self, client=client, url=url)

    monkeypatch.setattr(ToolboxClient, '__init__', initialize)
    try:
        yield
    finally:
        stack.close()
