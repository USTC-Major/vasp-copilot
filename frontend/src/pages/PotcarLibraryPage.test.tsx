import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ConfigProvider } from 'antd';
import { MemoryRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { vi } from 'vitest';
import { server } from '../mocks/server';
import { potcarApi } from '../api/potcar';
import PotcarLibraryPage from './PotcarLibraryPage';
import type { PotcarDataset, PotcarLibrary, PotcarScan } from '../types/potcar';
import { hasScientificContent, workspaceLocation } from '../components/workflow/scientificNavigation';

const base = '/api/v1/toolbox/potcar';
const timestamp = '2026-10-08T00:00:00Z';
const createLibrary = (id = 'lib-1'): PotcarLibrary => ({ library_id: id, display_name: `合成库 ${id}`, root_path: `D:\\中文赝势库 空格 (测试)\\${id}`, version_note: null, revision: 1, index_revision: 1, source_ack: { confirmed: true, confirmed_at: timestamp }, source_fingerprint: 'synthetic-fingerprint', created_at: timestamp, updated_at: timestamp, is_default: false, reachable: true, scan: null, summary: { total: 3, ready: 1, unsupported: 1, invalid: 0, ambiguous: 1 } });
const dataset = (id: string, status: PotcarDataset['status'] = 'ready'): PotcarDataset => ({ dataset_id: id, library_id: 'lib-1', relative_path: `嵌套目录/${id}/POTCAR.Z`, compression: 'Z', element: 'Fe', variant: 'Fe_pv', family: 'PAW_PBE', lexch: 'PE', zval: 14, enmax_ev: 350, dataset_date: null, title: '合成元数据', decoded_sha256: 'synthetic-decoded', source_sha256: 'synthetic-source', status, issues: status === 'ambiguous' ? [{ code: 'POTCAR_VARIANT_CONFLICT', message: '同变体内容不一致，需人工核对' }] : [], duplicate_of: null });
const scan = (status: PotcarScan['status']): PotcarScan => ({ scan_id: 'scan-1', library_id: 'lib-1', status, scanned_count: 2, candidate_count: 3, failed_count: 0, created_at: timestamp, finished_at: status === 'running' ? null : timestamp, error: null, index_revision: status === 'succeeded' ? 2 : null });
let records: PotcarLibrary[];
let registryRevision: number;
const clients: QueryClient[] = [];
function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  clients.push(client);
  return render(<ConfigProvider theme={{ token: { motion: false } }}><QueryClientProvider client={client}><MemoryRouter><PotcarLibraryPage /></MemoryRouter></QueryClientProvider></ConfigProvider>);
}
const dialog = () => screen.getByRole('dialog');
beforeEach(() => {
  records = [createLibrary()]; registryRevision = 1;
  server.use(
    http.get(`${base}/libraries`, () => HttpResponse.json({ mode: 'toolbox', libraries: records, default_library_id: records.find(item => item.is_default)?.library_id ?? null, revision: registryRevision })),
    http.get(`${base}/libraries/:id`, ({ params }) => HttpResponse.json({ mode: 'toolbox', library: records.find(item => item.library_id === params.id), revision: registryRevision })),
    http.get(`${base}/libraries/:id/datasets`, () => HttpResponse.json({ mode: 'toolbox', datasets: [dataset('a'), dataset('b', 'ambiguous')], index_revision: 1, next_cursor: null, total: 2 })),
    http.post(`${base}/discover`, async ({ request }) => { const body = await request.json() as { root_path: string }; return HttpResponse.json({ mode: 'toolbox', root_path: body.root_path, requires_selection: false, collections: [{ root_path: body.root_path, display_name: '具体集合', candidate_count: 3 }], candidate_count: 3 }); }),
  );
});
afterEach(() => { cleanup(); clients.splice(0).forEach(client => client.clear()); vi.restoreAllMocks(); });

