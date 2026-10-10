// Independent Chromium against the production package, no browser test library.
import { spawn } from 'node:child_process';
import { promises as fs } from 'node:fs';
import path from 'node:path';
const [browser, origin, output] = process.argv.slice(2);
const profile = path.join(output, 'browser-profile');
await fs.mkdir(profile, { recursive: true });
const child = spawn(browser, ['--headless=new', '--remote-debugging-port=0', `--user-data-dir=${profile}`,
  '--disable-background-networking', '--disable-extensions', '--disable-component-update', '--no-first-run', '--no-default-browser-check',
  '--no-proxy-server', '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1, EXCLUDE localhost', 'about:blank'], { windowsHide: true, stdio: 'ignore' });
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const require = (ok, text) => { if (!ok) throw new Error(text); };
let socket;
try {
  let port;
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) {
    try { port = Number((await fs.readFile(path.join(profile, 'DevToolsActivePort'), 'utf8')).split('\n')[0]); break; } catch { await sleep(100); }
  }
  require(port, 'Disposable browser did not expose DevTools');
  const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  const target = targets.find(t => t.type === 'page');
  require(target, 'Disposable browser page absent');
  socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
  let id = 0;
  const pending = new Map();
  const call = (method, params = {}) => new Promise((resolve, reject) => {
    const request = ++id;
    const timer = setTimeout(() => { pending.delete(request); reject(new Error(`${method} timed out`)); }, 15000);
    pending.set(request, { resolve, reject, timer }); socket.send(JSON.stringify({ id: request, method, params }));
  });
  socket.onmessage = event => {
    const message = JSON.parse(event.data);
    if (message.id && pending.has(message.id)) {
      const action = pending.get(message.id); pending.delete(message.id); clearTimeout(action.timer);
      if (message.error) action.reject(new Error(message.error.message)); else action.resolve(message.result);
    } else if (message.method === 'Fetch.requestPaused') {
      const url = new URL(message.params.request.url);
      const local = url.origin === origin || ['data:', 'blob:', 'about:'].includes(url.protocol);
      call(local ? 'Fetch.continueRequest' : 'Fetch.failRequest', { requestId: message.params.requestId, ...(local ? {} : { errorReason: 'BlockedByClient' }) }).catch(() => {});
    }
  };
  await call('Page.enable'); await call('Runtime.enable');
  await call('Fetch.enable', { patterns: [{ urlPattern: '*', requestStage: 'Request' }] });
  const evaluate = async expression => {
    const result = await call('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
    return result.result.value;
  };
  const wait = async (expression, text) => {
    const end = Date.now() + 20000;
    while (Date.now() < end) { if (await evaluate(`Boolean(${expression})`)) return; await sleep(100); }
    throw new Error(text);
  };
  const capture = async name => {
    await evaluate('document.fonts.ready'); await sleep(350);
    const result = await call('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
    await fs.writeFile(path.join(output, name), Buffer.from(result.data, 'base64'));
  };
  const input = (selector, value) => evaluate(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(e,${JSON.stringify(value)});e.dispatchEvent(new Event('input',{bubbles:true}));})()`);
  await call('Emulation.setDeviceMetricsOverride', { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false });
  for (const [route, hash] of [['/ai/settings','#settings-models'], ['/toolbox/settings','#settings-execution']]) {
    await call('Page.navigate', { url: origin + route });
    await wait(`location.pathname==='/settings' && location.hash===${JSON.stringify(hash)} && document.querySelector('button[type=submit]')`, 'Legacy settings route failed');
    require(await evaluate(`document.querySelectorAll('a[aria-label="设置"]').length===1 && !Array.from(document.querySelectorAll('nav a')).some(a=>a.textContent==='执行设置') && !window.chrome?.webview`), 'Browser settings entries incorrect');
    require(await evaluate(`['启动与运行','模型与材料','超算与执行'].every(t=>Array.from(document.querySelectorAll('h2')).filter(h=>h.textContent===t).length===1) && document.body.textContent.includes('此项需在桌面程序中操作')`), 'Browser sections or no-host downgrade missing');
  }
  await evaluate(`window.scrollTo(0,0)`);
  await capture('browser-dark-wide.png');
  await evaluate(`document.querySelector('button[aria-label="切换浅色主题"]').click()`);
  await wait(`document.querySelector('.scientific-shell').dataset.workflowTheme==='light'`, 'Light theme did not apply');
  await call('Emulation.setDeviceMetricsOverride', { width: 960, height: 700, deviceScaleFactor: 1.5, mobile: false });
  await evaluate(`document.getElementById('settings-execution').scrollIntoView()`);
  await capture('browser-light-narrow.png');
  await input('#max_jobs','9');
  await evaluate(`document.querySelector('a[aria-label="生成工作流"]').click()`);
  await wait(`document.body.textContent.includes('设置尚未保存')`, 'Dirty fallback navigation not guarded');
  await evaluate(`Array.from(document.querySelectorAll('button')).find(b=>b.textContent.split('').filter(c=>c.trim()).join('')==='继续编辑').click()`);
  require(await evaluate(`location.pathname==='/settings' && document.querySelector('#max_jobs').value==='9'`), 'Cancel navigation discarded edits');
  await evaluate(`document.querySelector('button[type=submit]').click()`);
  await wait(`document.body.textContent.includes('Toolbox 执行设置已保存')`, 'Browser execution save failed');
  require(await evaluate(`fetch('/api/v1/toolbox/settings').then(r=>r.json()).then(r=>r.settings.max_jobs===9 && r.settings.ssh.host==='')`), 'Browser saved wrong values or used real SSH');
  await evaluate(`Array.from(document.querySelectorAll('button')).find(b=>b.textContent.split('').filter(c=>c.trim()).join('')==='测试SSH连接').click()`);
  await wait(`document.body.textContent.includes('未配置') || document.body.textContent.includes('填写')`, 'Saved unconfigured SSH feedback absent');
  await evaluate(`document.querySelector('a[aria-label="生成工作流"]').click()`);
  await wait(`document.querySelector('input[type=file]')`, 'Browser workflow unavailable');
  const poscar = 'Si synthetic\n1.0\n5 0 0\n0 5 0\n0 0 5\nSi\n1\nDirect\n0 0 0\n';
  await evaluate(`window.__dt02Document='browser-document';const d=new DataTransfer();d.items.add(new File([${JSON.stringify(poscar)}],'Si.POSCAR'));const f=document.querySelector('input[type=file]');f.files=d.files;f.dispatchEvent(new Event('change',{bubbles:true}));`);
  await wait(`document.querySelector('input[aria-label="样品名称"]')`, 'Browser synthetic upload failed');
  await input('input[aria-label="样品名称"]','Browser retained draft');
  await evaluate(`document.querySelector('a[aria-label="设置"]').click()`);
  await wait(`location.pathname==='/settings' && document.querySelector('button[type=submit]')`, 'Browser center unavailable');
  await evaluate(`document.querySelector('a[aria-label="生成工作流"]').click()`);
  await wait(`document.querySelector('input[aria-label="样品名称"]')?.value==='Browser retained draft' && window.__dt02Document==='browser-document'`, 'Browser settings discarded workflow draft');
  await fs.writeFile(path.join(output,'browser-results.json'),JSON.stringify({ passed:true, actualIndependentBrowser:true, productionPackage:true,
    browserSettingsEntries:1, sidebarExecutionEntries:0, legacyRoutes:true, threeSections:true, toolboxSaveAndUnconfiguredSshTest:true, dirtyGuard:true, draftRetained:true, themesAndDpi:true, externalRequestsBlocked:true },null,2));
} finally {
  socket?.close(); child.kill();
  await Promise.race([new Promise(resolve=>child.once('exit',resolve)),sleep(3000)]);
}
