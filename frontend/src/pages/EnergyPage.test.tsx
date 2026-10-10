import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ConfigProvider, theme } from 'antd';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import { ApiError, toolboxApi } from '../api/client';
import { energyApi, type EnergyCollection, type EnergyRemovalRequest } from '../api/energy';
import type { ToolboxTaskDetail } from '../types/toolbox';
import { energyCsvFixture, energyFixture } from '../components/energy/energyTestFixtures';
import * as energyDraftHelpers from '../components/energy/energyDraft';
import EnergyPage from './EnergyPage';
import { clearEnergyDraftSessions } from '../components/energy/energyDraftSessions';

let record: EnergyCollection;
let clients: QueryClient[] = [];
function mount(dark = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } }); clients.push(client);
  return render(<ConfigProvider theme={{ algorithm: dark ? theme.darkAlgorithm : theme.defaultAlgorithm, token: { motion: false } }}><QueryClientProvider client={client}><MemoryRouter initialEntries={['/toolbox/postprocessing/energy?collection=ec_fixture']}><EnergyPage /></MemoryRouter></QueryClientProvider></ConfigProvider>);
}
beforeEach(() => {
  record = energyFixture(true);
  vi.spyOn(energyApi, 'list').mockImplementation(async () => ({ mode: 'toolbox', collections: [structuredClone(record)] }));
  vi.spyOn(energyApi, 'get').mockImplementation(async () => ({ mode: 'toolbox', collection: structuredClone(record) }));
  vi.spyOn(energyApi, 'save').mockImplementation(async (_, configuration) => {
    record = { ...record, title: configuration.title, revision: record.revision + 1, result: null, groups: structuredClone(configuration.groups), samples: record.samples.map(sample => { const row = configuration.samples.find(item => item.sample_id === sample.id)!; return { ...sample, ...row }; }) };
    return { mode: 'toolbox', collection: structuredClone(record) };
  });
  vi.spyOn(energyApi, 'lock').mockImplementation(async base => { record = { ...base, revision: base.revision + 1, locked: true, lock_fingerprint: 'synthetic-lock' }; return { mode: 'toolbox', collection: structuredClone(record) }; });
  vi.spyOn(energyApi, 'unlock').mockImplementation(async base => { record = { ...base, revision: base.revision + 1, locked: false, lock_fingerprint: null }; return { mode: 'toolbox', collection: structuredClone(record) }; });
  vi.spyOn(energyApi, 'autofill').mockImplementation(async base => { record = { ...base, revision: base.revision + 1, assignment_report: { state: 'pending', issues: [] } }; return { mode: 'toolbox', collection: structuredClone(record) }; });
});
afterEach(() => { cleanup(); clearEnergyDraftSessions(); clients.forEach(client => client.clear()); clients = []; vi.restoreAllMocks(); });

function removalPreview(input: EnergyRemovalRequest) {
  const ids = input.clear_all ? record.samples.map(sample => sample.id) : input.sample_ids;
  // Backend reports both removed targets and surviving users of removed references.
  return { sample_ids: ids, removed_count: ids.length, affected_target_ids: ids.includes('es_clean') || ids.includes('es_target') ? ['es_target'] : [], cleared_reference_keys: ids.includes('es_clean') ? ['clean_sample_id'] : [] };
}

it('marks results stale and preserves risk acceptance when a reference coefficient changes', async () => {
  mount(true);
  expect(await screen.findByText('-2 eV', { selector: 'strong' })).toBeInTheDocument();
  fireEvent.change(screen.getByRole('spinbutton', { name: '参考单元数 g_ads' }), { target: { value: '2' } });
  expect(screen.queryByText('-2 eV', { selector: 'strong' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '导出能量 CSV' })).toBeDisabled();
  expect(screen.getByRole('checkbox', { name: '人工确认 es_target' })).not.toBeChecked();
  expect(screen.getByRole('checkbox', { name: '接受风险 es_target' })).toBeChecked();
  expect(screen.getByRole('checkbox', { name: '已核对统一能量口径与参考定义' })).not.toBeChecked();
  expect(screen.getByText('科学输入已变化，旧结果已过期。')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '保存草稿' }));
  await waitFor(() => expect(energyApi.save).toHaveBeenCalledOnce());
  expect(vi.mocked(energyApi.save).mock.calls[0][1].samples.every(row => !row.confirmed && row.accepted_warnings)).toBe(true);
  expect(vi.mocked(energyApi.save).mock.calls[0][1].groups[0].basis_confirmed).toBe(false);
  expect(await screen.findByText('草稿已保存，未执行计算。')).toBeInTheDocument();
});

