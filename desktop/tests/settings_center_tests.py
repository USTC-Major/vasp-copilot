"""Actual shipped native shell, production frontend and isolated business stack."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from test_support import ROOT, output, start_process
parser = argparse.ArgumentParser()
parser.add_argument('--browser', type=Path, help='Explicit disposable Chromium executable for independent browser checks')
args = parser.parse_args()

OUT = output('settings-center')
BIN = OUT/'bin'
BIN.mkdir(parents=True)
package = Path(json.loads((ROOT/'desktop/dist/last-package.json').read_text(encoding='utf-8-sig'))['directory'])
for name in ('Microsoft.Web.WebView2.Core.dll', 'Microsoft.Web.WebView2.WinForms.dll', 'WebView2Loader.dll'):
    shutil.copyfile(package/name, BIN/name)
compiler = Path(os.environ['WINDIR'])/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
probe = BIN/'PackagedCenterProbe.exe'
subprocess.run([str(compiler), '/nologo', '/utf8output', '/target:exe', '/platform:x64',
    '/reference:System.Web.Extensions.dll', '/reference:System.Windows.Forms.dll', '/reference:System.Drawing.dll',
    '/reference:'+str(BIN/'Microsoft.Web.WebView2.Core.dll'), '/reference:'+str(BIN/'Microsoft.Web.WebView2.WinForms.dll'),
    '/out:'+str(probe), str(ROOT/'desktop/tests/PackagedCenterProbe.cs')], check=True)
shutil.copyfile(ROOT/'desktop/app.config', Path(str(probe)+'.config'))
results = []
browser_result = None
for scenario in ('ready', 'failure', 'webview-missing') + (('browser',) if args.browser else ()):
    state = OUT/scenario
    env = {k:v for k,v in os.environ.items() if k.upper() in {'SYSTEMROOT','SYSTEMDRIVE','WINDIR','PATH','PATHEXT','TEMP','TMP','COMSPEC','PROCESSOR_ARCHITECTURE','NUMBER_OF_PROCESSORS'}}
    env.update(VASP_LAUNCHER_STATE_DIR=str(state), PYTHONUTF8='1', PYTHONPYCACHEPREFIX=str(OUT/'pycache'),
        WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS='--disable-background-networking --no-first-run')
    if scenario == 'webview-missing':
        env['VASP_D2_BROWSER_FOLDER'] = str(state/'absent-owned-webview')
    child = start_process([str(probe),str(package),sys.executable,str(state),scenario],cwd=OUT,env=env)
    if scenario == 'browser':
        origin_file = state/'browser-origin.json'
        deadline = time.monotonic()+65
        while not origin_file.exists() and child.poll() is None and time.monotonic()<deadline:
            time.sleep(.1)
        if origin_file.exists():
            origin = json.loads(origin_file.read_text(encoding='utf-8-sig'))['origin']
            try:
                subprocess.run([shutil.which('node'),str(ROOT/'desktop/tests/browser_settings_center.mjs'),str(args.browser),origin,str(state)],check=True,timeout=110)
                browser_result = json.loads((state/'browser-results.json').read_text(encoding='utf-8'))
            finally:
                (state/'browser-done').touch()
    exit_code = child.wait(timeout=170)
    result = json.loads((state/'results.json').read_text(encoding='utf-8-sig'))
    assert exit_code == 0, result
    assert result['passed'] or result['missingRuntime'], result
    results.append(result)
summary = {'passed':all(r['passed'] for r in results), 'actualWebViewUnavailable':any(r['missingRuntime'] for r in results),
    'scenarios':results, 'independentBrowser':browser_result, 'package':str(package), 'syntheticProfilesOnly':True}
(OUT/'results.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print('Actual settings center evidence: '+str(OUT/'results.json'))
