import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { HttpResponse, http } from 'msw';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import HomePage from './HomePage';
import WorkflowHistoryPage from './WorkflowHistoryPage';
import { server } from '../mocks/server';
import type { HistoryRecord } from '../types/history';
import { historyRecordPath, mergeRecentRecords } from '../utils/history';

const renderHome = (existingClient?: QueryClient) => {
  const client = existingClient ?? new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const Location = () => <div data-testid="location">{useLocation().pathname}</div>;
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="*" element={<Location />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
};

const empty = (source: 'ai' | 'workflows' | 'diagnoses', retention: 'persistent' | 'ttl') =>
  HttpResponse.json({ source, retention, records: [] });

describe('HomePage recent history', () => {
  it('sorts, deduplicates, limits, and builds truthful routes', () => {
    const records: HistoryRecord[] = [
      { id: 'same', kind: 'workflow', title: 'old', status: 'planned', updated_at: '2026-01-01T00:00:00Z' },
      { id: 'same', kind: 'workflow', title: 'new', status: 'generated', updated_at: '2026-01-03T00:00:00Z' },
      { id: 'diag', kind: 'diagnosis', title: 'diag', status: 'succeeded', updated_at: '2026-01-02T00:00:00Z' },
    ];
    expect(mergeRecentRecords([records], 2).map((item) => item.title)).toEqual(['new', 'diag']);
    expect(historyRecordPath(records[1])).toBe('/workflow/history/same');
    expect(historyRecordPath({ ...records[2], id: 'a/b' })).toBe('/diagnosis/a%2Fb');
    expect(historyRecordPath({
      id: 'p:t', kind: 'ai_task', project_id: 'p', task_id: 't', title: 'task',
      status: 'idle', execution_mode: 'Real', updated_at: '2026-01-01T00:00:00Z',
    })).toBe('/ai/projects/p');
  });

  it('does not present the toolbox Fake switch as a global environment', async () => {
    server.use(http.get('/ai/v1/history/recent', () => HttpResponse.json({
      source: 'ai', retention: 'persistent', demo: true, records: [
        { id: 'p:real', kind: 'ai_task', project_id: 'p', task_id: 'real', title: '真实后端任务', status: 'running', execution_mode: 'Real', updated_at: '2026-09-18T03:00:00Z', demo: true },
        { id: 'p:fake', kind: 'ai_task', project_id: 'p', task_id: 'fake', title: '演示后端任务', status: 'idle', execution_mode: 'Fake', updated_at: '2026-09-18T02:00:00Z', demo: true },
        { id: 'p:none', kind: 'ai_task', project_id: 'p', task_id: 'none', title: '未配置后端任务', status: 'idle', execution_mode: 'None', updated_at: '2026-09-18T01:00:00Z', demo: true },
      ],
    })));
    renderHome();
    expect(screen.queryByText('运行环境以具体智能任务的 Real / Fake / None 标识为准')).not.toBeInTheDocument();
    expect(screen.getByText('远程部署（离线演示）')).toBeInTheDocument();
    expect(screen.queryByText(/模拟环境 - Fake HPC 模式/)).not.toBeInTheDocument();
    expect(await screen.findByText('真实后端任务')).toBeInTheDocument();
    expect(screen.getByText('Real')).toBeInTheDocument();
    expect(screen.getAllByText('Fake').length).toBeGreaterThan(0);
    expect(screen.getByText('None')).toBeInTheDocument();
    expect(await screen.findByText('演示数据')).toBeInTheDocument();
  });

  it('shows a real empty state only when all sources succeed empty', async () => {
    server.use(
      http.get('/ai/v1/history/recent', () => empty('ai', 'persistent')),
      http.get('/api/v1/workflows/recent', () => empty('workflows', 'ttl')),
      http.get('/api/v1/diagnosis/recent', () => empty('diagnoses', 'ttl')),
    );
    renderHome();
    expect(await screen.findByText('暂无可显示的真实记录')).toBeInTheDocument();
    expect(screen.queryByText(/历史暂不可用/)).not.toBeInTheDocument();
  });

  it('keeps successful records when one source fails', async () => {
    server.use(
      http.get('/ai/v1/history/recent', () => empty('ai', 'persistent')),
      http.get('/api/v1/workflows/recent', () => HttpResponse.json({ error: { code: 'DOWN', message: 'down' } }, { status: 503 })),
      http.get('/api/v1/diagnosis/recent', () => HttpResponse.json({
        source: 'diagnoses', retention: 'ttl', records: [{
          id: 'diag_real', kind: 'diagnosis', title: '真实诊断', status: 'succeeded',
          updated_at: '2026-09-18T01:00:00Z',
        }],
      })),
    );
    renderHome();
    expect(await screen.findByText('工作流历史暂不可用')).toBeInTheDocument();
    expect(screen.getByText('真实诊断')).toBeInTheDocument();
  });

  it('distinguishes total failure from an empty history', async () => {
    const fail = () => HttpResponse.json({ error: { code: 'DOWN', message: 'down' } }, { status: 503 });
    server.use(
      http.get('/ai/v1/history/recent', fail),
      http.get('/api/v1/workflows/recent', fail),
      http.get('/api/v1/diagnosis/recent', fail),
    );
    renderHome();
    expect(await screen.findByText('历史服务暂不可用')).toBeInTheDocument();
    expect(screen.queryByText('暂无可显示的真实记录')).not.toBeInTheDocument();
  });

  it('navigates a workflow record to the read-only detail route', async () => {
    server.use(
      http.get('/ai/v1/history/recent', () => empty('ai', 'persistent')),
      http.get('/api/v1/diagnosis/recent', () => empty('diagnoses', 'ttl')),
      http.get('/api/v1/workflows/recent', () => HttpResponse.json({
        source: 'workflows', retention: 'ttl', records: [{
          id: 'wf_real', kind: 'workflow', title: '真实工作流', status: 'generated',
          updated_at: '2026-09-18T01:00:00Z',
        }],
      })),
    );
    renderHome();
    fireEvent.click(await screen.findByText('真实工作流'));
    expect(screen.getByTestId('location')).toHaveTextContent('/workflow/history/wf_real');
  });

  it('refetches all sources whenever the home page remounts despite a fresh 30s cache', async () => {
    const calls = { ai: 0, workflows: 0, diagnoses: 0 };
    server.use(
      http.get('/ai/v1/history/recent', () => {
        calls.ai += 1;
        return empty('ai', 'persistent');
      }),
      http.get('/api/v1/workflows/recent', () => {
        calls.workflows += 1;
        return empty('workflows', 'ttl');
      }),
      http.get('/api/v1/diagnosis/recent', () => {
        calls.diagnoses += 1;
        return empty('diagnoses', 'ttl');
      }),
    );
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 30_000 } },
    });
    const first = renderHome(client);
    await screen.findByText('暂无可显示的真实记录');
    expect(calls).toEqual({ ai: 1, workflows: 1, diagnoses: 1 });
    first.unmount();

    renderHome(client);
    await waitFor(() => expect(calls).toEqual({ ai: 2, workflows: 2, diagnoses: 2 }));
  });
});

