import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { HttpResponse, http } from 'msw';
import { routes } from '../router';
import { server } from '../mocks/server';
import AiDirectoryPicker from '../components/ai/AiDirectoryPicker';

const TOOLBOX = '/api/v1/toolbox';

const detail = (phase = 'planning') => ({
  mode: 'toolbox',
  task_id: 'task-two',
  task: {
    id: 'task-two', project_id: 'project-one', title: '第二个精确任务', goal: '完成基础 static 计算',
    local_workspace: 'D:\\calc\\si', hpc_workspace: '/work/si', status: phase, updated_at: '2026-09-21T08:00:00Z', execution_mode: 'Fake',
  },
  flow: {
    execution_mode: 'Fake', phase, goal: '完成基础 static 计算', strategy: '按依赖顺序',
    local_dir: 'D:\\calc\\si', hpc_dir: '/work/si', waiting: [], precheck: { ok: false, issues: [] }, report: '',
    jobs: [{ key: 'static', label: '静态计算', kind: 'static', requires: [], status: phase, slurm_id: null, submission_state: 'not_submitted', attempt_history: [{ state: 'prepared', at: '2026-09-21T08:00:00Z' }], diagnosis: { verdict: 'inputs_pending' } }],
    draft: [], artifacts: { poscar: { name: 'POSCAR', path: 'D:\\calc\\si\\POSCAR', size: 128, sha256: 'abc' } },
  },
  consents: [],
  events: [{ id: 1, project_id: 'project-one', task_id: 'task-two', kind: 'created', at: '2026-09-21T08:00:00Z', message: '任务已创建' }],
  monitor: { state: 'idle', interval_seconds: 60, remote_cancelled: false },
  backend_mode: 'Fake',
});

const renderRoute = (path: string) => {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={queryClient}><RouterProvider router={router} /></QueryClientProvider>);
  return { router, queryClient };
};

