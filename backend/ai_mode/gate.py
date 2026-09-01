"""独立开关：智能模式是否激活。

开关 = 环境变量 ``ENABLE_AI_MODE``（默认 ``false``，尊重默认关）。
关闭时：工具箱与本包互不影响，本包只是不被加载。
开启时：可启动智能模式服务器（server.py）。
"""

from __future__ import annotations

import os
from typing import Mapping

ENV_NAME = "ENABLE_AI_MODE"
DEFAULT_VALUE = "false"
_TRUTHY = {"1", "true", "on", "yes", "y"}
_FALSY = {"0", "false", "off", "no", "n", ""}


def is_ai_mode_enabled(env: Mapping[str, str] | None = None) -> bool:
    """读取独立开关；非法取值抛 ValueError（宁可显式失败，不静默猜测）。"""
    if env is None:
        env = os.environ
    raw = env.get(ENV_NAME, DEFAULT_VALUE).strip().lower()
    if raw in _TRUTHY:
        return True
    if raw in _FALSY:
        return False
    raise ValueError(
        f"{ENV_NAME} 取值无法识别: {raw!r}（支持 true/false/1/0/on/off 等）"
    )