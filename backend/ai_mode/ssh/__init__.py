"""超算连接层（M6）——ssh 子包：凭据 + 连接管理 + 执行/传输原语。

对外承诺：
- 密码只经凭据管理器，进出本包边界即脱敏；connect 的 password 参数由
  run/switch 传入,manager 本身不把明文密码存为属性。
- switch 一次一个账号（设置页切换语义）。
- 所有错误统一 SSHError 子类,便于上层按场景捕获。
"""

from .credentials import *
from .connection import SSHManager
from . import errors

__all__ = [
    "SSHManager",
    "CredentialStore",
    "KeyringCredentialStore",
    "MemoryCredentialStore",
    "SERVICE_NAME",
    "account_key",
    "SSHCredentials",
    "errors",
]