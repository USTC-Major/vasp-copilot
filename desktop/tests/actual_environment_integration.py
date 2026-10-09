"""Opt-in actual installer + controller + business-stack integration.

This test can install locked dependencies from official PyPI. It is intentionally
excluded from desktop/test.ps1, whose controller tests remain network-free.
"""
from pathlib import Path
import argparse
import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_support

p=argparse.ArgumentParser()
p.add_argument('--output',type=Path,required=True)
p.add_argument('--cache-source',type=Path)
p.add_argument('--automatic', action='store_true', help='Discover the real base Python from PATH, without a manual selection')
a=p.parse_args()
ROOT=Path(__file__).resolve().parents[2]
OUT=a.output.resolve();OUT.mkdir(parents=True,exist_ok=True)
for name in ('ready.json','ready2.json','release','restart','result.json','integration-results.json'):
    (OUT/name).unlink(missing_ok=True)
HARNESS=OUT/'ControllerHarnessActualEnvironment.exe'
compiler=Path(os.environ['WINDIR'])/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
subprocess.run([str(compiler),'/nologo','/utf8output','/target:exe','/platform:x64',
    '/reference:System.Web.Extensions.dll','/out:'+str(HARNESS),
    str(ROOT/'launcher/windows/NativeProcess.cs'),str(ROOT/'launcher/windows/LauncherCore.cs'),
    str(ROOT/'desktop/tests/ControllerHarnessEnvironment.cs')],check=True)
cache=OUT/'state/runtime/full/pip-cache'
if a.cache_source and not cache.exists():shutil.copytree(a.cache_source,cache)
wrapper=OUT/'no-credentials-runtime.py'
wrapper.write_text('''import importlib.util,ipaddress,os,socket
import keyring
from keyring.backend import KeyringBackend
class NoCredentials(KeyringBackend):
    priority=1
    def get_password(self,*args):return None
    def set_password(self,*args):raise AssertionError('credential writes forbidden')
    def delete_password(self,*args):raise AssertionError('credential writes forbidden')
keyring.set_keyring(NoCredentials())
os.environ.pop('OPENAI_API_KEY',None)
import dotenv
dotenv.dotenv_values=lambda *a,**k:{}
dotenv.load_dotenv=lambda *a,**k:False
original=socket.socket.connect
def local_connect(self,address):
    if not ipaddress.ip_address(address[0]).is_loopback:raise AssertionError('external network forbidden in services')
    return original(self,address)
socket.socket.connect=local_connect
spec=importlib.util.spec_from_file_location('actual_runtime',RUNTIME_PATH)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
module.main()
'''.replace('RUNTIME_PATH',repr(str(ROOT/'launcher/runtime.py'))),encoding='utf-8')
env={k:v for k,v in os.environ.items() if k.upper() in {'SYSTEMROOT','SYSTEMDRIVE','WINDIR','PATH','PATHEXT','TEMP','TMP','COMSPEC','PROCESSOR_ARCHITECTURE','NUMBER_OF_PROCESSORS'}}
env.update(VASP_LAUNCHER_STATE_DIR=str(OUT/'state'),OPENAI_API_KEY='synthetic-controller-secret',PYTHONUTF8='1')
if a.automatic:
    env['PATH'] = str(Path(sys.executable).parent)
child=test_support.start_process([str(HARNESS),str(ROOT),'' if a.automatic else sys.executable,str(wrapper),str(OUT),'actual'],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
def wait(file,seconds):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        if file.exists():return json.loads(file.read_text(encoding='utf-8-sig'))
        if child.poll() is not None:raise AssertionError((OUT/'result.json').read_text(encoding='utf-8-sig'))
        time.sleep(.1)
    raise AssertionError('timeout: '+str(file))
def request(snapshot,path):
    port=next(s['Port'] for s in snapshot['Services'] if s['Key']=='web')
    c=http.client.HTTPConnection('127.0.0.1',port,timeout=15);c.request('GET',path)
    r=c.getresponse();status=r.status;data=json.loads(r.read());c.close();return status,data
first=wait(OUT/'ready.json',1200)
assert all(s['State']=='ready' and s['Pid']>0 for s in first['snapshot']['Services'])
status,bootstrap=request(first['snapshot'],'/api/v1/bootstrap')
assert status==200 and all(bootstrap[k] for k in ('ENABLE_LLM','ENABLE_POTCAR_ASSEMBLY','ENABLE_BAND_WORKFLOW'))
status,capabilities=request(first['snapshot'],'/ai/v1/diagnosis/capabilities')
assert status==200 and not capabilities['configured'] and capabilities['reason_code']=='AI_MODE_DIAGNOSIS_NOT_CONFIGURED'
markers=list((OUT/'state/runtime/full/environments').glob('*/.complete.json'))
assert len(markers)==1,markers
before={str(m):m.stat().st_mtime_ns for m in markers}
(OUT/'restart').touch();second=wait(OUT/'ready2.json',120)
assert second['selectedPython']==first['selectedPython']
assert all(s['State']=='ready' and s['Pid']>0 for s in second['snapshot']['Services'])
assert before=={str(m):m.stat().st_mtime_ns for m in markers}
request(second['snapshot'],'/api/v1/bootstrap')
(OUT/'release').touch();child.wait(timeout=30)
result=json.loads((OUT/'result.json').read_text(encoding='utf-8-sig'))
assert result['outcomes'][0]['ok'] and not result['running'] and result['parentSecretUnchanged']
for snapshot in (first['snapshot'],second['snapshot']):
    for service in snapshot['Services']:
        with socket.socket() as probe:assert probe.connect_ex(('127.0.0.1',service['Port']))!=0
evidence={'passed':True,'actualHelper':True,'autoPrepareEnvironment':True,'firstSelectedPython':first['selectedPython'],
    'secondSelectedPython':second['selectedPython'],'threeServicesReadyOnBothStarts':True,
    'secondStartWithProxyMarkerReusedOffline':True,'noCredentialsRuntimeAndExternalServiceNetworkBlocked':True,
    'bootstrapFullFlags':{k:bootstrap[k] for k in ('ENABLE_LLM','ENABLE_POTCAR_ASSEMBLY','ENABLE_BAND_WORKFLOW')},
    'diagnosisReason':capabilities['reason_code'],'allPortsReleased':True,'stages':result['stages']}
(OUT/'integration-results.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
print(json.dumps(evidence,ensure_ascii=False),flush=True)
print('Evidence: '+str(OUT),flush=True)
