import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { ConfigProvider, theme } from 'antd';
import { vi } from 'vitest';
import { ppApi, type PPDataset, type PPCurves, type PPView, type PPTaskSource } from '../api/postprocessing';
import { toolboxApi } from '../api/client';
import type { ToolboxTaskDetail } from '../types/toolbox';
import { plotPreferencesApi, defaultPlotPreferences, PLOT_PALETTES } from '../api/plotPreferences';
import PostprocessingPage from './PostprocessingPage';
import { clearAnalysisViewSessions, NUMERIC_SAVE_DELAY_MS } from '../components/postprocessing/analysisViewStore';
import { hasScientificContent, workspaceLocation } from '../components/workflow/scientificNavigation';

const chartLifecycle = vi.hoisted(() => ({ mounts: vi.fn(), unmounts: vi.fn(), options: vi.fn() }));
vi.mock('echarts-for-react', async () => {
  const { useEffect } = await import('react');
  function MockECharts({ option, notMerge }: { option: unknown; notMerge: boolean }) {
    useEffect(() => { chartLifecycle.mounts(); return () => chartLifecycle.unmounts(); }, []);
    chartLifecycle.options(option, notMerge);
    return <pre data-testid="chart">{JSON.stringify(option)}</pre>;
  }
  return { default: MockECharts };
});
const axes = { version: 'pp.axes.v1' as const, x_min: -5, x_max: 3, x_interval: 1, y_min: 0, y_max: 5, y_interval: 1 };
const view: PPView = { version: 'pp.view.v2', reference: 'fermi', reference_ev: 0, mirror_down: false, atoms: [], elements: [], orbitals: ['s', 'px'], projection_grouping: 'element', energy_min_ev: -5, energy_max_ev: 3, band_start: 1, band_end: 20, axes };
let record: PPDataset;
let records: Record<string, PPDataset>;
let clients: QueryClient[] = [];
function curvesFor(saved: PPDataset): PPCurves {
  return { id: saved.id, revision: saved.revision, kind: saved.kind, reference_ev: .5, effective_reference_ev: .5, energy_bounds_ev: { min_ev: -10, max_ev: 10 }, view: saved.view, ticks: [], curves: [{ name: '总 DOS · down', channel: 'down', x: [-10, 0, 10], y: [1, 3, 2] }] };
}
function deferred<T>() { let resolve!: (value: T) => void; let reject!: (reason: unknown) => void; const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
const taskSource: PPTaskSource = { kind: 'task_result', project_id: 'project-test', task_id: 'task-test', job_key: 'dos', attempt_id: 'attempt-test', submission_action_id: 'submit-test', slurm_id: '42', remote_directory: '/synthetic/task/dos', scheduler_target: 'fixture-only', submission_binding_sha256: 'synthetic-binding', cached_at: '2026-10-10T00:00:00Z' };
const taskDownload = { status: 'cached' as const, completed_bytes: 100, total_bytes: 100, current_file: null, completed_files: 1, total_files: 1, error: null };
function mount(path = '/toolbox/postprocessing?analysis=ds-test', dark = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } }); clients.push(client);
  return render(<ConfigProvider theme={{ algorithm: dark ? theme.darkAlgorithm : theme.defaultAlgorithm, token: { motion: false } }}><QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}><PostprocessingPage /></MemoryRouter></QueryClientProvider></ConfigProvider>);
}
beforeEach(() => {
  chartLifecycle.mounts.mockClear(); chartLifecycle.unmounts.mockClear(); chartLifecycle.options.mockClear();
  vi.spyOn(plotPreferencesApi, 'get').mockResolvedValue({ preferences: defaultPlotPreferences(), presets: PLOT_PALETTES });
  vi.spyOn(plotPreferencesApi, 'save').mockResolvedValue({ preferences: defaultPlotPreferences() });
  record = { id: 'ds-test', title: '合成中文分析', kind: 'dos', status: 'ready', revision: 4, files: [{ name: 'vasprun.xml', size_bytes: 100, sha256: 'test-only' }], error: null, view,
    summary: { spin_mode: 'collinear', efermi_ev: .5, convergence: 'unknown', warnings: ['合成验收'], atoms: [[1, 'Fe'], [2, 'O']], orbitals: ['s', 'px'], band_count: 0 } };
  records = { [record.id]: record };
  vi.spyOn(ppApi, 'list').mockImplementation(async () => ({ datasets: Object.values(records) }));
  vi.spyOn(ppApi, 'get').mockImplementation(async id => ({ dataset: records[id] }));
  vi.spyOn(ppApi, 'curves').mockImplementation(async id => curvesFor(records[id]));
  vi.spyOn(ppApi, 'save').mockImplementation(async (base, saved) => {
    const next = { ...records[base.id], revision: base.revision + 1, view: { ...saved, axes: { ...axes, x_min: saved.energy_min_ev!, x_max: saved.energy_max_ev! } } };
    records[base.id] = next;
    if (base.id === record.id) record = next;
    return { dataset: next };
  });
});
afterEach(() => { cleanup(); clearAnalysisViewSessions(); clients.forEach(client => client.clear()); clients = []; vi.restoreAllMocks(); });

