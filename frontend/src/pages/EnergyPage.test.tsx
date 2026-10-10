import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ConfigProvider, theme } from 'antd';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import { ApiError, toolboxApi } from '../api/client';
import { energyApi, type EnergyCollection, type EnergyGroup, type EnergyImpact, type EnergySamplePatch } from '../api/energy';
import { energyCsvFixture, energyFixture } from '../components/energy/energyTestFixtures';
import { cardSampleIds, cardScienceKey } from '../components/energy/energyCards';
import { clearEnergyDraftSessions, energyDraftSessions } from '../components/energy/energyDraftSessions';
import EnergyPage from './EnergyPage';

let record: EnergyCollection;
let clients: QueryClient[] = [];
function fixture(result = false): EnergyCollection {
  const collection = energyFixture(result);
  collection.schema_version = 'pp.energy.v2'; collection.workflow = 'cards'; collection.analysis_kind = 'adsorption'; collection.legacy_mode = false;
  collection.groups = [{ ...collection.groups[0], locked: result, confirmed: result, risk_accepted: result, confirmation_fingerprint: result ? 'confirmed-A' : null, risk_acceptance_fingerprint: result ? 'risk-A' : null, result: collection.result, status: result ? 'result' : 'draft', risks: [{ sample_id: 'es_target', name: '合成构型 A', warnings: ['unknown'] }], assignment_report: { state: 'pending', issues: [] } }, { ...collection.groups[0], id: 'g_B', name: '第二张独立卡', locked: false, confirmed: false, risk_accepted: false, confirmation_fingerprint: null, risk_acceptance_fingerprint: null, result: null, status: 'draft' }];
  collection.result = null; return collection;
}
function response() { return { mode: 'toolbox' as const, collection: structuredClone(record) }; }
function mount(path = '/toolbox/postprocessing/energy?collection=ec_fixture') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } }); clients.push(client);
  return render(<ConfigProvider theme={{ algorithm: theme.darkAlgorithm, token: { motion: false } }}><QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}><EnergyPage /></MemoryRouter></QueryClientProvider></ConfigProvider>);
}
function impact(patches: EnergySamplePatch[]): EnergyImpact { const scientificIds = patches.filter(patch => 'override' in patch).map(patch => patch.sample_id); return { preview_id: 'impact-token', expected_revision: record.revision, sample_ids: patches.map(patch => patch.sample_id), affected_cards: record.groups.filter(card => cardSampleIds(card).some(id => scientificIds.includes(id))).map(card => ({ card_id: card.id, name: card.name, locked: !!card.locked })), requires_acknowledgement: record.groups.some(card => card.locked && cardSampleIds(card).some(id => scientificIds.includes(id))) }; }
function invalidate(card: EnergyGroup): EnergyGroup { return { ...card, locked: false, confirmed: false, risk_accepted: false, confirmation_fingerprint: null, risk_acceptance_fingerprint: null, result: null, status: 'stale' }; }
async function pool() { fireEvent.click(screen.getByRole('button', { name: /共享样本与导入 ·/ })); await screen.findByRole('textbox', { name: '样本名称 es_clean' }); }
async function modal(title: string) { return (await screen.findByText(title)).closest('[role=dialog]') as HTMLElement; }
async function second() { fireEvent.click(screen.getByRole('button', { name: '打开计算卡 第二张独立卡' })); await screen.findByRole('textbox', { name: '卡名称 g_B' }); }
beforeEach(() => {
  record = fixture();
  vi.spyOn(energyApi, 'list').mockImplementation(async () => ({ mode: 'toolbox', collections: [structuredClone(record)] }));
  vi.spyOn(energyApi, 'get').mockImplementation(async () => response());
  vi.spyOn(energyApi, 'saveCard').mockImplementation(async (_, card) => { const old = record.groups.find(group => group.id === card.id)!; record = { ...record, revision: record.revision + 1, groups: record.groups.map(group => group.id === card.id ? { ...(cardScienceKey(old) === cardScienceKey(card) ? old : invalidate(old)), ...structuredClone(card) } : group) }; return response(); });
  vi.spyOn(energyApi, 'lockCard').mockImplementation(async (_, cardId, accepted) => { record = { ...record, revision: record.revision + 1, groups: record.groups.map(card => card.id === cardId ? { ...card, locked: true, confirmed: true, risk_accepted: accepted, confirmation_fingerprint: `confirmed-${cardId}`, risk_acceptance_fingerprint: accepted ? `risk-${cardId}` : null, status: 'locked' } : card) }; return response(); });
  vi.spyOn(energyApi, 'unlockCard').mockImplementation(async (_, cardId) => { record = { ...record, revision: record.revision + 1, groups: record.groups.map(card => card.id === cardId ? { ...card, locked: false } : card) }; return response(); });
  vi.spyOn(energyApi, 'previewSampleChange').mockImplementation(async (_, patches) => ({ mode: 'toolbox', impact: impact(patches) }));
  vi.spyOn(energyApi, 'saveSamples').mockImplementation(async (_, patches, preview, title) => { record = { ...record, title: title ?? record.title, revision: record.revision + 1, samples: record.samples.map(sample => { const patch = patches.find(item => item.sample_id === sample.id); return patch ? { ...sample, ...patch, revision: sample.revision + 1 } : sample; }), groups: record.groups.map(card => preview.affected_cards.some(affected => affected.card_id === card.id) ? invalidate(card) : card) }; return response(); });
  vi.spyOn(energyApi, 'addCard').mockImplementation(async (_, card) => { record = { ...record, revision: record.revision + 1, groups: [...record.groups, { ...card, locked: false, confirmed: false, risk_accepted: false, result: null, status: 'draft' }] }; return response(); });
});
afterEach(() => { cleanup(); clearEnergyDraftSessions(); clients.forEach(client => client.clear()); clients = []; vi.restoreAllMocks(); });

