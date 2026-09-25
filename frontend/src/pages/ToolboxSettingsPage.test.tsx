import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { HttpResponse, http } from 'msw';
import { server } from '../mocks/server';
import ToolboxSettingsPage from './ToolboxSettingsPage';

it('keeps settings and credential actions unavailable until a failed read recovers', async () => {
  let reads = 0;
  let writes = 0;
  server.use(
    http.get('/api/v1/toolbox/settings', () => {
      reads += 1;
      if (reads === 1) return HttpResponse.json({ error: { code: 'DOWN', message: 'offline' } }, { status: 503 });
      return HttpResponse.json({ settings: {
        max_jobs: 2, poll_interval_seconds: 60,
        ssh: { name: '', host: '', port: 22, username: '', known_hosts_path: '', identity_file: '', scheduler_backend: 'slurm' },
        materials_project: { configured: false },
      } });
    }),
    http.post('/api/v1/toolbox/settings/secrets/:kind', () => { writes += 1; return HttpResponse.json({}); }),
    http.post('/api/v1/toolbox/settings/test/ssh', () => { writes += 1; return HttpResponse.json({ ok: true, message: 'ok' }); }),
    http.put('/api/v1/toolbox/settings', () => { writes += 1; return HttpResponse.json({}); }),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><ToolboxSettingsPage /></QueryClientProvider>);

  expect(await screen.findByText('无法读取 Toolbox 设置')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '保存执行设置' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '测试 SSH 连接' })).not.toBeInTheDocument();
  expect(screen.queryByRole('textbox', { name: '新的 SSH 密码' })).not.toBeInTheDocument();
  expect(writes).toBe(0);

  await userEvent.click(screen.getByRole('button', { name: '重试读取设置' }));
  await waitFor(() => expect(screen.getByRole('button', { name: '保存执行设置' })).toBeEnabled());
  expect(screen.getByRole('button', { name: '测试 SSH 连接' })).toBeEnabled();
  expect(screen.getByLabelText('新的 SSH 密码')).toBeEnabled();
  expect(reads).toBe(2);
  expect(writes).toBe(0);
});
