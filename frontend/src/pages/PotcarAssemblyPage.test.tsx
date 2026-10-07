import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ConfigProvider } from 'antd';
import { MemoryRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { vi } from 'vitest';
import { server } from '../mocks/server';
import { potcarApi } from '../api/potcar';
import type { PotcarArtifact, PotcarDataset, PotcarLibrary, PotcarPreview, PotcarPreviewRequest } from '../types/potcar';
import { hasScientificContent, workspaceLocation } from '../components/workflow/scientificNavigation';
import PotcarAssemblyPage from './PotcarAssemblyPage';

const base = '/api/v1/toolbox/potcar';
const poscar = 'Synthetic only\n1\n1 0 0\n0 1 0\n0 0 1\nFe O Fe\n1 0 1\nDirect\n0 0 0\n0 0 0\n';
const future = (minutes: number) => new Date(Date.now() + minutes * 60_000).toISOString();
const library = (id = 'lib-1'): PotcarLibrary => ({ library_id: id, display_name: `合成库 ${id}`, root_path: 'D:\\中文 合成库', version_note: null, revision: 1, index_revision: 1, source_ack: { confirmed: true, confirmed_at: future(0) }, source_fingerprint: 'synthetic', created_at: future(0), updated_at: future(0), is_default: true, reachable: true, scan: null, summary: { total: 3, ready: 3, unsupported: 0, invalid: 0, ambiguous: 0 } });
const dataset = (id = 'fe', element = 'Fe'): PotcarDataset => ({ dataset_id: id, library_id: 'lib-1', relative_path: `合成/${id}/POTCAR`, compression: 'raw', element, variant: id, family: 'PAW_PBE', lexch: 'PE', zval: 8, enmax_ev: 350, dataset_date: null, title: `合成元数据 ${id}`, decoded_sha256: `decoded-${id}`, source_sha256: `source-${id}`, status: 'ready', issues: [], duplicate_of: null });
function preview(id = 'preview-1'): PotcarPreview {
  return { mode: 'toolbox', preview_id: id, selection_digest: `digest-${id}`, structure_sha256: 'synthetic-structure-hash', library: { library_id: 'lib-1', display_name: '合成库 lib-1', version_note: null, index_revision: 1 }, rows: ['Fe', 'O', 'Fe'].map((element, index) => ({ position: index + 1, element, atom_count: index === 1 ? 0 : 1, dataset_id: element.toLowerCase(), candidates: [dataset(element.toLowerCase(), element)], reason: { code: 'UNIQUE_COMPATIBLE', message: '唯一兼容候选，不代表最优' } })), blockers: [], expires_at: future(30) };
}
function artifact(): PotcarArtifact {
  const selection = preview();
  return { artifact_id: 'artifact-1', preview_id: selection.preview_id, selection_digest: selection.selection_digest, structure_sha256: selection.structure_sha256, library_id: 'lib-1', index_revision: 1, status: 'ready', size_bytes: 4, sha256: 'synthetic-artifact-hash', created_at: future(0), expires_at: future(1440), rows: selection.rows.map(row => ({ position: row.position, element: row.element, atom_count: row.atom_count, dataset_id: row.dataset_id!, variant: row.dataset_id, title: `合成元数据 ${row.dataset_id}`, decoded_sha256: `decoded-${row.dataset_id}`, source_sha256: `source-${row.dataset_id}` })) };
}
function mount() { return render(<ConfigProvider theme={{ token: { motion: false } }}><MemoryRouter><PotcarAssemblyPage /></MemoryRouter></ConfigProvider>); }
async function readPreview() {
  await waitFor(() => expect(screen.getByRole('button', { name: '刷新库信息' })).toBeEnabled());
  fireEvent.change(screen.getByLabelText('POSCAR 文本'), { target: { value: poscar } });
  fireEvent.click(screen.getByRole('button', { name: '读取 / 刷新预览' }));
  await waitFor(() => expect(screen.getByRole('checkbox')).toBeEnabled());
}
async function generate() {
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(screen.getByRole('button', { name: '生成 POTCAR' }));
  await screen.findByText('3. 已生成 POTCAR');
}
let previews: PotcarPreviewRequest[];
let writes: Record<string, unknown>[];
beforeEach(() => {
  previews = []; writes = [];
  server.use(
    http.get(`${base}/libraries`, () => HttpResponse.json({ mode: 'toolbox', libraries: [library()], default_library_id: 'lib-1', revision: 1 })),
    http.post(`${base}/previews`, async ({ request }) => { previews.push(await request.json() as PotcarPreviewRequest); return HttpResponse.json(preview()); }),
    http.post(`${base}/artifacts`, async ({ request }) => { writes.push(await request.json() as Record<string, unknown>); return HttpResponse.json({ mode: 'toolbox', artifact: artifact() }); }),
  );
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });

it('只在同面板确认后生成，精确绑定预览及digest，保留重复/零物种块与元数据', async () => {
  mount(); await readPreview();
  expect(screen.getByRole('button', { name: '生成 POTCAR' })).toBeDisabled();
  expect(screen.getByText('#3 · Fe')).toBeInTheDocument();
  expect(screen.getByText('数量：0')).toBeInTheDocument();
  expect(screen.getAllByText('唯一兼容候选')).toHaveLength(3);
  expect(screen.getAllByText(/来源相对路径：合成/)).toHaveLength(3);
  expect(screen.queryByText('UNIQUE_COMPATIBLE')).not.toBeInTheDocument();
  await generate();
  expect(previews[0]).toEqual({ library_id: 'lib-1', index_revision: 1, poscar_text: poscar });
  expect(writes[0]).toEqual({ preview_id: 'preview-1', selection_digest: 'digest-preview-1', confirmed_order_and_variants: true, idempotency_key: expect.any(String) });
  expect(screen.getByText('synthetic-artifact-hash')).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('POSCAR 文本'), { target: { value: `${poscar}\n` } });
  expect(screen.queryByText('3. 已生成 POTCAR')).not.toBeInTheDocument();
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
});