it('requires explicit acceptance for unknown samples and keeps markers through calculation and reopen', async () => {
  record = energyFixture(false);
  record.analysis_kind = 'adsorption'; record.legacy_mode = false; record.locked = false;
  vi.spyOn(energyApi, 'calculate').mockImplementation(async saved => { record = { ...energyFixture(true), analysis_kind: 'adsorption', legacy_mode: false, locked: true, revision: saved.revision + 1 }; return { mode: 'toolbox', collection: structuredClone(record) }; });
  mount();
  await screen.findByRole('checkbox', { name: '接受风险 es_target' });
  expect(screen.getByRole('button', { name: '确认并锁定当前分析' })).toBeDisabled();
  expect(screen.getByRole('checkbox', { name: '接受风险 es_target' })).not.toBeChecked();
  expect(energyApi.lock).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('checkbox', { name: '明确接受已纳入样本的未完成／未知状态及警告' }));
  fireEvent.click(screen.getByRole('button', { name: '确认并锁定当前分析' }));
  await screen.findByRole('button', { name: '解锁编辑' });
  expect(vi.mocked(energyApi.save).mock.calls[0][1].samples.every(row => !row.confirmed && row.accepted_warnings)).toBe(true);
  expect(vi.mocked(energyApi.save).mock.calls[1][1].samples.every(row => row.confirmed && row.accepted_warnings)).toBe(true);
  expect(screen.getByRole('checkbox', { name: '纳入 es_target' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: '计算能量' }));
  await waitFor(() => expect(energyApi.calculate).toHaveBeenCalledWith(expect.objectContaining({ revision: 7, locked: true })));
  expect(await screen.findByText('合成构型 A：运行及收敛状态未知')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '导出能量 JSON' })).toBeEnabled();
  cleanup(); mount();
  expect(await screen.findByRole('checkbox', { name: '接受风险 es_target' })).toBeChecked();
  expect(await screen.findByText('合成构型 A：运行及收敛状态未知')).toBeInTheDocument();
});

it('retains unsaved input after revision conflicts and explicitly rebases on the latest version', async () => {
  const savedImplementation = vi.mocked(energyApi.save).getMockImplementation()!;
  vi.mocked(energyApi.save).mockRejectedValueOnce(new ApiError('ENERGY_REVISION_CONFLICT', '修订冲突', false, 409));
  mount();
  fireEvent.change(await screen.findByRole('textbox', { name: '当前能量比较名称' }), { target: { value: '保留的本地名称' } });
  fireEvent.click(screen.getByRole('button', { name: '保存草稿' }));
  expect(await screen.findByText(/修订冲突 本地输入已保留/)).toBeInTheDocument();
  expect(screen.getByRole('textbox', { name: '当前能量比较名称' })).toHaveValue('保留的本地名称');
  cleanup(); record = { ...record, revision: 5, title: '其他窗口保存的名称', analysis_kind: 'adsorption', legacy_mode: false, locked: true }; mount();
  expect(await screen.findByRole('textbox', { name: '当前能量比较名称' })).toHaveValue('保留的本地名称');
  fireEvent.click(screen.getByRole('button', { name: '读取最新并保留本地输入' }));
  expect(await screen.findByText('已读取服务器锁定状态并保留本地输入；先解锁才能继续编辑与保存。')).toBeInTheDocument();
  expect(screen.getByRole('textbox', { name: '当前能量比较名称' })).toBeDisabled();
  expect(screen.getByRole('button', { name: '保存草稿' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: '解锁编辑' }));
  await waitFor(() => expect(screen.getByRole('textbox', { name: '当前能量比较名称' })).toBeEnabled());
  vi.mocked(energyApi.save).mockImplementation(savedImplementation);
  fireEvent.click(screen.getByRole('button', { name: '保存草稿' }));
  await waitFor(() => expect(vi.mocked(energyApi.save).mock.lastCall?.[1]).toMatchObject({ expected_revision: 6, title: '保留的本地名称' }));
});

it('imports same-name files as independent samples and preserves editable Chinese relative metadata', async () => {
  const group = energyFixture().groups[0];
  record = { ...energyFixture(), analysis_kind: 'adsorption', legacy_mode: false, samples: [], groups: [{ ...group, clean_sample_id: '', adsorbate_sample_id: '', targets: [] } as EnergyCollection['groups'][number]] };
  vi.spyOn(energyApi, 'upload').mockImplementation(async (base, file, relativePath) => {
    const sample = structuredClone(energyFixture().samples[0]); sample.id = `es_uploaded_${base.revision}`; sample.name = file.name; sample.source = { kind: 'local_upload', original_name: file.name, relative_path: relativePath, sha256: `synthetic-${base.revision}`, size_bytes: file.size }; sample.role = null; sample.included = false;
    record = { ...base, revision: base.revision + 1, samples: [...base.samples, sample], result: null }; return { mode: 'toolbox', collection: structuredClone(record) };
  });
  mount();
  fireEvent.change(await screen.findByLabelText('选择能量 OUTCAR'), { target: { files: [new File(['one'], 'OUTCAR'), new File(['two'], 'OUTCAR')] } });
  const paths = screen.getAllByRole('textbox', { name: /相对目录 / });
  fireEvent.change(paths[0], { target: { value: '中文 表面/位点 A/OUTCAR' } }); fireEvent.change(paths[1], { target: { value: '中文 表面/位点 B/OUTCAR' } });
  fireEvent.click(screen.getByRole('button', { name: '批量读取并加入确认表' }));
  await waitFor(() => expect(energyApi.upload).toHaveBeenCalledTimes(2));
  expect(vi.mocked(energyApi.upload).mock.calls[0][2]).toBe('中文 表面/位点 A/OUTCAR');
  expect(vi.mocked(energyApi.upload).mock.calls[1][0].revision).toBe(5);
  await waitFor(() => expect(document.querySelectorAll('tr[data-sample-id]')).toHaveLength(2));
  expect(screen.getByRole('checkbox', { name: '纳入 es_uploaded_4' })).not.toBeChecked();
  expect(screen.getByRole('checkbox', { name: '纳入 es_uploaded_5' })).not.toBeChecked();
});

