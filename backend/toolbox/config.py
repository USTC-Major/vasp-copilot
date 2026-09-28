"""Execution settings independent of AI flags and model configuration."""
from __future__ import annotations
import json
import os
from pathlib import Path
from typing import Literal, Mapping
from pydantic import BaseModel, Field
from . import paths
from .secrets import SecretStorageError, resolve_secret, secret_value_for_file

class ExecutionConfig(BaseModel):
    data_dir: Path = Field(default_factory=paths.home_dir)
    max_jobs: int = 20
    poll_interval_seconds: int = 60
    billing_estimate_enabled: bool = False
    ssh_name: str = ''
    ssh_host: str = ''
    ssh_port: int = 22
    ssh_username: str = ''
    ssh_known_hosts_path: str = ''
    ssh_identity_file: str = ''
    scheduler_backend: Literal['slurm', 'paracloud'] = 'slurm'
    mp_api_key: str = ''


# Historical annotations are compatible; there are no AI settings on this type.
AiModeConfig = ExecutionConfig

MP_ENV_NAMES = ('TOOLBOX_MP_API_KEY', 'AI_MODE_MP_API_KEY', 'MP_API_KEY')


def mp_environment_value(env: Mapping[str, str] | None = None) -> str:
    """The same precedence is used by settings, writes, status and MP requests."""
    env = os.environ if env is None else env
    return next((value for name in MP_ENV_NAMES if (value := env.get(name))), '')


def resolve_mp_api_key(legacy_value: str = '', *, env: Mapping[str, str] | None = None) -> str:
    return mp_environment_value(env) or resolve_secret('mp_api_key', legacy_value)


def load_mp_api_key(*, env: Mapping[str, str] | None = None, config_path: Path | None = None) -> str:
    """Read only the owner's MP field without depending on unrelated SSH settings."""
    root = Path(config_path).parent if config_path is not None else paths.home_dir()
    target = config_path or root / 'toolbox_config.json'
    source = target if target.exists() else root / 'config.json'
    legacy_value = ''
    if source.is_file():
        raw = json.loads(source.read_text(encoding='utf-8'))
        if not isinstance(raw, dict):
            raise ValueError('Invalid execution configuration; original file preserved')
        legacy_value = str(raw.get('mp_api_key') or '')
    return resolve_mp_api_key(legacy_value, env=env)

def load_settings(*, env: Mapping[str, str] | None = None, config_path: Path | None = None):
    env = os.environ if env is None else env
    root = Path(config_path).parent if config_path is not None else paths.home_dir()
    target = config_path or root / 'toolbox_config.json'
    source = target if target.exists() else root / 'config.json'
    data = {}
    if source.is_file():
        raw = json.loads(source.read_text(encoding='utf-8'))
        if not isinstance(raw, dict):
            raise ValueError('Invalid execution configuration; original file preserved')
        data = {key: value for key, value in raw.items() if key in ExecutionConfig.model_fields}
    for key in ExecutionConfig.model_fields:
        if key in {'data_dir', 'mp_api_key'}:
            continue
        value = env.get('TOOLBOX_' + key.upper(), env.get('AI_MODE_' + key.upper()))
        if value is not None and value != '':
            data[key] = value
    # 密钥优先级：环境变量 > 系统凭据管理器 > 本地配置文件（旧值仍能读，便于平滑迁移）。
    data['mp_api_key'] = resolve_mp_api_key(str(data.get('mp_api_key') or ''), env=env)
    data['data_dir'] = root
    return ExecutionConfig(**data)

def save_settings(config: ExecutionConfig, config_path: Path | None = None):
    target = config_path or paths.home_dir() / 'toolbox_config.json'
    data = config.model_dump(mode='json')
    before = {}
    source = target if target.exists() else target.parent / 'config.json'
    if source.is_file():
        before = json.loads(source.read_text(encoding='utf-8'))
        if not isinstance(before, dict):
            raise ValueError('Invalid execution configuration; original file preserved')
    env_mp = mp_environment_value()
    # 密钥迁移进系统凭据管理器；失败抛错误，尚未写入的原文件保持原样。
    data['mp_api_key'] = secret_value_for_file(
        'mp_api_key', str(data.get('mp_api_key') or ''),
        str(before.get('mp_api_key') or ''), env_mp)
    target.parent.mkdir(parents=True, exist_ok=True)
    from .storage import atomic_json
    try:
        atomic_json(target, data)
        legacy = target.parent / 'config.json'
        if target.name == 'toolbox_config.json' and legacy.is_file() and data['mp_api_key'] == '':
            scrub_legacy_secret(legacy, 'mp_api_key')
    except (OSError, ValueError) as exc:
        raise SecretStorageError('配置写入或旧密钥清理失败；凭据状态可能已改变，请重新查询') from exc


def scrub_legacy_secret(path: Path, field: str) -> None:
    """Remove only a migrated/explicitly managed secret, preserving legacy preferences."""
    raw = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(raw, dict):
        raise ValueError('Invalid legacy configuration; original file preserved')
    if raw.get(field):
        from .storage import atomic_json
        atomic_json(path, raw | {field: ''})

def execution_mode(hpc=None, *, explicit: str | None = None) -> str:
    declared = explicit if explicit is not None else getattr(hpc, 'execution_mode', None)
    requested = str(declared or 'None').strip().title()
    if requested not in {'Fake', 'Real', 'None'}:
        raise ValueError('execution mode must be Fake, Real, or None')
    if hpc is None:
        if requested != 'None':
            raise ValueError('an execution backend is required')
        return 'None'
    from .ssh.connection import SSHManager
    if isinstance(hpc, SSHManager):
        if requested not in {'None', 'Real'}:
            raise ValueError('SSHManager execution mode is always Real')
        return 'Real'
    if requested == 'Fake':
        return 'Fake'
    raise ValueError('non-SSH adapters must explicitly declare Fake mode')
