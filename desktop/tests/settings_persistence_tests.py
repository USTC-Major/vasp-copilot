"""Real settings form, independent-process restore, migration and I/O failures.

All preferences and UI evidence stay inside an explicit synthetic test directory.
No controller/business service, real credential or normal profile is accessed.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from test_support import ROOT, output

OUT = output('settings-persistence')
OUT.mkdir(parents=True)
BIN = OUT/'bin'
BIN.mkdir()
compiler = Path(os.environ['WINDIR'])/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
sources = [ROOT/'launcher/windows/NativeProcess.cs', ROOT/'launcher/windows/LauncherCore.cs']
common = [str(compiler), '/nologo', '/utf8output', '/target:exe', '/platform:x64', '/reference:System.Web.Extensions.dll']
unit = BIN/'PreferencesTests.exe'
subprocess.run(common+['/out:'+str(unit)]+list(map(str,sources))+[str(ROOT/'desktop/tests/PreferencesTests.cs')],check=True)
subprocess.run([str(unit), str(OUT/'store-tests')],check=True,timeout=40)
for name in ('Microsoft.Web.WebView2.Core.dll','Microsoft.Web.WebView2.WinForms.dll','WebView2Loader.dll'):
    shutil.copyfile(ROOT/'desktop/dist'/name,BIN/name)
ui = BIN/'SettingsHarness.exe'
subprocess.run(common+['/main:DesktopSettingsHarness','/reference:System.Windows.Forms.dll','/reference:System.Drawing.dll',
    '/reference:'+str(BIN/'Microsoft.Web.WebView2.Core.dll'),'/reference:'+str(BIN/'Microsoft.Web.WebView2.WinForms.dll'),
    '/resource:'+str(ROOT/'desktop/assets/app-icon.png')+',VaspCopilot.Brand', '/out:'+str(ui)]+
    list(map(str,sources))+[str(ROOT/'desktop/DesktopApp.cs'),str(ROOT/'desktop/tests/DesktopSettingsHarness.cs')],check=True)
shutil.copyfile(ROOT/'desktop/app.config',Path(str(ui)+'.config'))
profile = OUT/'中文 设置 profile'
profile.mkdir()
root = str(OUT/'中文 安装目录 with spaces')
python = str(Path(sys.executable))
results = []
for expected_python in (python, ''):
    for action in ('save','reopen'):
        result = subprocess.run([str(ui),action,str(profile),root,expected_python],check=True,timeout=30,capture_output=True,text=True,encoding='utf-8')
        results.append(json.loads(result.stdout))
    saved = json.loads((profile/'preferences.json').read_text(encoding='utf-8-sig'))
    assert saved['RootDirectory']==root and saved['PythonExecutable']==expected_python and saved['EnableAi'] is False
summary={'passed': True,'realFormAcrossProcesses':results,'manualAndAutomaticRestored':True,'aiFalseRestored':True,'syntheticProfilesOnly':True}
package = Path(json.loads((ROOT/'desktop/dist/last-package.json').read_text(encoding='utf-8-sig'))['directory'])
probe = BIN/'PackagedSettingsProbe.exe'
subprocess.run(common+['/reference:System.Windows.Forms.dll','/reference:System.Drawing.dll','/out:'+str(probe),str(ROOT/'desktop/tests/PackagedSettingsProbe.cs')],check=True)
pack_profile = OUT/'发布包 中文 设置'
pack_profile.mkdir()
pack_results=[]
for expected_python in (python,''):
    for action in ('save','reopen'):
        result=subprocess.run([str(probe),str(package/'VASP-Copilot.exe'),action,str(pack_profile),str(package),expected_python],check=True,timeout=30,capture_output=True,text=True,encoding='utf-8')
        pack_results.append(json.loads(result.stdout))
summary['actualPackagedFormAcrossProcesses']=pack_results
(OUT/'results.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding='utf-8')
print('Settings persistence evidence: '+str(OUT/'results.json'))
