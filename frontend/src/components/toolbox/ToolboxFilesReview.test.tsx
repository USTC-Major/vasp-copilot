import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { HttpResponse, http } from 'msw';
import { routes } from '../../router';
import { server } from '../../mocks/server';
import { createQueryClient } from '../../queryClient';
import type { ToolboxFileReceipt } from '../../types/toolbox';

const TOOLBOX = '/api/v1/toolbox';
const ACTION_ID = 'a'.repeat(32);
const SCOPE_ID = 'b'.repeat(32);
const ROOT_ID = 'c'.repeat(32);
const FULL_TEXT = '#!/bin/bash\necho exact review text\n';
const CARD_EXPIRY = new Date(Date.now() + 10 * 60_000).toISOString();
const SCOPE_EXPIRY = new Date(Date.now() + 60 * 60_000).toISOString();

const fileCard = (state: string, complete: boolean, taskId = 'task-one') => {
  const receipt: ToolboxFileReceipt = { phase: state, items: [], spent: { operations: 0, bytes: 0 }, held_unknown: { operations: 0, bytes: 0 }, released: { operations: 0, bytes: 0 } };
  return ({
  card_id: ACTION_ID, action_id: ACTION_ID, kind: 'remote_file', summary: '人工精确文件计划',
  state, reason: '文件完成不证明科学适用', expires_at: CARD_EXPIRY,
  binding: {
    project_id: 'project', task_id: taskId, scope_id: SCOPE_ID, scope_version: 1, job_key: 'a', attempt_id: 'attempt-a',
    manifest: {
      endpoint: { host: 'fixture.invalid', port: 22, username: 'fixture' },
      roots: [{ root_id: ROOT_ID, version: 1, requested_path: '/research', canonical_path: '/research' }],
      items: [{
        item_id: 'script', op: 'write_text', destination: { root_id: ROOT_ID, relative_path: 'a/run.sh' },
        bytes: new TextEncoder().encode(FULL_TEXT).length, content_class: 'text',
        ...(complete ? { text: FULL_TEXT } : { text_sha256: 'summary-only' }),
      }],
    },
  },
  receipt,
  });
};

const detail = (taskId = 'task-one', state = 'pending') => ({
  mode: 'toolbox', task_id: taskId,
  task: { id: taskId, project_id: 'project', title: taskId, goal: 'file QA', local_workspace: null, hpc_workspace: '/research', status: 'planned', updated_at: '2026-09-23T00:00:00Z', execution_mode: 'Fake' },
  flow: { execution_mode: 'Fake', phase: 'draft', goal: 'file QA', strategy: '', local_dir: '', hpc_dir: '/research', waiting: [], precheck: { ok: false, issues: [] }, report: '',
    jobs: [{ key: 'a', label: '计算 A', kind: 'custom', requires: [], status: 'draft', attempt_id: 'attempt-a' }], draft: [], artifacts: {} },
  consents: state === 'pending' ? [fileCard(state, false, taskId)] : [],
  file_roots: [{ root_id: ROOT_ID, version: 1, requested_path: '/research', canonical_path: '/research' }],
  file_roots_version: 1,
  file_scopes: [{ scope_id: SCOPE_ID, version: 1, state: 'proposed', job_key: 'a', attempt_id: 'attempt-a',
    root_bindings: [{ root_id: ROOT_ID, version: 1, destination_prefixes: ['a'] }], source_bindings: [],
    allowed_operations: ['write_text'], max_operations: 1, max_total_bytes: 1000, expires_at: SCOPE_EXPIRY }],
  file_actions: [fileCard(state, false, taskId)], events: [], monitor: { state: 'idle', interval_seconds: 60, remote_cancelled: false }, backend_mode: 'Fake',
});

const renderTask = (path = '/toolbox/projects/project/tasks/task-one', productionDefaults = false) => {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  const queryClient = productionDefaults
    ? createQueryClient()
    : new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={queryClient}><RouterProvider router={router} /></QueryClientProvider>);
  return { router, queryClient };
};

