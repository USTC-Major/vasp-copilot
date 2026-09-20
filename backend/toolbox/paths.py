"""智能模式本地数据目录布局（~/.vasp-ai）。

所有私人信息（MP key、LLM 密钥、SSH 密码）只存本机，不进项目文件、不上传。
可用 VASP_AI_HOME 覆盖（测试 / 多实例）；生产默认 ~/.vasp-ai。
"""

from __future__ import annotations

import os
from pathlib import Path

HOME_ENV = "VASP_AI_HOME"

_DEFAULT = ".vasp-ai"


def home_dir() -> Path:
    override = os.environ.get(HOME_ENV)
    base = Path(override).expanduser() if override else (Path.home() / _DEFAULT)
    return base.expanduser().resolve()


def sessions_dir() -> Path:
    return home_dir() / "sessions"


def skills_dir() -> Path:
    return home_dir() / "skills"


def logs_dir() -> Path:
    return home_dir() / "logs"


def config_path() -> Path:
    return home_dir() / "config.json"