it('多候选保持待选，明确选择后仍须刷新再确认，不自动科学排序或过滤ambiguous候选', async () => {
  const multiple = preview(); multiple.rows = [multiple.rows[0]];
  multiple.rows[0] = { ...multiple.rows[0], dataset_id: null, candidates: [{ ...dataset('a'), status: 'ambiguous' }, dataset('b')], reason: { code: 'SELECTION_REQUIRED', message: '存在多个兼容候选，请选择' } };
  multiple.blockers = [{ code: 'POTCAR_SELECTION_REQUIRED', position: 1, message: '请选择第1项变体' }];
  server.use(http.post(`${base}/previews`, async ({ request }) => {
    const body = await request.json() as PotcarPreviewRequest; previews.push(body);
    return HttpResponse.json(body.dataset_ids?.[0] ? { ...multiple, preview_id: 'selected', selection_digest: 'selected-digest', rows: [{ ...multiple.rows[0], dataset_id: body.dataset_ids[0], reason: { code: 'USER_SELECTED', message: '用户明确选择' } }], blockers: [] } : multiple);
  }));
  const user = userEvent.setup(); mount();
  await waitFor(() => expect(screen.getByRole('button', { name: '刷新库信息' })).toBeEnabled());
  fireEvent.change(screen.getByLabelText('POSCAR 文本'), { target: { value: poscar } });
  fireEvent.click(screen.getByRole('button', { name: '读取 / 刷新预览' }));
  await screen.findByText('存在多个兼容候选，请选择');
  expect(screen.getByRole('checkbox')).toBeDisabled();
  await user.click(screen.getByRole('combobox', { name: '第 1 项 Fe 变体' }));
  await user.click(screen.getByText(/a · ZVAL 8 · ENMAX 350 eV · 合成元数据 a · 合成\/a\/POTCAR/));
  expect(screen.getByRole('checkbox')).toBeDisabled();
  expect(screen.getByText('选择预览已失效，请刷新预览后重新核对并确认。')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '读取 / 刷新预览' }));
  await waitFor(() => expect(screen.getByRole('checkbox')).toBeEnabled());
  expect(screen.getByRole('checkbox')).not.toBeChecked();
  expect(previews[1].dataset_ids).toEqual(['a']);
  await generate(); expect(writes[0].selection_digest).toBe('selected-digest');
});

it('映射/库信息刷新均撤销确认；旧格式显式按空格分隔发送，元素行判断交后端', async () => {
  mount(); await readPreview(); fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.change(screen.getByLabelText('旧格式物种映射（VASP4）'), { target: { value: ' Fe   O Fe ' } });
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '读取 / 刷新预览' }));
  await waitFor(() => expect(previews).toHaveLength(2));
  expect(previews[1].legacy_species).toEqual(['Fe', 'O', 'Fe']);
  await waitFor(() => expect(screen.getByRole('checkbox')).toBeEnabled());
  fireEvent.click(screen.getByRole('checkbox')); fireEvent.click(screen.getByRole('button', { name: '刷新库信息' }));
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
});

