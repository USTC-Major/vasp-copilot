"""Controller/environment-helper contract tests: stdlib stubs, no pip or network."""
from pathlib import Path
import ctypes
import json
import os
import shutil
import socket
import subprocess
import sys
import test_support

ROOT = Path(__file__).resolve().parents[2]
OUT = test_support.output('environment-controller')
OUT.mkdir(parents=True)
ROWS = []
PROBE_BIN = OUT/'probe-bin'
PROBE_BIN.mkdir()
compiler = Path(os.environ['WINDIR'])/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
HARNESS = OUT/'ControllerHarnessEnvironment.exe'
subprocess.run([str(compiler),'/nologo','/utf8output','/target:exe','/platform:x64',
    '/reference:System.Web.Extensions.dll','/out:'+str(HARNESS),
    str(ROOT/'launcher/windows/NativeProcess.cs'),str(ROOT/'launcher/windows/LauncherCore.cs'),
    str(ROOT/'desktop/tests/ControllerHarnessEnvironment.cs')],check=True)
subprocess.run([str(compiler),'/nologo','/utf8output','/target:exe','/platform:x64',
    '/reference:System.Web.Extensions.dll','/out:'+str(PROBE_BIN/'python.exe'),
    str(ROOT/'desktop/tests/ControllerBasePythonStub.cs')],check=True)
shutil.copyfile(PROBE_BIN/'python.exe',PROBE_BIN/'py.exe')
(PROBE_BIN/'real-python.txt').write_text(sys.executable,encoding='utf-8')

HELPER = r'''
import argparse,json,os,subprocess,sys,time,venv
from pathlib import Path
p=argparse.ArgumentParser()
for key in ('root','state','result-file','progress-file'):p.add_argument('--'+key,required=True)
a=p.parse_args();root=Path(a.root);state=Path(a.state)
assert all(Path(v).is_absolute() for v in vars(a).values())
calls=root/'helper-calls.jsonl'
with calls.open('a',encoding='utf-8') as f:f.write(json.dumps({'env_keys':sorted(os.environ),'proxy_marker':os.environ.get('VASP_INSTALLER_PROXY_CONFIGURED'),'state':str(state)})+'\n')
mode=(root/'helper-mode').read_text()
def progress(stage):
    Path(a.progress_file).write_text(json.dumps({'stage':stage,'message':'synthetic '+stage}),encoding='utf-8');time.sleep(.2)
progress('validate');progress('create');progress('install')
log=state/'logs/stub-environment.local.log';log.parent.mkdir(parents=True,exist_ok=True);log.write_text('synthetic no-network preparation')
if mode=='cancel':
    child=subprocess.Popen([sys.executable,'-I','-c','import time;time.sleep(90)'])
    (root/'child-pid.json').write_text(json.dumps({'parent':os.getpid(),'child':child.pid}))
    time.sleep(90)
if mode in ('fail','fail-external-log'):
    reported_log=root/'backend/app/main.py' if mode=='fail-external-log' else log
    Path(a.result_file).write_text(json.dumps({'ok':False,'code':'NETWORK_FAILED','message':'synthetic network failure','log_path':str(reported_log)}));sys.exit(1)
target=state/'environments/stub'
if not target.exists():venv.EnvBuilder(with_pip=False).create(target)
progress('verify');progress('ready')
prepared=target/'Scripts/python.exe'
if mode=='invalid-result':prepared=Path(sys.executable)
Path(a.result_file).write_text(json.dumps({'ok':True,'python':str(prepared),'environment_id':'stub'}))
'''

RUNTIME = r'''
import argparse,hashlib,json,os,sys
from http.server import BaseHTTPRequestHandler,HTTPServer
from pathlib import Path
p=argparse.ArgumentParser()
for key in ('kind','root','result-file','port','toolbox-port','ai-port','token','stop-file','ai-home','data-dir'):p.add_argument('--'+key)
for key in ('enable-ai','full-features','isolated'):p.add_argument('--'+key,action='store_true')
a=p.parse_args();root=Path(a.root)
if a.kind=='check':
    with (root/'dependency-checks.jsonl').open('a') as f:f.write(json.dumps({'python':sys.executable})+'\n')
    Path(a.result_file).write_text(json.dumps({'ok':not (root/'check-fail').exists(),'missing':['synthetic-dependency']}));sys.exit(0)
fingerprint='.'.join(hashlib.sha256((root/file).read_bytes()).hexdigest()[:12] for file in ('backend/app/main.py','backend/ai_mode/server.py','frontend/dist/index.html'))
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        data={'token':a.token,'kind':a.kind,'pid':os.getpid(),'fingerprint':fingerprint,'root':str(root)} if self.path=='/__launcher__/health' else {'status':'ok','mode':'ai','version':'0.4.0','enabled':True}
        body=json.dumps(data).encode();self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
server=HTTPServer(('127.0.0.1',int(a.port)),Handler);server.timeout=.1
while not Path(a.stop_file).exists():server.handle_request()
server.server_close()
'''

