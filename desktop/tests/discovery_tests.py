"""Real C# discovery/probe checks; test-only files and a scoped temporary registry key."""
from pathlib import Path
import json
import os
import subprocess
import test_support

ROOT = Path(__file__).resolve().parents[2]
OUT = test_support.output('python-discovery')
OUT.mkdir(parents=True)
fixture = OUT/'中文 Python 环境'
fixture.mkdir()
compiler = Path(os.environ['WINDIR'])/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
stub = fixture/'python.exe'
harness = OUT/'DiscoveryTests.exe'
base = [str(compiler), '/nologo', '/utf8output', '/target:exe', '/platform:x64', '/reference:System.Web.Extensions.dll']
subprocess.run(base + ['/out:'+str(stub), str(ROOT/'desktop/tests/DiscoveryProbeStub.cs')], check=True)
subprocess.run(base + ['/out:'+str(harness)] + [str(ROOT/file) for file in
               ('launcher/windows/NativeProcess.cs', 'launcher/windows/LauncherCore.cs', 'desktop/tests/ControllerDiscoveryTests.cs')], check=True)
env = dict(os.environ, VASP_LAUNCHER_STATE_DIR=str(OUT/'state'), PYLAUNCHER_ALLOW_INSTALL='1')
result = subprocess.run([str(harness), str(ROOT), str(OUT), str(stub)], env=env, capture_output=True, timeout=70)
(OUT/'output.log').write_bytes(result.stdout + result.stderr)
print(result.stdout.decode('utf-8', errors='replace'))
assert result.returncode == 0, result.stderr.decode('utf-8', errors='replace')
(OUT/'results.json').write_text(json.dumps({'passed': True, 'checks': result.stdout.count(b'PASS ')}), encoding='utf-8')