it('automatically saves selections, restores successful settings and always draws a white plot', async () => {
  mount(undefined, true);
  expect(await screen.findByText('合成中文分析', { selector: '.ant-card-head-title' })).toBeInTheDocument();
  expect(await screen.findByTestId('chart')).toHaveTextContent('"backgroundColor":"#FFFFFF"');
  expect(screen.getByTestId('chart')).toHaveTextContent('[0,3]');
  expect(screen.queryByRole('button', { name: '保存并更新图形' })).not.toBeInTheDocument();
  expect(screen.queryByLabelText('X轴刻度间隔')).not.toBeInTheDocument();
  expect(screen.queryByLabelText('Y轴起点')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('checkbox', { name: '向下自旋镜像显示' }));
  await waitFor(() => expect(screen.getByTestId('chart')).toHaveTextContent('[0,-3]'));
  expect(ppApi.save).toHaveBeenCalledWith(expect.objectContaining({ revision: 4 }), expect.objectContaining({ mirror_down: true }), expect.any(AbortSignal));
  cleanup(); mount();
  expect(await screen.findByRole('checkbox', { name: '向下自旋镜像显示' })).toBeChecked();
});

it('keeps the same mounted chart while saved curves are pending and updates it in place once ready', async () => {
  mount();
  const chart = await screen.findByTestId('chart');
  const pending = deferred<PPCurves>();
  const pendingSave = deferred<{ dataset: PPDataset }>();
  vi.mocked(ppApi.save).mockImplementationOnce(() => pendingSave.promise);
  vi.mocked(ppApi.curves).mockImplementationOnce(() => pending.promise);
  fireEvent.click(screen.getByRole('checkbox', { name: '向下自旋镜像显示' }));
  await waitFor(() => expect(ppApi.save).toHaveBeenCalledOnce());
  expect(screen.getByTestId('chart')).toBe(chart);
  for (const format of ['CSV', 'JSON', 'PNG']) expect(screen.getByRole('button', { name: `导出 ${format}` })).toBeDisabled();
  const saved = { ...record, revision: 5, view: { ...view, mirror_down: true } };
  record = saved; records[saved.id] = saved;
  await act(async () => pendingSave.resolve({ dataset: saved }));
  await waitFor(() => expect(ppApi.curves).toHaveBeenCalledTimes(2));
  expect(screen.getByTestId('chart')).toBe(chart);
  expect(chart).toHaveTextContent('[0,3]');
  expect(chartLifecycle.mounts).toHaveBeenCalledOnce();
  expect(chartLifecycle.unmounts).not.toHaveBeenCalled();
  expect(screen.getByText('正在更新，当前显示上次已应用配置。')).toBeInTheDocument();
  for (const format of ['CSV', 'JSON', 'PNG']) expect(screen.getByRole('button', { name: `导出 ${format}` })).toBeDisabled();
  await act(async () => pending.resolve(curvesFor(record)));
  await waitFor(() => expect(chart).toHaveTextContent('[0,-3]'));
  expect(screen.getByTestId('chart')).toBe(chart);
  expect(chartLifecycle.mounts).toHaveBeenCalledOnce();
  expect(chartLifecycle.unmounts).not.toHaveBeenCalled();
  for (const format of ['CSV', 'JSON', 'PNG']) expect(screen.getByRole('button', { name: `导出 ${format}` })).toBeEnabled();
});