it('旧预览响应在结构编辑后不能恢复选择或确认，卸载取消未完成读取', async () => {
  let resolve!: (value: PotcarPreview) => void;
  let signal: AbortSignal | undefined;
  vi.spyOn(potcarApi, 'preview').mockImplementation((_body, currentSignal) => { signal = currentSignal; return new Promise(done => { resolve = done; }); });
  const view = mount();
  await waitFor(() => expect(screen.getByRole('button', { name: '刷新库信息' })).toBeEnabled());
  fireEvent.change(screen.getByLabelText('POSCAR 文本'), { target: { value: poscar } });
  fireEvent.click(screen.getByRole('button', { name: '读取 / 刷新预览' }));
  fireEvent.change(screen.getByLabelText('POSCAR 文本'), { target: { value: '新结构草稿' } });
  expect(signal?.aborted).toBe(true);
  await act(async () => resolve(preview('old')));
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  expect(screen.getByLabelText('POSCAR 文本')).toHaveValue('新结构草稿');
  fireEvent.click(screen.getByRole('button', { name: '读取 / 刷新预览' })); view.unmount();
  expect(signal?.aborted).toBe(true);
});

it('生成网络失败保留确认与幂等key，双击只发一次，输入在生成中禁用', async () => {
  let resolve!: (value: Awaited<ReturnType<typeof potcarApi.assemble>>) => void;
  const assemble = vi.spyOn(potcarApi, 'assemble').mockRejectedValueOnce(new Error('合成网络失败')).mockImplementation(() => new Promise(done => { resolve = done; }));
  mount(); await readPreview(); fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(screen.getByRole('button', { name: '生成 POTCAR' })); await screen.findByText('合成网络失败');
  expect(screen.getByRole('checkbox')).toBeChecked();
  const button = screen.getByRole('button', { name: '生成 POTCAR' }); fireEvent.click(button); fireEvent.click(button);
  expect(assemble).toHaveBeenCalledTimes(2);
  expect(assemble.mock.calls[0][0]).toEqual(assemble.mock.calls[1][0]);
  expect(screen.getByLabelText('POSCAR 文本')).toBeDisabled();
  expect(screen.getByRole('button', { name: '刷新库信息' })).toBeDisabled();
  await act(async () => resolve({ mode: 'toolbox', artifact: artifact() }));
  expect(await screen.findByRole('button', { name: '下载 POTCAR' })).toBeEnabled();
});

it.each(['POTCAR_SOURCE_CHANGED', 'POTCAR_PREVIEW_EXPIRED', 'POTCAR_INDEX_REVISION_CONFLICT'])('生成%s撤销确认并提供重新扫描/预览恢复路径', async code => {
  server.use(http.post(`${base}/artifacts`, () => HttpResponse.json({ mode: 'toolbox', ok: false, error: { code, message: '合成失效原因', retryable: false } }, { status: 409 })));
  mount(); await readPreview(); fireEvent.click(screen.getByRole('checkbox')); fireEvent.click(screen.getByRole('button', { name: '生成 POTCAR' }));
  await screen.findByText('合成失效原因');
  expect(screen.getByRole('checkbox')).not.toBeChecked(); expect(screen.getByRole('checkbox')).toBeDisabled();
  expect(screen.getByRole('link', { name: '管理 / 扫描库' })).toHaveAttribute('href', '/toolbox/potcar');
  fireEvent.click(screen.getByRole('button', { name: '读取 / 刷新预览' }));
  await waitFor(() => expect(screen.getByRole('checkbox')).toBeEnabled());
  expect(screen.getByRole('checkbox')).not.toBeChecked();
});

