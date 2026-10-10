import { useState } from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ConfigProvider } from 'antd';
import { MemoryRouter } from 'react-router-dom';
import { catalysisApi } from '../api/catalysis';
import { ApiError } from '../api/client';
import type { AdsorptionCandidate, CatalysisDraft } from '../types/catalysis';
import type { StructureGeometry } from '../types/structure-geometry';
import AdsorptionBuilder from './AdsorptionBuilder';
import { adsorptionEditorFor, parseXYZ, placementInput, siteInput, storeAdsorptionRecovery } from './adsorptionDraftState';
import { catalysisWorkflowResponseFixture } from '../mocks/catalysisWorkflowFixture';
import { getWorkflowDraft, resetWorkflowDraft } from '../stores/workflowDraft';

vi.mock('../components/structure/CrystalViewer', () => ({ CrystalGeometryViewer: ({ data, markers = [] }: { data: StructureGeometry; markers?: { id: string; label: string }[] }) => <div>候选几何 {data.structure_id}{markers.map(marker => <span key={marker.id} data-site-marker={marker.id} data-site-label={marker.label} />)}</div> }));
const snapshot = { snapshot_id: 'snap', coordinate_mode: 'cartesian' as const, lattice: [[4, 0, 0], [0, 4, 0], [0, 0, 20]] as StructureGeometry['basis_cartesian_angstrom'], sha256: 'a'.repeat(64), atoms: [{ atom_id: 'pt-1', element: 'Pt', fractional: [0, 0, .25] as [number, number, number], cartesian: [0, 0, 5] as [number, number, number], selective_dynamics: [false, false, false] as [boolean, boolean, boolean], provenance: {} }] };
function fixture(): CatalysisDraft {
  return { draft_id: 'cat-ads-test', schema_version: 1, revision: 4, name: '合成吸附草稿', created_at: '', updated_at: '', source: { kind: 'poscar', role: 'slab' }, input_snapshot: snapshot, parameters: null, active_surface_id: 'surface', surfaces: [{ surface_id: 'surface', snapshot, termination_shift: null, transform: {}, surface: { normal: [0, 0, 1], normal_period_angstrom: 20, actual_nuclei_span_angstrom: 0, periodic_vacuum_gap_angstrom: 20, in_plane_lengths_angstrom: [4, 4], layer_tolerance: .1, bottom_fixed_layers: 1, atom_overrides: {}, reset_existing: false, flag_basis: 'direct_lattice_vectors', layers: [{ layer_index: 0, atom_ids: ['pt-1'], projection_angstrom: 5 }] } }], warnings: [], adsorption: { adsorbate: { source_id: 'co-1', source_sha256: 'b'.repeat(64), source: { kind: 'co_example' }, atoms: [{ atom_id: 'C1', element: 'C', cartesian: [0, 0, 0] }, { atom_id: 'O1', element: 'O', cartesian: [0, 0, 1.15] }], anchor_index: 0 }, sites: [{ site_id: 'top', kind: 'ontop', label: '顶位', fractional: [0, 0, .25], cartesian: [0, 0, 5], source_atom_ids: ['pt-1'] }, { site_id: 'manual', kind: 'manual', label: '我的位置', fractional: [.5, .5, .25], cartesian: [2, 2, 5], source_atom_ids: [] }], site_parent_surface_id: 'surface', site_parent_snapshot_sha256: snapshot.sha256, site_parent_revision: 4, selected_site_ids: [], selected_candidate_ids: [], candidates: [], warnings: [], frame: { x: [1, 0, 0], y: [0, 1, 0], z: [0, 0, 1], rotation_convention: 'fixed_surface_xyz_X_then_Y_then_Z' } } };
}
const candidate = (id: string, site: string): AdsorptionCandidate => ({ candidate_id: id, site_id: site, label: site, parent_revision: 4, parent_surface_id: 'surface', parent_snapshot_sha256: snapshot.sha256, adsorbate_source_id: 'co-1', status: 'valid', snapshot, placement: { height_angstrom: 2, rotation_degrees: [0, 0, 0], screening_distance_angstrom: .8 }, transform: {}, validation: { minimum_adsorbate_surface_distance_angstrom: 2, minimum_periodic_self_image_distance_angstrom: 4, screening_distance_angstrom: .8, screening_passed: true, warnings: [] } });
const deferred = <T,>() => { let resolve!: (value: T) => void; const promise = new Promise<T>(r => { resolve = r; }); return { promise, resolve }; };
let doc: CatalysisDraft, client: QueryClient;
function mount(upstream = false, surfaceGeometry?: StructureGeometry) {
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  function Harness() { const [current, setCurrent] = useState(doc); return <AdsorptionBuilder doc={current} upstreamDirty={upstream} parentBusy={false} parentConflict={false} {...(surfaceGeometry ? { surfaceGeometry } : {})} onUpdate={setCurrent} onBusy={() => undefined} />; }
  return render(<ConfigProvider theme={{ token: { motion: false } }}><QueryClientProvider client={client}><MemoryRouter><Harness /></MemoryRouter></QueryClientProvider></ConfigProvider>);
}
beforeEach(() => {
  localStorage.clear(); resetWorkflowDraft(); doc = fixture();
  vi.spyOn(catalysisApi, 'get').mockImplementation(async () => ({ draft: doc }));
  vi.spyOn(catalysisApi, 'adsorbate').mockImplementation(async (_base, source, anchor_index) => { doc = { ...doc, revision: doc.revision + 1, adsorption: { ...doc.adsorption!, adsorbate: { ...doc.adsorption!.adsorbate!, source, anchor_index }, sites: [], selected_site_ids: [], candidates: [], selected_candidate_ids: [] } }; return { draft: doc }; });
  vi.spyOn(catalysisApi, 'sites').mockImplementation(async (_base, settings) => { doc = { ...doc, revision: doc.revision + 1, adsorption: { ...doc.adsorption!, site_settings: settings, selected_site_ids: [] } }; return { draft: doc }; });
  vi.spyOn(catalysisApi, 'candidates').mockImplementation(async (_base, ids, placement) => { doc = { ...doc, revision: doc.revision + 1, adsorption: { ...doc.adsorption!, placement, selected_site_ids: ids, candidates: ids.map((site, i) => ({ ...candidate(`candidate-${i + 1}`, site), placement })), selected_candidate_ids: ids.map((_site, i) => `candidate-${i + 1}`) } }; return { draft: doc }; });
  vi.spyOn(catalysisApi, 'selection').mockImplementation(async (_base, selected_candidate_ids) => { doc = { ...doc, revision: doc.revision + 1, adsorption: { ...doc.adsorption!, selected_candidate_ids } }; return { draft: doc }; });
  vi.spyOn(catalysisApi, 'candidateGeometry').mockImplementation(async (_id, candidate_id, revision) => ({ revision, candidate_id, geometry: { structure_id: candidate_id } as StructureGeometry }));
  vi.spyOn(catalysisApi, 'exportCandidates').mockResolvedValue(new Blob(['zip']));
  vi.spyOn(catalysisApi, 'workflowBinding').mockImplementation(async (_base, target) => catalysisWorkflowResponseFixture(doc, target.candidate_id));
});
afterEach(() => { cleanup(); client?.clear(); vi.restoreAllMocks(); });

