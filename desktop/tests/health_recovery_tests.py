"""Private stdlib controller fixtures and a disposable real WebView recovery check.

No user packages, configurations, running processes, model/MP/SSH/HPC services
are used. All evidence, compiled harnesses and WebView data stay under --output.
"""
from pathlib import Path
import argparse
import datetime
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
p = argparse.ArgumentParser()
p.add_argument('--output', type=Path, default=ROOT/'.tmp/rc3-mp-health-evidence')
p.add_argument('--controller-only', action='store_true', help='Skip the optional real WebView check for headless CI.')
a = p.parse_args()
BASE = a.output.resolve()
OUT = BASE/(time.strftime('%Y%m%dT%H%M%S')+'-'+str(os.getpid()))
BIN = OUT/'bin'
BIN.mkdir(parents=True)

RUNTIME = r'''
import argparse,hashlib,json,os,sys,time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
p=argparse.ArgumentParser()
for key in ('kind','root','result-file','port','toolbox-port','ai-port','token','stop-file','ai-home','data-dir'):p.add_argument('--'+key)
for key in ('enable-ai','full-features','isolated','external'):p.add_argument('--'+key,action='store_true')
a=p.parse_args();root=Path(a.root)
if a.kind=='check':Path(a.result_file).write_text(' {"ok":true}');sys.exit(0)
identity=json.loads((root/'identity-toolbox.json').read_text()) if a.external else {'token':a.token,'kind':a.kind,'pid':os.getpid(),'root':str(root),'fingerprint':'.'.join(hashlib.sha256((root/f).read_bytes()).hexdigest()[:12] for f in ('backend/app/main.py','backend/ai_mode/server.py','frontend/dist/index.html'))}
if not a.external:(root/('identity-'+a.kind+'.json')).write_text(json.dumps(identity))
def mode():
    try:return (root/'toolbox-mode').read_text() if a.kind=='toolbox' else 'normal'
    except FileNotFoundError:return 'normal'
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        m=mode()
        if self.path=='/__launcher__/health':
            if m=='slow':time.sleep(1.1)
            if m=='forge-auth':self.send_response(403);self.send_header('Content-Length','0');self.end_headers();return
            data=dict(identity)
            if m.startswith('forge-'):
                key=m[6:];data[key]=-1 if key=='pid' else 'synthetic-wrong-identity'
            body=json.dumps(data).encode();content='application/json'
        elif self.path in ('/health','/ai/v1/ping'):
            body=json.dumps({'status':'ok','mode':'ai','version':'0.4.0','enabled':True}).encode();content='application/json'
        else:body=b'<!doctype html><html><body><input id="draft"><p>Disposable health recovery fixture</p></body></html>';content='text/html'
        try:
            self.send_response(200);self.send_header('Content-Type',content);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        except OSError:pass
server=ThreadingHTTPServer(('127.0.0.1',int(a.port)),Handler);server.timeout=.1
while a.external or not Path(a.stop_file).exists():
    if mode()=='release':
        server.server_close();(root/'toolbox-released').touch()
        while not Path(a.stop_file).exists():time.sleep(.1)
        break
    server.handle_request()
server.server_close()
'''

compiler = Path(os.environ['WINDIR'])/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
sources = [ROOT/'launcher/windows/NativeProcess.cs', ROOT/'launcher/windows/LauncherCore.cs']
common = [str(compiler), '/nologo', '/utf8output', '/target:exe', '/platform:x64', '/reference:System.Web.Extensions.dll']
subprocess.run(common+['/out:'+str(BIN/'ControllerHealth.exe')]+list(map(str,sources))+[str(ROOT/'desktop/tests/ControllerHarnessHealth.cs')],check=True)
if not a.controller_only:
    for name in ('Microsoft.Web.WebView2.Core.dll','Microsoft.Web.WebView2.WinForms.dll','WebView2Loader.dll'):
        shutil.copyfile(ROOT/'desktop/dist'/name,BIN/name)
    subprocess.run(common+['/main:DesktopHealthHarness','/reference:System.Windows.Forms.dll','/reference:System.Drawing.dll',
        '/reference:'+str(BIN/'Microsoft.Web.WebView2.Core.dll'),'/reference:'+str(BIN/'Microsoft.Web.WebView2.WinForms.dll'),
        '/resource:'+str(ROOT/'desktop/assets/app-icon.png')+',VaspCopilot.Brand',
        '/win32manifest:'+str(ROOT/'desktop/app.manifest'),'/out:'+str(BIN/'DesktopHealth.exe')]+
        list(map(str,sources))+[str(ROOT/'desktop/DesktopApp.cs'),str(ROOT/'desktop/tests/DesktopHarnessHealth.cs')],check=True)
    shutil.copyfile(ROOT/'desktop/app.config',BIN/'DesktopHealth.exe.config')

def run(name, executable, result_file, seconds):
    directory=OUT/name;installation=directory/'installation'
    for folder in ('backend/app','backend/ai_mode','frontend/dist','launcher'):(installation/folder).mkdir(parents=True)
    for file in ('backend/app/main.py','backend/ai_mode/server.py','frontend/dist/index.html'):(installation/file).write_text('synthetic fixture')
    runtime=installation/'launcher/fixture.py';runtime.write_text(RUNTIME,encoding='utf-8')
    (installation/'toolbox-mode').write_text('normal')
    env={k:v for k,v in os.environ.items() if k.upper() in {'SYSTEMROOT','SYSTEMDRIVE','WINDIR','PATH','PATHEXT','TEMP','TMP','COMSPEC','PROCESSOR_ARCHITECTURE','NUMBER_OF_PROCESSORS'}}
    env.update(VASP_LAUNCHER_STATE_DIR=str(directory/'state'),HOME=str(directory/'user'),USERPROFILE=str(directory/'user'),
        PYTHONUTF8='1',PYTHONPYCACHEPREFIX=str(OUT/'pycache'),WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS='--disable-background-networking --no-first-run')
    process=subprocess.Popen([str(BIN/executable),str(installation),sys.executable,str(runtime),str(directory)],env=env,
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:process.wait(timeout=seconds)
    except BaseException:
        process.terminate();process.wait(timeout=10);raise
    result=json.loads((directory/result_file).read_text(encoding='utf-8-sig'))
    assert result['ok'] and process.returncode==0,result
    for service in result['snapshot']['Services']:
        with socket.socket() as probe:assert probe.connect_ex(('127.0.0.1',service['Port']))!=0,service
    return result,directory

controller,controller_dir=run('controller','ControllerHealth.exe','result.json',100)
checks=[json.loads(line) for line in (controller_dir/'checks.jsonl').read_text(encoding='utf-8-sig').splitlines()]
assert len(checks)==10 and all(c['passed'] for c in checks),checks
print('Controller checks passed: '+str(len(checks)),flush=True)
ui = None
if not a.controller_only:
    ui,ui_dir=run('webview','DesktopHealth.exe','ui-result.json',100)
summary={'passed':True,'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'controllerChecks':checks,
    'actualWebView':ui,'evidence':str(OUT),'noRealCredentialsOrExternalBusinessServices':True,
    'sources':{str(f.relative_to(ROOT)):hashlib.sha256(f.read_bytes()).hexdigest() for f in sources+[ROOT/'desktop/DesktopApp.cs']}}
(OUT/'results.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding='utf-8')
(BASE/'results.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding='utf-8')
if ui:print('Actual WebView recovery/draft/navigation/status checks passed',flush=True)
print('Evidence: '+str(BASE/'results.json'),flush=True)
