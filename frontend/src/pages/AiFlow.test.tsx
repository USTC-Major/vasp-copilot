// ============================================================
// M12 前端整合测试：项目列表 → 新建项目 → 任务对话 → 进度页 → 设置页
// （数据来自 MSW 演示后端 aiDemo / aiSettingsHandlers）
// ============================================================

import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { routes } from '../router';
import { aiDemo } from '../mocks/aiStore';

function renderPath(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  );
}

describe('AI 前端整合（M12）', () => {
  beforeEach(() => {
    aiDemo.reset();
  });

  it('项目列表页渲染种子项目与上下文/等待空位信息', async () => {
    renderPath('/ai');
    expect(await screen.findByText('Fe2O3 优化工程')).toBeInTheDocument();

    // 新建入口在列表最上方（「＋」卡片，非右上角按钮）
    expect(screen.getAllByText('新建项目').length).toBeGreaterThan(0);
    expect(screen.getByText('等待空位队列')).toBeInTheDocument();
    expect(screen.getAllByText('排队中').length).toBeGreaterThan(0);
  });

  it('新建项目 → 进入项目页（任务栏/聊天/额外设置入口可见）', async () => {
    const user = userEvent.setup();
    renderPath('/ai');
    await user.click(await screen.findByText('新建项目'));
    await user.type(await screen.findByPlaceholderText('如：Fe2O3 表面能研究'), 'NaCl 缺陷工程');
    await user.click(screen.getByRole('button', { name: /创\s*建/ }));
    expect(await screen.findByRole('button', { name: /新建计算任务/ }, { timeout: 15000 })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /额外设置/ })).toBeInTheDocument();
    // 新建计算任务需绑定本地/超算工作区（M11 设计项 3）
    await user.click(screen.getByRole('button', { name: /新建计算任务/ }));
    expect(await screen.findByText('本地工作区（必填 · 可复用）')).toBeInTheDocument();
    expect(screen.getByText('超算工作区（可留空）')).toBeInTheDocument();
  });

  it('项目聊天：给演示任务发消息得到回复并出现规划', async () => {
    const user = userEvent.setup();
    renderPath('/ai/projects/prj_001');
    expect(await screen.findByText('结构优化 + 静态 + DOS')).toBeInTheDocument();
    await user.clear(await screen.findByPlaceholderText(/描述计算需求/));
    await user.type(screen.getByPlaceholderText(/描述计算需求/), '对 NaCl 结构做 relax → static → dos 计算');
    await user.click(screen.getByRole('button', { name: /发送/ }));
    expect(await screen.findByText(/已生成计算计划/)).toBeInTheDocument();
  });
  it('M41 停止：处理中可点「停止」，结束后可继续编辑与发送', async () => {
    const user = userEvent.setup();
    renderPath('/ai/projects/prj_001');
    expect(await screen.findByText('结构优化 + 静态 + DOS')).toBeInTheDocument();
    await user.clear(await screen.findByPlaceholderText(/描述计算需求/));
    await user.type(screen.getByPlaceholderText(/描述计算需求/), '对 Si 结构做 relax 计算');
    await user.click(screen.getByRole('button', { name: /发送/ }));
    // 已开始流式（thinking 已上屏），发送按钮应已变为「停止」
    await screen.findByText(/正在读取任务与工作区/);
    await user.click(await screen.findByRole('button', { name: /停止/ }));
    // 停止后回到「发送」，且输入框可重新编辑发送下一条
    expect(await screen.findByRole('button', { name: /发送/ }, { timeout: 5000 })).toBeInTheDocument();
    const input = (await screen.findByPlaceholderText(/描述计算需求/)) as HTMLInputElement;
    expect(input).toBeEnabled();
  });

  it('进度页展示作业时间线', async () => {
    renderPath('/ai/projects/prj_001/progress/tsk_001');
    // 等待 GET .../detail 的 flow 数据真正渲染出来（作业链最深层 key）
    expect(await screen.findByText('relax/static/dos')).toBeInTheDocument();
    expect(screen.getByText('计算目标')).toBeInTheDocument();
    expect(screen.getByText('作业链与进度')).toBeInTheDocument();
    // 作业链：relax 已提交（含 Slurm 号），static/dos 依赖嵌套等待前置
    expect(screen.getByText('结构优化 relax')).toBeInTheDocument();
    expect(screen.getByText('relax/static')).toBeInTheDocument();
    expect(screen.getByText('已提交')).toBeInTheDocument();
    expect(screen.getByText('Slurm 作业号 12001')).toBeInTheDocument();
    expect(screen.getAllByText('等待前置').length).toBe(2);
    expect(screen.getByText(/依赖：relax（前序完成后自动补提）/)).toBeInTheDocument();
    // 等待队列：依赖闸门未放行的作业
    expect(screen.getByText('等待队列')).toBeInTheDocument();
    // 左任务栏保留：任务标题仍可见
    expect(await screen.findByText('结构优化 + 静态 + DOS')).toBeInTheDocument();
  });

  it('M47 弹卡：高风险命令先生成授权卡片，可拒绝', async () => {
    const user = userEvent.setup();
    renderPath('/ai/projects/prj_001');
    expect(await screen.findByText('结构优化 + 静态 + DOS')).toBeInTheDocument();
    await user.clear(await screen.findByPlaceholderText(/描述计算需求/));
    await user.type(screen.getByPlaceholderText(/描述计算需求/), '帮我 rm -rf 删除文件缓存');
    await user.click(screen.getByRole('button', { name: /发送/ }));
    expect(await screen.findByText('操作授权')).toBeInTheDocument();
    expect(screen.getByText(/需你授权后才能执行/)).toBeInTheDocument();
    const rejectButton = await screen.findByRole('button', { name: /拒\s*绝/ });
    expect(rejectButton).toBeInTheDocument();
    await user.click(rejectButton);
    await waitFor(() => expect(screen.queryByText('操作授权')).not.toBeInTheDocument());
    // 等待真正回到 idle：SSE 流结束后 send() 的 finally 复位 streaming，「发送」按钮恢复
    await screen.findByRole('button', { name: /发送/ }, { timeout: 5000 });
    await waitFor(() => {
      const input = screen.getByPlaceholderText(/描述计算需求/) as HTMLInputElement;
      expect(input).toBeEnabled();
    });
  });

  it('设置页渲染全局设置表单与连通测试入口', async () => {
    renderPath('/ai/settings');
    expect(await screen.findByText('智能体设置')).toBeInTheDocument();
    expect(screen.getByText('最大作业数')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /测试 LLM/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /保存设置/ })).toBeInTheDocument();
  });
});
