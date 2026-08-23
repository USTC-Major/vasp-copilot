// ============================================================
// Vitest 全局测试设置
// ============================================================
/// <reference types="vitest/globals" />

import * as jestDomMatchers from '@testing-library/jest-dom/matchers';
import { server } from './mocks/server';

// 直接在运行器的全局 expect 上注册 jest-dom 匹配器。
expect.extend(jestDomMatchers);

// 必须在模块顶层先启动 MSW，再包 fetch：
// 否则相对路径请求会先到达 MSW 的 Node 拦截器（无法解析相对 URL）而失败。
// 顺序：server.listen() 接管原生 fetch → 再用包装层把相对路径解析为绝对
// URL（以 jsdom 当前 URL 为 base，与 MSW 相对路径 handler 的解析基准一致）→
// 包装层成为入口，绝对 URL 交给 MSW 拦截。
server.listen({ onUnhandledRequest: 'bypass' });
const mswFetch = globalThis.fetch;
globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
  if (typeof input === 'string' && input.startsWith('/')) {
    return mswFetch(new URL(input, window.location.href), init);
  }
  return mswFetch(input, init);
}) as typeof fetch;

afterEach(() => server.resetHandlers());
afterAll(() => server.close());

// ---- jsdom 缺失浏览器 API 的最小 polyfill（antd 需要）----
if (typeof window !== 'undefined') {
  if (!window.matchMedia) {
    window.matchMedia = ((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    })) as typeof window.matchMedia;
  }

  if (!('ResizeObserver' in window)) {
    class ResizeObserverStub {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
    (window as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;
  }

  if (!window.scrollTo) {
    window.scrollTo = (() => {}) as typeof window.scrollTo;
  }
}