it('saves only the active card, filters GET-only fields and preserves inactive drafts through switching and refresh', async () => {
  mount();
  fireEvent.change(await screen.findByRole('textbox', { name: '参考定义 g_ads' }), { target: { value: 'A updated definition' } });
  await second(); fireEvent.change(screen.getByRole('textbox', { name: '参考定义 g_B' }), { target: { value: 'B local draft' } });
  fireEvent.click(screen.getByRole('button', { name: '打开计算卡 合成 CO 比较' }));
  fireEvent.click(screen.getByRole('button', { name: '保存当前卡草稿' }));
  await waitFor(() => expect(energyApi.saveCard).toHaveBeenCalledOnce());
  expect(vi.mocked(energyApi.saveCard).mock.calls[0][1]).not.toHaveProperty('risks');
  expect(vi.mocked(energyApi.saveCard).mock.calls[0][1]).not.toHaveProperty('assignment_report');
  expect(record.groups[1].reference_note).not.toBe('B local draft');
  await second(); expect(screen.getByRole('textbox', { name: '参考定义 g_B' })).toHaveValue('B local draft');
  cleanup(); Map.prototype.clear.call(energyDraftSessions); mount();
  expect(await screen.findByRole('textbox', { name: '参考定义 g_B' })).toHaveValue('B local draft');
  expect(screen.getByRole('button', { name: '保存当前卡草稿' })).toBeEnabled();
});