it('requires a type before creation and preserves the selected type through the import workflow', async () => {
  vi.spyOn(energyApi, 'create').mockImplementation(async (title, kind) => ({ mode: 'toolbox', collection: { ...energyFixture(), id: 'ec_new', title, analysis_kind: kind, legacy_mode: false, locked: false, samples: [], groups: [], result: null } }));
  mount();
  expect(screen.getByRole('button', { name: '创建分析并导入数据' })).toBeDisabled();
  fireEvent.click(screen.getByRole('radio', { name: '材料形成能' }));
  fireEvent.click(screen.getByRole('button', { name: '创建分析并导入数据' }));
  await waitFor(() => expect(energyApi.create).toHaveBeenCalledWith('新的材料形成能分析', 'formation'));
  expect(await screen.findByText('3 · 材料形成能参考与目标')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '添加形成能比较组' })).not.toBeInTheDocument();
  expect(screen.getByLabelText('选择能量 OUTCAR')).toBeEnabled();
});

it('unlocking preserves all confirmations and target-only edits leave references confirmed', async () => {
  record = { ...energyFixture(true), analysis_kind: 'adsorption', legacy_mode: false, locked: true };
  mount();
  await screen.findByRole('button', { name: '解锁编辑' });
  expect(screen.getByRole('button', { name: '清空当前分析样本' })).toBeDisabled();
  expect(screen.getByRole('checkbox', { name: '选择样本 es_clean' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: '解锁编辑' }));
  await waitFor(() => expect(screen.getByRole('checkbox', { name: '人工确认 es_target' })).toBeEnabled());
  expect(screen.getByRole('checkbox', { name: '人工确认 es_target' })).toBeChecked();
  expect(screen.getByRole('checkbox', { name: '接受风险 es_target' })).toBeChecked();
  expect(screen.getByRole('checkbox', { name: '已核对统一能量口径与参考定义' })).toBeChecked();
  fireEvent.change(screen.getByRole('spinbutton', { name: '吸附物数量 g_ads es_target' }), { target: { value: '2' } });
  expect(screen.getByRole('checkbox', { name: '人工确认 es_target' })).not.toBeChecked();
  expect(screen.getByRole('checkbox', { name: '人工确认 es_clean' })).toBeChecked();
  expect(screen.getByRole('checkbox', { name: '接受风险 es_target' })).toBeChecked();
  expect(screen.getByRole('checkbox', { name: '已核对统一能量口径与参考定义' })).toBeChecked();
});

it('preserves every legacy group and explicitly copies a selected type without changing the original', async () => {
  record.groups.push({ id: 'g_formation', kind: 'formation', name: '旧形成能条件', energy_basis: 'sigma_to_zero_ev', basis_confirmed: true, reference_note: '合成参考', element_references: {}, targets: [] });
  const original = structuredClone(record);
  vi.spyOn(energyApi, 'copy').mockImplementation(async (_, kind, groupId) => ({ mode: 'toolbox', collection: { ...energyFixture(false), id: 'ec_copy', analysis_kind: kind, legacy_mode: false, groups: [structuredClone(record.groups.find(group => group.id === groupId)!)] } }));
  mount();
  expect(await screen.findByText('旧分析完整保留，当前以只读方式打开。')).toBeInTheDocument();
  expect(screen.getAllByRole('textbox', { name: /组名称 / })).toHaveLength(2);
  expect(screen.getByRole('textbox', { name: '组名称 g_formation' })).toBeDisabled();
  expect(screen.getByRole('button', { name: '导出能量 CSV' })).toBeEnabled();
  fireEvent.click(screen.getByRole('button', { name: '复制为材料形成能分析' }));
  await waitFor(() => expect(energyApi.copy).toHaveBeenCalledWith(expect.objectContaining({ id: 'ec_fixture' }), 'formation', 'g_formation'));
  expect(record).toEqual(original);
  expect(energyApi.save).not.toHaveBeenCalled();
});

