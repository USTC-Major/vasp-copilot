import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { afterEach, expect, it, vi } from 'vitest';
import { routes } from '../router';
import { server } from '../mocks/server';
import { connectDesktopSettings, openDesktopLaunchSettings } from '../utils/desktopSettings';
import { defaultPlotPreferences, PLOT_PALETTES, plotPreferencesApi } from '../api/plotPreferences';

const renderPage = (path = '/settings') => {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>);
  return router;
};
afterEach(() => { delete (window as Window & { chrome?: unknown }).chrome; vi.restoreAllMocks(); });

it.each([['/ai/settings', '#settings-models'], ['/toolbox/settings', '#settings-execution']])('redirects %s to the same browser center', async (path, hash) => {
  const router = renderPage(path);
  await screen.findByRole('textbox', { name: '模型名称' });
  expect(await screen.findByRole('heading', { name: '启动与运行' })).toBeInTheDocument();
  expect(await screen.findByRole('heading', { name: '模型与材料' })).toBeInTheDocument();
  expect(await screen.findByRole('heading', { name: '超算与执行' })).toBeInTheDocument();
  expect(router.state.location).toMatchObject({ pathname: '/settings', hash });
  expect(screen.getAllByRole('link', { name: '设置' })).toHaveLength(1);
  expect(screen.queryByRole('link', { name: '执行设置' })).not.toBeInTheDocument();
  expect(screen.getByText('此项需在桌面程序中操作')).toBeInTheDocument();
});

it('retains the existing model save/test semantics and guards leaving dirty settings', async () => {
  const router = renderPage();
  const input = await screen.findByRole('textbox', { name: '模型名称' });
  fireEvent.change(input, { target: { value: 'synthetic-changed-model' } });
  await userEvent.click(screen.getByRole('button', { name: '测试 LLM（已保存配置）' }));
  expect(await screen.findByText(/当前表单有未保存修改/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole('link', { name: '生成工作流' }));
  const dialog = await screen.findByRole('dialog', { name: '设置尚未保存' });
  await userEvent.click(within(dialog).getByRole('button', { name: '继续编辑' }));
  expect(router.state.location.pathname).toBe('/settings');
  expect(input).toHaveValue('synthetic-changed-model');
  await userEvent.click(screen.getByRole('link', { name: '生成工作流' }));
  await userEvent.click(within(await screen.findByRole('dialog', { name: '设置尚未保存' })).getByRole('button', { name: '放弃修改并离开' }));
  await waitFor(() => expect(router.state.location.pathname).toBe('/workflow'));
});

it('uses Toolbox execution save/test while AI is unreachable and guards fallback edits', async () => {
  let saves = 0; let tests = 0;
  server.use(
    http.get('/ai/v1/settings', () => HttpResponse.json({ error: 'disabled' }, { status: 503 })),
    http.get('/ai/v1/settings/secret-status', () => HttpResponse.json({ error: 'disabled' }, { status: 503 })),
    http.put('/api/v1/toolbox/settings', async ({ request }) => { const values = await request.json() as { max_jobs: number }; expect(values.max_jobs).toBe(7); saves++; return HttpResponse.json({}); }),
    http.post('/api/v1/toolbox/settings/test/ssh', () => { tests++; return HttpResponse.json({ ok: false, message: 'synthetic saved SSH unavailable' }); }),
  );
  const router = renderPage();
  const input = await screen.findByRole('spinbutton', { name: '最大同时作业数' });
  fireEvent.change(input, { target: { value: '7' } });
  await userEvent.click(screen.getByRole('link', { name: '首页' }));
  await userEvent.click(within(await screen.findByRole('dialog', { name: '设置尚未保存' })).getByRole('button', { name: '继续编辑' }));
  expect(router.state.location.pathname).toBe('/settings');
  await userEvent.click(screen.getByRole('button', { name: '保存执行设置' }));
  await waitFor(() => expect(saves).toBe(1));
  await userEvent.click(screen.getByRole('button', { name: '测试 SSH 连接' }));
  await waitFor(() => expect(tests).toBe(1));
  expect(await screen.findByText('synthetic saved SSH unavailable')).toBeInTheDocument();
  expect(screen.queryByRole('textbox', { name: '模型名称' })).not.toBeInTheDocument();
});

it('guards palette drafts and clears only the saved section from the combined dirty state', async () => {
  let saved = defaultPlotPreferences();
  vi.spyOn(plotPreferencesApi, 'get').mockImplementation(async () => ({ preferences: saved, presets: PLOT_PALETTES }));
  const savePalette = vi.spyOn(plotPreferencesApi, 'save').mockImplementation(async (_revision, palette) => {
    saved = { ...saved, revision: saved.revision + 1, palette };
    return { preferences: saved };
  });
  const saveBusiness = vi.fn();
  server.use(http.put('/ai/v1/settings', () => { saveBusiness(); return HttpResponse.json({}); }));
  const router = renderPage();
  const model = await screen.findByRole('textbox', { name: '模型名称' });
  const originalModel = (model as HTMLInputElement).value;
  const color = screen.getByRole('textbox', { name: '颜色 1' });
  await waitFor(() => expect(color).toBeEnabled());
  fireEvent.change(color, { target: { value: '#112233' } });
  const unsavedUnload = new Event('beforeunload', { cancelable: true });
  window.dispatchEvent(unsavedUnload);
  expect(unsavedUnload.defaultPrevented).toBe(true);
  await userEvent.click(screen.getByRole('link', { name: '首页' }));
  await userEvent.click(within(await screen.findByRole('dialog', { name: '设置尚未保存' })).getByRole('button', { name: '继续编辑' }));
  expect(router.state.location.pathname).toBe('/settings');
  expect(color).toHaveValue('#112233');

  fireEvent.change(model, { target: { value: `${originalModel}-unsaved` } });
  await userEvent.click(screen.getByRole('button', { name: '保存默认配色' }));
  expect(await screen.findByText('默认配色已保存（仅本地）')).toBeInTheDocument();
  expect(savePalette).toHaveBeenCalledWith(0, expect.objectContaining({ colors: expect.arrayContaining(['#112233']) }));
  expect(saveBusiness).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole('link', { name: '首页' }));
  await userEvent.click(within(await screen.findByRole('dialog', { name: '设置尚未保存' })).getByRole('button', { name: '继续编辑' }));
  expect(model).toHaveValue(`${originalModel}-unsaved`);
  fireEvent.change(model, { target: { value: originalModel } });
  const savedUnload = new Event('beforeunload', { cancelable: true });
  window.dispatchEvent(savedUnload);
  expect(savedUnload.defaultPrevented).toBe(false);
  await userEvent.click(screen.getByRole('link', { name: '首页' }));
  await waitFor(() => expect(router.state.location.pathname).toBe('/'));
});