it('concentrates risk acceptance on the active card and keeps it through draft save before locking', async () => {
  mount(); await screen.findByRole('checkbox', { name: '接受当前卡风险 g_ads' });
  expect(screen.queryByRole('checkbox', { name: '人工确认 es_target' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '确认并锁定当前卡' }));
  expect(await screen.findByText('当前操作未完成')).toBeInTheDocument();
  expect(energyApi.lockCard).not.toHaveBeenCalled();
  fireEvent.change(screen.getByRole('textbox', { name: '参考定义 g_ads' }), { target: { value: 'A precise CO unit definition' } });
  fireEvent.click(screen.getByRole('checkbox', { name: '接受当前卡风险 g_ads' }));
  fireEvent.click(screen.getByRole('button', { name: '确认并锁定当前卡' }));
  await waitFor(() => expect(energyApi.lockCard).toHaveBeenCalledWith(expect.objectContaining({ revision: 5 }), 'g_ads', true));
  expect(await screen.findByRole('button', { name: '解锁当前卡' })).toBeEnabled();
  expect(record.groups[1].locked).toBe(false);
});

it('keeps A locked and exportable while B is edited; unlock preserves valid A confirmation and result', async () => {
  record = fixture(true); const lockedA = structuredClone(record.groups[0]); mount();
  await screen.findByRole('button', { name: '解锁当前卡' });
  expect(screen.getByRole('button', { name: '导出当前卡 CSV' })).toBeEnabled();
  await second(); fireEvent.change(screen.getByRole('spinbutton', { name: '吸附物数量 g_B es_target' }), { target: { value: '2' } });
  fireEvent.click(screen.getByRole('button', { name: '保存当前卡草稿' })); await waitFor(() => expect(energyApi.saveCard).toHaveBeenCalledOnce());
  expect(record.groups[0]).toEqual(lockedA);
  fireEvent.click(screen.getByRole('button', { name: '打开计算卡 合成 CO 比较' }));
  expect(screen.getByRole('button', { name: '导出当前卡 JSON' })).toBeEnabled();
  fireEvent.click(screen.getByRole('button', { name: '解锁当前卡' }));
  await waitFor(() => expect(energyApi.unlockCard).toHaveBeenCalledOnce());
  expect(record.groups[0].confirmed).toBe(true); expect(record.groups[0].result).toEqual(lockedA.result);
  expect(screen.getByRole('checkbox', { name: '接受当前卡风险 g_ads' })).toBeChecked();
  expect(screen.getByRole('button', { name: '导出当前卡 CSV' })).toBeEnabled();
});

it('validates quantities on blur or submit, keeps an inline error and locates the correct field without an error modal', async () => {
  mount(); const quantity = await screen.findByRole('spinbutton', { name: '吸附物数量 g_ads es_target' });
  fireEvent.change(quantity, { target: { value: '0' } });
  expect(screen.queryByText('当前操作未完成')).not.toBeInTheDocument();
  fireEvent.blur(quantity);
  await screen.findByText('请填写正整数，空值、零、负数或小数不能用于计算。', { selector: 'span.energy-field-error' });
  fireEvent.click(screen.getByRole('checkbox', { name: '接受当前卡风险 g_ads' }));
  fireEvent.click(screen.getByRole('button', { name: '确认并锁定当前卡' }));
  expect(await screen.findByText('当前操作未完成')).toBeInTheDocument(); expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '定位并修正' })); await waitFor(() => expect(quantity).toHaveFocus());
  // InputNumber can commit its numeric value while blur bubbles from input to
  // its wrapper. Composition keeps the controlled draft at 0 until that flush.
  fireEvent.compositionStart(quantity);
  fireEvent.input(quantity, { target: { value: '1' } });
  expect(quantity).toHaveValue('1');
  expect(energyDraftSessions.get('ec_fixture')?.draft.groups[0].targets[0]).toMatchObject({ adsorbate_count: 0 });
  fireEvent.blur(quantity);
  expect(energyDraftSessions.get('ec_fixture')?.draft.groups[0].targets[0]).toMatchObject({ adsorbate_count: 1 });
  await waitFor(() => expect(quantity).toHaveAttribute('aria-invalid', 'false'));
  expect(energyDraftSessions.get('ec_fixture')?.draft.groups[0].targets[0]).toMatchObject({ adsorbate_count: 1 });
  expect(screen.queryByText('请填写正整数，空值、零、负数或小数不能用于计算。', { selector: 'span.energy-field-error' })).not.toBeInTheDocument();
  await waitFor(() => expect(screen.queryByText('当前操作未完成')).not.toBeInTheDocument());
});

