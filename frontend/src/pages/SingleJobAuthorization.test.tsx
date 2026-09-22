import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { HttpResponse, http } from 'msw';
import { routes } from '../router';
import { server } from '../mocks/server';

const TOOLBOX = '/api/v1/toolbox';

type Job = Record<string, unknown>;

const taskDetail = (jobs: Job[], taskId = 'task-two') => ({
  mode: 'toolbox',
  task_id: taskId,
  task: {
    id: taskId, project_id: 'project-one', title: '单计算授权', goal: '逐计算确认',
    local_workspace: 'D:\\calc\\si', hpc_workspace: '/work/si', status: 'planned', updated_at: '2026-09-22T08:00:00Z', execution_mode: 'Fake',
  },
  flow: {
    execution_mode: 'Fake', phase: 'monitoring', goal: '逐计算确认', strategy: '按依赖顺序',
    local_dir: 'D:\\calc\\si', hpc_dir: '/work/si', waiting: [],
    precheck: { ok: false, issues: [], per_job: jobs.length > 1 }, report: '', jobs, draft: [], artifacts: {},
  },
  consents: [] as Array<Record<string, unknown>>, events: [], monitor: { state: 'monitoring', interval_seconds: 60, remote_cancelled: false }, backend_mode: 'Fake',
});

const job = (key: string, attemptId?: string, status = 'draft'): Job => ({
  key, label: `${key} 计算`, kind: 'static', requires: [], status, slurm_id: null,
  submission_state: 'not_submitted', ...(attemptId ? { attempt_id: attemptId } : {}),
  precheck: attemptId ? { attempt_id: attemptId, ok: key === 'B', hard: true, digest: `${key}-digest`, issues: [] } : undefined,
  draft: attemptId ? { attempt_id: attemptId, job_key: key, dir: `/work/${key}` } : undefined,
});

const renderRoute = (path = '/toolbox/projects/project-one/tasks/task-two') => {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={queryClient}><RouterProvider router={router} /></QueryClientProvider>);
  return { router, queryClient };
};

