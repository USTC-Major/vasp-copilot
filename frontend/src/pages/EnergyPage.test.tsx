import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ConfigProvider, theme } from 'antd';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import { ApiError, toolboxApi } from '../api/client';
import { energyApi, type EnergyCollection } from '../api/energy';
import type { ToolboxTaskDetail } from '../types/toolbox';
import { energyFixture } from '../components/energy/energyTestFixtures';
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
});
afterEach(() => { cleanup(); clearEnergyDraftSessions(); clients.forEach(client => client.clear()); clients = []; vi.restoreAllMocks(); });

it('hides stale results and clears scientific confirmations when a coefficient changes', async () => {
  mount(true);
  expect(await screen.findByText('-2 eV', { selector: 'strong' })).toBeInTheDocument();
  fireEvent.change(screen.getByRole('spinbutton', { name: '参考单元数 g_ads' }), { target: { value: '2' } });
  expect(screen.queryByText('-2 eV', { selector: 'strong' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '导出能量 CSV' })).toBeDisabled();
  expect(screen.getByRole('checkbox', { name: '人工确认 es_target' })).not.toBeChecked();
  expect(screen.getByRole('checkbox', { name: '接受风险 es_target' })).not.toBeChecked();
  expect(screen.getByRole('checkbox', { name: '已核对本组统一能量口径与参考定义' })).not.toBeChecked();
  fireEvent.click(screen.getByRole('button', { name: '保存草稿' }));
  await waitFor(() => expect(energyApi.save).toHaveBeenCalledOnce());
  expect(vi.mocked(energyApi.save).mock.calls[0][1].samples.every(row => !row.confirmed && !row.accepted_warnings)).toBe(true);
  expect(vi.mocked(energyApi.save).mock.calls[0][1].groups[0].basis_confirmed).toBe(false);
  expect(await screen.findByText('草稿已保存，未执行计算。')).toBeInTheDocument();
});

it('requires explicit acceptance for unknown samples and keeps markers through calculation and reopen', async () => {
  record = energyFixture(false);
  vi.spyOn(energyApi, 'calculate').mockImplementation(async saved => { record = { ...energyFixture(true), revision: saved.revision + 1 }; return { mode: 'toolbox', collection: structuredClone(record) }; });
  mount();
  await screen.findByRole('checkbox', { name: '接受风险 es_target' });
  expect(screen.getByRole('button', { name: '保存并计算能量' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: '确认已纳入样本与组字段' }));
  expect(screen.getByRole('button', { name: '保存并计算能量' })).toBeDisabled();
  expect(screen.getByRole('checkbox', { name: '接受风险 es_target' })).not.toBeChecked();
  fireEvent.click(screen.getByRole('checkbox', { name: '明确接受已纳入样本的未完成／未知状态及警告' }));
  expect(screen.getByRole('button', { name: '保存并计算能量' })).toBeEnabled();
  fireEvent.click(screen.getByRole('button', { name: '保存并计算能量' }));
  await waitFor(() => expect(energyApi.calculate).toHaveBeenCalledWith(expect.objectContaining({ revision: 5 })));
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
  cleanup(); record = { ...record, revision: 5, title: '其他窗口保存的名称' }; mount();
  expect(await screen.findByRole('textbox', { name: '当前能量比较名称' })).toHaveValue('保留的本地名称');
  fireEvent.click(screen.getByRole('button', { name: '读取最新并保留本地输入' }));
  expect(await screen.findByText('已读取最新版本并保留本地输入；确认已清除，请重新核对后保存。')).toBeInTheDocument();
  vi.mocked(energyApi.save).mockImplementation(savedImplementation);
  fireEvent.click(screen.getByRole('button', { name: '保存草稿' }));
  await waitFor(() => expect(vi.mocked(energyApi.save).mock.lastCall?.[1]).toMatchObject({ expected_revision: 5, title: '保留的本地名称' }));
});

it('imports same-name files as independent samples and preserves editable Chinese relative metadata', async () => {
  record = { ...energyFixture(), samples: [], groups: [] };
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
