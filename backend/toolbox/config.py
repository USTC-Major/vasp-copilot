"""Execution settings independent of AI flags and model configuration."""
from __future__ import annotations
import json
import os
from pathlib import Path
from typing import Literal, Mapping
from pydantic import BaseModel, Field
from . import paths

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
        if key == 'data_dir':
            continue
        value = env.get('TOOLBOX_' + key.upper(), env.get('AI_MODE_' + key.upper()))
        if value is not None and value != '':
            data[key] = value
    data['data_dir'] = root
    return ExecutionConfig(**data)

def save_settings(config: ExecutionConfig, config_path: Path | None = None):
    target = config_path or paths.home_dir() / 'toolbox_config.json'
    data = config.model_dump(mode='json')
    if os.environ.get('TOOLBOX_MP_API_KEY') or os.environ.get('AI_MODE_MP_API_KEY'):
        before = json.loads(target.read_text(encoding='utf-8')) if target.is_file() else {}
        data['mp_api_key'] = before.get('mp_api_key', '')
    target.parent.mkdir(parents=True, exist_ok=True)
    from .storage import atomic_json
    atomic_json(target, data)

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
