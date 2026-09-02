"""本地化存储骨架：~/.vasp-ai 布局 + 私有配置文件兜底创建。

M1 只负责「目录骨架」存在；会话文件读写与快照规则见 M2。
私人信息本地化不放松：本模块从不写项目目录。
"""

from __future__ import annotations

import logging
from pathlib import Path

from . import paths
from .config import load_settings, save_settings

logger = logging.getLogger("ai_mode")

_LAYOUT_DIRS = ("sessions", "skills", "logs")


def ensure_layout(data_dir: Path | None = None) -> dict[str, Path]:
    """幂等创建布局并兜底写入私有配置文件；返回各目录绝对路径。

    :param data_dir: 布局根目录；默认 paths.home_dir()。
    """
    if data_dir is None:
        data_dir = paths.home_dir()
    data_dir = data_dir.expanduser().resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    dirs = {name: (data_dir / name).resolve() for name in _LAYOUT_DIRS}
    for sub in dirs.values():
        sub.mkdir(parents=True, exist_ok=True)
    cfg = data_dir / "config.json"
    if not cfg.is_file():
        config = load_settings()
        save_settings(config, config_path=cfg)
        logger.info("已初始化智能模式本地配置: %s", cfg)
    return dirs