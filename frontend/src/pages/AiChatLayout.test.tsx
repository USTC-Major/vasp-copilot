import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { ConfigProvider } from 'antd';
import { http, HttpResponse } from 'msw';
import { vi } from 'vitest';
import AiProjectPage from './AiProjectPage';
import ScientificWorkflowShell from '../components/workflow/ScientificWorkflowShell';
import { aiDemo } from '../mocks/aiStore';
import { server } from '../mocks/server';
import { aiApi } from '../api/client';
import type { AiConsentCard, AiStreamEvent } from '../types/ai';
import chatStyles from './scientific-ai-chat.css?raw';

const originalMatchMedia = window.matchMedia;
const clients: QueryClient[] = [];
function renderChat() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } });
  clients.push(client);
  render(<ConfigProvider theme={{ token: { motion: false } }}><QueryClientProvider client={client}>
    <MemoryRouter initialEntries={['/ai/projects/prj_001']}><Routes>
      <Route path="/ai/projects/:projectId" element={<ScientificWorkflowShell><ConfigProvider theme={{ token: { motion: false } }}><AiProjectPage /></ConfigProvider></ScientificWorkflowShell>} />
    </Routes></MemoryRouter>
  </QueryClientProvider></ConfigProvider>);
  return client;
}
const card = (id: string): AiConsentCard => ({ card_id: id, tool: 'upload', args: {}, risk: 'medium',
  reason: '本次精确内容待确认', options: ['同意本次', '拒绝'], batch_key: 'prepare', kind: 'hpc_upload', summary: `文件 ${id}` });

beforeEach(() => {
  aiDemo.reset();
  aiDemo.tasks[0].updated_at = '2099-01-01T00:00:00Z';
  localStorage.clear();
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach(client => client.clear());
  window.matchMedia = originalMatchMedia;
  vi.restoreAllMocks();
});

it('任务键盘切换保留原草稿；任务栏鼠标与键盘调整有边界并保留宽度', async () => {
  const user = userEvent.setup();
  renderChat();
  const task = await screen.findByRole('button', { name: '带结构计算的能带' });
  const input = await screen.findByRole('textbox', { name: '计算需求消息' });
  await user.type(input, '保留这一段草稿');
  task.focus();
  await user.keyboard('{Enter}');
  expect(await screen.findByRole('heading', { name: '带结构计算的能带' })).toBeInTheDocument();
  expect(input).toHaveValue('保留这一段草稿');
  const resize = screen.getByRole('separator', { name: '调整任务栏宽度' });
  resize.focus();
  await user.keyboard('{End}');
  expect(resize).toHaveAttribute('aria-valuenow', '480');
  await user.keyboard('{ArrowRight}{Home}{ArrowLeft}');
  expect(resize).toHaveAttribute('aria-valuenow', '200');
  fireEvent.mouseDown(resize, { clientX: 100 });
  fireEvent.mouseMove(window, { clientX: 160 });
  fireEvent.mouseUp(window);
  expect(resize).toHaveAttribute('aria-valuenow', '260');
  expect(localStorage.getItem('ai_sidebar_width')).toBe('260');
  expect(document.body.style.cursor).toBe('');
});

it('窄屏任务抽屉默认收起，键盘选任务后收起且草稿保留', async () => {
  window.matchMedia = ((query: string) => ({ ...originalMatchMedia(query), matches: query === '(max-width: 900px)' })) as typeof window.matchMedia;
  const user = userEvent.setup();
  renderChat();
  const input = await screen.findByRole('textbox', { name: '计算需求消息' });
  await user.type(input, '窄屏草稿');
  const toggle = screen.getByRole('button', { name: '任务列表' });
  expect(toggle).toHaveAttribute('aria-expanded', 'false');
  expect(screen.queryByRole('separator')).not.toBeInTheDocument();
  toggle.focus();
  await user.keyboard('{Enter}');
  const drawer = await screen.findByRole('dialog');
  const task = within(drawer).getByRole('button', { name: '带结构计算的能带' });
  task.focus();
  await user.keyboard('{Enter}');
  await waitFor(() => expect(toggle).toHaveAttribute('aria-expanded', 'false'));
  expect(await screen.findByRole('heading', { name: '带结构计算的能带' })).toBeInTheDocument();
  expect(input).toHaveValue('窄屏草稿');
});

