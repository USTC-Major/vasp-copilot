import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { HttpResponse, http } from 'msw';
import { routes } from '../../router';
import { server } from '../../mocks/server';

const TOOLBOX = '/api/v1/toolbox';
const ACTION_ID = 'a'.repeat(32);
const SCOPE_ID = 'b'.repeat(32);
const ROOT_ID = 'c'.repeat(32);
const expiry = new Date(Date.now() + 60 * 60_000).toISOString();

const scope = (state: 'proposed' | 'active') => ({
  scope_id: SCOPE_ID, kind: 'file', version: 1, state, approval_mode: 'reviewer',
  job_key: 'a', attempt_id: 'attempt-a', allowed_operations: ['copy'],
  source_policy: 'exact_sources', source_bindings: [{ requested_path: '/outside/notes.txt', canonical_path: '/outside/notes.txt' }],
  root_bindings: [{ root_id: ROOT_ID, version: 1, destination_prefixes: ['a'] }],
  max_operations: 1, max_total_bytes: 1024, expires_at: expiry,
});

const card = (review?: { state: string; reason?: string }) => ({
  card_id: ACTION_ID, action_id: ACTION_ID, kind: 'remote_file', state: 'pending',
  binding_hash: 'd'.repeat(64), expires_at: expiry, review,
  binding: {
    project_id: 'project', task_id: 'task-one', scope_id: SCOPE_ID, scope_version: 1,
    job_key: 'a', attempt_id: 'attempt-a', endpoint_digest: 'e'.repeat(64),
    manifest: {
      endpoint: { host: 'fixture.invalid', port: 22, username: 'fixture' },
      roots: [{ root_id: ROOT_ID, version: 1, requested_path: '/research', canonical_path: '/research' }],
      items: [{ item_id: 'copy', op: 'copy', destination: { root_id: ROOT_ID, relative_path: 'a/notes.txt' },
        source: { requested_path: '/outside/notes.txt', canonical_path: '/outside/notes.txt', size: 12 },
        bytes: 12, content_class: 'unclassified_external' }],
    },
  },
  receipt: { phase: 'pending', items: [] },
});

const detail = (scopeState: 'proposed' | 'active', action?: ReturnType<typeof card>) => ({
  mode: 'toolbox', task_id: 'task-one',
  task: { id: 'task-one', project_id: 'project', title: 'reviewer QA', goal: '', local_workspace: null,
    hpc_workspace: '/research', status: 'planned', updated_at: '2026-09-24T00:00:00Z', execution_mode: 'Fake' },
  flow: { execution_mode: 'Fake', phase: 'draft', goal: '', strategy: '', local_dir: '', hpc_dir: '/research',
    waiting: [], precheck: { ok: false, issues: [] }, report: '',
    jobs: [{ key: 'a', label: '计算 A', kind: 'custom', requires: [], status: 'draft', attempt_id: 'attempt-a' }],
    draft: [], artifacts: {} },
  consents: action ? [action] : [],
  file_roots: [{ root_id: ROOT_ID, version: 1, requested_path: '/research', canonical_path: '/research' }],
  file_roots_version: 1, file_scopes: [scope(scopeState)], file_actions: action ? [action] : [],
  events: [], monitor: { state: 'idle', interval_seconds: 60, remote_cancelled: false }, backend_mode: 'Fake',
});

function renderTask(path = '/toolbox/projects/project/tasks/task-one') {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={queryClient}><RouterProvider router={router} /></QueryClientProvider>);
}

describe('independent reviewer UI authorization', () => {
  it('shows frozen scope and activates only after a separate user click', async () => {
    let active = false;
    const calls: Array<{ path: string; body: unknown }> = [];
    server.use(
      http.get(`${TOOLBOX}/projects/:projectId/tasks/:taskId/detail`, () => HttpResponse.json(detail(active ? 'active' : 'proposed'))),
      http.get(`${TOOLBOX}/reviewer/status`, () => HttpResponse.json({ mode: 'toolbox', configured: true, reason_code: 'READY' })),
      http.post(`${TOOLBOX}/projects/:projectId/tasks/:taskId/computation-scopes/:scopeId/activate`, async ({ request }) => {
        calls.push({ path: 'activate', body: await request.json() });
        active = true;
        return HttpResponse.json({ mode: 'toolbox', ok: true, data: scope('active') });
      }),
      http.post(`${TOOLBOX}/projects/:projectId/tasks/:taskId/file-actions/:actionId/review`, () => {
        calls.push({ path: 'review', body: null });
        return HttpResponse.json({});
      }),
    );
    const user = userEvent.setup();
    renderTask();
    await screen.findByText('远端文件准备与授权');
    await user.click(screen.getByLabelText('文件范围'));
    await user.click((await screen.findAllByText(/待首次确认 · reviewer/)).at(-1)!);
    expect(screen.getByText('服务器冻结的文件范围')).toBeInTheDocument();
    expect(screen.getByText(/模型不接收或审阅文件正文/)).toBeInTheDocument();
    expect(calls).toEqual([]);
    await user.click(screen.getByRole('button', { name: '确认以上范围并启用 reviewer' }));
    await waitFor(() => expect(calls).toEqual([
      { path: 'activate', body: { expected_version: 1, approval_mode: 'reviewer' } },
    ]));
  });

  it('keeps the full manual decision available when reviewer needs a human', async () => {
    const pending = card({ state: 'needs_human', reason: 'reviewer unavailable' });
    const calls: Array<{ path: string; body: unknown }> = [];
    server.use(
      http.get(`${TOOLBOX}/projects/:projectId/tasks/:taskId/detail`, () => HttpResponse.json(detail('active', pending))),
      http.get(`${TOOLBOX}/projects/:projectId/tasks/:taskId/consents/:cardId`, () => HttpResponse.json({ mode: 'toolbox', card: pending })),
      http.post(`${TOOLBOX}/projects/:projectId/tasks/:taskId/consents/:cardId`, async ({ request }) => {
        calls.push({ path: 'consent', body: await request.json() });
        return HttpResponse.json({ mode: 'toolbox', ok: true, card: { ...pending, state: 'approved' } }, { status: 202 });
      }),
      http.post(`${TOOLBOX}/projects/:projectId/tasks/:taskId/file-actions/:actionId/review`, () => {
        calls.push({ path: 'review', body: null });
        return HttpResponse.json({});
      }),
    );
    const user = userEvent.setup();
    renderTask(`/toolbox/projects/project/tasks/task-one?fileAction=${ACTION_ID}#toolbox-files`);
    await screen.findByText('远端文件准备与授权');
    await user.click(screen.getByLabelText('文件范围'));
    await user.click((await screen.findAllByText(/已激活 · reviewer/)).at(-1)!);
    expect(await screen.findByText('服务器冻结的文件范围')).toBeInTheDocument();
    expect((await screen.findAllByText('需要人工审阅')).length).toBeGreaterThan(0);
    expect(screen.getByText(/模型不接收或审阅文件正文/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '批准文件计划' }));
    await waitFor(() => expect(calls).toEqual([{ path: 'consent', body: { approved: true } }]));
  });
});
