"""Network-free full-feature launcher checks; never access the user's keyring/.env."""
from pathlib import Path
import http.client
import json
import os
import socket
import subprocess
import sys
import time

import test_support

ROOT = Path(__file__).resolve().parents[2]
OUT = test_support.output('full-features')
OUT.mkdir(parents=True)
RUNTIME = ROOT / 'launcher/runtime.py'
HARNESS = test_support.harness('ControllerHarnessFullFeatures.exe')
HARNESS.parent.mkdir(parents=True, exist_ok=True)
compiler = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
subprocess.run([str(compiler), '/nologo', '/utf8output', '/target:exe', '/platform:x64',
                '/reference:System.Web.Extensions.dll', '/out:' + str(HARNESS),
                str(ROOT/'launcher/windows/NativeProcess.cs'), str(ROOT/'launcher/windows/LauncherCore.cs'),
                str(ROOT/'desktop/tests/ControllerHarnessFullFeatures.cs')], check=True)
rows = []


def record(name):
    rows.append({'name': name, 'passed': True})
    (OUT/'results.json').write_text(json.dumps(rows, indent=2), encoding='utf-8')
    print(name + ': passed', flush=True)


def wait(path, process, timeout=75):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return json.loads(path.read_text(encoding='utf-8-sig'))
        if process.poll() is not None:
            raise AssertionError('harness exited before ' + path.name)
        time.sleep(.04)
    raise AssertionError('timeout: ' + path.name)


def request(snapshot, path, method='GET', body=None):
    port = next(s['Port'] for s in snapshot['Services'] if s['Key'] == 'web')
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=15)
    connection.request(method, path, json.dumps(body) if body is not None else None,
                       {'Content-Type': 'application/json'})
    response = connection.getresponse()
    data = json.loads(response.read())
    status = response.status
    connection.close()
    return status, data