it('键盘切任务中止旧SSE；旧分片与授权不会污染新任务', async () => {
  let signal: AbortSignal | undefined;
  let release: () => void = () => {};
  const gate = new Promise<void>(resolve => { release = resolve; });
  vi.spyOn(aiApi, 'sendMessageStream').mockImplementation(async function* (_project, _task, _content, streamSignal): AsyncGenerator<AiStreamEvent> {
    signal = streamSignal;
    yield { type: 'answer', text: '旧任务的实时内容' };
    await gate;
    yield { type: 'card', card: card('old-stream-card') };
    yield { type: 'done', answer: '旧任务结束' };
  });
  const user = userEvent.setup();
  renderChat();
  await user.type(await screen.findByRole('textbox', { name: '计算需求消息' }), '开始流式回复');
  await user.click(screen.getByRole('button', { name: '发送' }));
  expect(await screen.findByText('旧任务的实时内容')).toBeInTheDocument();
  const next = screen.getByRole('button', { name: '带结构计算的能带' });
  next.focus();
  await user.keyboard('{Enter}');
  expect(signal?.aborted).toBe(true);
  await act(async () => { release(); });
  expect(await screen.findByRole('heading', { name: '带结构计算的能带' })).toBeInTheDocument();
  expect(screen.queryByText('旧任务的实时内容')).not.toBeInTheDocument();
  expect(screen.queryByText('文件 old-stream-card')).not.toBeInTheDocument();
  expect(screen.queryByText('旧任务结束')).not.toBeInTheDocument();
});

it('消息与授权同处可滚动区域，markdown代码保留原文且不执行HTML', async () => {
  const code = 'ENCUT = 520\npath = /very/long/scientific/workspace/INCAR';
  const plainParameters = 'ENCUT = 520\nEDIFF = 1E-6\n说明保留原换行';
  server.use(http.get('/ai/v1/projects/:p/tasks/:t/messages', () => HttpResponse.json({
    messages: [{ role: 'assistant', content: `**输入建议**\n\n${plainParameters}\n\n\`\`\`text\n${code}\n\`\`\`\n<script>unsafe()</script>`, thinking: '折叠过程' }],
    pending_actions: [{ ...card('single'), kind: 'incar_write' }],
  })));
  renderChat();
  expect(await screen.findByText('输入建议')).toBeInTheDocument();
  expect(document.querySelector('.ai-chat-markdown pre code')?.textContent).toBe(code);
  const parameters = [...document.querySelectorAll('.ai-chat-markdown p')].find(paragraph => paragraph.textContent === plainParameters);
  expect(parameters).toBeDefined();
  expect(document.querySelector('.ai-chat-markdown script')).toBeNull();
  const region = screen.getByRole('region', { name: '对话与授权记录' });
  expect(within(region).getByText('文件 single')).toBeInTheDocument();
  expect(region).not.toContainElement(screen.getByRole('textbox', { name: '计算需求消息' }));
  expect(document.querySelector('.ai-chat-thinking')).not.toHaveAttribute('open');
});

it('批量确认取消零写入，已打开的确认仍使用原卡片快照', async () => {
  const initial = [card('snapshot-a'), card('snapshot-b')];
  let pending = initial;
  const writes: string[] = [];
  server.use(
    http.get('/ai/v1/projects/:p/tasks/:t/messages', () => HttpResponse.json({ messages: [], pending_actions: pending })),
    http.post('/ai/v1/projects/:p/tasks/:t/messages/consent', async ({ request }) => {
      const body = await request.json() as { card_id: string };
      writes.push(body.card_id);
      return HttpResponse.json({ mode: 'ai', ok: true, kind: 'hpc_upload', state: 'executed' });
    }),
  );
  const user = userEvent.setup();
  const client = renderChat();
  await user.click(await screen.findByRole('button', { name: '全部批准本批（2 项）' }));
  let dialog = await screen.findByRole('dialog');
  await user.click(within(dialog).getByRole('button', { name: /取\s*消/ }));
  expect(writes).toEqual([]);
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  await user.click(screen.getByRole('button', { name: '全部批准本批（2 项）' }));
  dialog = await screen.findByRole('dialog');
  pending = [card('replacement')];
  await act(async () => { await client.refetchQueries({ queryKey: ['aiMessages', 'prj_001', 'tsk_001'] }); });
  await user.click(within(dialog).getByRole('button', { name: '全部批准' }));
  await waitFor(() => expect(writes).toEqual(['snapshot-a', 'snapshot-b']));
  expect(await screen.findByText(/本批处理结果：执行完成 2/)).toBeInTheDocument();
});

