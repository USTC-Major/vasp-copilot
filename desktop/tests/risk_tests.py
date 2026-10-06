"""D2 risk-focused integration tests. Synthetic installations only; no real science or keys."""
from pathlib import Path
import hashlib, http.client, json, os, socket, subprocess, sys, time
import test_support

ROOT = Path(__file__).resolve().parents[2]
OUT = test_support.output("automated")
PYTHON = Path(sys.executable)
RUNTIME = ROOT / "launcher/runtime.py"
HARNESS = test_support.harness("ControllerHarness.exe")
OUT.mkdir(parents=True, exist_ok=True)
installation = OUT / "中文 安装目录"
for directory in ("backend/app", "backend/ai_mode", "frontend/dist"):
    (installation / directory).mkdir(parents=True, exist_ok=True)
app = '''from fastapi import FastAPI
from contextlib import asynccontextmanager
from pathlib import Path
import json,os,keyring
@asynccontextmanager
async def lifespan(app):
    home=Path(os.environ['VASP_AI_HOME']);home.mkdir(parents=True,exist_ok=True)
    f=home/'persistent.txt';f.write_text(str(int(f.read_text(encoding='utf-8-sig'))+1) if f.exists() else '1')
    blocked=0
    for operation in (lambda:keyring.set_password('d2-fake','fake','fake'),lambda:keyring.delete_password('d2-fake','fake')):
        try:operation()
        except RuntimeError:blocked+=1
    (home/'isolation.json').write_text(json.dumps({'dotenv_absent':'D2_DOTENV_TRAP' not in os.environ,'env_secret_absent':'OPENAI_API_KEY' not in os.environ,'keyring_read_empty':keyring.get_password('d2-fake','fake') is None,'keyring_writes_blocked':blocked==2,'cwd':os.getcwd(),'data':os.environ['DATA_DIR']}))
    yield
app=FastAPI(lifespan=lifespan)
@app.get('/health')
def health():return {'status':'ok'}
'''
(installation / "backend/app/main.py").write_text(app, encoding="utf-8")
(installation / "backend/ai_mode/server.py").write_text("from fastapi import FastAPI\napp=FastAPI()\n@app.get('/ai/v1/ping')\ndef ping():return {'mode':'ai','version':'0.3.0','enabled':True}\n", encoding="utf-8")
(installation / "frontend/dist/index.html").write_text("<html><body>D2 synthetic fixture</body></html>")
(installation / "backend/.env").write_text("D2_DOTENV_TRAP=synthetic-only\nOPENAI_API_KEY=synthetic-key-never-used\n")
report = []
def record(name, **data):
    report.append(dict(name=name, **data)); (OUT / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report[-1], ensure_ascii=False), flush=True)
def wait_for(path, process=None, timeout=70):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if path.exists():return
        if process and process.poll() is not None:raise AssertionError(f"process exited before {path.name}")
        time.sleep(.05)
    raise AssertionError(f"timeout: {path.name}")