it('登记先识别父目录并明确选择具体集合，仅初次登记发送来源确认', async () => {
  records = [];
  const writes: Record<string, unknown>[] = [];
  server.use(
    http.post(`${base}/discover`, () => HttpResponse.json({ mode: 'toolbox', root_path: 'D:\\合成库', requires_selection: true, candidate_count: 5, collections: [{ root_path: 'D:\\合成库\\paw_pbe', display_name: 'paw_pbe', candidate_count: 3 }, { root_path: 'D:\\合成库\\paw', display_name: 'paw', candidate_count: 2 }] })),
    http.post(`${base}/libraries`, async ({ request }) => { const body = await request.json() as Record<string, unknown>; writes.push(body); records = [{ ...createLibrary(), root_path: String(body.root_path), display_name: String(body.display_name), index_revision: null }]; registryRevision += 1; return HttpResponse.json({ mode: 'toolbox', library: records[0], revision: registryRevision }); }),
  );
  const user = userEvent.setup(); mount();
  await user.click(await screen.findByRole('button', { name: '登记现有库' }));
  await user.type(within(dialog()).getByLabelText('显示名称'), '中文 PBE 库');
  await user.type(within(dialog()).getByLabelText('后端电脑上的现有目录'), 'D:\\合成库');
  await user.click(within(dialog()).getByRole('button', { name: '识别候选集合' }));
  await screen.findByText('发现 5 个候选文件，请明确选择一个具体集合。');
  await user.click(within(dialog()).getByRole('button', { name: /^登\s*记$/ }));
  expect(writes).toEqual([]);
  await user.click(within(dialog()).getByRole('combobox', { name: '选择具体赝势集合' }));
  await user.click(screen.getByText('paw_pbe · 3 项 · D:\\合成库\\paw_pbe'));
  await user.click(within(dialog()).getByRole('checkbox'));
  await user.click(within(dialog()).getByRole('button', { name: /^登\s*记$/ }));
  await waitFor(() => expect(writes).toHaveLength(1));
  expect(writes[0]).toEqual({ display_name: '中文 PBE 库', root_path: 'D:\\合成库\\paw_pbe', version_note: null, source_ack: { confirmed: true }, expected_registry_revision: 1 });
  expect(await screen.findByText('已登记，请点击“刷新赝势库”建立索引。')).toBeInTheDocument();
});

it('区分手动扫描与读取库列表，未扫描时显示明确状态', async () => {
  const startScan = vi.spyOn(potcarApi, 'startScan');
  const user = userEvent.setup(); mount();
  await screen.findByText('嵌套目录/a/POTCAR.Z');
  expect(screen.getByRole('button', { name: '刷新赝势库' })).toBeEnabled();
  expect(screen.getByText('重新扫描本地文件并更新索引，不会下载或修改赝势。')).toBeInTheDocument();
  expect(screen.getByText('尚未扫描')).toBeInTheDocument();
  expect(screen.getByText('完成索引版本').nextElementSibling).toHaveTextContent('1');
  await user.click(screen.getByRole('button', { name: '重新读取库列表' }));
  expect(startScan).not.toHaveBeenCalled();
});

it('成功扫描显示完成时间和当前索引版本', async () => {
  const createdAt = '2026-10-07T00:00:00Z';
  const finishedAt = '2026-10-08T02:30:00Z';
  records[0] = { ...records[0], index_revision: 2, scan: { ...scan('succeeded'), created_at: createdAt, finished_at: finishedAt } };
  const expectedTime = new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(finishedAt));
  mount();
  await screen.findByText('嵌套目录/a/POTCAR.Z');
  expect(screen.getByText(`扫描完成 · ${expectedTime}`)).toBeInTheDocument();
  expect(screen.getByText('完成索引版本').nextElementSibling).toHaveTextContent('2');
});

it('成功扫描没有有效完成时间时显示未知，不把创建时间当成索引更新时间', async () => {
  records[0] = { ...records[0], scan: { ...scan('succeeded'), created_at: timestamp, finished_at: 'invalid-time' } };
  mount();
  await screen.findByText('嵌套目录/a/POTCAR.Z');
  expect(screen.getByText('扫描完成 · 未知')).toBeInTheDocument();
});

it('编辑revision冲突保留草稿，明确刷新后使用最新revision重试，不重复来源确认', async () => {
  const writes: Record<string, unknown>[] = [];
  server.use(http.patch(`${base}/libraries/:id`, async ({ request }) => {
    const body = await request.json() as Record<string, unknown>; writes.push(body);
    if (writes.length === 1) { records[0].revision = 2; return HttpResponse.json({ mode: 'toolbox', ok: false, error: { code: 'POTCAR_REVISION_CONFLICT', message: '登记已被修改', retryable: false } }, { status: 409 }); }
    records[0] = { ...records[0], revision: 3, display_name: String(body.display_name) };
    return HttpResponse.json({ mode: 'toolbox', library: records[0], revision: ++registryRevision });
  }));
  const user = userEvent.setup(); mount();
  await user.click(await screen.findByRole('button', { name: '编辑信息' }));
  const name = within(dialog()).getByLabelText('显示名称');
  await user.clear(name); await user.type(name, '冲突后保留的中文草稿');
  expect(within(dialog()).queryByRole('checkbox')).not.toBeInTheDocument();
  await user.click(within(dialog()).getByRole('button', { name: /^保\s*存$/ }));
  await screen.findByText('登记已被修改');
  expect(name).toHaveValue('冲突后保留的中文草稿');
  await user.click(within(dialog()).getByRole('button', { name: '读取最新版本，保留草稿' }));
  await waitFor(() => expect(screen.queryByText('登记已被修改')).not.toBeInTheDocument());
  expect(name).toHaveValue('冲突后保留的中文草稿');
  await user.click(within(dialog()).getByRole('button', { name: /^保\s*存$/ }));
  await waitFor(() => expect(writes).toHaveLength(2));
  expect(writes.map(body => body.expected_revision)).toEqual([1, 2]);
  expect(writes.every(body => !('source_ack' in body))).toBe(true);
});

