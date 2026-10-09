"""Shared interpreter policy; loading never installs or probes anything."""
import json
from pathlib import Path
import platform
import struct
import sys
import sysconfig


def load_policy():
    policy = json.loads(Path(__file__).with_name('python-support.json').read_text(encoding='utf-8'))
    if policy['schema'] != 1:
        raise ValueError('unsupported policy schema')
    if (policy['platform'], policy['architecture'], policy['implementation']) != ('win32', 'x64', 'CPython'):
        raise ValueError('unsupported policy platform')
    entries = policy['versions']
    if not (0 < len(entries) <= 16 and len({e['version'] for e in entries}) == len(entries)):
        raise ValueError('invalid policy entries')
    if not any(e['status'] == 'supported' for e in entries):
        raise ValueError('no supported runtime')
    for entry in entries:
        parts = entry['version'].split('.')
        if not (len(parts) == 2 and parts[0] == '3' and parts[1].isascii() and parts[1].isdigit() and str(int(parts[1])) == parts[1]):
            raise ValueError('invalid Python version')
        if entry['status'] not in ('supported', 'blocked'):
            raise ValueError('invalid support status')
        if entry['status'] == 'blocked' and not entry.get('reason'):
            raise ValueError('missing block reason')
    return policy


def compatibility():
    try:
        policy = load_policy()
    except (OSError, ValueError, KeyError, TypeError, AttributeError, AssertionError):
        return False, '安装目录缺少或损坏 launcher/python-support.json，请恢复完整应用目录。'
    version = f'{sys.version_info[0]}.{sys.version_info[1]}'
    entry = next((e for e in policy['versions'] if e['version'] == version), None)
    supported = ' / '.join(sorted(e['version'] for e in policy['versions'] if e['status'] == 'supported'))
    if not entry or entry['status'] != 'supported':
        return False, f'发现 Python {version}，' + (entry['reason'] if entry else f'此版本尚未验证；当前支持 {supported}。')
    if (sys.platform != 'win32' or platform.python_implementation() != 'CPython'
            or struct.calcsize('P') != 8 or platform.machine().lower() not in {'amd64', 'x86_64'}):
        return False, f'自动准备需要 Windows x64 CPython {supported} 标准版本。'
    if sysconfig.get_config_var('Py_GIL_DISABLED') or sys.version_info.releaselevel != 'final':
        return False, f'发现 Python {version}，自由线程或预发布版本尚未验证。'
    return True, f'Python {version} x64'