it('retains unresolved notification fields and a fresh global failure until a successful retry', async () => {
  mount();
  const quantity = await screen.findByRole('spinbutton', { name: '吸附物数量 g_ads es_target' });
  const units = screen.getByRole('spinbutton', { name: '参考单元数 g_ads' });
  fireEvent.change(quantity, { target: { value: '0' } }); fireEvent.change(units, { target: { value: '0' } });
  fireEvent.click(screen.getByRole('checkbox', { name: '接受当前卡风险 g_ads' }));
  fireEvent.click(screen.getByRole('button', { name: '确认并锁定当前卡' }));
  await screen.findByText(/共 2 项，请查看字段说明/);
  fireEvent.change(units, { target: { value: '1' } }); fireEvent.blur(units);
  await waitFor(() => expect(units).toHaveAttribute('aria-invalid', 'false'));
  expect(quantity).toHaveAttribute('aria-invalid', 'true');
  expect(screen.getByText('当前操作未完成')).toBeInTheDocument();
  await waitFor(() => expect(screen.queryByText(/共 2 项，请查看字段说明/)).not.toBeInTheDocument());
  fireEvent.change(quantity, { target: { value: '1' } }); fireEvent.blur(quantity);
  await waitFor(() => expect(screen.queryByText('当前操作未完成')).not.toBeInTheDocument());
  vi.mocked(energyApi.lockCard).mockRejectedValueOnce(new ApiError('ENERGY_INPUT_INVALID', '新的锁定失败', false, 422));
  fireEvent.click(screen.getByRole('checkbox', { name: '接受当前卡风险 g_ads' }));
  fireEvent.click(screen.getByRole('button', { name: '确认并锁定当前卡' }));
  await screen.findByText('新的锁定失败', { selector: '.ant-notification-notice-description p' });
  expect(screen.getByText('当前操作未完成')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '确认并锁定当前卡' }));
  await waitFor(() => expect(record.groups[0].locked).toBe(true));
  await waitFor(() => expect(screen.queryByText('当前操作未完成')).not.toBeInTheDocument());
});

it('previews shared scientific edits, cancels without saving and clears even unconfirmed local risk acceptance on confirmed mutation', async () => {
  mount(); fireEvent.click(await screen.findByRole('checkbox', { name: '接受当前卡风险 g_ads' }));
  await pool();
  const row = document.querySelector('tr[data-sample-id="es_clean"]')!; const overrides = within(row as HTMLElement).getByText('使用单独人工值'); fireEvent.click(overrides);
  fireEvent.change(screen.getByRole('textbox', { name: '人工能量 es_clean' }), { target: { value: '-101' } });
  fireEvent.change(screen.getByRole('textbox', { name: '人工值依据 es_clean' }), { target: { value: 'synthetic adjustment' } });
  fireEvent.click(screen.getByRole('button', { name: '预览影响并保存共享修改' }));
  const dialog = await modal('确认共享修改影响'); expect(within(dialog).getByText('第二张独立卡')).toBeInTheDocument();
  fireEvent.click(within(dialog).getByRole('button', { name: '取消，保留现状' })); expect(energyApi.saveSamples).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '预览影响并保存共享修改' })); fireEvent.click(within(await modal('确认共享修改影响')).getByRole('button', { name: '确认保存并更新相关卡' }));
  await waitFor(() => expect(energyApi.saveSamples).toHaveBeenCalledOnce());
  fireEvent.click(screen.getByRole('button', { name: 'Close' }));
  expect(screen.getByRole('checkbox', { name: '接受当前卡风险 g_ads' })).not.toBeChecked();
  expect(record.groups.every(card => !card.locked && card.status === 'stale')).toBe(true);
});