it('同一来源重关联不含ack；不匹配后必须显式替换并确认新来源', async () => {
  const relinks: Record<string, unknown>[] = []; const replacements: Record<string, unknown>[] = [];
  server.use(
    http.post(`${base}/libraries/:id/relink`, async ({ request }) => { relinks.push(await request.json() as Record<string, unknown>); return HttpResponse.json({ mode: 'toolbox', ok: false, error: { code: 'POTCAR_SOURCE_MISMATCH', message: '来源指纹不匹配', retryable: false } }, { status: 409 }); }),
    http.post(`${base}/libraries/:id/replace-source`, async ({ request }) => { const body = await request.json() as Record<string, unknown>; replacements.push(body); records[0] = { ...records[0], root_path: String(body.root_path), revision: 2, index_revision: null, summary: null }; return HttpResponse.json({ mode: 'toolbox', library: records[0], revision: ++registryRevision }); }),
  );
  const user = userEvent.setup(); mount();
  await user.click(await screen.findByRole('button', { name: '重新关联路径' }));
  expect(within(dialog()).queryByRole('checkbox')).not.toBeInTheDocument();
  await user.type(within(dialog()).getByLabelText('同一来源移动后的目录'), 'D:\\移动后的库');
  await user.click(within(dialog()).getByRole('button', { name: /^保\s*存$/ }));
  await screen.findByText('来源指纹不匹配');
  expect(relinks).toEqual([{ root_path: 'D:\\移动后的库', expected_revision: 1 }]);
  expect(replacements).toEqual([]);
  await user.click(within(dialog()).getByRole('button', { name: '改为显式替换来源' }));
  await user.click(within(dialog()).getByRole('button', { name: '识别候选集合' }));
  await screen.findByText('发现 3 个候选文件，将登记此具体集合。');
  await user.click(within(dialog()).getByRole('button', { name: '确认替换来源' }));
  expect(replacements).toEqual([]);
  await user.click(within(dialog()).getByRole('checkbox'));
  await user.click(within(dialog()).getByRole('button', { name: '确认替换来源' }));
  await waitFor(() => expect(replacements).toEqual([{ root_path: 'D:\\移动后的库', expected_revision: 1, source_ack: { confirmed: true } }]));
});

it('扫描与设默认不重复确认来源，取消以服务端终态为准并保留旧索引', async () => {
  const starts: Record<string, unknown>[] = []; const defaults: Record<string, unknown>[] = []; let cancels = 0;
  server.use(
    http.put(`${base}/default-library`, async ({ request }) => { defaults.push(await request.json() as Record<string, unknown>); records[0].is_default = true; return HttpResponse.json({ mode: 'toolbox', default_library_id: 'lib-1', revision: ++registryRevision }); }),
    http.post(`${base}/libraries/:id/scans`, async ({ request }) => { starts.push(await request.json() as Record<string, unknown>); records[0] = { ...records[0], revision: 2, scan: scan('running') }; return HttpResponse.json({ mode: 'toolbox', library: records[0], scan: records[0].scan, revision: ++registryRevision }); }),
    http.get(`${base}/scans/:id`, () => HttpResponse.json({ mode: 'toolbox', scan: records[0].scan })),
    http.post(`${base}/scans/:id/cancel`, () => { cancels += 1; records[0].scan = scan('cancelled'); return HttpResponse.json({ mode: 'toolbox', scan: records[0].scan }); }),
  );
  const user = userEvent.setup(); mount();
  await user.click(await screen.findByRole('button', { name: '设为默认库' }));
  await screen.findByRole('button', { name: '清空默认库' });
  await user.click(screen.getByRole('button', { name: '刷新赝势库' }));
  await screen.findByText('正在扫描');
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '刷新赝势库' })).toBeDisabled();
  await user.click(screen.getByRole('button', { name: '取消扫描' }));
  await screen.findByText('扫描已取消');
  expect(cancels).toBe(1);
  expect(starts).toEqual([{ expected_revision: 1 }]);
  expect(defaults).toEqual([{ library_id: 'lib-1', expected_registry_revision: 1 }]);
  expect(screen.getByText('本次未发布完整索引，之前完成的索引保持。可重新扫描。')).toBeInTheDocument();
  expect(screen.getByText('同变体内容不一致，需人工核对')).toBeInTheDocument();
});

