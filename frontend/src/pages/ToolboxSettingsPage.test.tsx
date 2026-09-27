import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { HttpResponse, http } from 'msw';
import { Modal } from 'antd';
import { server } from '../mocks/server';
import ToolboxSettingsPage from './ToolboxSettingsPage';

// antd 静态 Modal 不在 Testing Library 自动清理范围内，避免残留到下一条用例。
afterEach(() => {
  Modal.destroyAll();
  document.querySelectorAll('.ant-modal-root, .ant-modal-wrap, .ant-modal-mask')
    .forEach((node) => node.remove());
});

const settingsBody = (username: string, maxJobs = 2) => ({
  settings: {
    max_jobs: maxJobs, poll_interval_seconds: 60,
    ssh: { name: 'CLUSTER', host: 'ssh.example', port: 22, username,
           known_hosts_path: '', identity_file: '', scheduler_backend: 'slurm' },
    materials_project: { configured: false },
  },
});

const renderPage = () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><ToolboxSettingsPage /></QueryClientProvider>);
};

it('刷新后台查询状态后仍用原快照检测冲突，选择刷新则不覆盖', async () => {
  let reads = 0;
  let writes = 0;
  server.use(
    http.get('/api/v1/toolbox/settings', () => {
      reads += 1;
      return HttpResponse.json(reads === 1
        ? settingsBody('demo-user')
        : settingsBody('demo-user@CLUSTER'));
    }),
    http.put('/api/v1/toolbox/settings', () => { writes += 1; return HttpResponse.json({}); }),
  );
  renderPage();

  expect(await screen.findByDisplayValue('demo-user')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: '保存执行设置' }));

  const titles = await screen.findAllByText('后台配置已被修改');
  expect(titles.length).toBeGreaterThan(0);
  const refreshButtons = await screen.findAllByRole('button', { name: '用后台值刷新' });
  refreshButtons.forEach((button) => fireEvent.click(button));

  await waitFor(() => expect(writes).toBe(0));
  expect(await screen.findByDisplayValue('demo-user@CLUSTER')).toBeInTheDocument();
});

it('冲突时明确选择仍然覆盖，才写入本页值', async () => {
  let reads = 0;
  let writes = 0;
  server.use(
    http.get('/api/v1/toolbox/settings', () => {
      reads += 1;
      return HttpResponse.json(reads === 1 ? settingsBody('demo-user') : settingsBody('demo-user@CLUSTER'));
    }),
    http.put('/api/v1/toolbox/settings', () => { writes += 1; return HttpResponse.json({}); }),
  );
  renderPage();

  expect(await screen.findByDisplayValue('demo-user')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: '保存执行设置' }));

  const confirmButtons = await screen.findAllByRole('button', { name: '仍然覆盖' });
  confirmButtons.forEach((button) => fireEvent.click(button));
  await waitFor(() => expect(writes).toBe(1));
});

it('保存前读取最新设置失败时不写入，并解除保存中状态', async () => {
  let reads = 0;
  let writes = 0;
  server.use(
    http.get('/api/v1/toolbox/settings', () => {
      reads += 1;
      return reads === 1
        ? HttpResponse.json(settingsBody('demo-user'))
        : HttpResponse.json({ error: { code: 'DOWN', message: 'offline' } }, { status: 503 });
    }),
    http.put('/api/v1/toolbox/settings', () => { writes += 1; return HttpResponse.json({}); }),
  );
  renderPage();

  expect(await screen.findByDisplayValue('demo-user')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: '保存执行设置' }));

  expect(await screen.findByText('无法确认最新设置，未保存')).toBeInTheDocument();
  expect(writes).toBe(0);
  expect(await screen.findByText('无法读取 Toolbox 设置')).toBeInTheDocument();
});

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
