// ============================================================
// 路由懒加载冒烟测试（F10）：6 条路由均可渲染
// ============================================================

import { render, screen } from '@testing-library/react';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { routes } from './router';
import { http, HttpResponse } from 'msw';
import { server } from './mocks/server';

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
  it('基础能量可从独立工具箱入口进入', async () => {
    server.use(http.get('/api/v1/toolbox/postprocessing/energy/collections', () => HttpResponse.json({ mode: 'toolbox', collections: [] })));
    renderRoute('/toolbox/postprocessing/energy');
    expect(await screen.findByRole('heading', { name: '基础能量' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '基础能量' })).toHaveAttribute('aria-current', 'page');
    expect(await screen.findByText('尚无能量比较集')).toBeInTheDocument();
  });
  it('表面构建页可从工具箱独立进入', async () => {
    server.use(http.get('/api/v1/toolbox/catalysis/drafts', () => HttpResponse.json({ mode: 'toolbox', drafts: [] })));
    renderRoute('/toolbox/surface-builder');
    expect(await screen.findByRole('heading', { name: '表面构建' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '使用合成 Pt 示例' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '表面构建' })).toHaveAttribute('aria-current', 'page');
  });
  it('POTCAR独立拼接页可渲染（懒加载）', async () => {
    server.use(http.get('/api/v1/toolbox/potcar/libraries', () => HttpResponse.json({ mode: 'toolbox', libraries: [], default_library_id: null, revision: 0 })));
    renderRoute('/toolbox/potcar/assemble');
    expect(await screen.findByRole('heading', { name: '拼接 POTCAR' })).toBeInTheDocument();
    expect(await screen.findByText('尚未登记赝势库')).toBeInTheDocument();
  });
  it('首页可渲染', async () => {
    renderRoute('/');
    expect(await screen.findAllByText(/VASP-Copilot/)).not.toHaveLength(0);
    expect(screen.getByText('文件写入和计算提交前，会请你确认。')).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '确认后执行' })).not.toBeInTheDocument();
    expect(screen.queryByText('任务级运行环境')).not.toBeInTheDocument();
    expect(screen.queryByText('运行环境以具体智能任务的 Real / Fake / None 标识为准')).not.toBeInTheDocument();
  });

  it('工作流页可渲染（懒加载）', async () => {
    renderRoute('/workflow');
    expect(await screen.findByText('上传结构文件', {}, { timeout: 15000 })).toBeInTheDocument();
  });

  it('诊断上传页可渲染（懒加载）', async () => {
    renderRoute('/diagnosis/upload');
    expect(await screen.findByRole('heading', { name: '诊断计算' })).toBeInTheDocument();
  });

  it('诊断结果页可渲染（懒加载）', async () => {
    renderRoute('/diagnosis/diag_demo_01');
    expect(await screen.findAllByText(/诊断/, {}, { timeout: 15000 })).not.toHaveLength(0);
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