it('shows only selected 3D site markers with stable labels while keeping every map and list option', () => {
  mount(false, { structure_id: 'surface-preview' } as StructureGeometry);
  const markerIds = () => Array.from(document.querySelectorAll('[data-site-marker]'), marker => marker.getAttribute('data-site-marker'));
  const expectAllSites = () => {
    expect(screen.getByRole('button', { name: '位点 S1 顶位' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '位点 S2 手动' })).toBeInTheDocument();
    expect(screen.getByText('S1 · 顶位')).toBeInTheDocument();
    expect(screen.getByText('S2 · 手动')).toBeInTheDocument();
  };

  expect(markerIds()).toEqual([]);
  expect(screen.getByText('三维预览仅显示已选 0 个位点，请先在平面图或列表中选择位点。')).toBeInTheDocument();
  expectAllSites();

  fireEvent.click(screen.getByRole('button', { name: '位点 S2 手动' }));
  expect(markerIds()).toEqual(['manual']);
  expect(document.querySelector('[data-site-marker="manual"]')).toHaveAttribute('data-site-label', 'S2');
  expect(screen.getByText('三维预览仅显示已选 1 个位点，全部位点请在平面图或列表中选择。')).toBeInTheDocument();
  expectAllSites();

  fireEvent.click(screen.getByText('S1 · 顶位').closest('label')!);
  expect(markerIds()).toEqual(['top', 'manual']);
  expect(document.querySelector('[data-site-marker="top"]')).toHaveAttribute('data-site-label', 'S1');
  expect(document.querySelector('[data-site-marker="manual"]')).toHaveAttribute('data-site-label', 'S2');
  expect(screen.getByText('三维预览仅显示已选 2 个位点，全部位点请在平面图或列表中选择。')).toBeInTheDocument();
  expectAllSites();

  fireEvent.click(screen.getByText('S2 · 手动').closest('label')!);
  expect(markerIds()).toEqual(['top']);
  expect(document.querySelector('[data-site-marker="top"]')).toHaveAttribute('data-site-label', 'S1');
  expect(screen.getByText('三维预览仅显示已选 1 个位点，全部位点请在平面图或列表中选择。')).toBeInTheDocument();
  expectAllSites();
});

