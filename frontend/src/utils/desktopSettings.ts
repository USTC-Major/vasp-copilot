import { useSyncExternalStore } from 'react';

type Bridge = {
  postMessage: (message: unknown) => void;
  addEventListener: (type: 'message', listener: (event: MessageEvent) => void) => void;
  removeEventListener: (type: 'message', listener: (event: MessageEvent) => void) => void;
};
const bridge = () => (window as Window & { chrome?: { webview?: Bridge } }).chrome?.webview;
let nonce = '';
const listeners = new Set<() => void>();
const publish = () => listeners.forEach(listener => listener());
export const useDesktopSettings = () => useSyncExternalStore(
  listener => { listeners.add(listener); return () => { listeners.delete(listener); }; },
  () => nonce.length > 0,
  () => false,
);

/** The host grants a document-scoped capability, never startup values or secrets. */
export function connectDesktopSettings(openSettings: () => void) {
  const host = bridge();
  if (!host) return () => {};
  const receive = (event: MessageEvent) => {
    const message = event.data;
    if (!message || typeof message !== 'object' || Object.keys(message).sort().join(',') !== 'action,nonce,type,version'
      || message.type !== 'vasp-desktop-settings' || message.version !== 1 || typeof message.nonce !== 'string'
      || !/^[a-f0-9]{32}$/.test(message.nonce)) return;
    if (message.action === 'capabilities') {
      nonce = message.nonce; publish();
      host.postMessage({ type: 'vasp-desktop-settings', version: 1, action: 'ready', nonce });
    } else if (message.action === 'open-settings' && message.nonce === nonce && nonce) openSettings();
  };
  host.addEventListener('message', receive);
  host.postMessage({ type: 'vasp-desktop-settings', version: 1, action: 'query-capabilities' });
  return () => { host.removeEventListener('message', receive); nonce = ''; publish(); };
}

export function openDesktopLaunchSettings() {
  if (!nonce || !bridge()) return;
  bridge()!.postMessage({ type: 'vasp-desktop-settings', version: 1, action: 'open-launch-settings', nonce });
}