it('applies returned unique-composition roles once after a batch of path-unknown OUTCAR files', async () => {
  const fixture = energyFixture(false);
  record = { ...fixture, analysis_kind: 'adsorption', legacy_mode: false, samples: [], groups: [{ ...fixture.groups[0], clean_sample_id: '', adsorbate_sample_id: '', targets: [] } as EnergyCollection['groups'][number]] };
  vi.spyOn(energyApi, 'upload').mockImplementation(async (base, file) => {
    const sample = structuredClone(fixture.samples[base.samples.length]);
    sample.name = file.name; sample.source = { kind: 'local_upload', original_name: file.name, relative_path: '' }; sample.role = null; sample.included = false;
    record = { ...base, revision: base.revision + 1, samples: [...base.samples, sample], assignment_report: null };
    return { mode: 'toolbox', collection: structuredClone(record) };
  });
  vi.mocked(energyApi.autofill).mockImplementation(async base => {
    expect(base.samples).toHaveLength(3);
    record = { ...base, revision: base.revision + 1, samples: base.samples.map((sample, index) => ({ ...sample, role: fixture.samples[index].role, role_origin: 'auto', included: true, included_origin: 'auto', assignment_reasons: ['路径未知；宿主、参考和正整数计量关系形成唯一候选，不证明参考态适用性'] })), groups: [{ ...fixture.groups[0], reference_origins: { clean_sample_id: 'auto', adsorbate_sample_id: 'auto', reference_units: 'auto' } }], assignment_report: { state: 'ready', issues: [] } };
    return { mode: 'toolbox', collection: structuredClone(record) };
  });
  mount();
  fireEvent.change(await screen.findByLabelText('选择能量 OUTCAR'), { target: { files: [new File(['clean'], 'OUTCAR'), new File(['reference'], 'OUTCAR'), new File(['target'], 'OUTCAR')] } });
  fireEvent.click(screen.getByRole('button', { name: '批量读取并加入确认表' }));
  await waitFor(() => expect(energyApi.autofill).toHaveBeenCalledOnce());
  expect(await screen.findByText('明确角色与参考已填入，等待科学核对')).toBeInTheDocument();
  expect(screen.getAllByText('规则填入 · 待核对')).toHaveLength(3);
  expect(screen.getByRole('checkbox', { name: '纳入 es_target' })).toBeChecked();
  expect(screen.getByRole('checkbox', { name: '人工确认 es_target' })).not.toBeChecked();
  expect(screen.getByRole('checkbox', { name: '接受风险 es_target' })).not.toBeChecked();
  expect(screen.getByText(/规则将整份吸附物参考视为 1 个单元/)).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '采用角色建议' })).not.toBeInTheDocument();
  expect(record.samples.every(sample => !sample.source.relative_path)).toBe(true);
});

it('shows duplicate-reference ambiguity centrally without treating filename hints as verified roles', async () => {
  const duplicate = structuredClone(record.samples[0]); duplicate.id = 'es_duplicate'; duplicate.name = 'OUTCAR'; duplicate.role = null; duplicate.included = false;
  record.samples[0].role = null; record.samples[0].included = false; record.samples[0].role_suggestion = { role: 'clean_slab', reasons: ['文件名线索'], confidence: 'low' };
  record.samples.push(duplicate);
  record.assignment_report = { state: 'ambiguous', issues: [{ code: 'RELATION_AMBIGUOUS', message: '存在多个可行表面/吸附物配对，不按能量或原子数选择', sample_ids: ['es_clean', 'es_duplicate'] }] };
  if (record.groups[0].kind === 'adsorption') record.groups[0].clean_sample_id = '';
  mount();
  expect(await screen.findByText('待处理歧义与缺口 · 1 项')).toBeInTheDocument();
  expect(screen.getByText(/存在多个可行表面\/吸附物配对/)).toBeInTheDocument();
  expect(screen.getByRole('checkbox', { name: '纳入 es_clean' })).not.toBeChecked();
  expect(screen.getByRole('button', { name: '确认并锁定当前分析' })).toBeDisabled();
  expect(energyApi.autofill).not.toHaveBeenCalled();
});