def start(name, runtime=RUNTIME, python=str(PYTHON), mode="start", root=installation, extra_env=None):
    directory=OUT/name;directory.mkdir(exist_ok=True)
    for f in ("ready.json","result.json","release"):
        (directory/f).unlink(missing_ok=True)
    env=dict(os.environ, VASP_LAUNCHER_STATE_DIR=str(directory/"state"), OPENAI_API_KEY="synthetic-inherited-trap", PYTHONPYCACHEPREFIX=str(OUT/"pycache"))
    if extra_env:env.update(extra_env)
    process=test_support.start_process([str(HARNESS),str(root),python,str(runtime),str(directory),mode],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    return process,directory
def finish(process,directory):
    process.wait(timeout=90);return json.loads((directory/"result.json").read_text(encoding="utf-8"))
def listeners_released(result):
    for service in result['snapshot']['Services']:
        if service['State']=='disabled':continue
        probe=socket.socket();assert probe.connect_ex(('127.0.0.1',service['Port']))!=0;probe.close()
def wrapper(name, injection):
    file=OUT/(name+".py")
    file.write_text("import importlib.util,sys,time,json,errno\nfrom pathlib import Path\nspec=importlib.util.spec_from_file_location('d2runtime',"+repr(str(RUNTIME))+")\nm=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)\noriginal=m.run\ndef run(args):\n"+injection+"\n    return original(args)\nm.run=run\nm.main()\n",encoding="utf-8")
    return file

# Real Python dependency imports, synthetic business, persistence and keyring firewall.
p,d=start("restart-persistence",mode="restart");r=finish(p,d);assert r['ok'];listeners_released(r)
assert (d/"home/persistent.txt").read_text(encoding='utf-8-sig')=="2"
isolation=json.loads((d/"home/isolation.json").read_text(encoding='utf-8-sig'));assert all(isolation[k] for k in ('dotenv_absent','env_secret_absent','keyring_read_empty','keyring_writes_blocked'))
assert Path(isolation['cwd'])==installation/"backend";assert Path(isolation['data'])==d/"data"
prefs=json.loads((d/"state/preferences.json").read_text(encoding="utf-8-sig"));assert Path(prefs['RootDirectory'])==installation
record("restart-persistence-and-isolation",passed=True,restarts=2,isolated=isolation)

# A root .venv exists but lacks dependencies: search must continue to VIRTUAL_ENV.
subprocess.run([str(PYTHON),"-m","venv","--without-pip",str(installation/".venv")],check=True,stdout=subprocess.DEVNULL)
p,d=start("python-fallback",python="",extra_env={'VIRTUAL_ENV':str(PYTHON.parents[1])});r=finish(p,d);assert r['ok'];ready=json.loads((d/"ready.json").read_text(encoding="utf-8"));assert Path(ready['selectedPython'])==PYTHON;listeners_released(r)
record("dependency-insufficient-candidate-fallback",passed=True,selected=ready['selectedPython'])

# Already occupied default ports must remain owned by the external test process.
occupied=[]
try:
    for port in (8000,5173,8500):
        s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        try:s.bind(('127.0.0.1',port));s.listen();occupied.append(s)
        except OSError:s.close()
    p,d=start("existing-port-occupants",mode="ai");r=finish(p,d);assert r['ok'];listeners_released(r)
    assert all(s.fileno()!=-1 for s in occupied)
    record("existing-port-occupants-preserved",passed=True,occupied=[s.getsockname()[1] for s in occupied])
finally:
    for s in occupied:s.close()

# Deterministic external bind race after the internal free-port choice. One conflict, then recovery.
race_dir=OUT/"race-signals";race_dir.mkdir(exist_ok=True)
race_runtime=wrapper("race-runtime", "    if args.kind=='toolbox' and not Path("+repr(str(race_dir/"first-done"))+").exists():\n        signal=Path("+repr(str(race_dir/"port.json"))+");signal.write_text(json.dumps({'port':args.port}))\n        release=Path("+repr(str(race_dir/"release"))+")\n        while not release.exists():time.sleep(.02)\n        Path("+repr(str(race_dir/"first-done"))+").touch()")
for f in race_dir.iterdir():f.unlink()
p,d=start("deterministic-bind-race",runtime=race_runtime);wait_for(race_dir/"port.json",p)
race=socket.socket();race.bind(('127.0.0.1',json.loads((race_dir/"port.json").read_text(encoding='utf-8-sig'))['port']));race.listen();(race_dir/"release").touch()
try:
    r=finish(p,d);assert r['ok'] and r['attempts']==2,r;listeners_released(r);assert race.fileno()!=-1
    record("deterministic-bind-race-recovery",passed=True,attempts=r['attempts'])
finally:race.close()

# Web failure after healthy Toolbox must clean the partial stack. Business OSError is not bind conflict.
failure_runtime=wrapper("failure-runtime", "    if args.kind=='web':raise OSError(errno.EADDRINUSE,'synthetic business error')")
p,d=start("partial-stack-failure",runtime=failure_runtime);r=finish(p,d);assert not r['ok'] and not r['running'] and r['attempts']==1,r;listeners_released(r)
record("partial-stack-cleanup-no-business-error-retry",passed=True,attempts=r['attempts'])

# Forged identity from an owned process must not count as ready; no conflict retry.
forged=wrapper("forged-runtime", "    identity=m.identity\n    def wrong_identity(a):\n        value=identity(a);value['token']='forged';return value\n    m.identity=wrong_identity")
p,d=start("forged-health",runtime=forged);r=finish(p,d);assert not r['ok'] and not r['running'] and r['attempts']==1,r;listeners_released(r)
record("forged-health-rejected-and-cleaned",passed=True,attempts=r['attempts'])

# Full business stack smoke from the candidate itself, isolated and with AI service on (no calls).
p,d=start("actual-business-smoke",root=ROOT,mode="ai");r=finish(p,d);assert r['ok'];listeners_released(r)
record("actual-business-stack-isolated-smoke",passed=True,services=[s['Key'] for s in r['snapshot']['Services']])

record("complete",passed=True,total=len(report))
