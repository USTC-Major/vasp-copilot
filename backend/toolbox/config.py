"""Execution settings independent of AI flags and model configuration."""
from __future__ import annotations
import json
import os
from pathlib import Path
from typing import Literal, Mapping
from pydantic import BaseModel, Field
from . import paths
from .secrets import get_secret, secret_value_for_file

class ExecutionConfig(BaseModel):
    data_dir: Path = Field(default_factory=paths.home_dir)
    max_jobs: int = 20
    poll_interval_seconds: int = 60
    billing_estimate_enabled: bool = False
    #: 用户显式授权的免批范围（全局执行设置，默认空＝逐项确认）。
    #: 仅限下面 AUTO_APPROVE_KINDS 里的机械操作；科学输入/结构导入/脚本生成/提交永不免批。
    auto_approve_kinds: list[str] = Field(default_factory=list)
    #: POTCAR：允许按 vaspkit 自己的默认选择在超算作业目录里生成（默认关）。
    #: 打开时前端要弹风险提示与免责声明；每次生成仍单独弹卡，永不进入免批范围。
    allow_potcar_assembly: bool = False
    #: 提交脚本模板：超算上的绝对路径（一份通吃），以及是否允许 AI 把它复制进作业目录。
    #: 复制是逐字节搬运用户自己的文件；AI 永远不能修改或生成脚本内容。
    submit_script_template: str = ''
    allow_script_deploy: bool = False
    ssh_name: str = ''
    ssh_host: str = ''
    ssh_port: int = 22
    ssh_username: str = ''
    ssh_known_hosts_path: str = ''
    ssh_identity_file: str = ''
    scheduler_backend: Literal['slurm', 'paracloud'] = 'slurm'
    mp_api_key: str = ''


#: 允许免批的机械操作（本地复制已登记输入 / 确定性生成 KPOINTS / 上传已登记文件到超算）。
#: 不含提交计算、结构导入、脚本生成与任何科学参数修改；上传只是把用户已登记的文件
#: 放进该任务自己选定的超算工作区，每个文件仍单独留执行回执。
AUTO_APPROVE_KINDS = ('copy_inputs', 'generate_kpoints', 'hpc_upload')


def normalize_auto_approve_kinds(value: object) -> list[str]:
    """只保留白名单内的类型并按固定顺序去重；非法输入视为未开启。"""
    if not isinstance(value, (list, tuple)):
        return []
    chosen = {str(item) for item in value}
    return [kind for kind in AUTO_APPROVE_KINDS if kind in chosen]


def normalize_submit_script_template(value: object) -> str:
    """脚本模板只接受「超算绝对路径 + .sh 结尾」；其余一律视为未配置。"""
    text = str(value or '').strip().strip('"').strip("'")
    if not text or not text.startswith('/') or text.endswith('/'):
        return ''
    if '\\' in text or any(ch in text for ch in '\r\n\t'):
        return ''
    if any(segment in {'..', ''} for segment in text.split('/')[1:]):
        return ''
    name = text.rsplit('/', 1)[-1]
    if not name or name.startswith('.') or not name.lower().endswith('.sh'):
        return ''
    return text


def as_bool(value: object) -> bool:
    """真值解析：bool 原样，字符串按常见写法；读不懂一律 False（安全默认）。"""
    if isinstance(value, bool):
        return value
    return str(value or '').strip().lower() in {'1', 'true', 'yes', 'on'}


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
    # 密钥优先级：环境变量 > 系统凭据管理器 > 本地配置文件（旧值仍能读，便于平滑迁移）。
    if not (env.get('TOOLBOX_MP_API_KEY') or env.get('AI_MODE_MP_API_KEY')):
        stored = get_secret('mp_api_key')
        if stored:
            data['mp_api_key'] = stored
    data['data_dir'] = root
    # 免批范围只认白名单；文件里写了未知值也不会生效（退回逐项确认）。
    data['auto_approve_kinds'] = normalize_auto_approve_kinds(data.get('auto_approve_kinds'))
    # 脚本模板只认绝对 .sh 路径；两个新开关按真值解析，读不懂一律关。
    data['submit_script_template'] = normalize_submit_script_template(
        data.get('submit_script_template'))
    data['allow_potcar_assembly'] = as_bool(data.get('allow_potcar_assembly'))
    data['allow_script_deploy'] = as_bool(data.get('allow_script_deploy'))
    return ExecutionConfig(**data)

def save_settings(config: ExecutionConfig, config_path: Path | None = None):
    target = config_path or paths.home_dir() / 'toolbox_config.json'
    data = config.model_dump(mode='json')
    before = {}
    if target.is_file():
        try:
            before = json.loads(target.read_text(encoding='utf-8'))
        except Exception:  # noqa: BLE001 - 文件损坏时按“无旧值”处理，稍后整体重写
            before = {}
    env_mp = (os.environ.get('TOOLBOX_MP_API_KEY')
              or os.environ.get('AI_MODE_MP_API_KEY') or '')
    # 密钥不落明文：迁移进系统凭据管理器，失败才回退保留原值。
    data['mp_api_key'] = secret_value_for_file(
        'mp_api_key', str(data.get('mp_api_key') or ''),
        str(before.get('mp_api_key') or ''), env_mp)
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
