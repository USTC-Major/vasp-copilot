"""Additional controller integration checks: business persistence, cancellation, retry bound."""
from pathlib import Path
import http.client,json,os,socket,subprocess,sys,time
import test_support
ROOT=Path(__file__).resolve().parents[2]
OUT=test_support.output('additional');OUT.mkdir(parents=True)
PYTHON=Path(sys.executable);RUNTIME=ROOT/'launcher/runtime.py';HARNESS=test_support.harness('ControllerHarness.exe')
rows=[]
def record(name,**data):
    rows.append(dict(name=name,**data));(OUT/'results.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(rows[-1],ensure_ascii=False),flush=True)
def wait(path,p=None,timeout=60):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        if path.exists():return
        if p and p.poll() is not None:raise AssertionError('exited early: '+str(path))
        time.sleep(.03)
    raise AssertionError('timeout: '+str(path))
def start(name,mode='start',runtime=RUNTIME,root=ROOT,home=None):
    d=OUT/name;d.mkdir()
    env=dict(os.environ,VASP_LAUNCHER_STATE_DIR=str(d/'state'),PYTHONPYCACHEPREFIX=str(OUT/'pycache'))
    args=[str(HARNESS),str(root),str(PYTHON),str(runtime),str(d),mode]
    if home:args.append(str(home))
    p=test_support.start_process(args,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);return p,d
def finish(p,d):p.wait(timeout=80);return json.loads((d/'result.json').read_text(encoding='utf-8'))
def ready(d,second=False):return json.loads((d/('ready2.json' if second else 'ready.json')).read_text(encoding='utf-8'))['snapshot']
def api(snapshot,method,path,payload=None):
    c=http.client.HTTPConnection('127.0.0.1',next(s['Port'] for s in snapshot['Services'] if s['Key']=='web'),timeout=10)
    c.request(method,'/api/v1/toolbox'+path,body=json.dumps(payload).encode() if payload else None,headers={'Content-Type':'application/json'});r=c.getresponse();data=json.loads(r.read());c.close();assert r.status==200,(r.status,data);return data
def released(r):
    for s in r['snapshot']['Services']:
        if s['State']=='disabled':continue
        probe=socket.socket();assert probe.connect_ex(('127.0.0.1',s['Port']))!=0;probe.close()
def wrapper(name,code,check=''):
    f=OUT/(name+'.py');f.write_text("import importlib.util,sys,time,json,errno\nfrom pathlib import Path\nspec=importlib.util.spec_from_file_location('d2runtime',"+repr(str(RUNTIME))+")\nm=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)\noriginal=m.run\ndef run(args):\n"+code+"\n    return original(args)\nm.run=run\n"+check+"\nm.main()\n",encoding='utf-8');return f

# Real Toolbox CRUD, no tools/model/remote job. Existing config persists through restart and full exit.
p,d=start('business-persistence',mode='hold');wait(d/'ready.json',p);snapshot=ready(d)
project=api(snapshot,'POST','/projects',{'name':'D2 synthetic persistence'})['project'];pid=project['id']
task=api(snapshot,'POST',f'/projects/{pid}/tasks',{'title':'Unsubmitted local test','goal':'persistence only'})['task'];tid=task['id']
api(snapshot,'PUT','/settings',{'max_jobs':7,'poll_interval_seconds':120})
store_before=(d/'home/execution_store.json').read_bytes();config_before=(d/'home/toolbox_config.json').read_bytes()
# A second process uses the same home: existing owner must remain healthy and retry count stay one.
p2,d2=start('same-home-owner-rejected',home=d/'home');r2=finish(p2,d2);assert not r2['ok'] and not r2['running'] and r2['attempts']==1;released(r2)
assert api(snapshot,'GET','/projects')['projects'][0]['id']==pid
record('same-home-existing-owner-preserved',passed=True,attempts=r2['attempts'])
(d/'restart-request').touch();wait(d/'ready2.json',p);snapshot=ready(d,True)
assert any(x['id']==pid for x in api(snapshot,'GET','/projects')['projects'])
assert any(x['id']==tid for x in api(snapshot,'GET',f'/projects/{pid}/tasks')['tasks'])
assert api(snapshot,'GET','/settings')['settings']['max_jobs']==7
assert (d/'home/execution_store.json').read_bytes()==store_before and (d/'home/toolbox_config.json').read_bytes()==config_before
(d/'release').touch();r=finish(p,d);assert r['ok'];released(r)
p3,d3=start('business-relaunch',mode='hold',home=d/'home');wait(d3/'ready.json',p3);s3=ready(d3)
assert any(x['id']==pid for x in api(s3,'GET','/projects')['projects']);assert any(x['id']==tid for x in api(s3,'GET',f'/projects/{pid}/tasks')['tasks']);assert api(s3,'GET','/settings')['settings']['max_jobs']==7
(d3/'release').touch();assert finish(p3,d3)['ok'];record('real-toolbox-project-task-settings-preserved',passed=True,project=pid,task=tid,restart=True,relaunch=True)

# Dependency probe hangs: user exit cancels the owned check immediately, without 15s wait.
slow=wrapper('slow-check','    pass',"m.dependency_check=lambda args:time.sleep(60)")
p,d=start('cancel-dependency-check',mode='cancel',runtime=slow);r=finish(p,d);assert not r['ok'] and not r['running'] and r['elapsedMilliseconds']<4000,r
record('cancel-dependency-check-owned-job-cleanup',passed=True,elapsed_ms=r['elapsedMilliseconds'])

# Three deterministic races: exactly the configured bound, all external sockets kept alive.
signals=OUT/'race-signals';signals.mkdir()
race=wrapper('always-race',"    if args.kind=='toolbox':\n        signal=Path("+repr(str(signals))+ ")/(args.token+'.json');signal.write_text(json.dumps({'port':args.port}))\n        while not signal.with_suffix('.release').exists():time.sleep(.02)")
p,d=start('retry-bound',runtime=race);occupants=[];seen=set()
try:
    deadline=time.monotonic()+70
    while p.poll() is None and time.monotonic()<deadline:
        for f in signals.glob('*.json'):
            if f.name in seen:continue
            s=socket.socket();s.bind(('127.0.0.1',json.loads(f.read_text(encoding='utf-8-sig'))['port']));s.listen();occupants.append(s);seen.add(f.name);f.with_suffix('.release').touch()
        time.sleep(.03)
    r=finish(p,d);assert not r['ok'] and not r['running'] and r['attempts']==3 and len(occupants)==3,r
    assert all(s.fileno()!=-1 for s in occupants);record('definite-bind-conflict-three-attempt-bound',passed=True,attempts=3,external_alive=3)
finally:
    for s in occupants:s.close()

# Runtime normal precedence using synthetic app and directories, no keyring accesses.
root=OUT/'normal-priority';(root/'backend/app').mkdir(parents=True);(root/'backend/ai_mode').mkdir();(root/'frontend/dist').mkdir(parents=True)
(root/'backend/app/main.py').write_text("from fastapi import FastAPI\nfrom pathlib import Path\nimport os,json\napp=FastAPI()\nPath(os.environ['D2_PROBE_OUTPUT']).write_text(json.dumps({'home':os.environ['VASP_AI_HOME'],'data':os.environ['DATA_DIR'],'key_inherited':os.environ['OPENAI_API_KEY']=='synthetic-inherited','dotenv_loaded':os.environ['D2_DOTENV_TRAP']=='synthetic-dotenv','cwd':os.getcwd()}))\n@app.get('/health')\ndef health():return {'status':'ok'}\n")
(root/'backend/ai_mode/server.py').write_text('app=None');(root/'frontend/dist/index.html').write_text('synthetic')
(root/'backend/.env').write_text('VASP_AI_HOME='+str(OUT/'dotenv-home')+'\nDATA_DIR='+str(OUT/'dotenv-data')+'\nOPENAI_API_KEY=synthetic-dotenv-key\nD2_DOTENV_TRAP=synthetic-dotenv\n')
for explicit in (False,True):
    out=OUT/('priority-explicit.json' if explicit else 'priority-inherited.json');stop=OUT/('priority-explicit.stop' if explicit else 'priority-inherited.stop')
    s=socket.socket();s.bind(('127.0.0.1',0));port=s.getsockname()[1];s.close()
    env=dict(os.environ,D2_PROBE_OUTPUT=str(out),VASP_AI_HOME=str(OUT/'inherited-home'),DATA_DIR=str(OUT/'inherited-data'),OPENAI_API_KEY='synthetic-inherited',PYTHONPYCACHEPREFIX=str(OUT/'pycache'))
    args=[str(PYTHON),'-X','utf8',str(RUNTIME),'--kind','toolbox','--root',str(root),'--port',str(port),'--token','synthetic','--stop-file',str(stop)]
    if explicit:args+=['--ai-home',str(OUT/'explicit-home'),'--data-dir',str(OUT/'explicit-data')]
    p=test_support.start_process(args,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        wait(out,p);probe=json.loads(out.read_text(encoding='utf-8-sig'));prefix='explicit' if explicit else 'inherited';assert Path(probe['home'])==OUT/(prefix+'-home') and Path(probe['data'])==OUT/(prefix+'-data');assert probe['key_inherited'] and probe['dotenv_loaded'] and Path(probe['cwd'])==root/'backend'
    finally:stop.touch();p.wait(timeout=15)
record('normal-runtime-explicit-inherited-dotenv-priority',passed=True)
record('complete',passed=True,total=len(rows))