it('下载失败可重试、重复下载无需确认，createObjectURL直接接收原始Blob并及时回收', async () => {
  const blob = new Blob([new Uint8Array([0, 255, 13, 10])], { type: 'application/octet-stream' });
  const download = vi.spyOn(potcarApi, 'download').mockRejectedValueOnce(new Error('合成下载失败')).mockResolvedValue(blob);
  const createUrl = vi.fn(() => 'blob:synthetic'); const revoke = vi.fn();
  class DownloadURL extends URL { static createObjectURL = createUrl; static revokeObjectURL = revoke; }
  vi.stubGlobal('URL', DownloadURL);
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  mount(); await readPreview(); await generate();
  fireEvent.click(screen.getByRole('button', { name: '下载 POTCAR' })); await screen.findByText('合成下载失败');
  fireEvent.click(screen.getByRole('button', { name: '下载 POTCAR' }));
  await waitFor(() => expect(createUrl).toHaveBeenCalledWith(blob));
  expect(click).toHaveBeenCalledTimes(1); expect(revoke).toHaveBeenCalledWith('blob:synthetic');
  await waitFor(() => expect(screen.getByRole('button', { name: '下载 POTCAR' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: '下载 POTCAR' }));
  await waitFor(() => expect(download).toHaveBeenCalledTimes(3)); expect(writes).toHaveLength(1);
});

it('已生成产物在预览30分钟后仍可下载，24小时产物到期后禁用下载', async () => {
  mount(); await readPreview();
  vi.useFakeTimers();
  fireEvent.click(screen.getByRole('button', { name: '读取 / 刷新预览' }));
  await act(async () => { await vi.advanceTimersByTimeAsync(100); });
  fireEvent.click(screen.getByRole('checkbox')); fireEvent.click(screen.getByRole('button', { name: '生成 POTCAR' }));
  await act(async () => { await vi.advanceTimersByTimeAsync(100); });
  expect(screen.getByText('3. 已生成 POTCAR')).toBeInTheDocument();
  await act(async () => { await vi.advanceTimersByTimeAsync(31 * 60_000); });
  expect(screen.getByRole('button', { name: '下载 POTCAR' })).toBeEnabled();
  expect(screen.queryByText('预览已到期')).not.toBeInTheDocument();
  await act(async () => { await vi.advanceTimersByTimeAsync(24 * 60 * 60_000); });
  expect(screen.getByRole('button', { name: '下载 POTCAR' })).toBeDisabled();
});

it('生成跨越预览到期时保持锁定和幂等内容，成功产物仍可下载', async () => {
  const shortly = preview(); shortly.expires_at = new Date(Date.now() + 10_000).toISOString();
  vi.spyOn(potcarApi, 'preview').mockResolvedValue(shortly);
  let resolve!: (value: Awaited<ReturnType<typeof potcarApi.assemble>>) => void;
  const assemble = vi.spyOn(potcarApi, 'assemble').mockImplementation(() => new Promise(done => { resolve = done; }));
  mount(); await readPreview(); vi.useFakeTimers();
  fireEvent.click(screen.getByRole('button', { name: '读取 / 刷新预览' }));
  await act(async () => { await vi.advanceTimersByTimeAsync(100); });
  fireEvent.click(screen.getByRole('checkbox')); fireEvent.click(screen.getByRole('button', { name: '生成 POTCAR' }));
  await act(async () => { await vi.advanceTimersByTimeAsync(11_000); });
  expect(screen.getByLabelText('POSCAR 文本')).toBeDisabled(); expect(assemble).toHaveBeenCalledTimes(1);
  await act(async () => resolve({ mode: 'toolbox', artifact: artifact() }));
  expect(screen.getByRole('button', { name: '下载 POTCAR' })).toBeEnabled();
});

it('预览自身到期后立即撤销确认，必须重新预览', async () => {
  mount(); await readPreview(); vi.useFakeTimers();
  fireEvent.click(screen.getByRole('button', { name: '读取 / 刷新预览' }));
  await act(async () => { await vi.advanceTimersByTimeAsync(100); });
  fireEvent.click(screen.getByRole('checkbox'));
  await act(async () => { await vi.advanceTimersByTimeAsync(31 * 60_000); });
  expect(screen.getByRole('checkbox')).not.toBeChecked();
  expect(screen.getByRole('checkbox')).toBeDisabled();
  expect(screen.getByText('预览已到期')).toBeInTheDocument();
  expect(writes).toEqual([]);
});

it('切换已有库立即失效旧确认与产物，刷新发现库删除时不自动切到另一库', async () => {
  let records = [library(), library('lib-2')];
  server.use(http.get(`${base}/libraries`, () => HttpResponse.json({ mode: 'toolbox', libraries: records, default_library_id: 'lib-1', revision: 1 })));
  const user = userEvent.setup(); mount(); await readPreview(); await generate();
  await user.click(screen.getByRole('combobox', { name: '选择已有赝势库' }));
  await user.click(screen.getByText('合成库 lib-2'));
  expect(screen.queryByText('3. 已生成 POTCAR')).not.toBeInTheDocument();
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  records = [library()];
  fireEvent.click(screen.getByRole('button', { name: '刷新库信息' }));
  await waitFor(() => expect(screen.getByRole('button', { name: '刷新库信息' })).toBeEnabled());
  expect(screen.getByRole('button', { name: '读取 / 刷新预览' })).toBeDisabled();
  expect(screen.getByText('选择已有库')).toBeInTheDocument();
});

it('实际下载API返回原始字节Blob，编码artifact ID并保持同一下载路径', async () => {
  const bytes = new Uint8Array([0, 255, 13, 10, 128]);
  const requests: string[] = [];
  server.use(http.get(`${base}/artifacts/:id/download`, ({ request }) => { requests.push(request.url); return new HttpResponse(bytes, { headers: { 'Content-Type': 'application/octet-stream', 'Content-Disposition': 'attachment; filename="POTCAR"' } }); }));
  const blob = await potcarApi.download('artifact+/中文');
  expect(blob.type).toBe('application/octet-stream');
  expect(new Uint8Array(await blob.arrayBuffer())).toEqual(bytes);
  expect(requests[0]).toContain('/artifacts/artifact%2B%2F%E4%B8%AD%E6%96%87/download');
});

it.each(['none', 'unscanned', 'offline'] as const)('无库/未扫描/离线状态%s阻止预览且提供管理入口，不重复许可确认', async state => {
  server.use(http.get(`${base}/libraries`, () => HttpResponse.json({ mode: 'toolbox', libraries: state === 'none' ? [] : [{ ...library(), index_revision: state === 'unscanned' ? null : 1, reachable: state !== 'offline' }], default_library_id: state === 'none' ? null : 'lib-1', revision: 1 })));
  mount(); await waitFor(() => expect(screen.getByRole('button', { name: '刷新库信息' })).toBeEnabled());
  fireEvent.change(screen.getByLabelText('POSCAR 文本'), { target: { value: poscar } });
  expect(screen.getByRole('button', { name: '读取 / 刷新预览' })).toBeDisabled();
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument(); expect(previews).toEqual([]);
});

it('本地文件读取支持UTF8和2MiB上限，旧文件读响应不得覆盖新草稿', async () => {
  let resolve!: (value: string) => void;
  const file = new File(['synthetic'], 'POSCAR'); Object.defineProperty(file, 'text', { value: () => new Promise<string>(done => { resolve = done; }) });
  mount(); await waitFor(() => expect(screen.getByRole('button', { name: '刷新库信息' })).toBeEnabled());
  fireEvent.change(screen.getByLabelText('读取本地 POSCAR 文件'), { target: { files: [file] } });
  fireEvent.change(screen.getByLabelText('POSCAR 文本'), { target: { value: '新草稿' } });
  await act(async () => resolve(poscar)); expect(screen.getByLabelText('POSCAR 文本')).toHaveValue('新草稿');
  const huge = new File([''], 'POSCAR'); Object.defineProperty(huge, 'size', { value: 2 * 1024 * 1024 + 1 });
  fireEvent.change(screen.getByLabelText('读取本地 POSCAR 文件'), { target: { files: [huge] } });
  await screen.findByText('结构文件超过 2 MiB，请使用较小的 UTF-8 POSCAR 文件。');
  const valid = new File(['synthetic'], 'POSCAR'); Object.defineProperty(valid, 'text', { value: () => Promise.resolve(poscar) });
  fireEvent.change(screen.getByLabelText('读取本地 POSCAR 文件'), { target: { files: [valid] } });
  await waitFor(() => expect(screen.getByLabelText('POSCAR 文本')).toHaveValue(poscar));
  expect(previews).toEqual([]);
});

it('拼接路由精确匹配科研深浅主题，未知子路由不匹配', () => {
  expect(hasScientificContent('/Toolbox/Potcar/Assemble/')).toBe(true);
  expect(workspaceLocation('/toolbox/potcar/assemble')).toEqual({ current: '/toolbox/settings', group: '工具箱', title: '拼接 POTCAR' });
  expect(hasScientificContent('/toolbox/potcar/assemble/unknown')).toBe(false);
});