describe('单计算授权前端兼容', () => {
  it('多计算未选择时不发送准备或提交请求，并显示逐计算预检而非全局放行', async () => {
    const seen: unknown[] = [];
    const first = job('A', 'a-1');
    delete (first.precheck as Record<string, unknown>).hard;
    const response = taskDetail([first, job('B', 'b-1')]);
    response.consents = [{
      card_id: 'submit-b', action_id: 'submit-b', kind: 'submit', summary: '后端提交确认摘要', state: 'pending',
      args: { job_key: 'B' }, binding: { attempt_id: 'b-1', scope_id: 'scope-b' },
    }];
    server.use(
      http.get(`${TOOLBOX}/projects/:projectId/tasks/:taskId/detail`, () => HttpResponse.json(response)),
      http.post(`${TOOLBOX}/projects/:projectId/tasks/:taskId/tools`, async ({ request }) => {
        seen.push(await request.json());
        return HttpResponse.json({ mode: 'toolbox', task_id: 'task-two', ok: true, error: null, result: '', pending: null, flow: taskDetail([]).flow });
      }),
    );
    const user = userEvent.setup();
    renderRoute();

    expect(await screen.findByText('多个计算必须先选择一个具有当前尝试身份的计算；不会默认代为选择。')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '认领脚本 / 生成草稿' })).toBeDisabled();
    expect(screen.getByRole('button', { name: /运行硬预检/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: /请求提交确认/ })).toBeDisabled();
    expect(screen.getByText('预检概览')).toBeInTheDocument();
    expect(screen.getByText('预检：未通过（硬预检标记未记录） · 摘要 A-digest')).toBeInTheDocument();
    expect(screen.getByText('预检：通过（硬预检） · 摘要 B-digest')).toBeInTheDocument();
    expect(screen.getByText('计算：B')).toBeInTheDocument();
    expect(screen.getAllByText('尝试：b-1').length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText('范围：scope-b')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /请求提交确认/ }));
    expect(seen).toEqual([]);
  });

  it('选择 B 后三个授权操作均只发送 B 的当前尝试，且 A 监控中不禁用 B', async () => {
    const seen: Array<{ name: string; args: Record<string, unknown> }> = [];
    const response = taskDetail([job('A', 'a-1', 'monitoring'), job('B', 'b-1')]);
    server.use(
      http.get(`${TOOLBOX}/projects/:projectId/tasks/:taskId/detail`, () => HttpResponse.json(response)),
      http.post(`${TOOLBOX}/projects/:projectId/tasks/:taskId/tools`, async ({ request }) => {
        seen.push(await request.json() as { name: string; args: Record<string, unknown> });
        return HttpResponse.json({ mode: 'toolbox', task_id: 'task-two', ok: true, error: null, result: '已受理', pending: null, flow: response.flow });
      }),
    );
    const user = userEvent.setup();
    renderRoute();
    await screen.findAllByText('A 计算');
    await user.click(screen.getByLabelText('提交授权计算'));
    await user.click(await screen.findByText('B 计算 · 尝试 b-1'));
    expect(screen.getByRole('button', { name: /请求提交确认/ })).toBeEnabled();

    await user.click(screen.getByRole('button', { name: '认领脚本 / 生成草稿' }));
    await user.click(screen.getByRole('button', { name: /运行硬预检/ }));
    await user.click(screen.getByRole('button', { name: /请求提交确认/ }));
    await waitFor(() => expect(seen).toHaveLength(3));
    expect(seen).toEqual([
      { name: 'draft', args: { job_key: 'B', attempt_id: 'b-1' } },
      { name: 'precheck', args: { job_key: 'B', attempt_id: 'b-1' } },
      { name: 'submit', args: { job_key: 'B', attempt_id: 'b-1' } },
    ]);
  });

  it('旧单计算缺少 attempt_id 时保留空参数兼容调用', async () => {
    let requestBody: unknown;
    const response = taskDetail([job('legacy')]);
    server.use(
      http.get(`${TOOLBOX}/projects/:projectId/tasks/:taskId/detail`, () => HttpResponse.json(response)),
      http.post(`${TOOLBOX}/projects/:projectId/tasks/:taskId/tools`, async ({ request }) => {
        requestBody = await request.json();
        return HttpResponse.json({ mode: 'toolbox', task_id: 'task-two', ok: true, error: null, result: '', pending: null, flow: response.flow });
      }),
    );
    const user = userEvent.setup();
    renderRoute();
    expect(await screen.findByText('旧单计算兼容参数')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /运行硬预检/ })).toBeEnabled();
    await user.click(screen.getByRole('button', { name: /运行硬预检/ }));
    await waitFor(() => expect(requestBody).toEqual({ name: 'precheck', args: {} }));
  });

  it('旧终态恢复始终携带 job_key，缺少 attempt_id 时不被前端阻断', async () => {
    let requestBody: unknown;
    const failedLegacy = { ...job('B', undefined, 'failed'), diagnosis: { verdict: 'not_converged', reason: '收敛失败' } };
    const response = taskDetail([job('A', 'a-1'), failedLegacy]);
    server.use(
      http.get(`${TOOLBOX}/projects/:projectId/tasks/:taskId/detail`, () => HttpResponse.json(response)),
      http.post(`${TOOLBOX}/projects/:projectId/tasks/:taskId/tools`, async ({ request }) => {
        requestBody = await request.json();
        return HttpResponse.json({ mode: 'toolbox', task_id: 'task-two', ok: true, error: null, result: '', pending: null, flow: response.flow });
      }),
    );
    const user = userEvent.setup();
    renderRoute();
    await screen.findByText('收敛失败');
    const retry = screen.getAllByRole('button', { name: '请求恢复确认' }).find((button) => !button.hasAttribute('disabled'));
    expect(retry).toBeDefined();
    expect(retry).toBeEnabled();
    await user.click(retry!);
    await waitFor(() => expect(requestBody).toEqual({ name: 'retry_job', args: { job_key: 'B' } }));
  });

  it('切换任务或尝试后清空旧选择，不能重用旧授权参数', async () => {
    let currentAttempt = 'b-1';
    let taskThreeAttempt = 'b-3';
    server.use(http.get(`${TOOLBOX}/projects/:projectId/tasks/:taskId/detail`, ({ params }) => {
      const taskId = String(params.taskId);
      return HttpResponse.json(taskDetail([job('A', 'a-1'), job('B', taskId === 'task-three' ? taskThreeAttempt : currentAttempt)], taskId));
    }));
    const user = userEvent.setup();
    const { router, queryClient } = renderRoute();
    await screen.findAllByText('A 计算');
    await user.click(screen.getByLabelText('提交授权计算'));
    await user.click(await screen.findByText('B 计算 · 尝试 b-1'));
    expect(screen.getByRole('button', { name: /请求提交确认/ })).toBeEnabled();

    await act(async () => { await router.navigate('/toolbox/projects/project-one/tasks/task-three'); });
    await screen.findByText('尝试：b-3');
    await waitFor(() => expect(screen.getByRole('button', { name: /请求提交确认/ })).toBeDisabled());

    await user.click(screen.getByLabelText('提交授权计算'));
    await user.click(await screen.findByText('B 计算 · 尝试 b-3'));
    expect(screen.getByRole('button', { name: /请求提交确认/ })).toBeEnabled();
    taskThreeAttempt = 'b-4';
    await act(async () => { await queryClient.refetchQueries({ queryKey: ['toolboxTaskDetail', 'project-one', 'task-three'] }); });
    await waitFor(() => expect(screen.getByRole('button', { name: /请求提交确认/ })).toBeDisabled());
  });
});