it('persists manual same-role selection and exclusion before append assignment without clearing unrelated review', async () => {
  record = { ...record, analysis_kind: 'adsorption', legacy_mode: false, samples: record.samples.map(sample => ({ ...sample, role_origin: 'auto', included_origin: 'auto' })) };
  vi.spyOn(energyApi, 'upload').mockImplementation(async base => {
    const sample = { ...structuredClone(energyFixture(false).samples[2]), id: 'es_extra', role: null, included: false };
    record = { ...base, revision: base.revision + 1, samples: [...base.samples, sample], result: null };
    return { mode: 'toolbox', collection: structuredClone(record) };
  });
  vi.mocked(energyApi.autofill).mockImplementation(async base => {
    record = { ...base, revision: base.revision + 1, samples: base.samples.map(sample => ({ ...sample, role: sample.role_origin === 'manual' ? sample.role : sample.id === 'es_extra' ? 'adsorbed' : sample.role, included: sample.included_origin === 'manual' ? sample.included : true })) };
    if (record.groups[0].kind === 'adsorption') record.groups[0].targets = [{ sample_id: 'es_extra', adsorbate_count: 1 }];
    return { mode: 'toolbox', collection: structuredClone(record) };
  });
  mount();
  fireEvent.mouseDown(await screen.findByRole('combobox', { name: '确认角色 es_target' }));
  fireEvent.click(await screen.findByText('吸附构型', { selector: '.ant-select-item-option-content' }));
  fireEvent.click(screen.getByRole('checkbox', { name: '纳入 es_target' }));
  fireEvent.change(screen.getByLabelText('选择能量 OUTCAR'), { target: { files: [new File(['extra'], 'OUTCAR')] } });
  fireEvent.click(screen.getByRole('button', { name: '批量读取并加入确认表' }));
  await waitFor(() => expect(energyApi.autofill).toHaveBeenCalledOnce());
  expect(vi.mocked(energyApi.save).mock.calls[0][1].samples.find(row => row.sample_id === 'es_target')).toMatchObject({ role: 'adsorbed', role_origin: 'manual', included: false, included_origin: 'manual' });
  expect(screen.getByRole('checkbox', { name: '纳入 es_target' })).not.toBeChecked();
  expect(screen.getByText('人工指定')).toBeInTheDocument();
  expect(screen.getByRole('checkbox', { name: '人工确认 es_clean' })).toBeChecked();
  expect(screen.getByRole('checkbox', { name: '接受风险 es_clean' })).toBeChecked();
});