describe('Toolbox 无 AI 手动流程', () => {
  it('结构化规划使用工具合同，不发送 raw JSON', async () => {
    let requestBody: unknown;
    server.use(
      http.get(`${TOOLBOX}/projects/:projectId/tasks/:taskId/detail`, () => HttpResponse.json(detail())),
      http.post(`${TOOLBOX}/projects/:projectId/tasks/:taskId/tools`, async ({ request }) => {
        requestBody = await request.json();
        return HttpResponse.json({ mode: 'toolbox', task_id: 'task-two', ok: true, error: null, result: '计划已保存', pending: null, flow: detail().flow });
      }),
    );
    const user = userEvent.setup();
    renderRoute('/toolbox/projects/project-one/tasks/task-two');

    expect(await screen.findByRole('heading', { name: '第二个精确任务' })).toBeInTheDocument();
    expect(screen.getByLabelText('步骤 1 标识')).toHaveValue('static');
    expect(screen.queryByText(/raw JSON/i)).not.toBeInTheDocument();
    expect(screen.getByText('有界查看工作区文件')).toBeInTheDocument();
    expect(screen.getByText('4. Materials Project 结构导入')).toBeInTheDocument();
    expect(screen.getByText('6. 失败诊断与恢复')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /保存计算计划/ }));
    await waitFor(() => expect(requestBody).toEqual({
      name: 'plan',
      args: { strategy: '按依赖顺序完成基础计算', jobs: [{ key: 'static', label: '静态计算', kind: 'static', requires: [], description: '' }] },
    }));
  });

  it('提交授权失败时明确失败并重新读取，不显示提交成功', async () => {
    server.use(
      http.get(`${TOOLBOX}/projects/:projectId/tasks/:taskId/detail`, () => HttpResponse.json(detail('await_submit'))),
      http.post(`${TOOLBOX}/projects/:projectId/tasks/:taskId/tools`, () => HttpResponse.json({ mode: 'toolbox', error: { code: 'CONSENT_REQUIRED', message: '提交授权不存在或已失效', retryable: false } }, { status: 409 })),
    );
    const user = userEvent.setup();
    renderRoute('/toolbox/projects/project-one/tasks/task-two');
    await screen.findByRole('heading', { name: '第二个精确任务' });
    await user.click(screen.getByRole('button', { name: /请求提交确认/ }));
    expect(await screen.findAllByText('提交授权不存在或已失效')).not.toHaveLength(0);
    expect(screen.queryByText('提交成功')).not.toBeInTheDocument();
  });

  it('闲置页面每五秒读取 detail 并显示后来状态', async () => {
    let reads = 0;
    server.use(http.get(`${TOOLBOX}/projects/:projectId/tasks/:taskId/detail`, () => {
      reads += 1;
      const response = detail(reads >= 2 ? 'completed' : 'monitoring');
      response.task.updated_at = `2026-09-21T08:00:0${reads}Z`;
      response.task.title = reads >= 2 ? '服务端更新后的标题' : '第二个精确任务';
      return HttpResponse.json({
        ...response,
        monitor: {
          ...response.monitor,
          state: 'monitoring',
          interval_seconds: 60,
          last_success_at: '2020-01-01T00:00:00Z',
          remote_cancelled: false,
        },
      });
    }));
    const user = userEvent.setup();
    renderRoute('/toolbox/projects/project-one/tasks/task-two');
    expect(await screen.findAllByText('阶段：监控中')).not.toHaveLength(0);
    const titleInput = screen.getByLabelText('任务标题');
    await user.clear(titleInput);
    await user.type(titleInput, '尚未保存的用户编辑');
    expect(screen.getAllByText('最近采集已过期，请检查连接或 Toolbox 服务').length).toBeGreaterThan(0);
    expect(await screen.findAllByText('阶段：已完成', {}, { timeout: 7500 })).not.toHaveLength(0);
    expect(titleInput).toHaveValue('尚未保存的用户编辑');
    expect(reads).toBeGreaterThanOrEqual(2);
  });

  it('旧 progress URL 精确转到原任务，404 不默认选择其他任务', async () => {
    server.use(http.get(`${TOOLBOX}/projects/:projectId/tasks/:taskId/detail`, ({ params }) => {
      if (params.taskId === 'missing') return HttpResponse.json({ mode: 'toolbox', error: { code: 'TASK_NOT_FOUND', message: '任务不存在', retryable: false } }, { status: 404 });
      return HttpResponse.json(detail());
    }));
    const { router } = renderRoute('/ai/projects/project-one/progress/task-two');
    expect(await screen.findByRole('heading', { name: '第二个精确任务' })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/toolbox/projects/project-one/tasks/task-two');

    await act(async () => router.navigate('/toolbox/projects/project-one/tasks/missing'));
    expect(await screen.findByText('指定的 Toolbox 任务不存在')).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/toolbox/projects/project-one/tasks/missing');
  });

  it('目录选择与新建只调用 Toolbox API', async () => {
    const seen: string[] = [];
    server.use(
      http.get(`${TOOLBOX}/browse/local`, ({ request }) => {
        seen.push(new URL(request.url).pathname);
        const path = new URL(request.url).searchParams.get('path') ?? '';
        return HttpResponse.json({ mode: 'toolbox', kind: 'local', path, parent: null, exists: true, is_dir: true, entries: [], roots: path ? undefined : [{ name: 'D:\\', is_dir: true }] });
      }),
      http.post(`${TOOLBOX}/browse/local/mkdir`, async ({ request }) => {
        seen.push(new URL(request.url).pathname);
        return HttpResponse.json({ mode: 'toolbox', kind: 'local', ok: true, path: 'D:\\new' });
      }),
    );
    const user = userEvent.setup();
    render(<AiDirectoryPicker open kind="local" initialPath="D:\\" onSelect={() => undefined} onCancel={() => undefined} />);
    await screen.findByText('此目录下没有可用的子文件夹');
    await user.type(screen.getByLabelText('新建子目录名称'), 'new');
    await user.click(screen.getByRole('button', { name: /新建目录/ }));
    await waitFor(() => expect(seen).toContain('/api/v1/toolbox/browse/local/mkdir'));
    expect(seen.every((path) => path.startsWith('/api/v1/toolbox/'))).toBe(true);
  });

  it('确定性报告中的 Markdown 表格按表头和单元格展示', async () => {
    const response = detail('completed');
    response.flow.report = [
      '## 计算报告',
      '',
      '| 作业 | 状态 | 作业号 |',
      '| :--- | :---: | ---: |',
      '| 静态计算 | 已完成 | `7301` |',
    ].join('\n');
    server.use(http.get(`${TOOLBOX}/projects/:projectId/tasks/:taskId/detail`, () => HttpResponse.json(response)));
    const user = userEvent.setup();
    renderRoute('/toolbox/projects/project-one/tasks/task-two');

    await screen.findByRole('heading', { name: '第二个精确任务' });
    await user.click(screen.getByText('确定性报告'));
    const statusHeader = await screen.findByRole('columnheader', { name: '状态' });
    const table = statusHeader.closest('table');
    expect(table).not.toBeNull();
    expect(table).toHaveTextContent('作业');
    expect(table).toHaveTextContent('静态计算');
    expect(within(table!).getByRole('cell', { name: '7301' })).toBeInTheDocument();
    expect(screen.queryByText('| 作业 | 状态 | 作业号 |')).not.toBeInTheDocument();
  });
});