it('locates a structured backend error on another card by stable card and target ids', async () => {
  vi.mocked(energyApi.lockCard).mockRejectedValueOnce(new ApiError('ENERGY_INPUT_INVALID', '后端数量校验失败', false, 422, [{ card_id: 'g_B', sample_id: 'es_target', field: 'targets.es_target.adsorbate_count', code: 'COUNT', message: '第二张卡的数量需要修正' } as never]));
  mount(); fireEvent.click(await screen.findByRole('checkbox', { name: '接受当前卡风险 g_ads' })); fireEvent.click(screen.getByRole('button', { name: '确认并锁定当前卡' }));
  await screen.findByText('当前操作未完成'); fireEvent.click(screen.getByRole('button', { name: '定位并修正' }));
  const field = await screen.findByRole('spinbutton', { name: '吸附物数量 g_B es_target' }); await waitFor(() => expect(field).toHaveFocus());
  expect(screen.getByText('第二张卡的数量需要修正', { selector: 'span.energy-field-error' })).toBeInTheDocument();
});

it('retains conflicting local scientific drafts, rebases explicitly and respects the latest card lock', async () => {
  vi.mocked(energyApi.saveCard).mockRejectedValueOnce(new ApiError('ENERGY_REVISION_CONFLICT', '修订冲突', false, 409));
  mount(); fireEvent.change(await screen.findByRole('textbox', { name: '参考定义 g_ads' }), { target: { value: '保留的本地科学定义' } }); fireEvent.click(screen.getByRole('button', { name: '保存当前卡草稿' }));
  expect(await screen.findByText(/修订冲突 本地输入已保留/)).toBeInTheDocument();
  record = { ...record, revision: 5, groups: record.groups.map(card => card.id === 'g_ads' ? { ...card, locked: true, confirmed: true } : card) };
  fireEvent.click(screen.getByRole('button', { name: '读取最新并保留本地输入' }));
  await waitFor(() => expect(screen.getByRole('textbox', { name: '参考定义 g_ads' })).toBeDisabled());
  expect(screen.getByRole('textbox', { name: '参考定义 g_ads' })).toHaveValue('保留的本地科学定义');
  fireEvent.click(screen.getByRole('button', { name: '解锁当前卡' })); await waitFor(() => expect(screen.getByRole('textbox', { name: '参考定义 g_ads' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: '保存当前卡草稿' })); await waitFor(() => expect(vi.mocked(energyApi.saveCard).mock.lastCall?.[0].revision).toBe(6));
});

it('copies without inherited confirmation/result and deletes only the copied card', async () => {
  record = fixture(true);
  vi.spyOn(energyApi, 'copyCard').mockImplementation(async (_, cardId) => { const source = record.groups.find(card => card.id === cardId)!; record = { ...record, revision: record.revision + 1, groups: [...record.groups, { ...invalidate(source), id: 'g_copy', name: '复制卡', status: 'draft' }] }; return response(); });
  vi.spyOn(energyApi, 'deleteCard').mockImplementation(async (_, cardId) => { record = { ...record, revision: record.revision + 1, groups: record.groups.filter(card => card.id !== cardId) }; return response(); });
  const samples = structuredClone(record.samples), original = structuredClone(record.groups[0]); mount();
  fireEvent.click(await screen.findByRole('button', { name: '复制当前卡' }));
  const accepted = await screen.findByRole('checkbox', { name: '接受当前卡风险 g_copy' }); expect(accepted).not.toBeChecked(); expect(screen.getByRole('button', { name: '导出当前卡 CSV' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: '删除当前卡' })); fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: '确认删除计算卡' }));
  await waitFor(() => expect(energyApi.deleteCard).toHaveBeenCalledOnce()); expect(record.samples).toEqual(samples); expect(record.groups[0]).toEqual(original); expect(record.groups).toHaveLength(2);
});

