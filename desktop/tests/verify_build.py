"""Verify build provenance and actual files with only Python's standard library."""
from pathlib import Path
import hashlib
import json

ROOT = Path(__file__).resolve().parents[2]
manifest = json.loads((ROOT/'desktop/dist/build-manifest.json').read_text(encoding='utf-8-sig'))
expected = {'VASP-Copilot-Desktop-V3.exe', 'VASP-Copilot-Desktop-V3.exe.config',
            'Microsoft.Web.WebView2.Core.dll', 'Microsoft.Web.WebView2.WinForms.dll',
            'WebView2Loader.dll', 'WebView2-LICENSE.txt', 'WebView2-NOTICE.txt'}
assert set(manifest['output']) == expected
assert manifest['sdkVersion'] == '1.0.3650.58' and manifest['frontendMock'] is False
required = {'launcher/runtime.py', 'launcher/environment.py',
            'backend/requirements-runtime.txt', 'backend/requirements-win-cp311-x64.lock', 'backend/requirements-win-cp312-x64.lock', 'desktop/assets/app-icon.svg', 'desktop/assets/app-icon.png',
            'desktop/assets/app-icon.ico', 'desktop/build.ps1', 'desktop/restore-sdk.ps1'}
assert required <= set(manifest['inputs'])
assert any(name.startswith('frontend/src/') for name in manifest['inputs'])
for folder, rows in [(ROOT, manifest['inputs']), (ROOT/'desktop/dist', manifest['output']),
                     (ROOT/'frontend/dist', manifest['frontendDist'])]:
    for name, expected_hash in rows.items():
        relative = Path(name)
        assert not relative.is_absolute() and '..' not in relative.parts
        assert hashlib.sha256((folder/relative).read_bytes()).hexdigest() == expected_hash, name
lock = json.loads((ROOT/'desktop/webview2.lock.json').read_text(encoding='utf-8'))
for source, output in [('lib/net462/Microsoft.Web.WebView2.Core.dll', 'Microsoft.Web.WebView2.Core.dll'),
                       ('lib/net462/Microsoft.Web.WebView2.WinForms.dll', 'Microsoft.Web.WebView2.WinForms.dll'),
                       ('runtimes/win-x64/native/WebView2Loader.dll', 'WebView2Loader.dll'),
                       ('LICENSE.txt', 'WebView2-LICENSE.txt'), ('NOTICE.txt', 'WebView2-NOTICE.txt')]:
    assert manifest['output'][output] == lock['files'][source]
js = '\n'.join(path.read_text(encoding='utf-8') for path in (ROOT/'frontend/dist/assets').glob('*.js'))
assert not any(marker in js for marker in ['navigator.serviceWorker', 'mockServiceWorker', 'onUnhandledRequest', 'setupWorker'])
print(json.dumps({'passed': True, 'head': manifest['head'], 'inputs': len(manifest['inputs']),
                  'output': len(manifest['output']), 'frontendDist': len(manifest['frontendDist']),
                  'productionMocks': False}))
