"""Verify release-version AI readiness with one private stdlib service stack."""
from pathlib import Path
import json
import os
import socket
import subprocess
import sys

import test_support


ROOT = Path(__file__).resolve().parents[2]
OUT = test_support.output('version-health')
OUT.mkdir(parents=True)
HARNESS = test_support.harness('ControllerVersionHealth.exe')
HARNESS.parent.mkdir(parents=True, exist_ok=True)
compiler = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
subprocess.run([str(compiler), '/nologo', '/utf8output', '/target:exe', '/platform:x64',
                '/reference:System.Web.Extensions.dll', '/out:' + str(HARNESS),
                str(ROOT/'launcher/windows/NativeProcess.cs'),
                str(ROOT/'launcher/windows/LauncherCore.cs'),
                str(ROOT/'desktop/tests/ControllerHarness.cs')], check=True)

installation = OUT/'installation'
for name in ('backend/app/main.py', 'backend/ai_mode/server.py', 'frontend/dist/index.html'):
    target = installation/name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('synthetic version-health fixture', encoding='utf-8')
runtime = OUT/'fixture.py'
runtime.write_text(r'''
import argparse,hashlib,json,os,sys
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
p=argparse.ArgumentParser()
for key in ('kind','root','result-file','port','toolbox-port','ai-port','token','stop-file','ai-home','data-dir'):p.add_argument('--'+key)
for key in ('enable-ai','full-features','isolated'):p.add_argument('--'+key,action='store_true')
a=p.parse_args();root=Path(a.root)
if a.kind=='check':Path(a.result_file).write_text('{"ok":true}');sys.exit(0)
identity={'token':a.token,'kind':a.kind,'pid':os.getpid(),'root':str(root),'fingerprint':'.'.join(hashlib.sha256((root/f).read_bytes()).hexdigest()[:12] for f in ('backend/app/main.py','backend/ai_mode/server.py','frontend/dist/index.html'))}
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        if self.path=='/__launcher__/health':data=identity
        elif self.path=='/ai/v1/ping':data={'mode':'ai','version':'0.5.0','enabled':True}
        else:data={'status':'ok'}
        body=json.dumps(data).encode()
        self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
server=ThreadingHTTPServer(('127.0.0.1',int(a.port)),Handler);server.timeout=.1
while not Path(a.stop_file).exists():server.handle_request()
server.server_close()
''', encoding='utf-8')

# Child processes inherit only OS plumbing and private test state.
env = {k: v for k, v in os.environ.items() if k.upper() in {
    'SYSTEMROOT', 'SYSTEMDRIVE', 'WINDIR', 'PATH', 'PATHEXT', 'TEMP', 'TMP', 'COMSPEC',
    'PROCESSOR_ARCHITECTURE', 'NUMBER_OF_PROCESSORS'}}
env.update(VASP_LAUNCHER_STATE_DIR=str(OUT/'state'),
           PYTHONPYCACHEPREFIX=str(OUT/'pycache'))
process = test_support.start_process([str(HARNESS), str(installation), sys.executable,
    str(runtime), str(OUT), 'ai'], env=env, stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL)
try:
    process.wait(timeout=60)
except BaseException:
    process.terminate()
    process.wait(timeout=10)
    raise

result = json.loads((OUT/'result.json').read_text(encoding='utf-8-sig'))
assert process.returncode == 0 and result['ok'], result
ready = json.loads((OUT/'ready.json').read_text(encoding='utf-8-sig'))
assert ready['attempts'] == 1, ready
assert ready['snapshot']['Version'].startswith('0.5.0 / '), ready
assert len(ready['snapshot']['Services']) == 3, ready
assert all(service['State'] == 'ready' for service in ready['snapshot']['Services']), ready
assert not result['running'], result
for service in result['snapshot']['Services']:
    with socket.socket() as probe:
        assert probe.connect_ex(('127.0.0.1', service['Port'])) != 0, service
print('Release 0.5.0 AI health accepted; three private services stopped: passed', flush=True)
print('Evidence: ' + str(OUT), flush=True)