it('preserves the palette draft when pending AI settings replace the Toolbox fallback', async () => {
  const preferences = defaultPlotPreferences();
  const readPalette = vi.spyOn(plotPreferencesApi, 'get').mockResolvedValue({ preferences, presets: PLOT_PALETTES });
  const savePalette = vi.spyOn(plotPreferencesApi, 'save');
  let releaseSettings!: () => void;
  const pendingSettings = new Promise<void>(resolve => { releaseSettings = resolve; });
  server.use(http.get('/ai/v1/settings', async () => {
    await pendingSettings;
    return HttpResponse.json({ settings: { max_jobs: 20, poll_interval_seconds: 60, llm: { model: 'delayed-model' }, ssh: {} } });
  }));
  const router = renderPage();
  const color = await screen.findByRole('textbox', { name: '颜色 1' });
  await waitFor(() => expect(color).toBeEnabled());
  expect(screen.queryByRole('textbox', { name: '模型名称' })).not.toBeInTheDocument();
  fireEvent.change(color, { target: { value: '#445566' } });
  await act(async () => releaseSettings());
  expect(await screen.findByRole('textbox', { name: '模型名称' })).toHaveValue('delayed-model');
  expect(screen.getByRole('textbox', { name: '颜色 1' })).toBe(color);
  expect(color).toHaveValue('#445566');
  expect(screen.getByText('有未保存配色')).toBeInTheDocument();
  expect(readPalette).toHaveBeenCalledTimes(1);
  expect(savePalette).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole('link', { name: '首页' }));
  await userEvent.click(within(await screen.findByRole('dialog', { name: '设置尚未保存' })).getByRole('button', { name: '继续编辑' }));
  expect(router.state.location.pathname).toBe('/settings');
  expect(color).toHaveValue('#445566');
});

it('requires a live host capability, accepts fixed navigation and sends no configuration', async () => {
  let receive: ((event: MessageEvent) => void) | undefined;
  const postMessage = vi.fn();
  Object.defineProperty(window, 'chrome', { configurable: true, value: { webview: {
    postMessage, addEventListener: (_: string, callback: typeof receive) => { receive = callback; }, removeEventListener: vi.fn(),
  } } });
  const open = vi.fn(); const disconnect = connectDesktopSettings(open);
  receive!(new MessageEvent('message', { data: { type: 'vasp-desktop-settings', version: 1, action: 'capabilities', nonce: 'bad' } }));
  openDesktopLaunchSettings(); expect(postMessage).toHaveBeenCalledTimes(1);
  const nonce = 'a'.repeat(32);
  act(() => receive!(new MessageEvent('message', { data: { type: 'vasp-desktop-settings', version: 1, action: 'capabilities', nonce } })));
  openDesktopLaunchSettings();
  expect(postMessage).toHaveBeenLastCalledWith({ type: 'vasp-desktop-settings', version: 1, action: 'open-launch-settings', nonce });
  receive!(new MessageEvent('message', { data: { type: 'vasp-desktop-settings', version: 1, action: 'open-settings', nonce, url: 'https://example.invalid' } }));
  expect(open).not.toHaveBeenCalled();
  receive!(new MessageEvent('message', { data: { type: 'vasp-desktop-settings', version: 1, action: 'open-settings', nonce } }));
  expect(open).toHaveBeenCalledTimes(1);
  disconnect(); openDesktopLaunchSettings(); expect(postMessage).toHaveBeenCalledTimes(3);
});
