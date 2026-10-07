import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Routes, Route, useLocation } from 'react-router-dom';
import { HttpResponse, http } from 'msw';
import { ConfigProvider, message } from 'antd';
import { server } from '../mocks/server';
import AiProjectsPage from './AiProjectsPage';
import type { AiProject } from '../types/ai';

function Destination() { return <div data-testid="destination">{useLocation().pathname}</div>; }

const renderPage = () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<ConfigProvider theme={{ token: { motion: false } }}><QueryClientProvider client={client}><MemoryRouter initialEntries={['/ai']}><Routes>
    <Route path="/ai" element={<AiProjectsPage />} /><Route path="*" element={<Destination />} />
  </Routes></MemoryRouter></QueryClientProvider></ConfigProvider>);
};
afterEach(() => message.destroy());

const project = (id: string, created_at: string, updated_at?: string): AiProject => ({
  id, name: `研究 ${id}`, description: `说明 ${id}`, created_at, updated_at, job_count: 2, context_ratio: 0,
});
const mockProjects = (projects: AiProject[]) => server.use(
  http.get('/ai/v1/projects', () => HttpResponse.json({ projects })),
  http.get('/ai/v1/jobs/waiting', () => HttpResponse.json({ waiting: [], count: 0 })),
);

it('shows project failure without asserting an empty project list and retries only projects', async () => {
  let projects = 0;
  let queue = 0;
  server.use(
    http.get('/ai/v1/projects', () => {
      projects += 1;
      if (projects === 1) return HttpResponse.text('Bad Gateway', { status: 502 });
      return HttpResponse.json({ projects: [] });
    }),
    http.get('/ai/v1/jobs/waiting', () => { queue += 1; return HttpResponse.json({ waiting: [], count: 0 }); }),
  );
  renderPage();
  expect(await screen.findByText('项目加载失败')).toBeInTheDocument();
  expect(screen.queryByText(/共 0 个项目/)).not.toBeInTheDocument();
  expect(screen.queryByText(/暂无项目/)).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: '重试项目' }));
  expect(await screen.findByText(/暂无项目/)).toBeInTheDocument();
  expect(projects).toBe(2);
  expect(queue).toBe(1);
});

it('shows queue failure independently and retries only the queue', async () => {
  let projects = 0;
  let queue = 0;
  server.use(
    http.get('/ai/v1/projects', () => { projects += 1; return HttpResponse.json({ projects: [] }); }),
    http.get('/ai/v1/jobs/waiting', () => {
      queue += 1;
      if (queue === 1) return HttpResponse.text('Service Unavailable', { status: 503 });
      return HttpResponse.json({ waiting: [], count: 0 });
    }),
  );
  renderPage();
  expect(await screen.findByText('等待队列加载失败')).toBeInTheDocument();
  expect(screen.queryByText(/当前无排队作业/)).not.toBeInTheDocument();
  expect(screen.getByText(/暂无项目/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: '重试队列' }));
  await waitFor(() => expect(screen.getByText(/当前无排队作业/)).toBeInTheDocument());
  expect(projects).toBe(1);
  expect(queue).toBe(2);
});

it('sorts descending by creation or update time, falling back to creation when update is absent', async () => {
  mockProjects([
    project('OldUpdated', '2026-01-01', '2026-04-01'),
    project('NewestCreated', '2026-03-01'),
    project('Middle', '2026-02-01', '2026-02-02'),
  ]);
  renderPage();
  const names = () => screen.getAllByRole('button', { name: /^选择项目/ }).map(el => el.getAttribute('aria-label'));
  await screen.findByRole('button', { name: '选择项目 研究 OldUpdated' });
  expect(names()).toEqual(['选择项目 研究 NewestCreated', '选择项目 研究 Middle', '选择项目 研究 OldUpdated']);
  await userEvent.click(screen.getByText('按修改时间'));
  expect(names()).toEqual(['选择项目 研究 OldUpdated', '选择项目 研究 NewestCreated', '选择项目 研究 Middle']);
  await userEvent.click(screen.getByText('按创建时间'));
  expect(names()[0]).toBe('选择项目 研究 NewestCreated');
});

it('validates whitespace, cancels without writing, and preserves the raw create payload and destination ID', async () => {
  mockProjects([]);
  const bodies: unknown[] = [];
  server.use(http.post('/ai/v1/projects', async ({ request }) => {
    bodies.push(await request.json());
    return HttpResponse.json({ project: project('CaseSensitive-ID', '2026-01-01') });
  }));
  renderPage();
  await userEvent.click(await screen.findByRole('button', { name: '新建项目' }));
  const dialog = await screen.findByRole('dialog');
  await userEvent.type(within(dialog).getByLabelText('项目名称'), '   ');
  await userEvent.click(within(dialog).getByRole('button', { name: /^创\s*建$/ }));
  expect(await screen.findByText('请输入项目名称')).toBeInTheDocument();
  expect(bodies).toEqual([]);
  await userEvent.click(within(dialog).getByRole('button', { name: /Cancel|取\s*消/ }));
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  expect(bodies).toEqual([]);
  await userEvent.click(screen.getByRole('button', { name: '新建项目' }));
  const reopened = await screen.findByRole('dialog');
  const name = within(reopened).getByLabelText('项目名称');
  await userEvent.clear(name);
  await userEvent.type(name, '  Fe2O3 研究  ');
  await userEvent.type(within(reopened).getByLabelText('描述（可选）'), '  保留描述  ');
  await userEvent.click(within(reopened).getByRole('button', { name: /^创\s*建$/ }));
  expect(await screen.findByTestId('destination')).toHaveTextContent('/ai/projects/CaseSensitive-ID');
  expect(bodies).toEqual([{ name: '  Fe2O3 研究  ', description: '  保留描述  ' }]);
});