it('keeps the raw structure clean when the site geometry overlay no longer matches the surface', () => {
  doc.adsorption!.site_surface_atoms = [{ atom_id: 'pt-1', cartesian: [1, 0, 5] }];
  mount(false, { structure_id: 'surface-preview' } as StructureGeometry);

  expect(document.querySelector('[data-site-marker]')).not.toBeInTheDocument();
  expect(screen.queryByText('候选几何 surface-preview')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '位点 S1 顶位' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '位点 S2 手动' })).toBeInTheDocument();
  expect(screen.getByText('S1 · 顶位')).toBeInTheDocument();
  expect(screen.getByText('S2 · 手动')).toBeInTheDocument();
});

it('hands off only the explicitly previewed candidate while multi-selection remains a ZIP selection', async () => {
  mount();
  fireEvent.click(screen.getByRole('button', { name: '位点 S1 顶位' })); fireEvent.click(screen.getByRole('button', { name: '位点 S2 手动' }));
  fireEvent.click(screen.getByRole('button', { name: '生成 2 个独立候选' }));
  await screen.findByTestId('cat-candidate-geometry');
  await waitFor(() => expect(screen.getByRole('button', { name: '将当前预览候选传入 Workflow' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: '预览候选 2' }));
  await waitFor(() => expect(screen.getByTestId('cat-candidate-geometry')).toHaveAttribute('data-candidate-id', 'candidate-2'));
  fireEvent.click(screen.getByRole('button', { name: '将当前预览候选传入 Workflow' }));
  expect(catalysisApi.workflowBinding).not.toHaveBeenCalled();
  fireEvent.click(await screen.findByRole('button', { name: '确认并进入 Workflow' }));
  await waitFor(() => expect(getWorkflowDraft().catalysisBinding?.candidate_id).toBe('candidate-2'));
  expect(catalysisApi.workflowBinding).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ revision: 5 }), { candidate_id: 'candidate-2' });
  expect(doc.adsorption!.selected_candidate_ids).toEqual(['candidate-1', 'candidate-2']);
  expect(catalysisApi.exportCandidates).not.toHaveBeenCalled();
});

it('selects spatial markers, creates independent candidates with a pinned revision, and persists multi-export selection', async () => {
  mount();
  fireEvent.click(screen.getByRole('button', { name: '位点 S1 顶位' }));
  fireEvent.click(screen.getByRole('button', { name: '位点 S2 手动' }));
  fireEvent.change(screen.getByLabelText('绕固定 Y 旋转 (°)'), { target: { value: '45' } });
  fireEvent.click(screen.getByRole('button', { name: '生成 2 个独立候选' }));
  await screen.findByText('独立吸附候选已生成并保存。');
  expect(catalysisApi.candidates).toHaveBeenCalledWith(expect.objectContaining({ revision: 4 }), ['top', 'manual'], { height_angstrom: 2, rotation_degrees: [0, 45, 0], screening_distance_angstrom: .8 });
  expect(await screen.findByTestId('cat-candidate-geometry')).toHaveAttribute('data-revision', '5');
  expect(catalysisApi.candidateGeometry).toHaveBeenCalledWith(doc.draft_id, 'candidate-1', 5, expect.any(AbortSignal));
  fireEvent.click(screen.getByLabelText('导出候选 2'));
  expect(screen.getByRole('button', { name: '导出所选 1 个候选 ZIP' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: '保存候选导出选择' }));
  await screen.findByText('候选导出选择已保存。');
  expect(catalysisApi.selection).toHaveBeenCalledWith(expect.objectContaining({ revision: 5 }), ['candidate-1']);
  expect(screen.getByRole('button', { name: '导出所选 1 个候选 ZIP' })).toBeEnabled();
  cleanup(); mount(); expect(screen.getByLabelText('导出候选 1')).toBeChecked(); expect(screen.getByLabelText('导出候选 2')).not.toBeChecked();
});