it('AI不可用时保留明确错误，发送一次且输入恢复可用', async () => {
  let sends = 0;
  server.use(http.post('/ai/v1/projects/:p/tasks/:t/messages/stream', () => {
    sends += 1;
    return HttpResponse.json({ error: { code: 'AI_UNAVAILABLE', message: 'AI 服务暂不可用' } }, { status: 503 });
  }));
  const user = userEvent.setup();
  renderChat();
  await user.type(await screen.findByRole('textbox', { name: '计算需求消息' }), '合成错误请求');
  await user.click(screen.getByRole('button', { name: '发送' }));
  expect(await screen.findByText(/AI 服务暂不可用/)).toBeInTheDocument();
  expect(sends).toBe(1);
  await waitFor(() => expect(screen.getByRole('textbox', { name: '计算需求消息' })).toBeEnabled());
});

it.each([false, true])('旧批量确认在切任务后失效（返回原任务=%s），不发送授权', async (returnToOriginal) => {
  let writes = 0;
  server.use(
    http.get('/ai/v1/projects/:p/tasks/:t/messages', () => HttpResponse.json({ messages: [], pending_actions: [card('a'), card('b')] })),
    http.post('/ai/v1/projects/:p/tasks/:t/messages/consent', () => {
      writes += 1;
      return HttpResponse.json({ mode: 'ai', ok: true, kind: 'hpc_upload', state: 'executed' });
    }),
  );
  const user = userEvent.setup();
  renderChat();
  await user.click(await screen.findByRole('button', { name: '全部批准本批（2 项）' }));
  const dialog = await screen.findByRole('dialog');
  // Simulate selection changing while the modal's captured decision is pending.
  // The generation counter must invalidate it even if the original task returns.
  fireEvent.click(screen.getByRole('button', { name: '带结构计算的能带' }));
  expect(await screen.findByRole('heading', { name: '带结构计算的能带' })).toBeInTheDocument();
  if (returnToOriginal) {
    fireEvent.click(screen.getByRole('button', { name: '结构优化 + 静态 + DOS' }));
    expect(await screen.findByRole('heading', { name: '结构优化 + 静态 + DOS' })).toBeInTheDocument();
  }
  await user.click(within(dialog).getByRole('button', { name: '全部批准' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  expect(writes).toBe(0);
  expect(screen.queryByText(/本批处理结果/)).not.toBeInTheDocument();
});

it.each(['dark', 'light'])('%s主题保留路径语义及浅色原调色板资格', async (mode) => {
  localStorage.setItem('vasp-copilot.workflow-theme', mode);
  aiDemo.tasks[0].local_workspace = 'D:\\research\\local-workspace';
  aiDemo.tasks[0].hpc_workspace = '/research/hpc-workspace';
  renderChat();
  const local = (await screen.findByText('D:\\research\\local-workspace')).closest('.ai-chat-path-local')!;
  const hpc = screen.getByText('/research/hpc-workspace').closest('.ai-chat-path-hpc')!;
  expect(local).toHaveClass('ant-tag-geekblue');
  expect(hpc).toHaveClass('ant-tag-purple');
  expect(local.closest('.scientific-shell')).toHaveAttribute('data-workflow-theme', mode);
});

it('深色聊天全部强调文字/背景对比至少4.5，配色覆盖严格隔离于dark聊天', () => {
  const colors = new Map([...chatStyles.matchAll(/--ai-tag-([\w-]+):\s*(#[\da-f]{6})/gi)].map(match => [match[1], match[2]]));
  const luminance = (hex: string) => {
    const channels = [1, 3, 5].map(offset => {
      const value = Number.parseInt(hex.slice(offset, offset + 2), 16) / 255;
      return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
    });
    return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
  };
  const contrast = (foreground: string, background: string) => {
    const values = [luminance(foreground), luminance(background)].sort((a, b) => b - a);
    return (values[0] + 0.05) / (values[1] + 0.05);
  };
  expect(colors.size).toBeGreaterThan(0);
  for (const [name, foreground] of colors) {
    if (!name.endsWith('-text')) continue;
    const background = colors.get(name.replace(/-text$/, '-bg'));
    expect(background, `${name} has an explicit background`).toBeDefined();
    expect(contrast(foreground, background!), name).toBeGreaterThanOrEqual(4.5);
  }
  const rules = [...chatStyles.matchAll(/([^{}]+)\{([^{}]+)\}/g)].filter(match => match[2].includes('--ai-tag-'));
  expect(rules.length).toBeGreaterThan(1);
  for (const rule of rules) {
    expect(rule[1]).toContain('.scientific-shell[data-workflow-theme="dark"] .scientific-ai-chat');
  }
  const hover = chatStyles.match(/\.ant-btn-dangerous:not\(:disabled\):hover\s*\{\s*color:(#[\da-f]{6});\s*background:(#[\da-f]{6})/i);
  expect(hover).not.toBeNull();
  expect(contrast(hover![1], hover![2])).toBeGreaterThanOrEqual(4.5);
});