def record(name):
    ROWS.append({'name':name,'passed':True})
    (OUT/'results.json').write_text(json.dumps(ROWS,indent=2),encoding='utf-8')
    print(name+': passed',flush=True)


def run(name, mode='full', helper='success', python=sys.executable, extra_env=None, check_fail=False):
    directory=OUT/name;directory.mkdir()
    installation=directory/'中文 installation'
    for folder in ('backend/app','backend/ai_mode','frontend/dist','launcher'):(installation/folder).mkdir(parents=True)
    for file in ('backend/app/main.py','backend/ai_mode/server.py','frontend/dist/index.html'):(installation/file).write_text('synthetic fixture')
    (installation/'launcher/environment.py').write_text(HELPER,encoding='utf-8')
    runtime=installation/'launcher/stub-runtime.py';runtime.write_text(RUNTIME,encoding='utf-8')
    (installation/'helper-mode').write_text(helper)
    if check_fail:(installation/'check-fail').touch()
    env={k:v for k,v in os.environ.items() if k.upper() in {'SYSTEMROOT','SYSTEMDRIVE','WINDIR','PATH','PATHEXT','TEMP','TMP','COMSPEC','PROCESSOR_ARCHITECTURE','NUMBER_OF_PROCESSORS'}}
    env.update(VASP_LAUNCHER_STATE_DIR=str(directory/'state'),OPENAI_API_KEY='synthetic-controller-secret',
        VASP_AI_HOME='synthetic-personal-home',MP_API_KEY='synthetic-materials-secret',
        VASP_REVIEWER_SHARED_SECRET='synthetic-reviewer-secret',PIP_INDEX_URL='https://synthetic:password@invalid.test/simple',
        PYTHONPATH='synthetic-do-not-inherit',CONDA_PREFIX=str(Path(sys.executable).parent))
    if extra_env:env.update(extra_env)
    process=test_support.start_process([str(HARNESS),str(installation),str(python),str(runtime),str(directory),mode],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    process.wait(timeout=70)
    result=json.loads((directory/'result.json').read_text(encoding='utf-8-sig'))
    assert result['defaultsOff'] and result['parentSecretUnchanged'] and not result['running'],result
    for outcome in result['outcomes']:
        for service in outcome['snapshot']['Services']:
            if not service['Pid']:continue
            with socket.socket() as probe:assert probe.connect_ex(('127.0.0.1',service['Port']))!=0
    calls=[json.loads(line) for line in (installation/'helper-calls.jsonl').read_text().splitlines()] if (installation/'helper-calls.jsonl').exists() else []
    for call in calls:
        assert 'SystemDrive' in call['env_keys'] or 'SYSTEMDRIVE' in call['env_keys'],call
        assert not set(call['env_keys']) & {'OPENAI_API_KEY','MP_API_KEY','VASP_AI_HOME','VASP_REVIEWER_SHARED_SECRET','PIP_INDEX_URL','PYTHONPATH','HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','CONDA_PREFIX'},call
        assert Path(call['state'])==directory/'state/runtime/full'
    return result,calls,installation


if '--environment-smoke' in sys.argv:
    r,c,i=run('os-environment-smoke')
    assert r['outcomes'][0]['ok'] and len(c)==1,r
    record('system-drive-preserved-secrets-filtered-prepared-python-three-services-ready')
    print('Evidence: '+str(OUT),flush=True)
    sys.exit(0)

r,c,i=run('metadata-sharing',mode='metadata-sharing')
assert r['outcomes'][0]['ok'] and all(r['metadataChecks'].values()) and not c,r
record('metadata-readers-share-writes-and-atomic-replacement-with-live-writer-progress-and-log')
if '--metadata-sharing-only' in sys.argv:
    print('Evidence: '+str(OUT),flush=True)
    sys.exit(0)


r,c,i=run('successful-full-three-services')
assert r['outcomes'][0]['ok'] and len(c)==1,r
assert all(s['State']=='ready' and s['Pid']>0 for s in r['outcomes'][0]['snapshot']['Services'])
assert '/environments/' in r['outcomes'][0]['selectedPython'].replace('\\','/')
assert {'environment_install','environment_verify','environment_ready'} <= set(r['stages']),r
checks=[json.loads(line) for line in (i/'dependency-checks.jsonl').read_text().splitlines()]
assert len(checks)==1 and checks[0]['python']==r['outcomes'][0]['selectedPython']
record('successful-helper-prepared-python-dependency-check-three-services-stages-safe-environment')

r,c,i=run('failure-one-attempt',helper='fail',python='')
assert not r['outcomes'][0]['ok'] and len(c)==1 and 'NETWORK_FAILED' in r['outcomes'][0]['error'],r
assert not (i/'dependency-checks.jsonl').exists()
assert 'stub-environment.local.log' in r['outcomes'][0]['error']
assert Path(r['outcomes'][0]['environmentFailureLogPath']).is_file()
record('network-failure-reported-with-log-no-python-download-fallback')

r,c,i=run('failure-retry',mode='retry',helper='fail')
assert [x['ok'] for x in r['outcomes']]==[False,True] and len(c)==2,r
assert r['outcomes'][1]['environmentFailureLogPath'] is None
record('helper-failure-can-retry-same-controller')

r,c,i=run('reject-external-log',helper='fail-external-log')
assert not r['outcomes'][0]['ok'] and r['outcomes'][0]['environmentFailureLogPath'] is None,r
assert str(i/'backend/app/main.py') not in r['outcomes'][0]['error']
record('failure-log-must-exist-inside-owned-log-directory')

for mode in ('cancel','cancel-retry','dispose'):
    r,c,i=run(mode,mode=mode,helper='cancel')
    assert not r['outcomes'][0]['ok'] and '取消' in r['outcomes'][0]['error'],r
    pids=json.loads((i/'child-pid.json').read_text())
    for pid in pids.values():
        handle=ctypes.windll.kernel32.OpenProcess(0x100000,False,pid)
        if handle:
            try:assert ctypes.windll.kernel32.WaitForSingleObject(handle,0)==0,(pid,r)
            finally:ctypes.windll.kernel32.CloseHandle(handle)
    if mode=='cancel-retry':assert r['outcomes'][1]['ok'],r
record('cancel-cleans-helper-and-installer-child-retry-resets-cancellation')

r,c,i=run('pre-cancel-consumed-before-retry',mode='pre-cancel')
assert [x['ok'] for x in r['outcomes']]==[False,True] and len(c)==1,r
assert '取消' in r['outcomes'][0]['error']
record('cancel-before-start-is-honored-and-later-explicit-retry-succeeds')

for mode in ('normal','isolated','full-default'):
    r,c,i=run(mode,mode=mode)
    assert r['outcomes'][0]['ok'] and not c,r
record('normal-isolated-and-default-full-never-invoke-installer')

r,c,i=run('missing-base',python=OUT/'missing-python.exe')
assert not r['outcomes'][0]['ok'] and not c and not (i/'dependency-checks.jsonl').exists(),r
record('missing-base-python-fails-without-preparation-or-services')

r,c,i=run('unsupported-base',python=PROBE_BIN/'python.exe')
assert not r['outcomes'][0]['ok'] and not c and '3.11 / 3.12' in r['outcomes'][0]['error'],r
record('unsupported-python-version-rejected-before-helper')

# Real supported installations may precede py.exe. Inspect discovery and invoke
# its exact owned-process version-selector probe without hiding those installs.
r,c,i=run('py-launcher-discovery',mode='discovery-probe',python=PROBE_BIN/'py.exe',extra_env={'PATH':str(PROBE_BIN)})
assert r['outcomes'][0]['ok'] and not c and r['outcomes'][0]['selectedPython']==sys.executable,r
assert '-3.12' in (PROBE_BIN/'probe-calls.txt').read_text()
record('py-launcher-version-selector-discovery-with-quoted-stdlib-probe')

r,c,i=run('invalid-helper-result',helper='invalid-result')
assert not r['outcomes'][0]['ok'] and len(c)==1 and not (i/'dependency-checks.jsonl').exists(),r
record('helper-cannot-return-base-python-as-managed-environment')

r,c,i=run('post-install-check-fails',check_fail=True)
assert not r['outcomes'][0]['ok'] and len(c)==1 and not r['outcomes'][0]['running'],r
record('prepared-environment-still-must-pass-existing-dependency-check')

r,c,i=run('proxy-marker-only',extra_env={'HTTPS_PROXY':'http://synthetic:password@invalid.test:8080'})
assert r['outcomes'][0]['ok'] and c[0]['proxy_marker']=='1',r
record('proxy-presence-marker-only-no-proxy-credentials-in-helper')
print('Evidence: '+str(OUT),flush=True)
