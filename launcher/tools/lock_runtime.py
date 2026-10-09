"""Resolve binary-only Windows runtime locks with the actual target interpreter.

Developer command, not a user startup action. Run with each target Windows Python:
python launcher/tools/lock_runtime.py --report ABSOLUTE_REPORT_PATH
No packages are installed into the launching interpreter.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import platform
import struct
import sysconfig
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    if (sys.platform != 'win32' or platform.python_implementation() != 'CPython'
            or struct.calcsize('P') != 8 or platform.machine().lower() not in {'amd64', 'x86_64'}
            or sysconfig.get_config_var('Py_GIL_DISABLED')):
        parser.error('Run with the actual standard Windows x64 CPython target, not cross-resolution.')
    report = args.report.resolve()
    report.parent.mkdir(parents=True, exist_ok=True)
    report.unlink(missing_ok=True)
    requirements = ROOT/'backend/requirements-runtime.txt'
    subprocess.run([sys.executable, '-I', '-X', 'utf8', '-m', 'pip', '--isolated',
                    '--disable-pip-version-check', 'install', '--dry-run', '--ignore-installed',
                    '--only-binary=:all:', '--index-url', 'https://pypi.org/simple',
                    '--report', str(report), '-r', str(requirements)], check=True)
    data = json.loads(report.read_text(encoding='utf-8'))
    assert data['environment']['python_full_version'] == platform.python_version()
    rows = []
    for entry in data['install']:
        download = entry['download_info']
        url = urlsplit(download['url'])
        assert url.scheme == 'https' and url.hostname == 'files.pythonhosted.org' and url.path.endswith('.whl')
        digest = download['archive_info']['hashes']['sha256']
        assert re.fullmatch('[a-f0-9]{64}', digest)
        name = re.sub(r'[-_.]+', '-', entry['metadata']['name']).lower()
        rows.append(f"{name}=={entry['metadata']['version']} --hash=sha256:{digest}")
    assert rows and len({r.split('==')[0] for r in rows}) == len(rows)
    tag = f'cp{sys.version_info.major}{sys.version_info.minor}'
    lock = ROOT/f'backend/requirements-win-{tag}-x64.lock'
    header = (f'# Windows x64 CPython {sys.version_info.major}.{sys.version_info.minor}; binary-only runtime dependencies\n'
              '# Generated with launcher/tools/lock_runtime.py on the actual target interpreter.\n'
              '# requirements-runtime.txt SHA256: ' + hashlib.sha256(requirements.read_bytes().replace(b'\r\n', b'\n')).hexdigest() + '\n')
    lock.write_text(header + '\n'.join(sorted(rows)) + '\n', encoding='utf-8')
    print(f'Wrote {lock.name}: {len(rows)} locked distributions')


if __name__ == '__main__':
    main()
