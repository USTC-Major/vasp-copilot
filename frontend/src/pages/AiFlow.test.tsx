// ============================================================
// M12 前端整合测试：项目列表 → 新建项目 → 任务对话 → 设置页
// （数据来自 MSW 演示后端 aiDemo / aiSettingsHandlers）
// ============================================================

import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { routes } from '../router';
import { aiDemo } from '../mocks/aiStore';
import { server } from '../mocks/server';
import SecretInput from '../components/ai/SecretInput';
import { ConfigProvider, Modal, message } from 'antd';

function renderPath(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } },
  });
  render(
    <ConfigProvider theme={{ token: { motion: false } }}><QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider></ConfigProvider>
  );
  return queryClient;
}

const executionDetail = (taskId: string, historical: string, current: string) => ({
  mode: 'toolbox', task_id: taskId,
  task: { id: taskId, project_id: 'prj_001', title: '状态来源测试', status: 'planned' },
  flow: { execution_mode: historical, phase: 'planning', jobs: [], waiting: [], report: '', precheck: { ok: false, issues: [] }, draft: [], artifacts: {} },
  backend_mode: current, consents: [], events: [],
  monitor: { state: 'idle', interval_seconds: 60, remote_cancelled: false },
});

describe('AI 前端整合（M12）', () => {
  beforeEach(() => {
    aiDemo.reset();
    // JSDOM has no CSS transition completion events for static Ant portals.
    ConfigProvider.config({ holderRender: (children) => <ConfigProvider theme={{ token: { motion: false } }}>{children}</ConfigProvider> });
  });

  afterEach(async () => {
    cleanup();
    await act(async () => {
      Modal.destroyAll();
      message.destroy();
    });
    await waitFor(() => expect(screen.queryAllByRole('dialog')).toHaveLength(0));
    ConfigProvider.config({ holderRender: undefined });
  });

  it.each([
    ['Fake', 'Real', '模拟', '真实'],
    ['Real', 'Fake', '真实', '模拟'],
    ['None', 'None', '未记录', '未配置'],
  ])('顶部与共享卡共用详情：历史 %s / 当前 %s', async (historical, current, historyLabel, currentLabel) => {
    // The list value is deliberately inconsistent and must not be used.
    aiDemo.tasks[0].execution_mode = 'None';
    let reads = 0;
    server.use(http.get('/api/v1/toolbox/projects/:projectId/tasks/:taskId/detail', ({ params }) => {
      reads += 1;
      return HttpResponse.json(executionDetail(String(params.taskId), historical, current));
    }));
    renderPath('/ai/projects/prj_001');
    expect(await screen.findAllByText(`历史执行：${historyLabel}`)).toHaveLength(2);
    expect(screen.getAllByText(`当前后端：${currentLabel}`)).toHaveLength(2);
    expect(screen.queryByText(/运行环境: None/)).not.toBeInTheDocument();
    expect(reads).toBe(1);
  });

  it('共享详情刷新同步顶部，读取失败不把旧缓存当作当前环境，恢复后同步更新', async () => {
    aiDemo.tasks[0].updated_at = '2099-01-01T00:00:00Z';
    let current = 'Real';
    let unavailable = false;
    let reads = 0;
    server.use(http.get('/api/v1/toolbox/projects/:projectId/tasks/:taskId/detail', ({ params }) => {
      reads += 1;
      return unavailable ? HttpResponse.json({ error: { code: 'UNAVAILABLE', message: 'offline' } }, { status: 503 })
        : HttpResponse.json(executionDetail(String(params.taskId), 'Fake', current));
    }));
    const queryClient = renderPath('/ai/projects/prj_001');
    expect(await screen.findAllByText('当前后端：真实')).toHaveLength(2);
    current = 'None';
    await act(async () => { await queryClient.refetchQueries({ queryKey: ['toolboxTaskDetail', 'prj_001', 'tsk_001'] }); });
    await waitFor(() => expect(screen.getAllByText('当前后端：未配置')).toHaveLength(2));
    expect(screen.getAllByText('历史执行：模拟')).toHaveLength(2);
    unavailable = true;
    await act(async () => { await queryClient.refetchQueries({ queryKey: ['toolboxTaskDetail', 'prj_001', 'tsk_001'] }); });
    expect(await screen.findByText('执行环境：状态暂不可用')).toBeInTheDocument();
    expect(screen.getByText('Toolbox 状态暂时不可用')).toBeInTheDocument();
    expect(screen.queryByText('当前后端：未配置')).not.toBeInTheDocument();
    unavailable = false;
    current = 'Fake';
    await act(async () => { await queryClient.refetchQueries({ queryKey: ['toolboxTaskDetail', 'prj_001', 'tsk_001'] }); });
    await waitFor(() => expect(screen.getAllByText('当前后端：模拟')).toHaveLength(2));
    expect(reads).toBe(4);
  });

  it('任务切换等待新详情时不展示前一个任务的执行环境', async () => {
    aiDemo.tasks[0].updated_at = '2099-01-01T00:00:00Z';
    let release: (() => void) | undefined;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    server.use(http.get('/api/v1/toolbox/projects/:projectId/tasks/:taskId/detail', async ({ params }) => {
      const id = String(params.taskId);
      if (id === 'tsk_002') await gate;
      return HttpResponse.json(executionDetail(id, id === 'tsk_001' ? 'Real' : 'Fake', 'None'));
    }));
    const user = userEvent.setup();
    renderPath('/ai/projects/prj_001');
    expect(await screen.findAllByText('历史执行：真实')).toHaveLength(2);
    await user.click(screen.getByText('带结构计算的能带'));
    expect(await screen.findByText('执行环境：加载中')).toBeInTheDocument();
    expect(screen.queryByText('历史执行：真实')).not.toBeInTheDocument();
    await act(async () => { release?.(); });
    expect(await screen.findAllByText('历史执行：模拟')).toHaveLength(2);
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
    expect(screen.getByText('工作区仅指定路径；文件根登记及操作授权请在 Toolbox 中确认')).toBeInTheDocument();
  });

  it('项目聊天：给演示任务发消息得到回复并出现规划', async () => {
    const user = userEvent.setup();
    renderPath('/ai/projects/prj_001');
    expect(await screen.findByText('结构优化 + 静态 + DOS')).toBeInTheDocument();
    expect(await screen.findAllByText('当前后端：未配置')).toHaveLength(2);
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

  it.each([
    [true, '已停止生成'],
    [false, '当前没有正在生成的回复'],
  ])('停止生成只报告生成状态（stopped=%s），不停止 Toolbox 状态', async (stopped, expectedNotice) => {
    let toolboxReads = 0;
    let stopCalls = 0;
    server.use(
      http.get('/api/v1/toolbox/projects/:projectId/tasks/:taskId/detail', ({ params }) => {
        toolboxReads += 1;
        return HttpResponse.json(executionDetail(String(params.taskId), 'Real', 'Real'));
      }),
      http.post('/ai/v1/projects/:projectId/tasks/:taskId/messages/stop', () => {
        stopCalls += 1;
        return HttpResponse.json({ mode: 'ai', stopped });
      }),
    );
    const user = userEvent.setup();
    renderPath('/ai/projects/prj_001');
    await screen.findByText('结构优化 + 静态 + DOS');
    await waitFor(() => expect(toolboxReads).toBeGreaterThan(0));
    const originalToolboxReads = toolboxReads;

    await user.click(screen.getByRole('button', { name: '停止生成' }));

    expect(await screen.findByText(expectedNotice)).toBeInTheDocument();
    expect(stopCalls).toBe(1);
    expect(toolboxReads).toBe(originalToolboxReads);
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
      expect(screen.queryByRole('button', { name: '停止' })).not.toBeInTheDocument();
      expect(screen.getByRole('button', { name: '停止生成' })).toBeInTheDocument();
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
    expect(screen.getByRole('button', { name: '停止生成' })).toBeInTheDocument();
  });

  it('旧进度地址迁移到精确 Toolbox 任务页', async () => {
    const childPaths = routes.flatMap((r) => (r.children ?? []).map((c) => c.path ?? ''));
    expect(childPaths.some((p) => p.includes('/progress/'))).toBe(true);

    renderPath('/ai/projects/prj_001/progress/tsk_001');
    expect(await screen.findByRole('heading', { name: '结构优化 + 静态 + DOS' })).toBeInTheDocument();
    expect(await screen.findByText('Toolbox 执行状态')).toBeInTheDocument();
  });

  it('结构化 INCAR 草稿在写入前生成单次授权卡片，可拒绝', async () => {
    server.use(http.post('/ai/v1/projects/:projectId/tasks/:taskId/messages/consent', () => HttpResponse.json({
      mode: 'ai', ok: true, kind: 'workspace', approved: false, state: 'rejected',
      error: null,
      card: { state: 'rejected', result: '已拒绝，本次不执行' },
    })));
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
    expect(await screen.findByText('拒绝已记录')).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText('操作授权')).not.toBeInTheDocument());
    // 等待真正回到 idle：SSE 流结束后 send() 的 finally 复位 streaming，「发送」按钮恢复
    await screen.findByRole('button', { name: /发送/ }, { timeout: 5000 });
    await waitFor(() => {
      const input = screen.getByPlaceholderText(/描述计算需求/) as HTMLInputElement;
      expect(input).toBeEnabled();
    });
  });

  it('根据执行状态显示授权回执，不把请求中的批准回显当作成功', async () => {
    let consentCalls = 0;
    server.use(
      http.post('/ai/v1/projects/:projectId/tasks/:taskId/messages/consent', () => { consentCalls += 1; return HttpResponse.json({
        mode: 'ai', ok: false, kind: 'workspace', approved: true, state: 'executed',
        error: { code: 'OTHER_DIMENSION_FAILED', message: '执行已完成，其他检查未通过', retryable: false },
        card: { state: 'executed', result: '执行凭据已登记' }, result: '',
      }); }),
    );
    const user = userEvent.setup();
    renderPath('/ai/projects/prj_001');
    await screen.findByText('结构优化 + 静态 + DOS');
    await user.type(await screen.findByPlaceholderText(/描述计算需求/), '请生成 INCAR 草稿并弹卡');
    await user.click(screen.getByRole('button', { name: /发送/ }));
    await user.click(await screen.findByRole('button', { name: '同意本次' }));
    await waitFor(() => expect(consentCalls).toBe(1));

    expect(await screen.findByText('执行完成')).toBeInTheDocument();
    expect(screen.getByText('执行凭据已登记')).toBeInTheDocument();
    expect(screen.queryByText(/已批准本次操作/)).not.toBeInTheDocument();
  });

  it('单卡 failed 显示失败原因，不显示批准成功', async () => {
    let active = true;
    const failedCard = pendingCard({ card_id: 'failed-one', kind: 'workspace', summary: '失败的操作' });
    server.use(
      http.get('/ai/v1/projects/:projectId/tasks/:taskId/messages', () => HttpResponse.json({
        messages: [], generation: { running: false }, pending_actions: active ? [failedCard] : [],
      })),
      http.post('/ai/v1/projects/:projectId/tasks/:taskId/messages/consent', () => {
        active = false;
        return HttpResponse.json({ mode: 'ai', ok: false, kind: 'workspace', approved: true, state: 'failed',
          error: { code: 'CONSENT_FAILED', message: '操作失败且未重试：registered source changed after confirmation', retryable: false },
          card: { state: 'failed', reason: 'source changed after confirmation', result: '未执行' } });
      }),
    );
    const user = userEvent.setup();
    renderPath('/ai/projects/prj_001');
    await user.click(await screen.findByRole('button', { name: '同意本次' }));
    expect(await screen.findByText('执行失败/已过期')).toBeInTheDocument();
    expect(screen.getByText('未执行')).toBeInTheDocument();
    expect(screen.getByText('source changed after confirmation')).toBeInTheDocument();
    expect(screen.getByText('操作失败且未重试：registered source changed after confirmation')).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText('失败的操作')).not.toBeInTheDocument());
    expect(screen.queryByText(/已批准本次操作/)).not.toBeInTheDocument();
  });

  it.each([
    ['缺失状态', {}],
    ['冲突状态', { state: 'failed', card: { state: 'executed' } }],
    ['未知状态', { state: 'mystery' }],
  ])('将%s响应标记为未确认并保留待处理卡', async (_name, outcome) => {
    const card = pendingCard({ card_id: 'uncertain-one', kind: 'workspace', summary: '需要保留的卡' });
    server.use(
      http.get('/ai/v1/projects/:projectId/tasks/:taskId/messages', () => HttpResponse.json({ messages: [], pending_actions: [card] })),
      http.post('/ai/v1/projects/:projectId/tasks/:taskId/messages/consent', () => HttpResponse.json({
        mode: 'ai', ok: true, kind: 'workspace', ...outcome,
        error: { code: 'CONSENT_RESULT_UNAVAILABLE', message: '请核验操作结果', retryable: false },
      })),
    );
    const user = userEvent.setup();
    renderPath('/ai/projects/prj_001');
    await user.click(await screen.findByRole('button', { name: '同意本次' }));
    expect(await screen.findByText('结果未确认')).toBeInTheDocument();
    expect(await screen.findByText('需要保留的卡')).toBeInTheDocument();
  });

  it.each([
    ['executed', '执行完成', false],
    ['rejected', '拒绝已记录', false],
    ['failed', '执行失败/已过期', false],
    ['expired', '执行失败/已过期', false],
    ['approved', '已接受，处理中', false],
    ['executing', '已接受，处理中', false],
    ['unknown', '结果未确认', false],
    ['pending', '仍待处理', true],
  ])('card.state=%s 分类准确，后续刷新不会恢复已处理授权', async (state, label, remainsPending) => {
    aiDemo.tasks[0].updated_at = '2099-01-01T00:00:00Z';
    const card = pendingCard({ card_id: 'state-card', kind: 'workspace', summary: '状态合同卡' });
    let reads = 0;
    server.use(
      http.get('/ai/v1/projects/:projectId/tasks/:taskId/messages', () => {
        reads += 1;
        return HttpResponse.json({ messages: [], pending_actions: [card] });
      }),
      http.post('/ai/v1/projects/:projectId/tasks/:taskId/messages/consent', () => HttpResponse.json({
        mode: 'ai', ok: true, approved: true, kind: 'workspace', card: { state, result: '权威执行凭据' },
        ...(['failed', 'expired', 'unknown'].includes(state) ? { ok: false, error: { code: `CONSENT_${state.toUpperCase()}`, message: `${state} 业务错误`, retryable: false } } : {}),
        ...(['executed', 'rejected'].includes(state) ? { error: null, result: null } : {}),
      })),
    );
    const user = userEvent.setup();
    const queryClient = renderPath('/ai/projects/prj_001');
    await user.click(await screen.findByRole('button', { name: '同意本次' }));
    expect(await screen.findByText(label)).toBeInTheDocument();
    expect(screen.getByText('权威执行凭据')).toBeInTheDocument();
    if (['failed', 'expired', 'unknown'].includes(state)) expect(screen.getByText(`${state} 业务错误`)).toBeInTheDocument();
    await waitFor(() => expect(reads).toBeGreaterThanOrEqual(2));
    await act(async () => { await queryClient.refetchQueries({ queryKey: ['aiMessages', 'prj_001', 'tsk_001'] }); });
    await waitFor(() => expect(screen.queryByRole('button', { name: /同意本次/ }) !== null).toBe(remainsPending));
    if (state === 'unknown') expect(screen.getByText(/执行结果未知，请核验任务状态/)).toBeInTheDocument();
    if (state !== 'executed') expect(screen.queryByText('执行完成')).not.toBeInTheDocument();
  });

  it.each(['缺失', '冲突'])('%s状态只在权威刷新确认后移除授权', async (kind) => {
    const card = pendingCard({ card_id: 'reconciled-card', kind: 'workspace', summary: '刷新确认卡' });
    let active = true;
    server.use(
      http.get('/ai/v1/projects/:projectId/tasks/:taskId/messages', () => HttpResponse.json({ messages: [], pending_actions: active ? [card] : [] })),
      http.post('/ai/v1/projects/:projectId/tasks/:taskId/messages/consent', () => {
        active = false;
        return HttpResponse.json({ mode: 'ai', ok: true, kind: 'workspace',
          ...(kind === '冲突' ? { state: 'failed', card: { state: 'executed' } } : {}),
        });
      }),
    );
    const user = userEvent.setup();
    renderPath('/ai/projects/prj_001');
    await user.click(await screen.findByRole('button', { name: '同意本次' }));
    expect(await screen.findByText('结果未确认')).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole('button', { name: '同意本次' })).not.toBeInTheDocument());
    expect(screen.queryByText('执行完成')).not.toBeInTheDocument();
  });

  it('HTTP 错误后先刷新权威状态，卡片仍待处理时予以保留', async () => {
    const card = pendingCard({ card_id: 'http-error-one', kind: 'workspace', summary: '网络错误卡' });
    server.use(
      http.get('/ai/v1/projects/:projectId/tasks/:taskId/messages', () => HttpResponse.json({ messages: [], pending_actions: [card] })),
      http.post('/ai/v1/projects/:projectId/tasks/:taskId/messages/consent', () => HttpResponse.json({ error: 'unavailable' }, { status: 503 })),
    );
    const user = userEvent.setup();
    renderPath('/ai/projects/prj_001');
    await user.click(await screen.findByRole('button', { name: '同意本次' }));
    expect(await screen.findByText('结果未确认')).toBeInTheDocument();
    expect(await screen.findByText('网络错误卡')).toBeInTheDocument();
  });

  it.each([false, true])('HTTP 错误后刷新失败=%s，仅成功的权威刷新可移除卡', async (refreshFails) => {
    const card = pendingCard({ card_id: 'http-reconcile', kind: 'workspace', summary: 'HTTP 对账卡' });
    let attempted = false;
    server.use(
      http.get('/ai/v1/projects/:projectId/tasks/:taskId/messages', () => attempted && refreshFails
        ? HttpResponse.json({ error: 'offline' }, { status: 503 })
        : HttpResponse.json({ messages: [], pending_actions: attempted ? [] : [card] })),
      http.post('/ai/v1/projects/:projectId/tasks/:taskId/messages/consent', () => {
        attempted = true;
        return HttpResponse.json({ error: 'offline' }, { status: 503 });
      }),
    );
    const user = userEvent.setup();
    renderPath('/ai/projects/prj_001');
    await user.click(await screen.findByRole('button', { name: '同意本次' }));
    expect(await screen.findByText('结果未确认')).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole('button', { name: /同意本次/ }) !== null).toBe(refreshFails));
    expect(screen.queryByText('执行完成')).not.toBeInTheDocument();
  });

  it.each([
    [503, JSON.stringify({ mode: 'ai', ok: false, kind: 'workspace', state: 'failed', card: { state: 'failed', result: '不可采信的凭据' } })],
    [200, 'not JSON'],
    [200, JSON.stringify({ mode: 'ai', ok: false, kind: 'workspace', state: 'executed', card: 'malformed' })],
  ])('HTTP %s 无效传输回执仅显示未确认，保留卡且不重复请求 %#', async (status, body) => {
    const card = pendingCard({ card_id: 'invalid-receipt', kind: 'workspace', summary: '待核验传输卡' });
    let calls = 0;
    server.use(
      http.get('/ai/v1/projects/:projectId/tasks/:taskId/messages', () => HttpResponse.json({ messages: [], pending_actions: [card] })),
      http.post('/ai/v1/projects/:projectId/tasks/:taskId/messages/consent', () => {
        calls += 1;
        return new HttpResponse(body, { status, headers: { 'Content-Type': 'application/json' } });
      }),
    );
    const user = userEvent.setup();
    renderPath('/ai/projects/prj_001');
    await user.click(await screen.findByRole('button', { name: '同意本次' }));
    expect(await screen.findByText('结果未确认')).toBeInTheDocument();
    expect(screen.getByText('待核验传输卡')).toBeInTheDocument();
    expect(screen.queryByText('执行失败/已过期')).not.toBeInTheDocument();
    expect(screen.queryByText('执行完成')).not.toBeInTheDocument();
    expect(screen.queryByText('不可采信的凭据')).not.toBeInTheDocument();
    expect(calls).toBe(1);
  });

  it.each([false, true])('授权请求完成前切换任务（返回原任务=%s），旧回调不污染当前选择', async (returnToOriginal) => {
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
    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => { release = resolve; });
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
        await gate;
        consentFinished = true;
        return HttpResponse.json({
          mode: 'ai', ok: true, kind: 'workspace', approved: true, state: 'executed',
          result: '旧请求的执行凭据',
        });
      }),
    );
    const user = userEvent.setup();
    renderPath('/ai/projects/prj_001');
    await screen.findByText('待切换授权卡');
    await user.click(screen.getByRole('button', { name: '同意本次' }));
    await user.click(screen.getByText('结构优化 + 静态 + DOS'));

    expect(await screen.findByRole('heading', { name: '结构优化 + 静态 + DOS' })).toBeInTheDocument();
    if (returnToOriginal) await user.click(screen.getByText('带结构计算的能带'));
    const readsBeforeRelease = oldTaskMessageGets;
    release();
    await waitFor(() => expect(consentFinished).toBe(true));
    if (!returnToOriginal) expect(screen.queryByText('待切换授权卡')).not.toBeInTheDocument();
    expect(screen.queryByText('旧请求的执行凭据')).not.toBeInTheDocument();
    expect(oldTaskMessageGets).toBe(readsBeforeRelease);
  });

  it('设置页渲染全局设置表单与连通测试入口', async () => {
    renderPath('/ai/settings');
    expect(await screen.findByRole('textbox', { name: '模型名称' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '设置', level: 1 })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '模型与材料' })).toBeInTheDocument();
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

  it('草稿超过单条上下文上限时提示会被截断', async () => {
    server.use(http.get('/ai/v1/settings', () => HttpResponse.json({
      mode: 'ai', enabled: true, writable: [],
      settings: {
        enabled: true, max_jobs: 20, poll_interval_seconds: 60, message_char_limit: 5,
        llm: { base_url: '', model: '', provider: 'auto', api_key: '' },
        ssh: { name: '', host: '', port: 22, username: '', known_hosts_path: '', identity_file: '', scheduler_backend: 'slurm' },
        materials_project: { api_key: '' },
      },
    })));
    renderPath('/ai/projects/prj_001');
    const input = await screen.findByPlaceholderText(/描述计算需求/);
    await userEvent.type(input, '1234567890');
    expect(await screen.findByText(/超过 5 字上限/)).toBeInTheDocument();
  });

  const pendingCard = (over: Record<string, unknown>) => ({
    card_id: 'card-x', tool: 'hpc_upload', args: {}, risk: 'medium',
    reason: '上传前需你确认本次精确内容。', options: ['同意本次', '拒绝'],
    batch_key: 'batch-x', kind: 'hpc_upload', summary: '上传已登记输入 `INCAR`',
    ...over,
  });

  it('同类文件上传卡合并为一组，可一次批准本批', async () => {
    const approved: string[] = [];
    server.use(
      http.get('/ai/v1/projects/:projectId/tasks/:taskId/messages', () => HttpResponse.json({
        messages: [], generation: { running: false },
        pending_actions: [
          pendingCard({ card_id: 'up-1', summary: '上传已登记输入 `INCAR`（31 B）到 `/hpc/relax/INCAR`' }),
          pendingCard({ card_id: 'up-2', summary: '上传已登记输入 `POSCAR`（62 B）到 `/hpc/relax/POSCAR`' }),
        ],
      })),
      http.post('/ai/v1/projects/:projectId/tasks/:taskId/messages/consent', async ({ request }) => {
        const body = await request.json() as { card_id: string; approved: boolean };
        approved.push(body.card_id);
        return HttpResponse.json({ mode: 'ai', ok: true, kind: 'hpc_upload', approved: body.approved, result: '' });
      }),
    );
    const user = userEvent.setup();
    renderPath('/ai/projects/prj_001');
    // 同类文件上传仍可批量确认。
    expect(await screen.findByText('文件准备')).toBeInTheDocument();
    expect(screen.getByText('2 项待批准（可批量）')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /全部批准本批（2 项）/ }));
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: /全部批准/ }));
    await waitFor(() => expect(approved).toEqual(['up-1', 'up-2']));
  });

  it('混合批次逐卡显示执行、失败和 HTTP 未确认结果，并汇总分类', async () => {
    const cards = [
      pendingCard({ card_id: 'mix-failed', summary: '会失败' }),
      pendingCard({ card_id: 'mix-done', summary: '已完成' }),
      pendingCard({ card_id: 'mix-http', summary: 'HTTP 错误' }),
    ];
    const attempted = new Set<string>();
    server.use(
      http.get('/ai/v1/projects/:projectId/tasks/:taskId/messages', () => HttpResponse.json({
        messages: [], generation: { running: false }, pending_actions: cards.filter((card) => !attempted.has(card.card_id)),
      })),
      http.post('/ai/v1/projects/:projectId/tasks/:taskId/messages/consent', async ({ request }) => {
        const body = await request.json() as { card_id: string };
        if (body.card_id === 'mix-http') return HttpResponse.json({ error: 'temporarily unavailable' }, { status: 503 });
        attempted.add(body.card_id);
        const state = body.card_id === 'mix-failed' ? 'failed' : 'executed';
        return HttpResponse.json({ mode: 'ai', ok: state === 'executed', kind: 'hpc_upload', approved: true,
          ...(state === 'failed' ? { error: { code: 'CONSENT_FAILED', message: '操作失败且未重试', retryable: false } } : {}),
          ...(state === 'executed' ? { error: null } : {}),
          state, card: { state, result: state === 'failed' ? 'source changed' : 'upload finished' } });
      }),
    );
    const user = userEvent.setup();
    renderPath('/ai/projects/prj_001');
    expect(await screen.findByText('3 项待批准（可批量）')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /全部批准本批（3 项）/ }));
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: /全部批准/ }));
    expect(await screen.findByText(/本批处理结果：执行完成 1；拒绝已记录 0；执行失败\/已过期 1；已接受，处理中 0；仍待处理 0；结果未确认 1/)).toBeInTheDocument();
    expect(screen.getByText('source changed')).toBeInTheDocument();
    expect(screen.getByText('操作失败且未重试')).toBeInTheDocument();
    expect(screen.getByText('upload finished')).toBeInTheDocument();
    expect(await screen.findByText('HTTP 错误')).toBeInTheDocument();
    expect(screen.getByText('结果未确认')).toBeInTheDocument();
  });

  it('混批 unknown 与 executing 不冒充完成，陈旧刷新只保留 pending 授权', async () => {
    const states = ['unknown', 'executing', 'pending'];
    const cards = states.map((state) => pendingCard({ card_id: state, summary: `批次 ${state}` }));
    server.use(
      http.get('/ai/v1/projects/:projectId/tasks/:taskId/messages', () => HttpResponse.json({ messages: [], pending_actions: cards })),
      http.post('/ai/v1/projects/:projectId/tasks/:taskId/messages/consent', async ({ request }) => {
        const { card_id: state } = await request.json() as { card_id: string };
        return HttpResponse.json({ mode: 'ai', ok: state !== 'unknown', approved: true, kind: 'hpc_upload', state, card: { state, result: `${state} 凭据` },
          ...(state === 'unknown' ? { error: { code: 'CONSENT_UNKNOWN', message: '远端执行结果未确认', retryable: false } } : {}),
        });
      }),
    );
    const user = userEvent.setup();
    renderPath('/ai/projects/prj_001');
    await user.click(await screen.findByRole('button', { name: /全部批准本批（3 项）/ }));
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: /全部批准/ }));
    expect(await screen.findByText(/本批处理结果：执行完成 0；拒绝已记录 0；执行失败\/已过期 0；已接受，处理中 1；仍待处理 1；结果未确认 1/)).toBeInTheDocument();
    for (const state of states) expect(screen.getByText(`${state} 凭据`)).toBeInTheDocument();
    expect(screen.getByText(/执行结果未知，请核验任务状态/)).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: /同意本次/ })).toHaveLength(1);
  });

  it('科学输入卡不参与批量，只逐项确认', async () => {
    server.use(
      http.get('/ai/v1/projects/:projectId/tasks/:taskId/messages', () => HttpResponse.json({
        messages: [], generation: { running: false },
        pending_actions: [pendingCard({
          card_id: 'incar-1', tool: 'propose_incar', kind: 'incar_write', batch_key: 'incar|relax',
          summary: '写入 `relax/INCAR`；参数：ENCUT\nSHA-256：abcdef\n\n- ENCUT = 520\n+ ENCUT = 600',
        })],
      })),
    );
    renderPath('/ai/projects/prj_001');
    expect(await screen.findByText('INCAR 参数写入')).toBeInTheDocument();
    expect(screen.getByText('1 项待批准（逐项确认）')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /全部批准本批/ })).not.toBeInTheDocument();
  });

  it('卡片默认只显示指纹，完整预览按需展开', async () => {
    server.use(
      http.get('/ai/v1/projects/:projectId/tasks/:taskId/messages', () => HttpResponse.json({
        messages: [], generation: { running: false },
        pending_actions: [pendingCard({
          card_id: 'mp-1', tool: 'mp_import_poscar', kind: 'mp_poscar_write',
          summary: 'Materials Project mp-aaaaaaft / Si / 2 原子\n写入：`relax/POSCAR`\n```\nSi\n1.0\n0.0 2.7 2.7\n```',
        })],
      })),
    );
    const user = userEvent.setup();
    renderPath('/ai/projects/prj_001');
    expect(await screen.findByText(/Materials Project mp-aaaaaaft \/ Si \/ 2 原子/)).toBeInTheDocument();
    expect(screen.queryByText(/0.0 2.7 2.7/)).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '展开完整预览' }));
    expect(await screen.findByText(/0.0 2.7 2.7/)).toBeInTheDocument();
  });
});