it('uses independent selection and cancels removal without changing included, reviewed or accepted states', async () => {
  vi.spyOn(energyApi, 'previewRemoval').mockImplementation(async (_, input) => ({ mode: 'toolbox', removal: removalPreview(input) }));
  vi.spyOn(energyApi, 'removeSamples');
  mount();
  fireEvent.click(await screen.findByRole('checkbox', { name: '选择样本 es_clean' }));
  expect(screen.getByText('已选 1 个样本')).toBeInTheDocument();
  expect(screen.getByRole('checkbox', { name: '纳入 es_clean' })).toBeChecked();
  expect(screen.getByRole('checkbox', { name: '人工确认 es_clean' })).toBeChecked();
  expect(screen.getByRole('checkbox', { name: '接受风险 es_clean' })).toBeChecked();
  fireEvent.click(screen.getByRole('button', { name: '删除所选' }));
  // rc-component uses the same test-id for Select inputs and Modal titles in NODE_ENV=test.
  expect(within(await screen.findByRole('dialog')).getByText('确认删除所选样本')).toBeInTheDocument();
  expect(screen.getByText('1 个保留目标将缺少参考，相关计算会被阻止。')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '取消删除' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  expect(energyApi.removeSamples).not.toHaveBeenCalled();
  expect(screen.getByRole('checkbox', { name: '选择样本 es_clean' })).toBeChecked();
  expect(record.samples).toHaveLength(3);
  fireEvent.click(screen.getByRole('checkbox', { name: '选择样本 es_clean' }));
  fireEvent.click(screen.getByRole('checkbox', { name: '选择样本 es_target' }));
  fireEvent.click(screen.getByRole('button', { name: '删除所选' }));
  await screen.findByRole('dialog');
  expect(screen.queryByText(/个保留目标将缺少参考/)).not.toBeInTheDocument();
  expect(energyApi.removeSamples).not.toHaveBeenCalled();
});

it('retains the dialog, saved draft and selection on failure, then deletes a shared reference without replacement', async () => {
  vi.spyOn(energyApi, 'previewRemoval').mockImplementation(async (_, input) => ({ mode: 'toolbox', removal: removalPreview(input) }));
  const remove = vi.spyOn(energyApi, 'removeSamples').mockRejectedValueOnce(new ApiError('ENERGY_REMOVE_FAILED', '模拟删除失败', true, 500)).mockImplementationOnce(async (base, input) => {
    const preview = removalPreview(input);
    record = { ...base, revision: base.revision + 1, samples: base.samples.filter(sample => !preview.sample_ids.includes(sample.id)).map(sample => sample.id === 'es_target' ? { ...sample, confirmed: false } : sample), result: null };
    if (record.groups[0].kind === 'adsorption') record.groups[0] = { ...record.groups[0], clean_sample_id: '', basis_confirmed: false, reference_origins: { clean_sample_id: 'manual' } };
    return { mode: 'toolbox', collection: structuredClone(record), removal: preview };
  });
  mount();
  fireEvent.change(await screen.findByRole('textbox', { name: '当前能量比较名称' }), { target: { value: '删除前本地名称' } });
  fireEvent.click(screen.getByRole('checkbox', { name: '选择样本 es_clean' }));
  fireEvent.click(screen.getByRole('button', { name: '删除所选' }));
  expect(within(await screen.findByRole('dialog')).getByText('确认删除所选样本')).toBeInTheDocument();
  expect(energyApi.previewRemoval).toHaveBeenCalledWith(expect.objectContaining({ revision: 5, title: '删除前本地名称' }), { sample_ids: ['es_clean'] });
  fireEvent.click(screen.getByRole('button', { name: '确认删除样本' }));
  expect(await screen.findByText(/模拟删除失败 样本与管理选择保留/)).toBeInTheDocument();
  expect(screen.getByRole('dialog')).toBeInTheDocument();
  expect(screen.getByRole('checkbox', { name: '选择样本 es_clean' })).toBeChecked();
  expect(screen.getByRole('textbox', { name: '当前能量比较名称' })).toHaveValue('删除前本地名称');
  fireEvent.click(screen.getByRole('button', { name: '确认删除样本' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  expect(remove).toHaveBeenCalledTimes(2);
  expect(screen.queryByRole('checkbox', { name: '选择样本 es_clean' })).not.toBeInTheDocument();
  expect(screen.getByRole('checkbox', { name: '人工确认 es_reference' })).toBeChecked();
  expect(screen.getByRole('checkbox', { name: '接受风险 es_target' })).toBeChecked();
  expect(screen.getByRole('button', { name: '确认并锁定当前分析' })).toBeDisabled();
  expect(energyApi.autofill).not.toHaveBeenCalled();
});

it('requires clear confirmation and returns to an importable empty table after atomic clear', async () => {
  vi.spyOn(energyApi, 'previewRemoval').mockImplementation(async (_, input) => ({ mode: 'toolbox', removal: removalPreview(input) }));
  vi.spyOn(energyApi, 'removeSamples').mockImplementation(async (base, input) => {
    const preview = removalPreview(input);
    record = { ...base, revision: base.revision + 1, samples: [], result: null, groups: base.groups.map(group => group.kind === 'adsorption' ? { ...group, clean_sample_id: '', adsorbate_sample_id: '', targets: [] } : { ...group, element_references: {}, targets: [] }) };
    return { mode: 'toolbox', collection: structuredClone(record), removal: preview };
  });
  mount();
  fireEvent.click(await screen.findByRole('button', { name: '清空当前分析样本' }));
  expect(within(await screen.findByRole('dialog')).getByText('确认清空当前分析样本')).toBeInTheDocument();
  expect(screen.queryByText(/个保留目标将缺少参考/)).not.toBeInTheDocument();
  expect(energyApi.removeSamples).not.toHaveBeenCalled();
  expect(energyApi.previewRemoval).toHaveBeenCalledWith(expect.anything(), { clear_all: true });
  fireEvent.click(screen.getByRole('button', { name: '确认清空样本' }));
  expect(await screen.findByText('导入来源后，在同一张表核对组成、能量、角色及状态。')).toBeInTheDocument();
  expect(document.querySelectorAll('tr[data-sample-id]')).toHaveLength(0);
  expect(screen.getByLabelText('选择能量 OUTCAR')).toBeEnabled();
});

it('offers submitted running job snapshots and requires the exact current attempt', async () => {
  vi.spyOn(toolboxApi, 'listProjects').mockResolvedValue({ mode: 'toolbox', projects: [{ id: 'p_synthetic', name: '合成项目' }] });
  vi.spyOn(toolboxApi, 'listTasks').mockResolvedValue({ mode: 'toolbox', tasks: [{ id: 't_synthetic', project_id: 'p_synthetic', title: '合成任务', goal: '', local_workspace: null, hpc_workspace: null, status: 'running', updated_at: '' }] });
  vi.spyOn(toolboxApi, 'getTaskDetail').mockResolvedValue({ mode: 'toolbox', flow: { jobs: [{ key: 'relax', label: '合成弛豫', kind: 'relax', requires: [], status: 'running', submission_state: 'submitted', attempt_id: 'a_current', slurm_id: '42' }] } } as unknown as ToolboxTaskDetail);
  vi.spyOn(energyApi, 'previewTask').mockResolvedValue({ mode: 'toolbox', preview: { id: 'preview_synthetic', source: { kind: 'task_result', slurm_id: '42' }, files: [{ name: 'OUTCAR', available: true, size_bytes: 40 }], expires_at: '2026-10-11T01:00:00Z', warnings: ['运行中快照，状态仍需确认'] } });
  mount(); await screen.findByRole('textbox', { name: '当前能量比较名称' });
  fireEvent.click(screen.getByRole('button', { name: '已有任务／缓存' }));
  fireEvent.mouseDown(screen.getByRole('combobox', { name: '能量来源项目' })); fireEvent.click(await screen.findByText('合成项目', { selector: '.ant-select-item-option-content' }));
  fireEvent.mouseDown(screen.getByRole('combobox', { name: '能量来源任务' })); fireEvent.click(await screen.findByText('合成任务', { selector: '.ant-select-item-option-content' }));
  fireEvent.mouseDown(screen.getByRole('combobox', { name: '能量来源作业' })); fireEvent.click(await screen.findByText('合成弛豫 · 运行中', { selector: '.ant-select-item-option-content' }));
  expect(screen.getByRole('button', { name: '预览能量 OUTCAR 快照' })).toBeDisabled();
  fireEvent.mouseDown(screen.getByRole('combobox', { name: '能量来源执行尝试' })); fireEvent.click(await screen.findByText('a_current · 作业号 42', { selector: '.ant-select-item-option-content' }));
  fireEvent.click(screen.getByRole('button', { name: '预览能量 OUTCAR 快照' }));
  await waitFor(() => expect(energyApi.previewTask).toHaveBeenCalledWith({ project_id: 'p_synthetic', task_id: 't_synthetic', job_key: 'relax', attempt_id: 'a_current' }, expect.any(AbortSignal)));
  expect(await screen.findByRole('button', { name: '确认取回能量快照' })).toBeDisabled();
});

it('explicitly saves before CSV preview, invalidates edited drafts and assigns the imported unknown batch once', async () => {
  const fixture = energyFixture(false);
  record = { ...fixture, analysis_kind: 'adsorption', legacy_mode: false, samples: [], groups: [{ ...fixture.groups[0], clean_sample_id: '', adsorbate_sample_id: '', targets: [] } as EnergyCollection['groups'][number]] };
  vi.spyOn(energyApi, 'previewCsv').mockResolvedValue({ mode: 'toolbox', preview: energyCsvFixture() });
  vi.spyOn(energyApi, 'importCsv').mockImplementation(async base => {
    record = { ...base, revision: base.revision + 1, result: null, locked: false, samples: fixture.samples.map(sample => ({ ...sample, source: { kind: 'csv', relative_path: '合成 中文/位点 A' }, parsed: { ...sample.parsed, provenance: { kind: 'csv' } }, role: null, included: false, confirmed: false, accepted_warnings: false, confirmation_fingerprint: null })) };
    return { mode: 'toolbox', collection: structuredClone(record) };
  });
  vi.mocked(energyApi.autofill).mockImplementation(async base => {
    expect(base.samples.every(sample => sample.source.kind === 'csv' && !sample.confirmed && !sample.accepted_warnings && sample.parsed.status.completion === 'unknown')).toBe(true);
    record = { ...base, revision: base.revision + 1, samples: base.samples.map((sample, index) => ({ ...sample, role: fixture.samples[index].role, role_origin: 'auto', included: true, included_origin: 'auto' })), groups: fixture.groups, assignment_report: { state: 'ready', issues: [] } };
    return { mode: 'toolbox', collection: structuredClone(record) };
  });
  mount(); await screen.findByRole('textbox', { name: '当前能量比较名称' });
  fireEvent.click(screen.getByRole('button', { name: 'CSV' }));
  const file = new File(['合成 CSV 软件测试'], '中文样本.csv');
  fireEvent.change(screen.getByLabelText('选择能量 CSV'), { target: { files: [file] } });
  fireEvent.change(screen.getByRole('textbox', { name: '当前能量比较名称' }), { target: { value: '预览前明确保存' } });
  fireEvent.click(screen.getByRole('button', { name: '保存草稿并预览 CSV' }));
  await waitFor(() => expect(screen.getByRole('button', { name: '确认导入预览中的 CSV' })).toBeEnabled());
  expect(energyApi.previewCsv).toHaveBeenLastCalledWith(expect.objectContaining({ revision: 5, title: '预览前明确保存' }), file, undefined);
  expect(energyApi.importCsv).not.toHaveBeenCalled();
  fireEvent.change(screen.getByRole('textbox', { name: '当前能量比较名称' }), { target: { value: '预览后再次编辑' } });
  expect(screen.getByRole('button', { name: '确认导入预览中的 CSV' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: '保存草稿并预览 CSV' }));
  await waitFor(() => expect(screen.getByRole('button', { name: '确认导入预览中的 CSV' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: '确认导入预览中的 CSV' }));
  await waitFor(() => expect(energyApi.autofill).toHaveBeenCalledOnce());
  expect(energyApi.importCsv).toHaveBeenCalledWith(expect.objectContaining({ revision: 6, title: '预览后再次编辑' }), file, undefined);
  expect(await screen.findByRole('checkbox', { name: '人工确认 es_target' })).not.toBeChecked();
  expect(screen.getByRole('checkbox', { name: '接受风险 es_target' })).not.toBeChecked();
  expect(screen.getByRole('button', { name: '确认并锁定当前分析' })).toBeDisabled();
  expect(energyApi.lock).not.toHaveBeenCalled();
  expect(screen.queryByRole('region', { name: 'CSV 导入预览' })).not.toBeInTheDocument();
});

it('retains dirty input on CSV preview revision conflict without importing or bypassing the conflict', async () => {
  vi.mocked(energyApi.save).mockRejectedValueOnce(new ApiError('ENERGY_REVISION_CONFLICT', 'CSV 预览保存修订冲突', false, 409));
  vi.spyOn(energyApi, 'previewCsv'); vi.spyOn(energyApi, 'importCsv');
  mount(); fireEvent.change(await screen.findByRole('textbox', { name: '当前能量比较名称' }), { target: { value: '冲突保留名称' } });
  fireEvent.click(screen.getByRole('button', { name: 'CSV' }));
  fireEvent.change(screen.getByLabelText('选择能量 CSV'), { target: { files: [new File(['test'], '冲突.csv')] } });
  fireEvent.click(screen.getByRole('button', { name: '保存草稿并预览 CSV' }));
  await screen.findByText(/CSV 预览保存修订冲突 本地输入已保留/);
  expect(screen.getByRole('textbox', { name: '当前能量比较名称' })).toHaveValue('冲突保留名称');
  expect(screen.getByText('冲突.csv')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '确认导入预览中的 CSV' })).toBeDisabled();
  expect(energyApi.previewCsv).not.toHaveBeenCalled(); expect(energyApi.importCsv).not.toHaveBeenCalled();
});

it('exports explicitly saved effective or original sample values without calculation and reports a missing chosen field', async () => {
  record = { ...energyFixture(false), analysis_kind: 'adsorption', legacy_mode: false };
  record.samples[2].override = { energy_fields: { sigma_to_zero_ev: -113 }, energy_basis: 'sigma_to_zero_ev', unit: 'eV', note: '软件测试合成修订值' };
  const effective = new Blob(['energy_ev,original_energy_ev\n-113,-112']); const original = new Blob(['energy_ev,original_energy_ev\n-112,-112']);
  const download = vi.spyOn(energyApi, 'downloadSamples').mockResolvedValueOnce(effective).mockResolvedValueOnce(original).mockRejectedValueOnce(new ApiError('ENERGY_FIELD_REQUIRED', '第4行（合成构型 A）缺少所选without_entropy_ev的original有限能量字段，不会回退其他字段', false, 422));
  const saveBlob = vi.spyOn(energyDraftHelpers, 'saveEnergyBlob').mockImplementation(() => {});
  vi.spyOn(energyApi, 'calculate');
  mount(); fireEvent.change(await screen.findByRole('textbox', { name: '当前能量比较名称' }), { target: { value: '导出前明确保存' } });
  expect(screen.getByText(/有效值使用当前人工修订后的组成和所选能量/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '保存草稿并导出可导入样本表' }));
  await waitFor(() => expect(saveBlob).toHaveBeenCalledWith(effective, 'ec_fixture.samples.effective.csv'));
  expect(download).toHaveBeenNthCalledWith(1, expect.objectContaining({ revision: 5, title: '导出前明确保存', result: null }), 'sigma_to_zero_ev', 'effective');
  expect(vi.mocked(energyApi.save).mock.calls[0][1].samples.find(sample => sample.sample_id === 'es_target')?.override?.energy_fields?.sigma_to_zero_ev).toBe(-113);
  fireEvent.mouseDown(screen.getByRole('combobox', { name: '样本表取值' }));
  fireEvent.click(await screen.findByText('原始值（忽略人工修订）', { selector: '.ant-select-item-option-content' }));
  fireEvent.click(screen.getByRole('button', { name: '导出可导入样本表' }));
  await waitFor(() => expect(saveBlob).toHaveBeenCalledWith(original, 'ec_fixture.samples.original.csv'));
  expect(download).toHaveBeenNthCalledWith(2, expect.objectContaining({ revision: 5, result: null }), 'sigma_to_zero_ev', 'original');
  fireEvent.mouseDown(screen.getByRole('combobox', { name: '样本表能量字段' }));
  fireEvent.click(await screen.findByText(energyDraftHelpers.basisLabel('without_entropy_ev'), { selector: '.ant-select-item-option-content' }));
  fireEvent.click(screen.getByRole('button', { name: '导出可导入样本表' }));
  await waitFor(() => expect(download).toHaveBeenNthCalledWith(3, expect.anything(), 'without_entropy_ev', 'original'));
  expect(await screen.findAllByText(/第4行（合成构型 A）缺少所选without_entropy_ev的original有限能量字段/)).toHaveLength(2);
  expect(saveBlob).toHaveBeenCalledTimes(2); expect(energyApi.calculate).not.toHaveBeenCalled();
  expect(record.samples[2].parsed.energy_fields.sigma_to_zero_ev).toBe(-112);
});
