import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { vi } from 'vitest';
import { toolboxApi } from '../../api/client';
import { ppApi, type PPDataset, type PPTaskPreview, type PPTaskSource } from '../../api/postprocessing';
import type { ToolboxJob, ToolboxTaskDetail } from '../../types/toolbox';
import TaskSourceImport from './TaskSourceImport';
import { taskSelectionError } from './taskSourceSelection';

const source: PPTaskSource = { kind: 'task_result', project_id: 'p', task_id: 't', job_key: 'dos', attempt_id: 'a1', submission_action_id: 'submit-1', slurm_id: '42', remote_directory: '/scratch/t/dos', scheduler_target: 'test-only', submission_binding_sha256: 'synthetic-binding' };
const preview: PPTaskPreview = { id: 'preview-1', kind: 'dos', source, files: [{ name: 'vasprun.xml', size_bytes: 100, available: true }, { name: 'INCAR', size_bytes: 10, available: true }, { name: 'DOSCAR', size_bytes: null, available: false, reason: '文件缺失' }], suggested_files: ['vasprun.xml', 'INCAR'], warnings: [], expires_at: '2099-01-01T00:00:00Z', limits: { max_file_bytes: 64 * 1024 ** 2, max_total_bytes: 128 * 1024 ** 2 } };
const dataset: PPDataset = { id: 'cached-1', title: '合成缓存', kind: 'dos', source: { ...source, cached_at: '2026-10-10T00:00:00Z' }, download: { status: 'cached', completed_bytes: 100, total_bytes: 100, completed_files: 1, total_files: 1, current_file: null, error: null }, status: 'ready', revision: 1, files: [{ name: 'vasprun.xml', size_bytes: 100, sha256: 'synthetic-only' }], error: null, summary: null, view: { reference: 'raw', reference_ev: 0, mirror_down: false, atoms: [], orbitals: [], band_start: 1, band_end: 20 } };
function taskDetail(attemptId = 'a1'): ToolboxTaskDetail {
  const job: ToolboxJob = { key: 'dos', label: '静态 DOS', kind: 'static', requires: [], status: 'failed', submission_state: 'submitted', slurm_id: '42', attempt_id: attemptId };
  return { mode: 'toolbox', task_id: 't', task: { id: 't', project_id: 'p', title: '合成任务', goal: '', local_workspace: null, hpc_workspace: null, status: 'completed', updated_at: '' }, flow: { execution_mode: 'Fake', phase: 'completed', goal: '', strategy: '', local_dir: '', hpc_dir: '', waiting: [], precheck: { ok: true, issues: [] }, report: '', jobs: [job], draft: [], artifacts: {} }, consents: [], events: [], monitor: { state: 'idle', interval_seconds: 5, remote_cancelled: false }, backend_mode: 'Fake' };
}
let clients: QueryClient[] = [];
function mount(datasets: PPDataset[] = []) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } }); clients.push(client);
  const onDataset = vi.fn(); const onBusy = vi.fn();
  const initial = { project_id: 'p', task_id: 't', job_key: 'dos', attempt_id: 'a1' };
  const rendered = render(<QueryClientProvider client={client}><TaskSourceImport kind="dos" title="合成分析" initial={initial} refreshRequest={0} datasets={datasets} disabled={false} onBusy={onBusy} onDataset={onDataset} /></QueryClientProvider>);
  return { ...rendered, onDataset, onBusy, client };
}
beforeEach(() => {
  vi.spyOn(toolboxApi, 'listProjects').mockResolvedValue({ mode: 'toolbox', projects: [{ id: 'p', name: '合成项目' }] });
  vi.spyOn(toolboxApi, 'listTasks').mockResolvedValue({ mode: 'toolbox', tasks: [{ id: 't', project_id: 'p', title: '合成任务', goal: '', local_workspace: null, hpc_workspace: null, status: 'completed', updated_at: '' }] });
  vi.spyOn(toolboxApi, 'getTaskDetail').mockResolvedValue(taskDetail());
  vi.spyOn(ppApi, 'previewTaskSource').mockResolvedValue({ preview });
  vi.spyOn(ppApi, 'importTaskSource').mockResolvedValue({ dataset });
});
afterEach(() => { cleanup(); clients.forEach(client => client.clear()); clients = []; vi.restoreAllMocks(); });