it('keeps the applied graph after a curve error and retries without disposing it', async () => {
  mount();
  const chart = await screen.findByTestId('chart');
  const pending = deferred<PPCurves>();
  vi.mocked(ppApi.curves).mockImplementationOnce(() => pending.promise);
  fireEvent.click(screen.getByRole('checkbox', { name: '向下自旋镜像显示' }));
  await waitFor(() => expect(ppApi.curves).toHaveBeenCalledTimes(2));
  await act(async () => pending.reject(new Error('曲线读取暂不可用')));
  expect(await screen.findByText('曲线读取暂不可用')).toBeInTheDocument();
  expect(screen.getByText('图形更新失败，当前仍显示上次已应用配置。')).toBeInTheDocument();
  expect(screen.getByTestId('chart')).toBe(chart);
  expect(chart).toHaveTextContent('[0,3]');
  for (const format of ['CSV', 'JSON', 'PNG']) expect(screen.getByRole('button', { name: `导出 ${format}` })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: '重试图形' }));
  await waitFor(() => expect(chart).toHaveTextContent('[0,-3]'));
  expect(chartLifecycle.mounts).toHaveBeenCalledOnce();
  expect(chartLifecycle.unmounts).not.toHaveBeenCalled();
});

it('ignores late curves of an older saved revision after the newest revision is drawn', async () => {
  mount();
  const chart = await screen.findByTestId('chart');
  const older = deferred<PPCurves>();
  const newest = deferred<PPCurves>();
  vi.mocked(ppApi.curves).mockImplementationOnce(() => older.promise).mockImplementationOnce(() => newest.promise);
  fireEvent.click(screen.getByRole('checkbox', { name: '向下自旋镜像显示' }));
  await waitFor(() => expect(ppApi.curves).toHaveBeenCalledTimes(2));
  const olderData = curvesFor(record);
  fireEvent.click(screen.getByRole('checkbox', { name: '向下自旋镜像显示' }));
  await waitFor(() => expect(ppApi.curves).toHaveBeenCalledTimes(3));
  await act(async () => newest.resolve(curvesFor(record)));
  await waitFor(() => expect(chart.closest('.pp-scientific-plot')).toHaveAttribute('data-applied-revision', '6'));
  await act(async () => older.resolve(olderData));
  expect(screen.getByTestId('chart')).toBe(chart);
  expect(chart).toHaveTextContent('[0,3]');
  expect(chart).not.toHaveTextContent('[0,-3]');
  expect(chart.closest('.pp-scientific-plot')).toHaveAttribute('data-applied-revision', '6');
  expect(chartLifecycle.mounts).toHaveBeenCalledOnce();
  expect(chartLifecycle.unmounts).not.toHaveBeenCalled();
});

it('does not display the previous dataset graph while the next dataset is still loading', async () => {
  records['ds-b'] = { ...record, id: 'ds-b', title: '分析 B' };
  mount();
  const chart = await screen.findByTestId('chart');
  const pending = deferred<PPCurves>();
  vi.mocked(ppApi.curves).mockImplementationOnce(() => pending.promise);
  fireEvent.click(screen.getByRole('button', { name: /分析 B DOS/ }));
  await waitFor(() => expect(screen.getByText('分析 B', { selector: '.ant-card-head-title' })).toBeInTheDocument());
  await waitFor(() => expect(ppApi.curves).toHaveBeenCalledWith('ds-b', expect.any(AbortSignal)));
  expect(screen.queryByTestId('chart')).not.toBeInTheDocument();
  expect(document.querySelector('[data-analysis-id="ds-test"]')).toBeNull();
  const bCurves = curvesFor(records['ds-b']);
  bCurves.curves[0].y = [10, 30, 20];
  await act(async () => pending.resolve(bCurves));
  const nextChart = await screen.findByTestId('chart');
  expect(nextChart).not.toBe(chart);
  expect(nextChart).toHaveTextContent('[0,30]');
  expect(nextChart.closest('.pp-scientific-plot')).toHaveAttribute('data-analysis-id', 'ds-b');
  expect(chartLifecycle.mounts).toHaveBeenCalledTimes(2);
  expect(chartLifecycle.unmounts).toHaveBeenCalledOnce();
});

it('removes deselected projection series through the existing chart update instead of remounting', async () => {
  vi.mocked(ppApi.curves).mockImplementation(async id => {
    const saved = records[id];
    const data = curvesFor(saved);
    data.curves.push(...(saved.view.elements ?? []).map((element, index) => ({ id: `dos.projection.${element}.up`, name: `${element} 投影之和 · up`, channel: 'up', element, element_index: index, x: [-10, 0, 10], y: [1, 2, 1] })));
    return data;
  });
  mount();
  const chart = await screen.findByTestId('chart');
  fireEvent.click(screen.getByRole('checkbox', { name: 'Fe 全部原子' }));
  await waitFor(() => expect(chart).toHaveTextContent('dos.projection.Fe.up'));
  fireEvent.click(screen.getByRole('checkbox', { name: 'O 全部原子' }));
  await waitFor(() => expect(chart).toHaveTextContent('dos.projection.O.up'));
  fireEvent.click(screen.getByRole('checkbox', { name: 'Fe 全部原子' }));
  await waitFor(() => expect(chart).not.toHaveTextContent('dos.projection.Fe.up'));
  expect(chart).toHaveTextContent('dos.projection.O.up');
  fireEvent.click(screen.getByRole('checkbox', { name: 'O 全部原子' }));
  await waitFor(() => expect(chart).not.toHaveTextContent('dos.projection.O.up'));
  expect(screen.getByTestId('chart')).toBe(chart);
  expect(chartLifecycle.options.mock.calls.every(([, notMerge]) => notMerge)).toBe(true);
  expect(chartLifecycle.mounts).toHaveBeenCalledOnce();
  expect(chartLifecycle.unmounts).not.toHaveBeenCalled();
});

it('does not request a save for cleared or invalid numeric input, then saves the completed range after debounce', async () => {
  mount();
  await screen.findByTestId('chart');
  fireEvent.change(screen.getByLabelText('能量起点'), { target: { value: '' } });
  await act(() => new Promise(resolve => setTimeout(resolve, NUMERIC_SAVE_DELAY_MS + 30)));
  expect(screen.getByLabelText('能量起点')).toHaveValue('');
  expect(ppApi.save).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText('能量起点'), { target: { value: 'nope' } });
  await act(() => new Promise(resolve => setTimeout(resolve, NUMERIC_SAVE_DELAY_MS + 30)));
  expect(screen.getByLabelText('能量起点')).toHaveValue('nope');
  expect(ppApi.save).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText('能量起点'), { target: { value: '-4' } });
  fireEvent.change(screen.getByLabelText('能量终点'), { target: { value: '2' } });
  await waitFor(() => expect(ppApi.save).toHaveBeenCalledOnce());
  expect(record.view).toMatchObject({ energy_min_ev: -4, energy_max_ev: 2 });
});

it('keeps an owned pending save across analysis navigation without writing its response to the other analysis', async () => {
  records['ds-b'] = { ...record, id: 'ds-b', title: '分析 B' };
  let resolveSave!: (value: { dataset: PPDataset }) => void;
  vi.mocked(ppApi.save).mockImplementationOnce(() => new Promise(resolve => { resolveSave = resolve; }));
  mount();
  fireEvent.click(await screen.findByRole('checkbox', { name: '向下自旋镜像显示' }));
  await waitFor(() => expect(ppApi.save).toHaveBeenCalledOnce());
  fireEvent.click(screen.getByRole('button', { name: /分析 B DOS/ }));
  await waitFor(() => expect(screen.getByText('分析 B', { selector: '.ant-card-head-title' })).toBeInTheDocument());
  expect(screen.getByRole('checkbox', { name: '向下自旋镜像显示' })).not.toBeChecked();
  const saved = { ...record, revision: 5, view: { ...view, mirror_down: true } };
  records[record.id] = saved;
  await act(async () => resolveSave({ dataset: saved }));
  expect(screen.getByRole('checkbox', { name: '向下自旋镜像显示' })).not.toBeChecked();
  expect(screen.getByTestId('chart')).toHaveTextContent('[0,3]');
  fireEvent.click(screen.getByRole('button', { name: /合成中文分析 DOS/ }));
  await waitFor(() => expect(screen.getByRole('checkbox', { name: '向下自旋镜像显示' })).toBeChecked());
  await waitFor(() => expect(screen.getByTestId('chart')).toHaveTextContent('[0,-3]'));
});

it('uses the complete source energy bounds without changing source arrays', async () => {
  mount();
  await waitFor(() => expect(screen.getByRole('button', { name: '完整能量范围' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: '完整能量范围' }));
  await waitFor(() => expect(record.view).toMatchObject({ energy_min_ev: -10, energy_max_ev: 10 }));
  expect(screen.getByTestId('chart')).toHaveTextContent('[-10,1]');
  expect(screen.getByTestId('chart')).toHaveTextContent('[10,2]');
});

it('keeps failed automatic-save drafts visible and offers retry', async () => {
  vi.mocked(ppApi.save).mockRejectedValueOnce(new Error('网络暂不可用'));
  mount();
  fireEvent.click(await screen.findByRole('checkbox', { name: '向下自旋镜像显示' }));
  expect(await screen.findByText('自动保存失败，当前选择已保留')).toBeInTheDocument();
  expect(screen.getByRole('checkbox', { name: '向下自旋镜像显示' })).toBeChecked();
  for (const format of ['CSV', 'JSON', 'PNG']) expect(screen.getByRole('button', { name: `导出 ${format}` })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: '重试自动保存' }));
  await waitFor(() => expect(screen.getByTestId('chart')).toHaveTextContent('[0,-3]'));
});

it('rejects unsupported files before creating a dataset', async () => {
  const create = vi.spyOn(ppApi, 'create');
  mount('/toolbox/postprocessing');
  fireEvent.change(screen.getByLabelText('选择本地文件'), { target: { files: [new File(['synthetic'], 'POTCAR')] } });
  fireEvent.click(screen.getByRole('button', { name: '导入并解析' }));
  expect(await screen.findByText(/请选择不重名的原始文件/)).toBeInTheDocument();
  expect(create).not.toHaveBeenCalled();
});

it('uploads chosen files and uses the returned dataset id', async () => {
  const file = new File(['synthetic'], 'vasprun.xml');
  vi.spyOn(ppApi, 'create').mockResolvedValue({ dataset: { ...record, status: 'draft' } });
  const upload = vi.spyOn(ppApi, 'upload').mockResolvedValue({ dataset: { ...record, revision: 5, status: 'draft' } });
  const start = vi.spyOn(ppApi, 'start').mockResolvedValue({ dataset: { ...record, revision: 6, status: 'failed', error: { code: 'PP_TEST', message: '合成文件无法解析' } } });
  mount('/toolbox/postprocessing');
  fireEvent.change(screen.getByLabelText('选择本地文件'), { target: { files: [file] } });
  fireEvent.click(screen.getByRole('button', { name: '导入并解析' }));
  await waitFor(() => expect(start).toHaveBeenCalledWith('ds-test'));
  expect(upload).toHaveBeenCalledWith('ds-test', file, expect.any(AbortSignal));
  expect(await screen.findByText('合成文件无法解析')).toBeInTheDocument();
});

it('reports export failures without losing the plot', async () => {
  vi.spyOn(ppApi, 'download').mockRejectedValue(new Error('下载暂不可用'));
  mount();
  fireEvent.click(await screen.findByRole('button', { name: '导出 CSV' }));
  expect(await screen.findByText('下载暂不可用')).toBeInTheDocument();
  expect(screen.getByTestId('chart')).toBeInTheDocument();
});

it('uses the scientific shell only for the real post-processing route', () => {
  expect(hasScientificContent('/toolbox/postprocessing')).toBe(true);
  expect(hasScientificContent('/toolbox/postprocessing-extra')).toBe(false);
  expect(workspaceLocation('/toolbox/postprocessing').title).toBe('结果后处理');
});

it('polls download separately, then offers explicit parsing of the completed cache without starting automatically', async () => {
  record = { ...record, source: taskSource, status: 'downloading', download: { ...taskDownload, status: 'downloading', completed_bytes: 50, completed_files: 0, current_file: 'vasprun.xml' } };
  records[record.id] = record;
  const complete = { ...record, revision: 5, status: 'draft' as const, download: taskDownload };
  vi.mocked(ppApi.get).mockResolvedValueOnce({ dataset: record }).mockImplementation(async () => ({ dataset: complete }));
  const start = vi.spyOn(ppApi, 'start').mockResolvedValue({ dataset: { ...complete, revision: 6, status: 'processing' } });
  mount();
  expect(await screen.findByRole('button', { name: '取消取回' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '取消解析' })).not.toBeInTheDocument();
  expect(start).not.toHaveBeenCalled();
  const parse = await screen.findByRole('button', { name: '解析缓存文件并查看图形' }, { timeout: 3000 });
  expect(start).not.toHaveBeenCalled();
  expect(screen.queryByRole('button', { name: '取消取回' })).not.toBeInTheDocument();
  expect(screen.getByText('/synthetic/task/dos')).toBeInTheDocument();
  fireEvent.click(parse);
  await waitFor(() => expect(start).toHaveBeenCalledWith('ds-test'));
});

it('cancels and retries an incomplete download without offering scientific parsing', async () => {
  record = { ...record, source: taskSource, status: 'downloading', download: { ...taskDownload, status: 'downloading', completed_bytes: 50, completed_files: 0 } };
  records[record.id] = record;
  const cancelled: PPDataset = { ...record, revision: 5, status: 'cancelled', download: { ...record.download!, status: 'cancelled' } };
  const cancel = vi.spyOn(ppApi, 'cancel').mockResolvedValue({ dataset: cancelled });
  const retry = vi.spyOn(ppApi, 'retryDownload').mockResolvedValue({ dataset: { ...record, revision: 6 } });
  const start = vi.spyOn(ppApi, 'start');
  mount();
  fireEvent.click(await screen.findByRole('button', { name: '取消取回' }));
  await waitFor(() => expect(cancel).toHaveBeenCalledWith('ds-test'));
  fireEvent.click(await screen.findByRole('button', { name: '重试下载已确认文件' }));
  await waitFor(() => expect(retry).toHaveBeenCalledWith('ds-test'));
  expect(screen.queryByRole('button', { name: '解析已保存文件' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '重试解析缓存文件' })).not.toBeInTheDocument();
  expect(start).not.toHaveBeenCalled();
});

it('keeps polling a retried download after an older failed read returns late', async () => {
  record = { ...record, source: taskSource, status: 'failed', download: { ...taskDownload, status: 'failed', completed_bytes: 0, completed_files: 0 } };
  records[record.id] = record;
  const pending = deferred<{ dataset: PPDataset }>();
  const retried: PPDataset = { ...record, revision: 5, status: 'downloading', download: { ...record.download!, status: 'downloading' } };
  const complete: PPDataset = { ...retried, revision: 6, status: 'draft', download: taskDownload };
  vi.mocked(ppApi.get).mockResolvedValueOnce({ dataset: record }).mockImplementationOnce(() => pending.promise).mockImplementation(async () => ({ dataset: complete }));
  vi.spyOn(ppApi, 'retryDownload').mockResolvedValue({ dataset: retried });
  mount();
  const retry = await screen.findByRole('button', { name: '重试下载已确认文件' });
  await act(async () => { void clients.at(-1)!.invalidateQueries({ queryKey: ['pp-dataset', record.id] }); });
  await waitFor(() => expect(ppApi.get).toHaveBeenCalledTimes(2));
  fireEvent.click(retry);
  await screen.findByRole('button', { name: '取消取回' });
  await act(async () => pending.resolve({ dataset: record }));
  expect(screen.getByRole('button', { name: '取消取回' })).toBeInTheDocument();
  await screen.findByRole('button', { name: '解析缓存文件并查看图形' }, { timeout: 3000 });
  expect(ppApi.get).toHaveBeenCalledTimes(3);
});

it('prepares an explicit new remote snapshot while retaining the existing dataset and mounted plot', async () => {
  record = { ...record, source: taskSource, download: taskDownload }; records[record.id] = record;
  vi.spyOn(toolboxApi, 'listProjects').mockResolvedValue({ mode: 'toolbox', projects: [{ id: 'project-test', name: '合成项目' }] });
  vi.spyOn(toolboxApi, 'listTasks').mockResolvedValue({ mode: 'toolbox', tasks: [{ id: 'task-test', project_id: 'project-test', title: '合成任务', goal: '', local_workspace: null, hpc_workspace: null, status: 'completed', updated_at: '' }] });
  const detail: ToolboxTaskDetail = { mode: 'toolbox', task_id: 'task-test', task: { id: 'task-test', project_id: 'project-test', title: '合成任务', goal: '', local_workspace: null, hpc_workspace: null, status: 'completed', updated_at: '' }, flow: { execution_mode: 'Fake', phase: 'completed', goal: '', strategy: '', local_dir: '', hpc_dir: '', waiting: [], precheck: { ok: true, issues: [] }, report: '', jobs: [{ key: 'dos', label: 'DOS', kind: 'static', requires: [], status: 'completed', submission_state: 'submitted', slurm_id: '42', attempt_id: 'attempt-test' }], draft: [], artifacts: {} }, consents: [], events: [], monitor: { state: 'idle', interval_seconds: 5, remote_cancelled: false }, backend_mode: 'Fake' };
  vi.spyOn(toolboxApi, 'getTaskDetail').mockResolvedValue(detail);
  const preview = vi.spyOn(ppApi, 'previewTaskSource'); const importing = vi.spyOn(ppApi, 'importTaskSource');
  mount();
  const chart = await screen.findByTestId('chart');
  fireEvent.click(screen.getByRole('button', { name: '刷新远端并新建分析' }));
  await waitFor(() => expect(screen.getByRole('button', { name: '预览远端新快照' })).toBeEnabled());
  expect(screen.getByTestId('chart')).toBe(chart);
  expect(screen.getByLabelText('分析名称')).toHaveValue('合成中文分析（新快照）');
  expect(preview).not.toHaveBeenCalled(); expect(importing).not.toHaveBeenCalled();
  expect(chartLifecycle.unmounts).not.toHaveBeenCalled();
});