it('删除仅发送ID及revision，取消确认零删除请求', async () => {
  const deletes: string[] = [];
  server.use(http.delete(`${base}/libraries/:id`, ({ request, params }) => { deletes.push(request.url); records = records.filter(item => item.library_id !== params.id); return HttpResponse.json({ mode: 'toolbox', deleted: true, library_id: params.id, revision: ++registryRevision }); }));
  const user = userEvent.setup(); mount();
  await user.click(await screen.findByRole('button', { name: '删除登记' }));
  await user.click(within(dialog()).getByRole('button', { name: /^取\s*消$/ }));
  expect(deletes).toEqual([]);
  await user.click(screen.getByRole('button', { name: '删除登记' }));
  await user.click(within(dialog()).getByRole('button', { name: '仅删除登记' }));
  await screen.findByText('仅已删除应用登记与索引，源文件保留。');
  expect(deletes).toHaveLength(1);
  expect(new URL(deletes[0]).searchParams.get('expected_revision')).toBe('1');
});

it('候选分页发送opaque cursor，筛选返回第一页，异常原因与未知日期准确显示', async () => {
  const requests: URL[] = [];
  server.use(http.get(`${base}/libraries/:id/datasets`, ({ request }) => { const url = new URL(request.url); requests.push(url); return HttpResponse.json({ mode: 'toolbox', datasets: [dataset(url.searchParams.has('cursor') ? 'next' : 'first', 'ambiguous')], index_revision: 1, next_cursor: url.searchParams.has('cursor') ? null : 'opaque+/cursor=', total: 51 }); }));
  const user = userEvent.setup(); mount();
  await screen.findByText('嵌套目录/first/POTCAR.Z');
  expect(screen.getByText('未知')).toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: '下一页' }));
  await screen.findByText('嵌套目录/next/POTCAR.Z');
  expect(requests.at(-1)?.searchParams.get('cursor')).toBe('opaque+/cursor=');
  await user.type(screen.getByRole('textbox', { name: '按元素筛选' }), 'Fe');
  await waitFor(() => expect(requests.at(-1)?.searchParams.get('element')).toBe('Fe'));
  expect(requests.at(-1)?.searchParams.has('cursor')).toBe(false);
  expect(requests.at(-1)?.searchParams.get('limit')).toBe('50');
});

it('切换库和离开页面均取消旧库扫描读取', async () => {
  records = [createLibrary(), createLibrary('lib-2')]; records[0].scan = scan('running');
  let scanSignal: AbortSignal | undefined;
  vi.spyOn(potcarApi, 'scan').mockImplementation((_id, signal) => { scanSignal = signal; return new Promise(() => {}); });
  const user = userEvent.setup();
  const view = mount();
  await screen.findByText('正在扫描');
  await waitFor(() => expect(scanSignal).toBeDefined());
  await user.click(screen.getByRole('button', { name: /合成库 lib-2/ }));
  await waitFor(() => expect(scanSignal?.aborted).toBe(true));
  expect(screen.queryByText('正在扫描')).not.toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: /合成库 lib-1/ }));
  await waitFor(() => expect(scanSignal?.aborted).toBe(false));
  view.unmount();
  expect(scanSignal?.aborted).toBe(true);
});

it.each(['failed', 'cancelled'] as const)('刷新发现外部%s终态时覆盖旧running缓存并恢复操作', async (terminal) => {
  records[0].scan = scan('running');
  server.use(http.get(`${base}/scans/:id`, () => HttpResponse.json({ mode: 'toolbox', scan: scan('running') })));
  const user = userEvent.setup(); mount();
  await screen.findByText('正在扫描');
  expect(screen.getByRole('button', { name: '刷新赝势库' })).toBeDisabled();
  const createdAt = '2026-10-07T00:00:00Z';
  records[0] = { ...records[0], scan: { ...scan(terminal), created_at: createdAt, finished_at: null } };
  await user.click(screen.getByRole('button', { name: '重新读取库列表' }));
  await screen.findByText(terminal === 'failed' ? '扫描失败' : '扫描已取消');
  const expectedTime = new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(createdAt));
  expect(screen.getByText(`${terminal === 'failed' ? '扫描失败' : '扫描已取消'} · ${expectedTime}`)).toBeInTheDocument();
  expect(screen.getByText('完成索引版本').nextElementSibling).toHaveTextContent('1');
  expect(screen.queryByText('正在扫描')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '刷新赝势库' })).toBeEnabled();
  expect(screen.getByRole('button', { name: '编辑信息' })).toBeEnabled();
});

