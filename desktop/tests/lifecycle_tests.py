"""V3 targeted checks, each with independent HOME/data/preferences. No UI/model calls."""
from pathlib import Path
import json, os, socket, subprocess, sys, time
import test_support

ROOT=Path(__file__).resolve().parents[2]
OUT=test_support.output('lifecycle')
OUT.mkdir(parents=True)
rows=[]

def run(name, mode='start', root=ROOT, python=sys.executable):
    directory=OUT/name; directory.mkdir()
    env=dict(os.environ,VASP_LAUNCHER_STATE_DIR=str(directory/'state'),PYTHONPYCACHEPREFIX=str(OUT/'pycache'))
    args=[str(test_support.harness('ControllerHarnessLifecycle.exe')),str(root),str(python),str(ROOT/'launcher/runtime.py'),str(directory),mode]
    process=test_support.start_process(args,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try: process.wait(timeout=90)
    except BaseException:
        process.terminate(); process.wait(timeout=10); raise
    result=json.loads((directory/'result.json').read_text(encoding='utf-8-sig'))
    for service in result['snapshot']['Services']:
        if service['State']=='disabled': continue
        with socket.socket() as probe: assert probe.connect_ex(('127.0.0.1',service['Port']))!=0
    return result,directory

def record(name, **values):
    rows.append(dict(name=name,passed=True,**values))
    (OUT/'results.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(rows[-1],ensure_ascii=False),flush=True)

result,directory=run('all-owned-services-exit-and-retry','crash-all')
assert result['ok'] and not result['running']
broken=json.loads((directory/'failed.json').read_text(encoding='utf-8-sig'))
recovered=json.loads((directory/'ready2.json').read_text(encoding='utf-8-sig'))
assert not broken['running'] and broken['errorObserved']
assert all(s['State']=='ready' for s in recovered['snapshot']['Services'] if s['State']!='disabled')
record('all-owned-services-exit-observed-with-IsRunning-false-and-retry',recovered=True,ports_released=True)

result,directory=run('missing-python',python=OUT/'does-not-exist/python.exe')
assert not result['ok'] and not result['running'] and 'Python' in result['error']
record('explicit-missing-python-fails-without-services',error=result['error'])

result,directory=run('missing-installation',root=OUT/'does-not-exist')
assert not result['ok'] and not result['running'] and '安装目录' in result['error']
record('invalid-installation-fails-without-services',error=result['error'])

result,directory=run('valid-after-invalid-configuration')
assert result['ok'] and not result['running']
record('valid-configuration-recovery',ports_released=True)
record('complete',total=len(rows))
