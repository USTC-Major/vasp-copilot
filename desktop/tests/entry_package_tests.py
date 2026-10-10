"""Actual root EXE, ZIP extraction, different CWD, same-profile activation/exit.

Only an explicit isolated profile is started. UI-only reflection tests elsewhere
exercise full-mode preferences without touching the current user's formal state.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import zipfile
from test_support import ROOT, output, start_process

OUT=output('entry-package')
OUT.mkdir(parents=True)
source=Path(json.loads((ROOT/'desktop/dist/last-package.json').read_text(encoding='utf-8-sig'))['directory'])
archive=OUT/'DT-01-acceptance.zip'
with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
    for f in source.rglob('*'):
        if f.is_file():z.write(f,f.relative_to(source))
package=OUT/'中文 解压包 with spaces'
with zipfile.ZipFile(archive) as z:z.extractall(package)
inventory=json.loads((package/'package-manifest.json').read_text(encoding='utf-8-sig'))
for name,digest in inventory['files'].items():assert hashlib.sha256((package/name).read_bytes()).hexdigest()==digest,name
assert list(package.rglob('*.exe'))==[package/'VASP-Copilot.exe']
close=OUT/'CloseEntryProbe.exe'
compiler=Path(os.environ['WINDIR'])/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
subprocess.run([str(compiler),'/nologo','/target:exe','/platform:x64','/out:'+str(close),str(ROOT/'desktop/tests/CloseEntryProbe.cs')],check=True)
profile=OUT/'isolated-profile'
profile.mkdir()
preferences={'RootDirectory':str(package),'PythonExecutable':sys.executable,'EnableAi':False,'VaspAiHome':str(profile/'home'),'DataDirectory':str(profile/'data')}
(profile/'preferences.json').write_text(json.dumps(preferences),encoding='utf-8')
env={k:v for k,v in os.environ.items() if k.upper() in {'SYSTEMROOT','SYSTEMDRIVE','WINDIR','PATH','PATHEXT','TEMP','TMP','COMSPEC','PROCESSOR_ARCHITECTURE','NUMBER_OF_PROCESSORS'}}
env.update(VASP_LAUNCHER_STATE_DIR=str(profile),PYTHONUTF8='1',HOME=str(profile/'user'),USERPROFILE=str(profile/'user'))
args=[str(package/'VASP-Copilot.exe'),'--test-profile',str(profile),'--test-offscreen']
def events():
    try:return [json.loads(row) for row in (profile/'desktop-events.jsonl').read_text(encoding='utf-8-sig').splitlines()]
    except (FileNotFoundError,json.JSONDecodeError):return []
def wait(predicate,seconds):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        if predicate():return
        time.sleep(.1)
    raise AssertionError('Package entry timed out')
results=[]
for attempt in range(2):
    before=len(events())
    child=start_process(args,cwd=OUT,env=env)
    wait(lambda:any(e['kind'] in ('workspace-ready','startup-failed') for e in events()[before:]),60)
    new=events()[before:]
    ready=any(e['kind']=='workspace-ready' for e in new)
    missing_runtime=any(e['kind']=='startup-failed' and e['data']['category']=='WEBVIEW2_RUNTIME_NOT_FOUND' for e in new)
    assert ready or missing_runtime,new
    second=subprocess.run(args,cwd=package/'backend',env=env,timeout=15)
    assert second.returncode==0 and child.poll() is None
    wait(lambda:any(e['kind']=='activated' for e in events()[before:]),5)
    subprocess.run([str(close),str(child.pid)],check=True,timeout=40)
    assert child.wait(timeout=5)==0
    restored=json.loads((profile/'preferences.json').read_text(encoding='utf-8-sig'))
    assert all(restored[k]==v for k,v in preferences.items())
    diag=json.loads((profile/'startup-diagnostics.json').read_text(encoding='utf-8-sig'))
    assert Path(diag['target'])==profile/'preferences.json' and diag['mode']=='test-profile'
    results.append({'newProcess':True,'sameTarget':True,'secondLaunchActivatedFirst':True,'gracefulExit':True,'realWorkspaceReady':ready,'webViewRuntimeMissing':missing_runtime})
summary={'passed':True,'actualRootExe':True,'zipSha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'package':str(package),'attempts':results,'noFormalUserState':True}
(OUT/'results.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding='utf-8')
print('Actual package evidence: '+str(OUT/'results.json'))