it('removes all retired per-row inclusion/confirmation/risk controls and shows shared card uses', async () => {
  mount(); await screen.findByRole('textbox', { name: '卡名称 g_ads' }); await pool();
  expect(screen.queryByRole('checkbox', { name: '纳入 es_target' })).not.toBeInTheDocument(); expect(screen.queryByRole('checkbox', { name: '人工确认 es_target' })).not.toBeInTheDocument(); expect(screen.queryByRole('checkbox', { name: '接受风险 es_target' })).not.toBeInTheDocument();
  const row = document.querySelector('tr[data-sample-id="es_clean"]')!;
  expect(within(row as HTMLElement).getByText('合成 CO 比较 · 清洁表面参考')).toBeInTheDocument(); expect(within(row as HTMLElement).getByText('第二张独立卡 · 清洁表面参考')).toBeInTheDocument();
  expect(screen.getByRole('checkbox', { name: '选择样本 es_clean' })).toBeEnabled();
});

it('imports same-name OUTCAR files with independent paths/revisions without altering existing target choices', async () => {
  record.samples = []; record.groups = record.groups.map(card => card.kind === 'adsorption' ? { ...card, clean_sample_id: '', adsorbate_sample_id: '', targets: [] } : card);
  vi.spyOn(energyApi, 'upload').mockImplementation(async (collection, file, path) => { const sample = { ...structuredClone(energyFixture().samples[0]), id: `es_import_${collection.revision}`, name: file.name, source: { kind: 'local_upload' as const, relative_path: path } }; record = { ...collection, revision: collection.revision + 1, samples: [...collection.samples, sample] }; return response(); });
  mount(); await screen.findByRole('textbox', { name: '卡名称 g_ads' }); fireEvent.click(screen.getByRole('button', { name: /共享样本与导入 ·/ }));
  fireEvent.change(await screen.findByLabelText('选择能量 OUTCAR'), { target: { files: [new File(['one'], 'OUTCAR'), new File(['two'], 'OUTCAR')] } });
  const paths = screen.getAllByRole('textbox', { name: /相对目录 / }); fireEvent.change(paths[0], { target: { value: '中文 表面/位点 A/OUTCAR' } }); fireEvent.change(paths[1], { target: { value: '中文 表面/位点 B/OUTCAR' } });
  fireEvent.click(screen.getByRole('button', { name: '批量读取并加入确认表' }));
  await waitFor(() => expect(energyApi.upload).toHaveBeenCalledTimes(2)); expect(vi.mocked(energyApi.upload).mock.calls[0][2]).toBe('中文 表面/位点 A/OUTCAR'); expect(vi.mocked(energyApi.upload).mock.calls[1][0].revision).toBe(5); expect(record.groups.every(card => card.targets.length === 0)).toBe(true);
});