it('previews exact submitted terminal identity, requires subset confirmation, and never downloads on preview', async () => {
  const { onDataset } = mount();
  const button = await screen.findByRole('button', { name: '预览结果文件' });
  await waitFor(() => expect(button).toBeEnabled());
  expect(ppApi.previewTaskSource).not.toHaveBeenCalled();
  fireEvent.click(button);
  expect(await screen.findByText('/scratch/t/dos')).toBeInTheDocument();
  expect(ppApi.previewTaskSource).toHaveBeenCalledWith({ project_id: 'p', task_id: 't', job_key: 'dos', attempt_id: 'a1', kind: 'dos' }, expect.any(AbortSignal));
  expect(ppApi.importTaskSource).not.toHaveBeenCalled();
  const confirmDownload = screen.getByRole('button', { name: '确认下载并缓存' });
  expect(confirmDownload).toBeDisabled();
  fireEvent.click(screen.getByRole('checkbox', { name: /INCAR/ }));
  fireEvent.click(screen.getByRole('checkbox', { name: /确认从上述作业/ }));
  fireEvent.click(confirmDownload);
  await waitFor(() => expect(ppApi.importTaskSource).toHaveBeenCalledWith({ preview_id: 'preview-1', files: ['vasprun.xml'], title: '合成分析' }));
  await waitFor(() => expect(onDataset).toHaveBeenCalledWith(dataset));
});

it('reuses a cached dataset without requesting a remote preview or analysis', async () => {
  const start = vi.spyOn(ppApi, 'start');
  const { onDataset } = mount([dataset]);
  fireEvent.click(await screen.findByRole('button', { name: '打开缓存：合成缓存' }));
  await waitFor(() => expect(ppApi.importTaskSource).toHaveBeenCalledWith({ reuse_dataset_id: 'cached-1' }));
  expect(ppApi.previewTaskSource).not.toHaveBeenCalled();
  expect(start).not.toHaveBeenCalled();
  expect(onDataset).toHaveBeenCalledWith(dataset);
});

it('drops a cancelled late preview so it cannot authorize a download', async () => {
  let release!: (value: { preview: PPTaskPreview }) => void;
  vi.mocked(ppApi.previewTaskSource).mockImplementationOnce(() => new Promise(resolve => { release = resolve; }));
  mount();
  await waitFor(() => expect(screen.getByRole('button', { name: '预览结果文件' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: '预览结果文件' }));
  fireEvent.click(await screen.findByRole('button', { name: '取消预览' }));
  await act(async () => release({ preview }));
  expect(screen.queryByRole('button', { name: '确认下载并缓存' })).not.toBeInTheDocument();
  expect(ppApi.importTaskSource).not.toHaveBeenCalled();
});

it('rejects a stale attempt rather than silently replacing it with the current attempt', async () => {
  vi.mocked(toolboxApi.getTaskDetail).mockResolvedValueOnce(taskDetail('a2'));
  mount();
  expect(await screen.findByText(/该执行尝试不再是当前/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '预览结果文件' })).toBeDisabled();
  expect(ppApi.previewTaskSource).not.toHaveBeenCalled();
});

it('keeps the chosen subset and confirmation after an import failure so the user can retry', async () => {
  vi.mocked(ppApi.importTaskSource).mockRejectedValueOnce(new Error('缓存创建暂不可用'));
  mount();
  await waitFor(() => expect(screen.getByRole('button', { name: '预览结果文件' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: '预览结果文件' }));
  fireEvent.click(await screen.findByRole('checkbox', { name: /确认从上述作业/ }));
  fireEvent.click(screen.getByRole('button', { name: '确认下载并缓存' }));
  expect(await screen.findByText('缓存创建暂不可用')).toBeInTheDocument();
  expect(screen.getByRole('checkbox', { name: /确认从上述作业/ })).toBeChecked();
  fireEvent.click(screen.getByRole('button', { name: '确认下载并缓存' }));
  await waitFor(() => expect(ppApi.importTaskSource).toHaveBeenCalledTimes(2));
});

it('blocks missing, oversized, over-budget and incomplete scientific file selections', () => {
  expect(taskSelectionError(preview, ['DOSCAR'])).toMatch(/缺失/);
  expect(taskSelectionError(preview, ['INCAR'])).toMatch(/DOS 需要/);
  expect(taskSelectionError({ ...preview, limits: { ...preview.limits, max_file_bytes: 50 } }, ['vasprun.xml'])).toMatch(/单文件/);
  expect(taskSelectionError({ ...preview, limits: { ...preview.limits, max_total_bytes: 105 } }, ['vasprun.xml', 'INCAR'])).toMatch(/总量/);
  expect(taskSelectionError({ ...preview, kind: 'band' }, ['vasprun.xml'])).toMatch(/能带需要/);
  const variants = { ...preview, files: [...preview.files, ...['EIGENVAL', 'POSCAR', 'CONTCAR'].map(name => ({ name, size_bytes: 20, available: true }))] };
  expect(taskSelectionError(variants, ['vasprun.xml', 'EIGENVAL'])).toMatch(/多个主结果/);
  expect(taskSelectionError(variants, ['vasprun.xml', 'POSCAR', 'CONTCAR'])).toMatch(/歧义/);
  expect(taskSelectionError(preview, ['vasprun.xml'])).toBe('');
});