it('keeps the creation draft after a request error and permits retry', async () => {
  mockProjects([]);
  let writes = 0;
  server.use(http.post('/ai/v1/projects', () => {
    writes += 1;
    if (writes === 1) return HttpResponse.json({ error: { code: 'CREATE_FAILED', message: '创建请求失败', retryable: true } }, { status: 503 });
    return HttpResponse.json({ project: project('Retry-ID', '2026-01-01') });
  }));
  renderPage();
  await userEvent.click(await screen.findByRole('button', { name: '新建项目' }));
  const dialog = await screen.findByRole('dialog');
  await userEvent.type(within(dialog).getByLabelText('项目名称'), '保留的名称');
  await userEvent.type(within(dialog).getByLabelText('描述（可选）'), '保留的说明');
  await userEvent.click(within(dialog).getByRole('button', { name: /^创\s*建$/ }));
  expect(await screen.findByText('创建请求失败')).toBeInTheDocument();
  expect(within(dialog).getByLabelText('项目名称')).toHaveValue('保留的名称');
  expect(within(dialog).getByLabelText('描述（可选）')).toHaveValue('保留的说明');
  await userEvent.click(within(dialog).getByRole('button', { name: /^创\s*建$/ }));
  expect(await screen.findByTestId('destination')).toHaveTextContent('/ai/projects/Retry-ID');
  expect(writes).toBe(2);
});

it('cancels deletion without a request, retains the project after failure, then deletes the exact ID', async () => {
  const target = project('CaseSensitive-ID', '2026-01-01');
  let deleted = false;
  const ids: string[] = [];
  mockProjects([target]);
  server.use(
    http.get('/ai/v1/projects', () => HttpResponse.json({ projects: deleted ? [] : [target] })),
    http.delete('/ai/v1/projects/:projectId', ({ params }) => {
      ids.push(String(params.projectId));
      if (ids.length === 1) return HttpResponse.json({ error: { code: 'FILE_AUDIT_RETAINED', message: '须保留远端文件审计', retryable: false } }, { status: 409 });
      deleted = true;
      return HttpResponse.json({ deleted: true });
    }),
  );
  renderPage();
  await userEvent.click(await screen.findByRole('button', { name: '删除项目' }));
  expect(await screen.findByText('将删除该项目及其计算任务记录；不会取消超算作业或删除工作区文件。')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: /Cancel|取\s*消/ }));
  expect(ids).toEqual([]);
  await userEvent.click(screen.getByRole('button', { name: '删除项目' }));
  await userEvent.click(await screen.findByRole('button', { name: /^(OK|确\s*定)$/ }));
  expect(await screen.findByText('须保留远端文件审计')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '选择项目 研究 CaseSensitive-ID' })).toBeInTheDocument();
  await waitFor(() => expect(screen.queryByRole('button', { name: /OK|确\s*定/ })).not.toBeInTheDocument());
  await userEvent.click(screen.getByRole('button', { name: '删除项目' }));
  await userEvent.click(await screen.findByRole('button', { name: /^(OK|确\s*定)$/ }));
  expect(await screen.findByText(/暂无项目/)).toBeInTheDocument();
  expect(ids).toEqual(['CaseSensitive-ID', 'CaseSensitive-ID']);
});

it('supports keyboard selection and keeps delete/enter controls independent', async () => {
  mockProjects([project('CaseSensitive-ID', '2026-01-01')]);
  renderPage();
  const select = await screen.findByRole('button', { name: '选择项目 研究 CaseSensitive-ID' });
  select.focus();
  await userEvent.keyboard(' ');
  expect(select).toHaveAttribute('aria-pressed', 'true');
  expect(screen.queryByTestId('destination')).not.toBeInTheDocument();
  await userEvent.tab();
  expect(screen.getByRole('button', { name: '删除项目' })).toHaveFocus();
  await userEvent.tab();
  expect(screen.getByRole('button', { name: /进\s*入/ })).toHaveFocus();
  await userEvent.keyboard('{Enter}');
  expect(await screen.findByTestId('destination')).toHaveTextContent('/ai/projects/CaseSensitive-ID');
});

it('uses the returned queue count and fields, with no submit action when conditions change', async () => {
  mockProjects([]);
  server.use(http.get('/ai/v1/jobs/waiting', () => HttpResponse.json({ count: 9, waiting: [
    { task_title: '表面计算', reason: '等待前置任务完成 / long-reason', queued_at: '2026-10-07T10:30:00Z' },
    { reason: '等待可用空位', queued_at: 'invalid' },
  ] })));
  renderPage();
  await screen.findByText('表面计算');
  const heading = await screen.findByRole('heading', { name: '等待空位队列' });
  expect(heading.parentElement).toHaveTextContent('9');
  expect(screen.getByText('表面计算')).toBeInTheDocument();
  expect(screen.getByText('待定任务')).toBeInTheDocument();
  expect(screen.getByText('等待前置任务完成 / long-reason')).toBeInTheDocument();
  expect(screen.getByText(/排队时间 —/)).toBeInTheDocument();
  expect(screen.getByText('条件满足后重新预检并确认提交，不自动补提。')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /提交/ })).not.toBeInTheDocument();
});