describe('独立文件卡审阅', () => {
  it('提交确认响应丢失时提示核对，且生产默认重试不重发确认', async () => {
    const task = detail();
    task.consents.push({
      card_id: 'submit-card', action_id: 'submit-card', kind: 'submit', summary: '提交确认',
      state: 'pending', reason: '逐次提交', binding: { job_key: 'a', attempt_id: 'attempt-a' },
    } as typeof task.consents[number]);
    let posts = 0;
    server.use(
      http.get(`${TOOLBOX}/projects/:projectId/tasks/:taskId/detail`, () => HttpResponse.json(task)),
      http.post(`${TOOLBOX}/projects/:projectId/tasks/:taskId/consents/:cardId`, () => {
        posts += 1;
        return HttpResponse.error();
      }),
    );
    const user = userEvent.setup();
    renderTask('/toolbox/projects/project/tasks/task-one', true);
    await screen.findByRole('button', { name: /确认本次操作/ });
    await user.click(screen.getByRole('button', { name: /确认本次操作/ }));
    expect(await screen.findByText(/正在重新读取任务状态，请勿重复确认/)).toBeInTheDocument();
    expect(posts).toBe(1);
  });

  it('共享状态中的旧提交卡保留原确认请求，文件卡只给完整审阅导航', async () => {
    const task = detail();
    task.consents.push({
      card_id: 'submit-card', action_id: 'submit-card', kind: 'submit', summary: '旧提交确认', state: 'pending',
      reason: '逐次提交', binding: { job_key: 'a', attempt_id: 'attempt-a' },
    } as typeof task.consents[number]);
    const decisions: Array<{ cardId: string; approved: boolean }> = [];
    server.use(
      http.get(`${TOOLBOX}/projects/:projectId/tasks/:taskId/detail`, () => HttpResponse.json(task)),
      http.post(`${TOOLBOX}/projects/:projectId/tasks/:taskId/consents/:cardId`, async ({ params, request }) => {
        decisions.push({ cardId: String(params.cardId), approved: (await request.json() as { approved: boolean }).approved });
        return HttpResponse.json({ mode: 'toolbox', ok: true, card: { card_id: 'submit-card', kind: 'submit', state: 'executed' }, result: 'submitted' });
      }),
    );
    const user = userEvent.setup();
    renderTask();
    expect(await screen.findByRole('link', { name: /审阅完整文件计划/ })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /确认本次操作/ }));
    await waitFor(() => expect(decisions).toEqual([{ cardId: 'submit-card', approved: true }]));
  });

  it('摘要卡只能进入完整审阅；首次批准绑定原scope与全文', async () => {
    const posts: Array<Record<string, unknown>> = [];
    let fullGets = 0;
    server.use(
      http.get(`${TOOLBOX}/projects/:projectId/tasks/:taskId/detail`, ({ params }) => HttpResponse.json(detail(String(params.taskId)))),
      http.get(`${TOOLBOX}/projects/:projectId/tasks/:taskId/consents/:cardId`, () => {
        fullGets += 1;
        return HttpResponse.json({ mode: 'toolbox', card: fileCard('pending', true) });
      }),
      http.post(`${TOOLBOX}/projects/:projectId/tasks/:taskId/consents/:cardId`, async ({ request }) => {
        posts.push(await request.json() as Record<string, unknown>);
        return HttpResponse.json({ mode: 'toolbox', ok: true, card: fileCard('approved', true), result: '' }, { status: 202 });
      }),
    );
    const user = userEvent.setup();
    renderTask();
    expect(await screen.findByText('人工精确文件计划')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '确认本次操作' })).not.toBeInTheDocument();
    expect(posts).toHaveLength(0);
    await user.click(screen.getByRole('link', { name: /审阅完整文件计划/ }));
    expect(await screen.findByText(/echo exact review text/)).toBeInTheDocument();
    expect(fullGets).toBeGreaterThan(0);
    await user.click(screen.getByLabelText('文件授权计算'));
    await user.click((await screen.findAllByText(/计算 A · 当前尝试 attempt-a/)).at(-1)!);
    const card = screen.getByText('完整文件确认卡与回执').closest('.ant-card')!;
    await user.click(within(card as HTMLElement).getByRole('button', { name: '批准文件计划' }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]).toEqual({ approved: true, scope_confirmation: { scope_id: SCOPE_ID, version: 1 } });
  });

  it('unknown 只做显式 reconcile，不把文件动作重新计划或批准', async () => {
    const writes: string[] = [];
    server.use(
      http.get(`${TOOLBOX}/projects/:projectId/tasks/:taskId/detail`, () => HttpResponse.json(detail('task-one', 'unknown'))),
      http.get(`${TOOLBOX}/projects/:projectId/tasks/:taskId/consents/:cardId`, () => HttpResponse.json({ mode: 'toolbox', card: fileCard('unknown', true) })),
      http.post(`${TOOLBOX}/projects/:projectId/tasks/:taskId/file-actions/:actionId/reconcile`, async ({ request }) => {
        writes.push(`reconcile:${await request.text()}`);
        return HttpResponse.json({ mode: 'toolbox', ok: true, data: fileCard('unknown', false) });
      }),
      http.post(`${TOOLBOX}/projects/:projectId/tasks/:taskId/tools`, () => { writes.push('tool'); return HttpResponse.json({}); }),
      http.post(`${TOOLBOX}/projects/:projectId/tasks/:taskId/consents/:cardId`, () => { writes.push('consent'); return HttpResponse.json({}); }),
    );
    const user = userEvent.setup();
    renderTask(`/toolbox/projects/project/tasks/task-one?fileAction=${ACTION_ID}#toolbox-files`);
    expect(await screen.findByRole('button', { name: '核对未知结果' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '核对未知结果' }));
    await waitFor(() => expect(writes).toHaveLength(1));
    expect(writes).toEqual(['reconcile:{}']);
    expect(screen.getByText(/结果未知保留/)).toBeInTheDocument();
  });

  it('文件计划已受理而detail回读挂起时切计算，不套旧卡且新计算仍可操作', async () => {
    const jobs = [
      { key: 'a', label: '计算 A', kind: 'custom', requires: [], status: 'draft', attempt_id: 'attempt-a' },
      { key: 'b', label: '计算 B', kind: 'custom', requires: [], status: 'draft', attempt_id: 'attempt-b' },
    ];
    const task = () => {
      const value = detail();
      value.flow.jobs = jobs;
      value.consents = [];
      value.file_actions = [];
      return value;
    };
    let afterPlan = false;
    let refetchStarted = false;
    let releaseRefetch = () => {};
    const blocked = new Promise<void>((resolve) => { releaseRefetch = resolve; });
    let fullCardGets = 0;
    server.use(
      http.get(`${TOOLBOX}/projects/:projectId/tasks/:taskId/detail`, async () => {
        if (afterPlan) { refetchStarted = true; await blocked; }
        return HttpResponse.json(task());
      }),
      http.post(`${TOOLBOX}/projects/:projectId/tasks/:taskId/tools`, async ({ request }) => {
        const body = await request.json() as { name: string };
        expect(body.name).toBe('remote_file_plan');
        afterPlan = true;
        return HttpResponse.json({ mode: 'toolbox', ok: true, pending: fileCard('pending', false), data: fileCard('pending', false) });
      }),
      http.get(`${TOOLBOX}/projects/:projectId/tasks/:taskId/consents/:cardId`, () => {
        fullCardGets += 1;
        return HttpResponse.json({ mode: 'toolbox', card: fileCard('pending', true) });
      }),
    );
    const user = userEvent.setup();
    renderTask();
    await screen.findByText('远端文件准备与授权');
    await user.click(screen.getByLabelText('文件授权计算'));
    await user.click((await screen.findAllByText(/计算 A · 当前尝试 attempt-a/)).at(-1)!);
    await user.click(screen.getByLabelText('文件范围'));
    await user.click((await screen.findAllByText(/待首次确认/)).at(-1)!);
    await screen.findByLabelText('写入的完整文本');
    await screen.findByLabelText('文件目标相对路径');
    await user.type(screen.getByLabelText('文件目标相对路径'), 'a/notes.txt');
    await user.type(screen.getByLabelText('写入的完整文本'), 'text for A');
    await user.click(screen.getByRole('button', { name: '加入文件项' }));
    await user.click(screen.getByRole('button', { name: '生成文件确认卡' }));
    await waitFor(() => expect(refetchStarted).toBe(true));
    await user.click(screen.getByLabelText('文件授权计算'));
    await user.click((await screen.findAllByText(/计算 B · 当前尝试 attempt-b/)).at(-1)!);
    await act(async () => { releaseRefetch(); });
    await waitFor(() => expect(screen.getAllByText(/计算 B · 当前尝试 attempt-b/).length).toBeGreaterThan(0));
    expect(screen.queryByText('完整文件确认卡与回执')).not.toBeInTheDocument();
    expect(fullCardGets).toBe(0);
    await user.click(screen.getByLabelText('范围研究根'));
    await user.click((await screen.findAllByText(/research（版本 1/)).at(-1)!);
    expect(screen.getByRole('button', { name: '创建文件范围' })).toBeEnabled();
  });

  it('部分发布与链接风险按逐项回执显示，不能把unknown当整批成功', async () => {
    const mixed = fileCard('unknown', true);
    mixed.binding.manifest.items = [
      { item_id: 'copied', op: 'copy', destination: { root_id: ROOT_ID, relative_path: 'a/POSCAR' }, bytes: 12, content_class: 'text', source: { requested_path: '/readonly/source/reuse.txt', canonical_path: '/readonly/source/reuse.txt' } },
      { item_id: 'linked', op: 'symlink', destination: { root_id: ROOT_ID, relative_path: 'a/CHGCAR' }, bytes: 0, content_class: 'large_vasp', source: { requested_path: '/readonly/source/CHGCAR', canonical_path: '/readonly/source/CHGCAR' } },
      { item_id: 'later', op: 'mkdir', destination: { root_id: ROOT_ID, relative_path: 'a/later' }, bytes: 0, content_class: 'ordinary' },
    ] as typeof mixed.binding.manifest.items;
    mixed.receipt.phase = 'finished';
    mixed.receipt.items = [
      { item_id: 'copied', state: 'committed' },
      { item_id: 'linked', state: 'unknown' },
      { item_id: 'later', state: 'not_executed' },
    ] as typeof mixed.receipt.items;
    mixed.receipt.spent = { operations: 1, bytes: 12 };
    mixed.receipt.held_unknown = { operations: 1, bytes: 0 };
    mixed.receipt.released = { operations: 1, bytes: 0 };
    const task = detail('task-one', 'unknown');
    task.file_actions = [mixed];
    server.use(
      http.get(`${TOOLBOX}/projects/:projectId/tasks/:taskId/detail`, () => HttpResponse.json(task)),
      http.get(`${TOOLBOX}/projects/:projectId/tasks/:taskId/consents/:cardId`, () => HttpResponse.json({ mode: 'toolbox', card: mixed })),
    );
    renderTask(`/toolbox/projects/project/tasks/task-one?fileAction=${ACTION_ID}#toolbox-files`);
    expect(await screen.findByText(/计算可能沿软链接写回来源/)).toBeInTheDocument();
    expect(screen.getByText(/已完成：1 项；结果未知保留：1 项；已证明未执行：1 项/)).toBeInTheDocument();
    expect(screen.getByText(/链接实际指向 \/readonly\/source\/CHGCAR/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '批准文件计划' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '核对未知结果' })).toBeInTheDocument();
  });

  it('执行中撤销只发scope revoke，页面保留在途发布可能生效的提示', async () => {
    const task = detail('task-one', 'executing');
    task.file_scopes[0].state = 'active';
    const executing = fileCard('executing', true);
    executing.receipt = { ...executing.receipt, cancel_requested_at: '2026-09-23T08:00:00Z' };
    task.file_actions = [executing];
    const calls: Array<{ path: string; body: unknown }> = [];
    server.use(
      http.get(`${TOOLBOX}/projects/:projectId/tasks/:taskId/detail`, () => HttpResponse.json(task)),
      http.get(`${TOOLBOX}/projects/:projectId/tasks/:taskId/consents/:cardId`, () => HttpResponse.json({ mode: 'toolbox', card: executing })),
      http.post(`${TOOLBOX}/projects/:projectId/tasks/:taskId/computation-scopes/:scopeId/revoke`, async ({ request }) => {
        calls.push({ path: 'revoke', body: await request.json() });
        return HttpResponse.json({ mode: 'toolbox', ok: true, data: { ...task.file_scopes[0], state: 'revoked' } });
      }),
      http.post(`${TOOLBOX}/projects/:projectId/tasks/:taskId/consents/:cardId`, () => {
        calls.push({ path: 'consent', body: null });
        return HttpResponse.json({});
      }),
    );
    const user = userEvent.setup();
    renderTask(`/toolbox/projects/project/tasks/task-one?fileAction=${ACTION_ID}#toolbox-files`);
    expect(await screen.findByText(/已请求撤销；在途发布仍可能生效/)).toBeInTheDocument();
    await user.click(screen.getByLabelText('文件范围'));
    await user.click((await screen.findAllByText(/已激活/)).at(-1)!);
    await user.click(screen.getByRole('button', { name: '撤销文件范围' }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]).toEqual({ path: 'revoke', body: { expected_version: 1, reason: '用户在任务页撤销' } });
  });
});
