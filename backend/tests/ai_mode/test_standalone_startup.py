"""A clean subprocess must import the AI service without pytest's sys.path."""

import os
from pathlib import Path
import subprocess
import sys


def test_ai_server_imports_from_backend_without_pythonpath(tmp_path):
    backend = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env["VASP_AI_HOME"] = str(tmp_path / "isolated-ai")
    env["ENABLE_AI_MODE"] = "false"
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-B", "-c",
         "import ai_mode.server; print('STANDALONE_IMPORT_OK')"],
        cwd=backend, env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "STANDALONE_IMPORT_OK" in result.stdout