describe('WorkflowHistoryPage', () => {
  it('explains expired or missing TTL records instead of pretending to restore them', async () => {
    server.use(http.get('/api/v1/workflows/wf_missing', () =>
      HttpResponse.json({ error: { code: 'WORKFLOW_NOT_FOUND', message: 'expired' } }, { status: 404 })));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={['/workflow/history/wf_missing']}>
          <Routes><Route path="/workflow/history/:id" element={<WorkflowHistoryPage />} /></Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(await screen.findByText('工作流记录不可用或已过期')).toBeInTheDocument();
    expect(screen.getByText(/TTL 快照/)).toBeInTheDocument();
  });

  it('shows a retryable load failure for 503 and recovers after retry', async () => {
    let calls = 0;
    server.use(http.get('/api/v1/workflows/wf_retry', () => {
      calls += 1;
      if (calls === 1) {
        return HttpResponse.json({ error: { code: 'SERVICE_DOWN', message: '暂时不可用' } }, { status: 503 });
      }
      return HttpResponse.json({
        request_id: 'req_retry', workflow_id: 'wf_retry', workflow_status: 'generated',
        plan: { steps: [{ step_id: '01', task: 'relax', label: '结构优化' }] },
      });
    }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={['/workflow/history/wf_retry']}>
          <Routes><Route path="/workflow/history/:id" element={<WorkflowHistoryPage />} /></Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(await screen.findByText('工作流详情加载失败')).toBeInTheDocument();
    expect(screen.queryByText('工作流记录不可用或已过期')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /重\s*试/ }));
    expect(await screen.findByText('结构优化')).toBeInTheDocument();
    expect(calls).toBe(2);
  });

  it('renders a successful read-only workflow snapshot', async () => {
    server.use(http.get('/api/v1/workflows/wf_success', () => HttpResponse.json({
      request_id: 'req_success', workflow_id: 'wf_success', workflow_status: 'planned',
      plan: { steps: [{ step_id: '01', task: 'static', label: '静态计算' }] },
    })));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={['/workflow/history/wf_success']}>
          <Routes><Route path="/workflow/history/:id" element={<WorkflowHistoryPage />} /></Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(await screen.findByText('工作流详情')).toBeInTheDocument();
    expect(screen.getByText('wf_success')).toBeInTheDocument();
    expect(screen.getByText('静态计算')).toBeInTheDocument();
    expect(screen.queryByText(/加载失败|已过期/)).not.toBeInTheDocument();
  });
});