it('keeps saved manual configuration across site discovery and reopening', async () => {
  mount(); fireEvent.click(screen.getByLabelText('添加手动位置'));
  fireEvent.change(screen.getByLabelText('手动位置 u'), { target: { value: '.25' } });
  fireEvent.change(screen.getByLabelText('手动位置 v'), { target: { value: '.75' } });
  fireEvent.change(screen.getByLabelText('位点去重容差 (Å)'), { target: { value: '.02' } });
  fireEvent.click(screen.getByRole('button', { name: '生成／更新几何位点' }));
  await screen.findByText('几何位点已保存，请选择生成位置。');
  expect(screen.getByLabelText('手动位置 u')).toHaveValue('0.25');
  cleanup(); mount(); expect(screen.getByLabelText('添加手动位置')).toBeChecked(); expect(screen.getByLabelText('手动位置 v')).toHaveValue('0.75'); expect(screen.getByLabelText('位点去重容差 (Å)')).toHaveValue('0.02');
});

it('recovers invalid pending XYZ and placement text without substituting defaults', () => {
  const input = { ...adsorptionEditorFor(doc), kind: 'xyz' as const, content: '2\nCO\nC 0 0 0', height: '' };
  storeAdsorptionRecovery(doc, input, true); mount();
  expect(screen.getByLabelText('XYZ 分子内容')).toHaveValue(input.content); expect(screen.getByLabelText('初始锚点高度 (Å)')).toHaveValue('');
  expect(screen.getByRole('button', { name: '应用并保存吸附物' })).toBeDisabled();
  expect(screen.getByText('已恢复本机尚未应用的吸附输入')).toBeInTheDocument();
});

it('retains inputs typed during a late candidate build and clears prior site/candidate IDs', async () => {
  const pending = deferred<{ draft: CatalysisDraft }>(); vi.mocked(catalysisApi.candidates).mockImplementationOnce(() => pending.promise); mount();
  fireEvent.click(screen.getByRole('button', { name: '位点 S1 顶位' })); fireEvent.click(screen.getByRole('button', { name: '生成 1 个独立候选' }));
  fireEvent.change(screen.getByLabelText('初始锚点高度 (Å)'), { target: { value: '3.2' } });
  await act(async () => pending.resolve({ draft: { ...doc, revision: 5, adsorption: { ...doc.adsorption!, selected_site_ids: ['top'], candidates: [candidate('candidate-1', 'top')], selected_candidate_ids: ['candidate-1'] } } }));
  expect(screen.getByLabelText('初始锚点高度 (Å)')).toHaveValue('3.2');
  expect(screen.getByRole('button', { name: '生成 0 个独立候选' })).toBeDisabled();
  expect(screen.queryByTestId('cat-candidate-geometry')).not.toBeInTheDocument(); expect(screen.getByRole('button', { name: '导出所选 0 个候选 ZIP' })).toBeDisabled();
});

it('reloads a newer revision after a conflict while preserving source and placement inputs', async () => {
  vi.mocked(catalysisApi.adsorbate).mockRejectedValueOnce(new ApiError('CAT_REVISION_CONFLICT', '已更新', false, 409)); mount();
  fireEvent.mouseDown(screen.getByLabelText('锚定原子')); fireEvent.click(await screen.findByText('#2 O · 0.000 / 0.000 / 1.150 Å'));
  fireEvent.change(screen.getByLabelText('初始锚点高度 (Å)'), { target: { value: '3' } });
  fireEvent.click(screen.getByRole('button', { name: '应用并保存吸附物' })); await screen.findByText('草稿 revision 已变化，吸附输入已保留');
  doc = { ...doc, revision: 8 }; fireEvent.click(screen.getByRole('button', { name: '保留输入并读取最新草稿' }));
  await screen.findByText(/已读取最新草稿并保留吸附输入/); expect(catalysisApi.get).toHaveBeenCalledWith(doc.draft_id);
  expect(screen.getByLabelText('初始锚点高度 (Å)')).toHaveValue('3');
  fireEvent.click(screen.getByRole('button', { name: '应用并保存吸附物' }));
  await waitFor(() => expect(catalysisApi.adsorbate).toHaveBeenLastCalledWith(expect.objectContaining({ revision: 8 }), { kind: 'co_example' }, 1));
});

it('blocks candidate preview and export for dirty upstream input', () => {
  doc = { ...doc, adsorption: { ...doc.adsorption!, selected_site_ids: ['top'], placement: candidate('c', 'top').placement, candidates: [candidate('c', 'top')], selected_candidate_ids: ['c'] } }; mount(true);
  expect(screen.getByRole('button', { name: '预览候选 1' })).toBeDisabled(); expect(screen.getByRole('button', { name: '导出所选 1 个候选 ZIP' })).toBeDisabled(); expect(catalysisApi.candidateGeometry).not.toHaveBeenCalled();
});