it('目录发现响应在关闭并重开登记后失效，不覆盖新草稿', async () => {
  let resolve: (value: Awaited<ReturnType<typeof potcarApi.discover>>) => void = () => {};
  let signal: AbortSignal | undefined;
  vi.spyOn(potcarApi, 'discover').mockImplementation((_path, requestSignal) => { signal = requestSignal; return new Promise(done => { resolve = done; }); });
  const user = userEvent.setup(); mount();
  await user.click(await screen.findByRole('button', { name: '登记现有库' }));
  await user.type(within(dialog()).getByLabelText('后端电脑上的现有目录'), 'D:\\旧目录');
  await user.click(within(dialog()).getByRole('button', { name: '识别候选集合' }));
  await user.click(within(dialog()).getByRole('button', { name: /^取\s*消$/ }));
  expect(signal?.aborted).toBe(true);
  await user.click(screen.getByRole('button', { name: '登记现有库' }));
  await user.type(within(dialog()).getByLabelText('后端电脑上的现有目录'), 'D:\\新目录');
  await act(async () => { resolve({ mode: 'toolbox', root_path: 'D:\\旧目录', requires_selection: false, collections: [{ root_path: 'D:\\旧目录', display_name: '旧集合', candidate_count: 1 }], candidate_count: 1 }); });
  expect(within(dialog()).getByLabelText('后端电脑上的现有目录')).toHaveValue('D:\\新目录');
  expect(within(dialog()).queryByText(/发现 1 个候选文件/)).not.toBeInTheDocument();
});

it('复用本地目录选择接口，保留中文路径且不创建或读取源文件', async () => {
  const requests: unknown[] = [];
  server.use(http.post('/api/v1/toolbox/browse/local/pick', async ({ request }) => { requests.push(await request.json()); return HttpResponse.json({ mode: 'toolbox', kind: 'local', ok: true, path: 'D:\\中文库 空格 (测试)\\paw_pbe' }); }));
  const user = userEvent.setup(); mount();
  await user.click(await screen.findByRole('button', { name: '登记现有库' }));
  await user.type(within(dialog()).getByLabelText('后端电脑上的现有目录'), 'D:\\起始目录');
  await user.click(within(dialog()).getByRole('button', { name: /选择目录/ }));
  await waitFor(() => expect(within(dialog()).getByLabelText('后端电脑上的现有目录')).toHaveValue('D:\\中文库 空格 (测试)\\paw_pbe'));
  expect(requests).toEqual([{ initial_dir: 'D:\\起始目录' }]);
  expect(within(dialog()).getByRole('checkbox')).not.toBeChecked();
});

it('延迟旧库详情在选择另一库后到达，不污染当前详情', async () => {
  records = [createLibrary(), createLibrary('lib-2')];
  let resolve: (value: Awaited<ReturnType<typeof potcarApi.library>>) => void = () => {};
  let signal: AbortSignal | undefined;
  vi.spyOn(potcarApi, 'library').mockImplementation((id, requestSignal) => id === 'lib-1' ? new Promise(done => { resolve = done; signal = requestSignal; }) : Promise.resolve({ mode: 'toolbox', library: records[1], revision: 1 }));
  const user = userEvent.setup(); mount();
  await waitFor(() => expect(signal).toBeDefined());
  await user.click(screen.getByRole('button', { name: /合成库 lib-2/ }));
  await screen.findByText('用户版本备注');
  expect(signal?.aborted).toBe(true);
  await act(async () => { resolve({ mode: 'toolbox', library: { ...records[0], display_name: '迟到旧详情' }, revision: 1 }); });
  expect(screen.queryByText('迟到旧详情')).not.toBeInTheDocument();
  expect(within(screen.getByRole('region', { name: '赝势库详情' })).getByText('合成库 lib-2')).toBeInTheDocument();
});

it('POTCAR路由精确继承科研主题，不把未知子路由纳入', () => {
  expect(hasScientificContent('/Toolbox/Potcar/')).toBe(true);
  expect(workspaceLocation('/toolbox/potcar')).toEqual({ current: '/toolbox/settings', group: '工具箱', title: '本地赝势库' });
  expect(hasScientificContent('/toolbox/potcar/unknown')).toBe(false);
});