it('preserves CSV preview/import and keeps management deletion behind card impact confirmation', async () => {
  vi.spyOn(energyApi, 'previewCsv').mockResolvedValue({ mode: 'toolbox', preview: energyCsvFixture() });
  vi.spyOn(energyApi, 'importCsv').mockImplementation(async collection => { record = { ...collection, revision: collection.revision + 1 }; return response(); });
  vi.spyOn(energyApi, 'previewRemoval').mockImplementation(async () => ({ mode: 'toolbox', removal: { sample_ids: ['es_clean'], removed_count: 1, affected_target_ids: ['es_target'], cleared_reference_keys: ['clean_sample_id'], impact: { ...impact([{ sample_id: 'es_clean', override: null }]), sample_ids: ['es_clean'] } } }));
  vi.spyOn(energyApi, 'removeSamplesWithImpact').mockImplementation(async () => { record = { ...record, revision: record.revision + 1, samples: record.samples.filter(sample => sample.id !== 'es_clean'), groups: record.groups.map(card => card.kind === 'adsorption' ? { ...invalidate(card), clean_sample_id: '' } : card) }; return { ...response(), removal: { sample_ids: ['es_clean'], removed_count: 1, affected_target_ids: ['es_target'], cleared_reference_keys: ['clean_sample_id'] } }; });
  mount(); await screen.findByRole('textbox', { name: '卡名称 g_ads' }); await pool();
  fireEvent.click(screen.getByRole('button', { name: 'CSV' })); fireEvent.change(screen.getByLabelText('选择能量 CSV'), { target: { files: [new File(['name,composition,energy_ev\na,Pt:4,-100'], 'samples.csv')] } });
  fireEvent.click(screen.getByRole('button', { name: '预览 CSV' })); await screen.findByText('预览 3 行，3 行通过检查。尚未导入。'); fireEvent.click(screen.getByRole('button', { name: '确认导入预览中的 CSV' })); await waitFor(() => expect(energyApi.importCsv).toHaveBeenCalledOnce());
  fireEvent.click(screen.getByRole('checkbox', { name: '选择样本 es_clean' })); fireEvent.click(screen.getByRole('button', { name: '删除所选' }));
  const dialog = await modal('确认共享样本删除影响'); expect(within(dialog).getByText('第二张独立卡')).toBeInTheDocument(); expect(energyApi.removeSamplesWithImpact).not.toHaveBeenCalled();
  fireEvent.click(within(dialog).getByRole('button', { name: '确认删除并更新相关卡' })); await waitFor(() => expect(energyApi.removeSamplesWithImpact).toHaveBeenCalledOnce()); expect(record.samples).toHaveLength(2); expect(record.groups.every(card => card.status === 'stale')).toBe(true);
});

it('requires an initial analysis type and creates a default independent card after the empty v2 collection', async () => {
  vi.spyOn(energyApi, 'create').mockImplementation(async (title, kind) => { record = { ...fixture(), id: 'ec_new', title, analysis_kind: kind, samples: [], groups: [] }; return response(); });
  mount('/toolbox/postprocessing/energy');
  expect(await screen.findByRole('button', { name: '创建分析并导入数据' })).toBeDisabled(); fireEvent.click(screen.getByRole('radio', { name: '材料形成能' })); fireEvent.click(screen.getByRole('button', { name: '创建分析并导入数据' }));
  await waitFor(() => expect(energyApi.create).toHaveBeenCalledWith('新的材料形成能分析', 'formation')); await waitFor(() => expect(energyApi.addCard).toHaveBeenCalledWith(expect.objectContaining({ id: 'ec_new', analysis_kind: 'formation' }), expect.objectContaining({ kind: 'formation' })));
  expect(await screen.findByLabelText('选择能量 OUTCAR')).toBeEnabled();
});

it('calculates only the locked active card without requiring an unrelated incomplete card', async () => {
  record.groups[0] = { ...record.groups[0], locked: true, confirmed: true, risk_accepted: true };
  record.groups[1] = { ...record.groups[1], targets: [] };
  vi.spyOn(energyApi, 'calculateCard').mockImplementation(async (_, cardId) => { record = { ...record, revision: record.revision + 1, groups: record.groups.map(card => card.id === cardId ? { ...card, result: energyFixture(true).result, status: 'result' } : card) }; return response(); });
  mount(); fireEvent.click(await screen.findByRole('button', { name: '计算当前卡' })); await waitFor(() => expect(energyApi.calculateCard).toHaveBeenCalledWith(expect.objectContaining({ id: 'ec_fixture' }), 'g_ads'));
  expect(await screen.findByText('-2 eV', { selector: 'strong' })).toBeInTheDocument(); expect(record.groups[1].result).toBeNull();
});