it('aligns skew-c periodic top atoms and ontop markers using server-provided canonical images', () => {
  doc.surfaces[0].snapshot = { ...snapshot, lattice: [[4, 0, 0], [0, 4, 0], [1, 2, 10]], atoms: [{ ...snapshot.atoms[0], cartesian: [1, 1, 1] }] };
  doc.adsorption = { ...doc.adsorption!, sites: [{ ...doc.adsorption!.sites[0], cartesian: [2, 3, 11] }], site_surface_atoms: [{ atom_id: 'pt-1', cartesian: [2, 3, 11] }], site_map_origin_cartesian: [1.1, 2.2, 11] };
  const mounted = mount(), atom = mounted.container.querySelector('[data-map-atom="pt-1"]')!, marker = screen.getByRole('button', { name: '位点 S1 顶位' }).querySelector('circle')!;
  expect(atom.getAttribute('cx')).toBe(marker.getAttribute('cx')); expect(atom.getAttribute('cy')).toBe(marker.getAttribute('cy'));
  expect(doc.surfaces[0].snapshot.atoms[0].cartesian).toEqual([1, 1, 1]);
});

it('preserves newer XYZ source content during a late save and clears IDs before regeneration', async () => {
  const pending = deferred<{ draft: CatalysisDraft }>(); vi.mocked(catalysisApi.adsorbate).mockImplementationOnce(() => pending.promise);
  storeAdsorptionRecovery(doc, { ...adsorptionEditorFor(doc), kind: 'xyz', content: '2\nfirst\nC 0 0 0\nO 0 0 1.15', sourceName: 'first.xyz', siteIds: ['top'] }, true); mount();
  fireEvent.click(screen.getByRole('button', { name: '应用并保存吸附物' }));
  const newer = '2\nnewer\nC 0 0 0\nO 1.15 0 0'; fireEvent.change(screen.getByLabelText('XYZ 分子内容'), { target: { value: newer } });
  await act(async () => pending.resolve({ draft: { ...doc, revision: 5, adsorption: { ...doc.adsorption!, adsorbate: { ...doc.adsorption!.adsorbate!, source: { kind: 'xyz', content: '2\nfirst\nC 0 0 0\nO 0 0 1.15' } }, sites: [], selected_site_ids: [] } } }));
  expect(screen.getByLabelText('XYZ 分子内容')).toHaveValue(newer); expect(screen.getByRole('button', { name: '生成 0 个独立候选' })).toBeDisabled();
  expect(screen.getByRole('button', { name: '应用并保存吸附物' })).toBeEnabled();
});

it('cancels a pending candidate download when placement input changes', async () => {
  doc = { ...doc, adsorption: { ...doc.adsorption!, selected_site_ids: ['top'], placement: candidate('c', 'top').placement, candidates: [candidate('c', 'top')], selected_candidate_ids: ['c'] } };
  const pending = deferred<Blob>(); vi.mocked(catalysisApi.exportCandidates).mockImplementationOnce(() => pending.promise);
  const createUrl = vi.fn(), oldCreate = URL.createObjectURL; URL.createObjectURL = createUrl;
  mount(); fireEvent.click(screen.getByRole('button', { name: '导出所选 1 个候选 ZIP' }));
  fireEvent.change(screen.getByLabelText('初始锚点高度 (Å)'), { target: { value: '3' } });
  await act(async () => pending.resolve(new Blob(['ZIP'])));
  expect(screen.getByText('导出期间输入已变化，已取消旧候选下载。')).toBeInTheDocument(); expect(createUrl).not.toHaveBeenCalled(); URL.createObjectURL = oldCreate;
});

it('rejects malformed XYZ frames and nonfinite placement/manual values with explicit bounds', () => {
  expect(parseXYZ('2\nCO\nC 0 0 0\nO 0 0 1.15')).toHaveLength(2);
  for (const text of ['129\nlarge', '1\natom\nC NaN 0 0', '1\natom\nC 0 0 0\n1\nsecond\nC 0 0 0']) expect(typeof parseXYZ(text)).toBe('string');
  const editor = adsorptionEditorFor(doc);
  expect(typeof placementInput({ ...editor, height: '' })).toBe('string'); expect(typeof placementInput({ ...editor, ry: '361' })).toBe('string');
  expect(typeof siteInput({ ...editor, manualEnabled: true, u: '1' })).toBe('string');
});
