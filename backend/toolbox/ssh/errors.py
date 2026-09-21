"""超算连接层错误（M6）。"""

from __future__ import annotations


class SSHError(Exception):
    """SSH 层通用异常基类。"""


class SSHConfigError(SSHError):
    """配置缺失/非法：如未配置账号（name/host/username 为空）。"""


class SSHAuthError(SSHError):
    """认证失败：密码错误、用户不存在、拒绝权限。"""


class SSHConnectError(SSHError):
    """无法建立连接：网络不可达、超时、主机拒绝。"""


class SSHHostKeyError(SSHConnectError):
    """Host-key trust failed before authentication."""


class SSHHostKeyUnknownError(SSHHostKeyError):
    """The server is absent from the configured known_hosts sources."""


class SSHHostKeyMismatchError(SSHHostKeyError):
    """The server key differs from the trusted known_hosts entry."""


class SSHKnownHostsError(SSHHostKeyError):
    """The configured known_hosts file cannot be loaded safely."""


class SSHUnavailableError(SSHError):
    """SSH 未配置或不可用——对应「整体瘫痪/等待恢复」提示。"""


class SSHExecuteError(SSHError):
    """命令执行失败（连接断开、超时、超长输出等）。"""


class SSHSFTPError(SSHError):
    """文件传输失败（本地/远端读写失败、传输中断）。"""