it('shows old mixed records completely and copies every card of the selected type without overwriting the original', async () => {
  const old = energyFixture(true); old.analysis_kind = null; old.card_migration = { required: true, read_only: true, reason: 'mixed' };
  old.groups.push({ id: 'g_formation', name: '旧形成能卡', kind: 'formation', energy_basis: 'sigma_to_zero_ev', basis_confirmed: true, reference_note: 'old reference', element_references: {}, targets: [] }); record = old; const original = structuredClone(record);
  vi.spyOn(energyApi, 'copy').mockImplementation(async (_, kind) => ({ mode: 'toolbox', collection: { ...fixture(), id: 'ec_copy', analysis_kind: kind, groups: [structuredClone(old.groups[1])] } }));
  mount(); expect(await screen.findByText('旧混合分析完整保留，以只读方式打开。')).toBeInTheDocument(); expect(screen.getByRole('button', { name: '打开计算卡 旧形成能卡' })).toBeEnabled(); expect(screen.getByRole('button', { name: '导出当前卡 CSV' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: '复制为材料形成能分析' })); await waitFor(() => expect(energyApi.copy).toHaveBeenCalledWith(expect.objectContaining({ id: 'ec_fixture' }), 'formation')); expect(record).toEqual(original); expect(energyApi.saveCard).not.toHaveBeenCalled();
});
it('keeps shared input errors beside the actual field, locates it and clears only corrected errors on blur', async () => {
  mount(); await screen.findByRole('textbox', { name: '卡名称 g_ads' }); await pool();
  const row = document.querySelector<HTMLElement>('tr[data-sample-id="es_clean"]')!; fireEvent.click(within(row).getByText('使用单独人工值'));
  const energy = screen.getByRole('textbox', { name: '人工能量 es_clean' });
  fireEvent.change(energy, { target: { value: 'invalid-energy' } }); fireEvent.click(screen.getByRole('button', { name: '预览影响并保存共享修改' }));
  expect(await screen.findByText('当前操作未完成')).toBeInTheDocument(); expect(energy).toHaveAttribute('aria-invalid', 'true'); expect(energyApi.previewSampleChange).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '定位并修正' })); await waitFor(() => expect(energy).toHaveFocus());
  fireEvent.change(energy, { target: { value: '-101' } }); fireEvent.blur(energy);
  expect(energy).toHaveAttribute('aria-invalid', 'false'); expect(screen.getByRole('textbox', { name: '人工值依据 es_clean' })).toHaveAttribute('aria-invalid', 'true');
});
it('reuses immutable task-cache samples without changing existing card choices or locks', async () => {
  record = fixture(true); record.samples[0].source = { ...record.samples[0].source, kind: 'task_result', project_id: 'p', task_id: 't', job_key: 'relax', attempt_id: 'attempt', snapshot_sha256: 'stable-snapshot' };
  vi.spyOn(toolboxApi, 'listProjects').mockResolvedValue({ mode: 'toolbox', projects: [] });
  vi.spyOn(energyApi, 'reuse').mockImplementation(async (collection, sourceId, sampleId) => { expect(sourceId).toBe('ec_fixture'); const copied = { ...structuredClone(record.samples.find(sample => sample.id === sampleId)!), id: 'es_reused' }; record = { ...collection, revision: collection.revision + 1, samples: [...collection.samples, copied] }; return response(); });
  const savedCards = structuredClone(record.groups); mount(); await screen.findByRole('textbox', { name: '卡名称 g_ads' }); await pool(); fireEvent.click(screen.getByRole('button', { name: '已有任务／缓存' }));
  const heading = await screen.findByText('复用已缓存任务样本（无需连接超算）'); fireEvent.click(heading);
  fireEvent.click(screen.getByRole('button', { name: /合成能量比较 · 合成表面 · es_clean/ })); await waitFor(() => expect(energyApi.reuse).toHaveBeenCalledOnce()); expect(record.groups).toEqual(savedCards); expect(record.samples.at(-1)?.source.snapshot_sha256).toBe('stable-snapshot');
});
