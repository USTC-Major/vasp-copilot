// ============================================================
// Main Entry — MSW + React Query + Router
// ============================================================

import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { RouterProvider } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { router } from './router';
import './index.css';

// MSW — 开发环境启用 Mock Service Worker
async function enableMocking() {
  // 默认直连真实后端（/api/v1 -> Vite proxy -> 127.0.0.1:8000）。
  // 仅当显式开启（VITE_USE_MOCKS=true 或 URL 带 ?mock=1）时才启用 MSW 离线演示。
  const useMocks =
    import.meta.env.DEV &&
    (import.meta.env.VITE_USE_MOCKS === 'true' ||
      new URLSearchParams(window.location.search).has('mock'));
  if (useMocks) {
    const { worker } = await import('./mocks/browser');
    return worker.start({
      onUnhandledRequest: 'bypass',
    });
  }
  return Promise.resolve();
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 2,
      staleTime: 30 * 1000,
      refetchOnWindowFocus: false,
    },
    mutations: {
      retry: 1,
    },
  },
});

// Apple 风格主题（改动前后版本均已备份，可通过 design_backup 回退）
const appleTheme = {
  token: {
    colorPrimary: '#0071e3',
    colorInfo: '#0071e3',
    colorLink: '#0071e3',
    colorSuccess: '#34c759',
    colorWarning: '#ff9f0a',
    colorError: '#ff3b30',
    colorTextBase: '#1d1d1f',
    colorText: '#1d1d1f',
    colorTextSecondary: '#6e6e73',
    colorBgLayout: '#f5f5f7',
    colorBgContainer: '#ffffff',
    colorBorder: 'rgba(0,0,0,0.12)',
    colorBorderSecondary: 'rgba(0,0,0,0.06)',
    borderRadius: 14,
    borderRadiusLG: 20,
    borderRadiusSM: 10,
    fontSize: 15,
    fontFamily: '-apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text", "Segoe UI", Roboto, "Helvetica Neue", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", Arial, sans-serif',
    boxShadowTertiary: '0 12px 32px rgba(0,0,0,0.08)',
    boxShadowSecondary: '0 6px 20px rgba(0,0,0,0.06)',
  },
  components: {
    Button: {
      borderRadius: 999,
      controlHeight: 38,
      fontWeight: 500,
      primaryShadow: 'none',
    },
    Card: {
      borderRadiusLG: 20,
      paddingLG: 28,
    },
    Modal: { borderRadiusLG: 18 },
    Tag: { borderRadiusSM: 999 },
    Steps: { titleLineHeight: 2 },
  },
};

enableMocking().then(() => {
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <ConfigProvider
          locale={zhCN}
          theme={appleTheme}
        >
          <RouterProvider router={router} />
        </ConfigProvider>
      </QueryClientProvider>
    </StrictMode>
  );
});