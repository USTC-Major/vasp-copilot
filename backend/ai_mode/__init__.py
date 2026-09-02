"""智能模式（智能模式）命名空间 —— 完全独立于工具箱。

- 不 import 工具箱（backend.app.*）任何代码。
- 由独立开关 ENABLE_AI_MODE 激活（gate.py）。
- 关闭时本包不被工具箱加载，零影响；显式启动智能模式服务器（server.py）时才进入。
"""

from ai_mode.gate import is_ai_mode_enabled as is_enabled

__version__ = "0.2.0"
VERSION = __version__

__all__ = ["is_enabled", "VERSION"]