// ============================================================
// 路由懒加载冒烟测试（F10）：6 条路由均可渲染
// ============================================================

import { render, screen } from '@testing-library/react';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { routes } from './router';

const renderRoute = (path: string) => {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  );
};

describe('路由懒加载', () => {
  it('首页可渲染', async () => {
    renderRoute('/');
    expect(await screen.findAllByText(/VASP-Copilot/)).not.toHaveLength(0);
  });

  it('工作流页可渲染（懒加载）', async () => {
    renderRoute('/workflow');
    expect(await screen.findByText('上传结构文件')).toBeInTheDocument();
  });

  it('诊断上传页可渲染（懒加载）', async () => {
    renderRoute('/diagnosis/upload');
    expect(await screen.findByText('诊断计算 (VASP-Doctor+)')).toBeInTheDocument();
  });

  it('诊断结果页可渲染（懒加载）', async () => {
    renderRoute('/diagnosis/diag_demo_01');
    expect(await screen.findAllByText(/诊断/)).not.toHaveLength(0);
  });

  it('HPC 部署页可渲染（懒加载）', async () => {
    renderRoute('/hpc/deploy');
    expect(await screen.findAllByText(/部署/)).not.toHaveLength(0);
  });

  it('远程作业页可渲染（懒加载）', async () => {
    renderRoute('/hpc/jobs/rjob_01');
    expect(await screen.findAllByText(/作业/)).not.toHaveLength(0);
  });
});
