"""智能模式命名空间，独立服务，共用确定性输入校验组件。

- 不启动工具箱服务；输入生成与结果核验复用其纯函数模块。
- 由独立开关 ENABLE_AI_MODE 激活（gate.py）。
- 关闭时本包不被工具箱加载，零影响；显式启动智能模式服务器（server.py）时才进入。
"""

import sys
from pathlib import Path

# Standalone ``uvicorn ai_mode.server:app`` starts in backend/, whereas shared
# deterministic modules import as backend.app.*. Resolve only this known source
# layout, as app.main does; do not rely on pytest having imported app.main first.
_repo_root = Path(__file__).resolve().parents[2]
if (_repo_root / "backend" / "app").is_dir() and str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from .gate import is_ai_mode_enabled as is_enabled

__version__ = "0.3.0"
VERSION = __version__

__all__ = ["is_enabled", "VERSION"]