def start(name, root, mode='full', runtime=RUNTIME):
    directory = OUT/name
    directory.mkdir()
    # Build an allowlisted environment: no real model/SSH/MP keys are inherited.
    env = {k: v for k, v in os.environ.items() if k.upper() in {
        'SYSTEMROOT', 'WINDIR', 'PATH', 'PATHEXT', 'TEMP', 'TMP', 'COMSPEC',
        'PROCESSOR_ARCHITECTURE', 'NUMBER_OF_PROCESSORS'}}
    env.update(VASP_LAUNCHER_STATE_DIR=str(directory/'state'),
               VASP_REVIEWER_SHARED_SECRET='synthetic-parent-secret-must-stay-parent',
               FULL_TEST_UNICODE='中文 环境值', AI_MODE_LLM_PROVIDER='fake',
               PYTHONPYCACHEPREFIX=str(OUT/'pycache'))
    process = test_support.start_process([str(HARNESS), str(root), sys.executable,
        str(runtime), str(directory), mode], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return process, directory


def finish(process, directory):
    (directory/'release').touch()
    process.wait(timeout=30)
    result = json.loads((directory/'result.json').read_text(encoding='utf-8-sig'))
    assert result['parentEnvironmentUnchanged'], result
    for service in result['snapshot']['Services'] if (directory/'ready.json').exists() else []:
        if service['State'] == 'disabled':
            continue
        with socket.socket() as probe:
            assert probe.connect_ex(('127.0.0.1', service['Port'])) != 0
    return result


installation = OUT/'中文 合成安装'
for directory in ('backend/app', 'backend/ai_mode', 'frontend/dist'):
    (installation/directory).mkdir(parents=True)
fixture = '''from fastapi import FastAPI,Request
import hashlib,hmac,http.client,json,os,sys
from pathlib import Path
from urllib.parse import urlsplit
app=FastAPI()
@app.get('/health')
def health():return {'status':'ok'}
@app.get('/ai/v1/ping')
def ping():return {'mode':'ai','version':'0.4.0','enabled':True}
@app.post('/ai/internal/reviewer/review')
def review(request:Request):
    assert hmac.compare_digest(request.headers.get('authorization',''), 'Bearer '+os.environ['VASP_REVIEWER_SHARED_SECRET'])
    return {'authorized':True}
@app.get('/api/v1/reviewer-connection')
def reviewer_connection():
    url=urlsplit(os.environ['VASP_REVIEWER_URL'])
    connection=http.client.HTTPConnection(url.hostname,url.port,timeout=5)
    connection.request('POST','/ai/internal/reviewer/review',headers={'Authorization':'Bearer '+os.environ['VASP_REVIEWER_SHARED_SECRET']})
    response=connection.getresponse();result=json.loads(response.read());connection.close()
    return result
@app.get('/api/v1/probe')
@app.get('/ai/v1/probe')
def probe():
    secret=os.environ.get('VASP_REVIEWER_SHARED_SECRET','')
    return {'flags':{k:os.environ.get(k) for k in ('ENABLE_BAND_WORKFLOW','ENABLE_LLM','ENABLE_MATERIALS_PROJECT','ENABLE_POTCAR_ASSEMBLY','ENABLE_LOCAL_FAKE_HPC')},
        'provider':os.environ.get('AI_MODE_LLM_PROVIDER'),
        'reviewer':os.environ.get('VASP_REVIEWER_ENABLED'),
        'url':os.environ.get('VASP_REVIEWER_URL'),
        'secret_bytes':len(secret.encode()),'digest':hashlib.sha256(secret.encode()).hexdigest(),
        'secret_in_command':secret in ' '.join(sys.argv) if secret else False,
        'secret_on_disk':any(secret.encode() in p.read_bytes() for p in Path(os.environ['VASP_LAUNCHER_STATE_DIR']).rglob('*') if p.is_file()) if secret else False,
        'unicode':os.environ.get('FULL_TEST_UNICODE'),
        'dotenv':os.environ.get('FULL_TEST_DOTENV')}
'''
for target in ('backend/app/main.py', 'backend/ai_mode/server.py'):
    (installation/target).write_text(fixture, encoding='utf-8')
(installation/'frontend/dist/index.html').write_text('synthetic full-feature fixture', encoding='utf-8')
(installation/'backend/.env').write_text('FULL_TEST_DOTENV=loaded\nENABLE_LLM=false\nAI_MODE_LLM_PROVIDER=fake\n', encoding='utf-8')

p, d = start('synthetic-full', installation)
s = wait(d/'ready.json', p)
toolbox = request(s, '/api/v1/probe')[1]
ai = request(s, '/ai/v1/probe')[1]
assert toolbox['digest'] == ai['digest'] and toolbox['secret_bytes'] >= 32
assert toolbox['reviewer'] == ai['reviewer'] == 'true'
assert toolbox['url'] == 'http://127.0.0.1:' + str(next(x['Port'] for x in s['Services'] if x['Key'] == 'ai'))
assert all(toolbox['flags'][k] == 'true' for k in ('ENABLE_BAND_WORKFLOW', 'ENABLE_LLM', 'ENABLE_MATERIALS_PROJECT', 'ENABLE_POTCAR_ASSEMBLY'))
assert toolbox['flags']['ENABLE_LOCAL_FAKE_HPC'] == 'false' and toolbox['provider'] == ai['provider'] == 'openai'
assert toolbox['unicode'] == '中文 环境值' and toolbox['dotenv'] == 'loaded'
assert not toolbox['secret_in_command'] and not toolbox['secret_on_disk']
assert request(s, '/api/v1/reviewer-connection') == (200, {'authorized': True})
prefs = json.loads((d/'state/preferences.json').read_text(encoding='utf-8-sig'))
assert 'FullFeatures' not in prefs and 'VASP_REVIEWER_SHARED_SECRET' not in prefs
(d/'restart').touch()
s2 = wait(d/'ready2.json', p)
assert request(s2, '/api/v1/probe')[1]['digest'] != toolbox['digest']
assert finish(p, d)['ok']
record('full-flags-dynamic-reviewer-url-shared-secret-rotation-unicode-no-persistence-parent-unchanged')

for mode in ('full-no-ai', 'normal', 'isolated'):
    p, d = start(mode, installation, mode)
    s = wait(d/'ready.json', p)
    probe = request(s, '/api/v1/probe')[1]
    assert probe['reviewer'] == 'false' and probe['secret_bytes'] == 0 and probe['url'] is None
    if mode == 'isolated':
        assert probe['dotenv'] is None and probe['unicode'] is None
        assert probe['flags']['ENABLE_LLM'] == probe['flags']['ENABLE_MATERIALS_PROJECT'] == 'false'
    assert finish(p, d)['ok']
record('reviewer-disabled-without-ai-normal-and-isolated-compatibility')

p, d = start('full-conflict', installation, 'full-conflict')
p.wait(timeout=20)
assert not finish(p, d)['ok']
record('full-isolated-conflict-rejected')

# Real business stack, with a no-key credential substitute installed before imports.
# This wrapper is test-only and never packaged. Non-loopback network is blocked.
wrapper = OUT/'no-credentials-runtime.py'
wrapper.write_text('''import importlib.util,ipaddress,socket
from pathlib import Path
import keyring
from keyring.backend import KeyringBackend
class NoCredentials(KeyringBackend):
    priority=1
    def get_password(self,service,username):return None
    def set_password(self,*args):raise AssertionError('credential writes forbidden')
    def delete_password(self,*args):raise AssertionError('credential writes forbidden')
keyring.set_keyring(NoCredentials())
import dotenv
dotenv.dotenv_values=lambda *a,**k:{}
connect=socket.socket.connect
def local_connect(self,address):
    if not ipaddress.ip_address(address[0]).is_loopback:raise AssertionError('external network forbidden')
    return connect(self,address)
socket.socket.connect=local_connect
spec=importlib.util.spec_from_file_location('full_runtime',RUNTIME_PATH)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
module.main()
'''.replace('RUNTIME_PATH', repr(str(RUNTIME))), encoding='utf-8')
p, d = start('actual-three-services', ROOT, runtime=wrapper)
s = wait(d/'ready.json', p)
status, bootstrap = request(s, '/api/v1/bootstrap')
assert status == 200 and all(bootstrap[k] for k in ('ENABLE_LLM', 'ENABLE_POTCAR_ASSEMBLY', 'ENABLE_BAND_WORKFLOW'))
status, capabilities = request(s, '/ai/v1/diagnosis/capabilities')
assert status == 200 and not capabilities['configured']
assert capabilities['reason_code'] == 'AI_MODE_DIAGNOSIS_NOT_CONFIGURED'
status, generated = request(s, '/api/v1/workflows/generate', 'POST', {'workflow': {
    'workflow_id': 'full-features-missing-potcar', 'requested_tasks': ['static'],
    'structure': {'formula': 'Si', 'elements': ['Si'], 'counts': [1],
                  'poscar_text': 'synthetic Si\n1.0\n5 0 0\n0 5 0\n0 0 5\nSi\n1\nDirect\n0 0 0\n'}}})
assert status == 200, (status, generated)
assert generated['data']['steps'] and all('POTCAR_NOT_PREPARED' in step['blocked_by'] and not step['runnable']
                                        for step in generated['data']['steps'])
# Internal reviewer cannot be reached through the public proxy, even though enabled.
assert request(s, '/ai/internal/reviewer/review', 'POST', {})[0] != 200
assert finish(p, d)['ok']
record('actual-three-services-full-start-missing-model-explicit-potcar-gate-no-external-calls-normal-stop')
print('Evidence: ' + str(OUT), flush=True)
