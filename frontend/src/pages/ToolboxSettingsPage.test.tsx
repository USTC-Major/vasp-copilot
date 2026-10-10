import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { HttpResponse, http } from 'msw';
import { server } from '../mocks/server';
import ToolboxSettingsPage from './ToolboxSettingsPage';
import { MemoryRouter } from 'react-router-dom';
import { ConfigProvider, message } from 'antd';

const clients: QueryClient[] = [];
beforeEach(() => {
  // JSDOM does not complete the static message root's CSS animations.
  ConfigProvider.config({ holderRender: children => <ConfigProvider theme={{ token: { motion: false } }}>{children}</ConfigProvider> });
});
afterEach(async () => {
  try {
    await act(async () => {
      cleanup();
      clients.splice(0).forEach(client => client.clear());
      // Static messages own a React root outside RTL's page cleanup.
      message.destroy();
    });
    await waitFor(() => expect(document.querySelector('.ant-message-notice')).not.toBeInTheDocument());
  } finally {
    await act(async () => { ConfigProvider.config({ holderRender: undefined }); });
  }
});

// Context Modal is owned by React and uses Testing Library's normal unmount cleanup.

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
  clients.push(client);
  render(<ConfigProvider theme={{ token: { motion: false } }}><MemoryRouter><QueryClientProvider client={client}><ToolboxSettingsPage /></QueryClientProvider></MemoryRouter></ConfigProvider>);
  return client;
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
  await waitFor(() => expect(screen.getByRole('button', { name: '保存执行设置' })).not.toHaveClass('ant-btn-loading'));
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
  const client = renderPage();

  expect(await screen.findByDisplayValue('demo-user')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: '保存执行设置' }));

  const confirmButtons = await screen.findAllByRole('button', { name: '仍然覆盖' });
  confirmButtons.forEach((button) => fireEvent.click(button));
  await waitFor(() => expect(writes).toBe(1));
  await screen.findByText('Toolbox 执行设置已保存');
  await waitFor(() => expect(screen.getByRole('button', { name: '保存执行设置' })).not.toHaveClass('ant-btn-loading'));
  await waitFor(() => expect(client.isFetching()).toBe(0));
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
  renderPage();

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

it('tests the saved backend SSH configuration even when the form has unsaved edits', async () => {
  let tests = 0;
  let writes = 0;
  server.use(
    http.get('/api/v1/toolbox/settings', () => HttpResponse.json(settingsBody('saved-user'))),
    http.post('/api/v1/toolbox/settings/test/ssh', async ({ request }) => {
      expect(await request.text()).toBe('');
      tests += 1;
      return HttpResponse.json({ ok: true, message: '已测试后台 SSH 配置' });
    }),
    http.put('/api/v1/toolbox/settings', () => { writes += 1; return HttpResponse.json({}); }),
  );
  renderPage();
  const username = await screen.findByLabelText('用户名');
  fireEvent.change(username, { target: { value: 'unsaved-user' } });
  await userEvent.click(screen.getByRole('button', { name: '测试 SSH 连接' }));
  expect(await screen.findByText('已测试后台 SSH 配置')).toBeInTheDocument();
  await waitFor(() => expect(screen.getByRole('button', { name: '测试 SSH 连接' })).not.toHaveClass('ant-btn-loading'));
  expect(tests).toBe(1);
  expect(writes).toBe(0);
  expect(username).toHaveValue('unsaved-user');
});

it('saves and clears each credential independently of the execution settings form', async () => {
  const secrets: { kind: string; body: unknown }[] = [];
  let writes = 0;
  let reads = 0;
  server.use(
    http.get('/api/v1/toolbox/settings', () => { reads += 1; return HttpResponse.json(settingsBody('saved-user')); }),
    http.post('/api/v1/toolbox/settings/secrets/:kind', async ({ request, params }) => {
      secrets.push({ kind: String(params.kind), body: await request.json() });
      return HttpResponse.json({ mode: 'toolbox', configured: true });
    }),
    http.put('/api/v1/toolbox/settings', () => { writes += 1; return HttpResponse.json({}); }),
  );
  const client = renderPage();
  const waitForRefresh = async (expectedReads: number) => {
    await waitFor(() => expect(reads).toBe(expectedReads));
    await waitFor(() => expect(client.isFetching()).toBe(0));
  };
  const password = await screen.findByLabelText('新的 SSH 密码');
  const sshCard = within(password.closest('.ant-card')!);
  expect(sshCard.getByRole('button', { name: /保\s*存/ })).toBeDisabled();
  fireEvent.change(password, { target: { value: 'synthetic-password' } });
  await userEvent.click(sshCard.getByRole('button', { name: /保\s*存/ }));
  await waitFor(() => expect(password).toHaveValue(''));
  await waitForRefresh(2);
  await userEvent.click(sshCard.getByRole('button', { name: /清\s*除/ }));
  await waitForRefresh(3);
  const mp = screen.getByLabelText('新的 Materials Project 密钥');
  fireEvent.change(mp, { target: { value: 'synthetic-mp-key' } });
  const mpCard = within(mp.closest('.ant-card')!);
  await userEvent.click(mpCard.getByRole('button', { name: /保\s*存/ }));
  await waitFor(() => expect(mp).toHaveValue(''));
  await waitForRefresh(4);
  await userEvent.click(mpCard.getByRole('button', { name: /清\s*除/ }));
  await waitForRefresh(5);
  await waitFor(() => expect(secrets).toEqual([
    { kind: 'ssh', body: { value: 'synthetic-password' } },
    { kind: 'ssh', body: { value: '' } },
    { kind: 'mp', body: { value: 'synthetic-mp-key' } },
    { kind: 'mp', body: { value: '' } },
  ]));
  expect(writes).toBe(0);
});
