from __future__ import annotations

import json
import threading
from pathlib import Path

from ..core.config import LlmConfig

_lock = threading.Lock()
_active: 'LlmConfig | None' = None


def get_active() -> 'LlmConfig | None':
    with _lock:
        return _active


def set_active(cfg: 'LlmConfig | None') -> None:
    global _active
    with _lock:
        _active = cfg


def resolve(env: LlmConfig) -> LlmConfig:
    '''运行期覆盖优先；否则回退环境配置。僅在运行期配置可用时接管。'''
    with _lock:
        if _active is not None and _active.usable:
            return _active
    return env


def load(path: Path) -> None:
    '''启动时读取持久化配置到运行期（不存在/损坏则忽略，不阻塞启动）。'''
    global _active
    try:
        if not path.is_file():
            return
        data = json.loads(path.read_text(encoding='utf-8'))
        cfg = LlmConfig(
            enabled=bool(data.get('enabled', True)),
            base_url=str(data.get('base_url') or ''),
            api_key=str(data.get('api_key') or ''),
            model=str(data.get('model') or ''),
            timeout_seconds=float(data.get('timeout_seconds', 30.0)),
            max_retries=int(data.get('max_retries', 1)),
            max_tokens=int(data.get('max_tokens', 1024)),
            temperature=float(data.get('temperature', 0.2)),
        )
        if cfg.usable:  # 只有合法（含 key）配置才接管
            with _lock:
                _active = cfg
    except Exception:  # noqa: BLE001 - 损坏配置不阻塞启动
        pass


def save(path: Path, cfg: LlmConfig) -> None:
    '''持久化运行期配置到 data 目录（单用户本地部署，重启仍生效）。'''
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        'enabled': cfg.enabled,
        'base_url': cfg.base_url,
        'api_key': cfg.api_key,
        'model': cfg.model,
        'timeout_seconds': cfg.timeout_seconds,
        'max_retries': cfg.max_retries,
        'max_tokens': cfg.max_tokens,
        'temperature': cfg.temperature,
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8')


def clear_disk(path: Path) -> None:
    '''删除持久化配置文件（恢复环境默认）。'''
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass