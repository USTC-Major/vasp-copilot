import { StrictMode } from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ConfigProvider, theme } from 'antd';
import { MemoryRouter } from 'react-router-dom';
import { catalysisApi } from '../api/catalysis';
import { ApiError } from '../api/client';
import type { CatalysisDraft, SurfaceOption } from '../types/catalysis';
import type { StructureGeometry } from '../types/structure-geometry';
import SurfaceBuilderPage from './SurfaceBuilderPage';
import { defaultParameters, editorFor, parameterFields, parsedParameters, storeRecovery } from './surfaceDraftState';

vi.mock('../components/structure/CrystalViewer', () => ({ CrystalGeometryViewer: ({ data }: { data: StructureGeometry }) => <div>真实几何 {data.structure_id}</div> }));
const deferred = <T,>() => { let resolve!: (v: T) => void; let reject!: (e: unknown) => void; const promise = new Promise<T>((r, j) => { resolve = r; reject = j; }); return { promise, resolve, reject }; };
const snapshot = { snapshot_id: 'snapshot-1', coordinate_mode: 'cartesian' as const, lattice: [[4, 0, 0], [0, 4, 0], [0, 0, 24]] as StructureGeometry['basis_cartesian_angstrom'], sha256: 'a'.repeat(64), atoms: Array.from({ length: 4 }, (_, i) => ({ atom_id: `atom-${i + 1}`, element: 'Pt', fractional: [0, 0, .2 + i * .1] as [number, number, number], cartesian: [0, 0, 4.8 + i * 2.4] as [number, number, number], selective_dynamics: [true, true, true] as [boolean, boolean, boolean], provenance: {} })) };
const option = (id: string): SurfaceOption => ({ surface_id: id, termination_shift: .25, snapshot, transform: {}, surface: { normal: [0, 0, 1], normal_period_angstrom: 24, actual_nuclei_span_angstrom: 7.2, periodic_vacuum_gap_angstrom: 16.8, in_plane_lengths_angstrom: [4, 4], layer_tolerance: .1, bottom_fixed_layers: 0, atom_overrides: {}, reset_existing: false, flag_basis: 'direct_lattice_vectors', layers: [{ layer_index: 0, atom_ids: ['atom-1', 'atom-2'], projection_angstrom: 4.8 }, { layer_index: 1, atom_ids: ['atom-3', 'atom-4'], projection_angstrom: 9.6 }] } });
function fixture(): CatalysisDraft { return { draft_id: 'cat-test', schema_version: 1, revision: 3, name: '合成表面草稿', created_at: '2026-10-10T12:00:00Z', updated_at: '2026-10-10T12:00:00Z', source: { kind: 'example', role: 'bulk' }, input_snapshot: snapshot, parameters: defaultParameters, surfaces: [option('surface-1'), option('surface-2')], active_surface_id: 'surface-1', warnings: [] }; }
function geometry(id: string): StructureGeometry { return { structure_id: id, formula: 'Pt4', atom_count: 4, basis_cartesian_angstrom: snapshot.lattice, lattice: { a: 4, b: 4, c: 24, alpha: 90, beta: 90, gamma: 90, volume: 384, matrix: snapshot.lattice }, coordinate_mode: 'cartesian', selective_dynamics: true, selective_flags_basis: 'direct_lattice_vectors', sites: snapshot.atoms.map((a, i) => ({ id: i + 1, element: a.element, fractional: a.fractional, cartesian_angstrom: a.cartesian, selective_flags: a.selective_dynamics })), source: { format: 'poscar', file_name: 'POSCAR', material_id: null, coordinate_source: 'snapshot', poscar_sha256: snapshot.sha256 }, geometry_sha256: snapshot.sha256 }; }
let doc: CatalysisDraft;
let clients: QueryClient[] = [];
function mount(path = '/toolbox/surface-builder?draft=cat-test', dark = false, strict = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } }); clients.push(client);
  const page = <ConfigProvider theme={{ algorithm: dark ? theme.darkAlgorithm : theme.defaultAlgorithm, token: { motion: false } }}><QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}><SurfaceBuilderPage /></MemoryRouter></QueryClientProvider></ConfigProvider>;
  return render(strict ? <StrictMode>{page}</StrictMode> : page);
}
beforeEach(() => {
  localStorage.clear(); doc = fixture();
  vi.spyOn(catalysisApi, 'list').mockImplementation(async () => ({ drafts: [{ draft_id: doc.draft_id, revision: doc.revision, name: doc.name, updated_at: doc.updated_at, source_role: doc.source.role, atom_count: 4, surface_count: doc.surfaces.length, active_surface_id: doc.active_surface_id }] }));
  vi.spyOn(catalysisApi, 'get').mockImplementation(async () => ({ draft: doc }));
  vi.spyOn(catalysisApi, 'geometry').mockImplementation(async (_id, surfaceId, revision) => ({ revision, surface_id: surfaceId, geometry: geometry(surfaceId) }));
  vi.spyOn(catalysisApi, 'save').mockImplementation(async (_base, changes) => { doc = { ...doc, ...changes, revision: doc.revision + 1, ...(changes.parameters ? { surfaces: [], active_surface_id: null } : {}) }; return { draft: doc }; });
  vi.spyOn(catalysisApi, 'build').mockImplementation(async (_base, parameters) => { doc = { ...doc, parameters, revision: doc.revision + 1 }; return { draft: doc }; });
  vi.spyOn(catalysisApi, 'constraints').mockImplementation(async (_base, settings) => { doc = { ...doc, revision: doc.revision + 1, surfaces: doc.surfaces.map(s => s.surface_id === doc.active_surface_id ? { ...s, surface: { ...s.surface, ...settings } } : s) }; return { draft: doc }; });
  vi.spyOn(catalysisApi, 'create').mockImplementation(async () => ({ draft: doc }));
  vi.spyOn(catalysisApi, 'export').mockResolvedValue(new Blob(['test']));
});
afterEach(() => { cleanup(); clients.forEach(c => c.clear()); clients = []; vi.restoreAllMocks(); });

it('shows actual geometric measures, supplied-cell hkl semantics, and works under StrictMode', async () => {
  mount(undefined, true, true);
  expect(await screen.findByTestId('cat-geometry')).toHaveAttribute('data-revision', '3');
  expect(screen.getByText('7.2000 Å')).toBeInTheDocument();
  expect(screen.getByText('16.8000 Å')).toBeInTheDocument();
  expect(screen.getByText(/hkl 相对于导入的输入晶胞/)).toBeInTheDocument();
  expect(screen.getByText(/混合 T\/F 按直接晶格/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('草稿名称'), { target: { value: '已保存名称' } });
  fireEvent.click(screen.getByRole('button', { name: '保存草稿' }));
  await screen.findByText('草稿已保存。');
  expect(screen.getByLabelText('草稿名称')).toHaveValue('已保存名称');
  await waitFor(() => expect(screen.getByRole('button', { name: '导出 POSCAR + metadata ZIP' })).toBeEnabled());
});

it('preserves invalid and cleared numeric text across a page reopen, and blocks generation/export', async () => {
  mount(); await screen.findByTestId('cat-geometry');
  fireEvent.change(screen.getByLabelText('最小真空 (Å)'), { target: { value: '' } });
  expect(screen.getByRole('button', { name: '重新生成表面' })).toBeDisabled();
  expect(screen.getByRole('button', { name: '导出 POSCAR + metadata ZIP' })).toBeDisabled();
  expect(screen.queryByTestId('cat-geometry')).not.toBeInTheDocument();
  cleanup(); mount();
  expect(await screen.findByLabelText('最小真空 (Å)')).toHaveValue('');
  expect(screen.getByText('已恢复本机尚未保存的输入')).toBeInTheDocument();
  expect(catalysisApi.build).not.toHaveBeenCalled();
});

it('saves valid pending surface parameters with the parent revision and removes obsolete surfaces', async () => {
  mount(); await screen.findByTestId('cat-geometry');
  fireEvent.change(screen.getByLabelText('最小真空 (Å)'), { target: { value: '20' } });
  fireEvent.click(screen.getByRole('button', { name: '保存草稿' }));
  await waitFor(() => expect(catalysisApi.save).toHaveBeenCalledWith(expect.objectContaining({ revision: 3 }), expect.objectContaining({ parameters: expect.objectContaining({ min_vacuum_size: 20 }) })));
  await screen.findByText('草稿已保存。');
  expect(screen.queryByLabelText('终止面')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '生成表面' })).toBeEnabled();
  expect(screen.getByRole('button', { name: '导出 POSCAR + metadata ZIP' })).toBeDisabled();
  cleanup(); mount(); expect(await screen.findByLabelText('最小真空 (Å)')).toHaveValue('20');
});

it('keeps newer input after a late build response and never previews/exports that stale output', async () => {
  const pending = deferred<{ draft: CatalysisDraft }>(); vi.mocked(catalysisApi.build).mockImplementationOnce(() => pending.promise);
  mount(); await screen.findByTestId('cat-geometry');
  fireEvent.click(screen.getByRole('button', { name: '重新生成表面' }));
  fireEvent.change(screen.getByLabelText('最小真空 (Å)'), { target: { value: '21' } });
  await act(async () => pending.resolve({ draft: { ...doc, revision: 4 } }));
  expect(screen.getByLabelText('最小真空 (Å)')).toHaveValue('21');
  expect(screen.getByText(/请求已完成，期间的新输入已保留/)).toBeInTheDocument();
  expect(screen.queryByTestId('cat-geometry')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '导出 POSCAR + metadata ZIP' })).toBeDisabled();
});

it('drops old snapshot atom exceptions after regeneration while retaining edits made during the request', async () => {
  doc = { ...doc, surfaces: [{ ...option('surface-1'), surface: { ...option('surface-1').surface, bottom_fixed_layers: 1, atom_overrides: { 'atom-1': 'free' } } }] };
  const pending = deferred<{ draft: CatalysisDraft }>(); vi.mocked(catalysisApi.build).mockImplementationOnce(() => pending.promise);
  mount(); await screen.findByTestId('cat-geometry');
  fireEvent.click(screen.getByRole('button', { name: '重新生成表面' }));
  fireEvent.change(screen.getByLabelText('草稿名称'), { target: { value: '期间编辑的名称' } });
  const nextSurface = option('new-surface');
  await act(async () => pending.resolve({ draft: { ...doc, revision: 4, surfaces: [nextSurface], active_surface_id: nextSurface.surface_id } }));
  expect(screen.getByLabelText('草稿名称')).toHaveValue('期间编辑的名称');
  expect(screen.getByLabelText('底部固定层数')).toHaveValue('0');
  expect(screen.getByRole('button', { name: '应用并保存约束' })).toBeDisabled();
  expect(screen.getByRole('button', { name: '导出 POSCAR + metadata ZIP' })).toBeDisabled();
});

it('does not lose unrelated name edits when building or applying constraints', async () => {
  mount(); await screen.findByTestId('cat-geometry');
  fireEvent.change(screen.getByLabelText('草稿名称'), { target: { value: '尚未保存的名称' } });
  fireEvent.click(screen.getByRole('button', { name: '重新生成表面' }));
  await screen.findByText('表面已生成并保存。');
  expect(screen.getByLabelText('草稿名称')).toHaveValue('尚未保存的名称');
  fireEvent.change(screen.getByLabelText('底部固定层数'), { target: { value: '1' } });
  fireEvent.click(screen.getByRole('button', { name: '应用并保存约束' }));
  await screen.findByText('约束已应用并保存。');
  expect(screen.getByLabelText('草稿名称')).toHaveValue('尚未保存的名称');
  expect(screen.getByRole('button', { name: '导出 POSCAR + metadata ZIP' })).toBeDisabled();
});

it('preserves input on revision conflict and reloads only through the explicit discard action', async () => {
  vi.mocked(catalysisApi.save).mockRejectedValueOnce(new ApiError('CAT_REVISION_CONFLICT', '另一个窗口已修改草稿', false, 409));
  mount(); await screen.findByTestId('cat-geometry');
  fireEvent.change(screen.getByLabelText('草稿名称'), { target: { value: '本页待保存' } });
  fireEvent.click(screen.getByRole('button', { name: '保存草稿' }));
  await screen.findByText('草稿 revision 已变化，本页输入已保留');
  expect(screen.getByLabelText('草稿名称')).toHaveValue('本页待保存');
  expect(screen.getByRole('button', { name: '导出 POSCAR + metadata ZIP' })).toBeDisabled();
  doc = { ...doc, revision: 8, name: '其他窗口的名称' };
  fireEvent.click(screen.getByRole('button', { name: '放弃本页输入并载入最新' }));
  await screen.findByText('已载入最新草稿。');
  expect(screen.getByLabelText('草稿名称')).toHaveValue('其他窗口的名称');
});

it('never silently applies recovered input from an older revision', async () => {
  storeRecovery({ ...doc, revision: 2 }, { ...editorFor(doc), name: '旧名称', fields: { ...parameterFields(defaultParameters), vacuum: '30' } }, true);
  mount(); await screen.findByTestId('cat-geometry');
  expect(screen.getByText('发现旧 revision 的本机输入')).toBeInTheDocument();
  expect(screen.getByLabelText('最小真空 (Å)')).toHaveValue('15');
  fireEvent.click(screen.getByRole('button', { name: '恢复旧参数' }));
  expect(screen.getByLabelText('最小真空 (Å)')).toHaveValue('30');
  expect(screen.getByLabelText('草稿名称')).toHaveValue('旧名称');
  expect(screen.queryByTestId('cat-geometry')).not.toBeInTheDocument();
});

it('imports an existing slab without a bulk cut and preserves mixed constraints until explicit reset', async () => {
  doc = { ...doc, source: { kind: 'poscar', role: 'slab', name: 'local-slab' }, parameters: null, surfaces: [{ ...option('surface-1'), snapshot: { ...snapshot, atoms: [{ ...snapshot.atoms[0], selective_dynamics: [true, false, true] }, ...snapshot.atoms.slice(1)] } }] };
  mount(); await screen.findByTestId('cat-geometry');
  expect(screen.queryByLabelText('晶面 h')).not.toBeInTheDocument();
  expect(screen.getByText('#1 Pt · TFT')).toBeInTheDocument();
  expect(screen.getByRole('checkbox', { name: '先释放所有已有约束，再应用本页固定层和例外' })).not.toBeChecked();
  fireEvent.click(screen.getByRole('checkbox', { name: '先释放所有已有约束，再应用本页固定层和例外' }));
  fireEvent.click(screen.getByRole('button', { name: '应用并保存约束' }));
  await waitFor(() => expect(catalysisApi.constraints).toHaveBeenCalledWith(expect.objectContaining({ revision: 3 }), { bottom_fixed_layers: 0, atom_overrides: {}, reset_existing: true }));
});

it('applies a per-atom free exception together with fixed bottom layers using stable atom IDs', async () => {
  mount(); await screen.findByTestId('cat-geometry');
  fireEvent.change(screen.getByLabelText('底部固定层数'), { target: { value: '1' } });
  fireEvent.mouseDown(screen.getByLabelText('原子 1 Pt 约束例外'));
  fireEvent.click(await screen.findByText('例外：自由 TTT'));
  fireEvent.click(screen.getByRole('button', { name: '应用并保存约束' }));
  await waitFor(() => expect(catalysisApi.constraints).toHaveBeenCalledWith(expect.objectContaining({ revision: 3 }), { bottom_fixed_layers: 1, atom_overrides: { 'atom-1': 'free' }, reset_existing: false }));
  await screen.findByText('约束已应用并保存。');
});

it('uses backend-compatible finite parameter limits and does not substitute cleared input', () => {
  const fields = parameterFields(defaultParameters);
  expect(parsedParameters(fields)).toEqual(defaultParameters);
  for (const [field, value] of [['h', '4'], ['thickness', '0.4'], ['vacuum', '101'], ['nx', '9'], ['tolerance', '0.0001'], ['vacuum', 'Infinity'], ['h', '']] as const) expect(typeof parsedParameters({ ...fields, [field]: value })).toBe('string');
});

it('retains the released-baseline policy across subsequent constraint edits and reopening', async () => {
  doc = { ...doc, source: { kind: 'poscar', role: 'slab' }, parameters: null };
  mount(); await screen.findByTestId('cat-geometry');
  const resetLabel = '先释放所有已有约束，再应用本页固定层和例外';
  fireEvent.click(screen.getByRole('checkbox', { name: resetLabel }));
  fireEvent.click(screen.getByRole('button', { name: '应用并保存约束' }));
  await screen.findByText('约束已应用并保存。');
  expect(screen.getByRole('checkbox', { name: resetLabel })).toBeChecked();
  await waitFor(() => expect(screen.getByRole('button', { name: '导出 POSCAR + metadata ZIP' })).toBeEnabled());
  fireEvent.change(screen.getByLabelText('底部固定层数'), { target: { value: '1' } });
  fireEvent.click(screen.getByRole('button', { name: '应用并保存约束' }));
  await waitFor(() => expect(catalysisApi.constraints).toHaveBeenLastCalledWith(expect.objectContaining({ revision: 4 }), { bottom_fixed_layers: 1, atom_overrides: {}, reset_existing: true }));
  await screen.findByText('约束已应用并保存。');
  cleanup(); mount(); await screen.findByTestId('cat-geometry');
  expect(screen.getByRole('checkbox', { name: resetLabel })).toBeChecked();
  expect(screen.getByLabelText('底部固定层数')).toHaveValue('1');
});

it('creates a draft from a pre-existing local structure record without starting an external search', async () => {
  mount('/toolbox/surface-builder');
  fireEvent.change(screen.getByLabelText('已有结构 ID'), { target: { value: ' str-existing ' } });
  fireEvent.click(screen.getByRole('button', { name: '从已有记录创建草稿' }));
  await waitFor(() => expect(catalysisApi.create).toHaveBeenCalledWith('', { kind: 'structure_id', role: 'bulk', structure_id: 'str-existing' }));
  await screen.findByTestId('cat-geometry');
});

it('cancels a late ZIP download when input changes while export is pending', async () => {
  const pending = deferred<Blob>(); vi.mocked(catalysisApi.export).mockImplementationOnce(() => pending.promise);
  const createUrl = vi.fn(); vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: createUrl, revokeObjectURL: vi.fn() }));
  mount(); await screen.findByTestId('cat-geometry');
  fireEvent.click(screen.getByRole('button', { name: '导出 POSCAR + metadata ZIP' }));
  fireEvent.change(screen.getByLabelText('最小真空 (Å)'), { target: { value: '22' } });
  await act(async () => pending.resolve(new Blob(['ZIP'])));
  expect(screen.getByText('导出期间输入已变化，已取消旧结构下载。')).toBeInTheDocument();
  expect(createUrl).not.toHaveBeenCalled();
  vi.unstubAllGlobals();
});
