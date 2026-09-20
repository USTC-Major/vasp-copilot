// ============================================================
// M12 前端整合测试：项目列表 → 新建项目 → 任务对话 → 设置页
// （数据来自 MSW 演示后端 aiDemo / aiSettingsHandlers）
// ============================================================

import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { delay, http, HttpResponse } from 'msw';
import { routes } from '../router';
import { aiDemo } from '../mocks/aiStore';
import { server } from '../mocks/server';
import SecretInput from '../components/ai/SecretInput';

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
    expect(screen.getAllByText(/条件满足后重新预检并确认提交/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/有空位后自动提交/)).not.toBeInTheDocument();
  });

  it('空等待队列不承诺自动提交', async () => {
    server.use(
      http.get('/ai/v1/jobs/waiting', () => HttpResponse.json({ waiting: [], count: 0 })),
    );
    renderPath('/ai');

    expect(await screen.findByText(/前置完成或有空位后仍会重新预检并确认提交/)).toBeInTheDocument();
    expect(screen.queryByText(/有空位时自动提交/)).not.toBeInTheDocument();
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
    expect(await screen.findByText('运行环境: None')).toBeInTheDocument();
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

  it('SSE 提前 EOF 时保留可见错误，不把断流当成成功', async () => {
    server.use(
      http.post('/ai/v1/projects/:projectId/tasks/:taskId/messages/stream', () => (
        new HttpResponse('data: {"type":"answer","text":"未完成的片段"}\n\n', {
          headers: { 'Content-Type': 'text/event-stream' },
        })
      )),
    );
    const user = userEvent.setup();
    renderPath('/ai/projects/prj_001');
    await screen.findByText('结构优化 + 静态 + DOS');
    await user.type(await screen.findByPlaceholderText(/描述计算需求/), '测试断流');
    await user.click(screen.getByRole('button', { name: /发送/ }));

    expect(await screen.findByText('回复连接中断')).toBeInTheDocument();
    expect(screen.getByText(/未收到完成标记/)).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: /发送/ }, { timeout: 5000 })).toBeInTheDocument();
  });

  it('业务 error 后继续接收 done，并使用生成提示而非连接故障', async () => {
    let terminalSent = false;
    let messagesRefreshedAfterTerminal = false;
    server.use(
      http.get('/ai/v1/projects/:projectId/tasks/:taskId/messages', () => {
        if (terminalSent) messagesRefreshedAfterTerminal = true;
        return HttpResponse.json({ messages: [], generation: { running: false } });
      }),
      http.post('/ai/v1/projects/:projectId/tasks/:taskId/messages/stream', () => {
        terminalSent = true;
        return new HttpResponse([
          'data: {"type":"error","message":"模型未配置"}\n\n',
          'data: {"type":"done","answer":"模型未配置"}\n\n',
        ].join(''), { headers: { 'Content-Type': 'text/event-stream' } });
      }),
    );
    const user = userEvent.setup();
    renderPath('/ai/projects/prj_001');
    await screen.findByText('结构优化 + 静态 + DOS');
    await user.type(await screen.findByPlaceholderText(/描述计算需求/), '测试业务错误');
    await user.click(screen.getByRole('button', { name: /发送/ }));

    expect(await screen.findByText('回复生成提示')).toBeInTheDocument();
    expect(screen.getAllByText('模型未配置').length).toBeGreaterThan(0);
    expect(screen.queryByText('回复连接中断')).not.toBeInTheDocument();
    // done 后 finally 还要刷新持久化消息；提示上屏不代表输入区已回到 idle。
    await waitFor(() => {
      expect(messagesRefreshedAfterTerminal).toBe(true);
      expect(screen.getByRole('button', { name: /发送/ })).toBeInTheDocument();
      expect(screen.getByPlaceholderText(/描述计算需求/)).toBeEnabled();
      expect(screen.queryByRole('button', { name: /停止/ })).not.toBeInTheDocument();
    }, { timeout: 5000 });
  });

  it('刷新后恢复后台生成状态和持久化授权卡', async () => {
    server.use(
      http.get('/ai/v1/projects/:projectId/tasks/:taskId/messages', () => HttpResponse.json({
        messages: [],
        generation: { running: true },
        pending_actions: [{
          card_id: 'persisted-card',
          tool: 'write_file',
          args: { path: 'INCAR' },
          risk: 'medium',
          reason: '写入前需确认本次精确内容。',
          options: ['同意本次', '拒绝'],
          batch_key: 'write|INCAR|persisted',
          kind: 'workspace',
          summary: '恢复的 INCAR 写入确认',
        }],
      })),
    );
    renderPath('/ai/projects/prj_001');

    expect(await screen.findByText('后台仍在生成回复')).toBeInTheDocument();
    expect(await screen.findByText('恢复的 INCAR 写入确认')).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/描述计算需求/)).toBeDisabled();
    expect(screen.getByRole('button', { name: /停止/ })).toBeInTheDocument();
  });

  it('进度监控已从智能模式移除：不再注册进度页，旧地址不渲染作业链', async () => {
    const childPaths = routes.flatMap((r) => (r.children ?? []).map((c) => c.path ?? ''));
    expect(childPaths.some((p) => p.includes('/progress/'))).toBe(false);

    renderPath('/ai/projects/prj_001/progress/tsk_001');
    await waitFor(() => {
      expect(screen.queryByText('作业链与进度')).not.toBeInTheDocument();
    });
    expect(screen.queryByText('计算目标')).not.toBeInTheDocument();
  });

  it('结构化 INCAR 草稿在写入前生成单次授权卡片，可拒绝', async () => {
    const user = userEvent.setup();
    renderPath('/ai/projects/prj_001');
    expect(await screen.findByText('结构优化 + 静态 + DOS')).toBeInTheDocument();
    await user.clear(await screen.findByPlaceholderText(/描述计算需求/));
    await user.type(screen.getByPlaceholderText(/描述计算需求/), '请为 relax 生成 INCAR 草稿并弹卡确认');
    await user.click(screen.getByRole('button', { name: /发送/ }));
    expect(await screen.findByText('操作授权')).toBeInTheDocument();
    expect(screen.getByText(/写入前需你确认本次精确内容/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '同意本批' })).not.toBeInTheDocument();
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

  it('授权响应缺少结果文案时明确仅批准本次操作', async () => {
    server.use(
      http.post('/ai/v1/projects/:projectId/tasks/:taskId/messages/consent', () => HttpResponse.json({
        mode: 'ai', ok: true, kind: 'workspace', approved: true, result: '',
      })),
    );
    const user = userEvent.setup();
    renderPath('/ai/projects/prj_001');
    await screen.findByText('结构优化 + 静态 + DOS');
    await user.type(await screen.findByPlaceholderText(/描述计算需求/), '请生成 INCAR 草稿并弹卡');
    await user.click(screen.getByRole('button', { name: /发送/ }));
    await user.click(await screen.findByRole('button', { name: '同意本次' }));

    expect(await screen.findByText('已批准本次操作；后续操作仍需单独确认')).toBeInTheDocument();
  });

  it('授权请求完成前切换任务，不让旧回调污染新任务界面', async () => {
    const card = {
      card_id: 'switch-card',
      tool: 'write_file',
      args: { path: 'INCAR' },
      risk: 'medium',
      reason: '写入前需确认本次精确内容。',
      options: ['同意本次', '拒绝'],
      batch_key: 'write|INCAR|switch',
      kind: 'workspace',
      summary: '待切换授权卡',
    };
    let oldTaskMessageGets = 0;
    let consentFinished = false;
    server.use(
      http.get('/ai/v1/projects/:projectId/tasks/:taskId/messages', ({ params }) => {
        const taskId = String(params.taskId);
        if (taskId === 'tsk_002') oldTaskMessageGets += 1;
        return HttpResponse.json({
          messages: [],
          generation: { running: false },
          pending_actions: taskId === 'tsk_002' ? [card] : [],
        });
      }),
      http.post('/ai/v1/projects/:projectId/tasks/:taskId/messages/consent', async () => {
        await delay(180);
        consentFinished = true;
        return HttpResponse.json({
          mode: 'ai', ok: true, kind: 'workspace', approved: true,
          result: '已批准本次操作；后续操作仍需单独确认',
        });
      }),
    );
    const user = userEvent.setup();
    renderPath('/ai/projects/prj_001');
    await screen.findByText('待切换授权卡');
    await user.click(screen.getByRole('button', { name: '同意本次' }));
    await user.click(screen.getByText('结构优化 + 静态 + DOS'));

    expect(await screen.findByRole('heading', { name: '结构优化 + 静态 + DOS' })).toBeInTheDocument();
    await waitFor(() => expect(consentFinished).toBe(true));
    expect(screen.queryByText('待切换授权卡')).not.toBeInTheDocument();
    expect(oldTaskMessageGets).toBe(1);
  });

  it('设置页渲染全局设置表单与连通测试入口', async () => {
    renderPath('/ai/settings');
    expect(await screen.findByText('智能体设置')).toBeInTheDocument();
    expect(screen.getByText('最大作业数')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /测试 LLM/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /保存设置/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /显示原文/ })).not.toBeInTheDocument();
    expect(screen.getByText(/不可查看或复制/)).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: /清\s*除/ })).toHaveLength(3);
  });

  it('环境变量管理的密钥不可在页面替换或清除', () => {
    render(<SecretInput hasSecret manageable={false} source="environment"
      value="" onChange={() => undefined} onClear={() => undefined}
      placeholder="环境变量" />);
    expect(screen.getByText(/由环境变量管理/)).toBeInTheDocument();
    expect(screen.getByLabelText('输入新的密钥以整体替换')).toBeDisabled();
    expect(screen.getByRole('button', { name: /清\s*除/ })).toBeDisabled();
  });
});
