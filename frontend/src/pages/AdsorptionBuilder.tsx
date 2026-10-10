import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Card, Checkbox, Empty, Input, Select, Space, Spin, Tag, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { catalysisApi } from '../api/catalysis';
import { ApiError } from '../api/client';
import { CrystalGeometryViewer } from '../components/structure/CrystalViewer';
import CatalysisWorkflowHandoff from '../components/workflow/CatalysisWorkflowHandoff';
import type { AdsorptionSite, CatalysisDraft, SurfaceOption } from '../types/catalysis';
import type { StructureGeometry, Vec3 } from '../types/structure-geometry';
import { activeSurface, same } from './surfaceDraftState';
import { adsorbateInput, adsorptionEditorFor, coXYZ, kindLabels, parseXYZ, placementEqual, placementInput, readAdsorptionRecovery, siteInput, sourceEqual, storeAdsorptionRecovery, type AdsorptionEditor } from './adsorptionDraftState';

const errorText = (error: unknown) => error instanceof Error ? error.message : '操作失败，请重试。';
const vector = (v: number[]) => v.map(n => n.toFixed(3)).join(' / ');
const toggle = (ids: string[], id: string) => ids.includes(id) ? ids.filter(value => value !== id) : [...ids, id];
const sourceFields = (e: AdsorptionEditor) => ({ kind: e.kind, element: e.element, content: e.content, sourceName: e.sourceName, anchor: e.anchor });
const placementFields = (e: AdsorptionEditor) => ({ height: e.height, rx: e.rx, ry: e.ry, rz: e.rz, screening: e.screening });
const siteFields = (e: AdsorptionEditor) => ({ kinds: e.kinds, manualEnabled: e.manualEnabled, u: e.u, v: e.v, manualLabel: e.manualLabel, tolerance: e.tolerance });

function SiteMap({ surface, sites, selected, disabled, origin = [0, 0, 0], topAtoms, onToggle }: { surface: SurfaceOption; sites: AdsorptionSite[]; selected: string[]; disabled: boolean; origin?: Vec3; topAtoms?: { atom_id: string; cartesian: Vec3 }[]; onToggle: (id: string) => void }) {
  const a = surface.snapshot.lattice[0], b = surface.snapshot.lattice[1], z = surface.surface.normal;
  const length = Math.hypot(...a), x = a.map(v => v / length) as Vec3;
  const y = [z[1] * x[2] - z[2] * x[1], z[2] * x[0] - z[0] * x[2], z[0] * x[1] - z[1] * x[0]];
  const project = (v: Vec3) => [v.reduce((sum, n, i) => sum + n * x[i], 0), v.reduce((sum, n, i) => sum + n * y[i], 0)];
  const corners = [[0, 0, 0], a, a.map((v, i) => v + b[i]), b].map(v => project(v.map((n, i) => n + origin[i]) as Vec3));
  const topIds = new Set(surface.surface.layers.at(-1)?.atom_ids ?? []);
  const displayedAtoms = topAtoms ?? surface.snapshot.atoms.filter(atom => topIds.has(atom.atom_id));
  const points = sites.map(site => project(site.cartesian)), all = [...corners, ...points, ...displayedAtoms.map(atom => project(atom.cartesian))];
  const xmin = Math.min(...all.map(v => v[0])), xmax = Math.max(...all.map(v => v[0])), ymin = Math.min(...all.map(v => v[1])), ymax = Math.max(...all.map(v => v[1]));
  const scale = Math.min(480 / Math.max(xmax - xmin, 1), 280 / Math.max(ymax - ymin, 1));
  const screen = (v: number[]) => [60 + (v[0] - xmin) * scale, 320 - (v[1] - ymin) * scale];
  const colors = { ontop: 'var(--wf-blue)', bridge: '#bc9158', hollow: '#82998a', manual: '#b08daf' };
  return <div className="cat-site-map"><svg viewBox="0 0 600 380" role="img" aria-label="表面位点平面图，沿外法向俯视；点击标记选择位点">
    <polygon points={corners.map(v => screen(v).join(',')).join(' ')} fill="var(--wf-soft)" stroke="var(--wf-border)" strokeWidth={2} />
    {displayedAtoms.map(atom => { const p = screen(project(atom.cartesian)); const element = surface.snapshot.atoms.find(a => a.atom_id === atom.atom_id)?.element; return <circle key={atom.atom_id} data-map-atom={atom.atom_id} cx={p[0]} cy={p[1]} r={4} fill="var(--wf-muted)"><title>{element} · 顶层表面原子周期图像</title></circle>; })}
    {sites.map((site, i) => { const p = screen(points[i]), checked = selected.includes(site.site_id); return <g key={site.site_id} role="button" aria-label={`位点 S${i + 1} ${kindLabels[site.kind]}`} aria-pressed={checked} aria-disabled={disabled || (!checked && selected.length >= 16)} tabIndex={disabled ? -1 : 0} onClick={() => !disabled && (checked || selected.length < 16) && onToggle(site.site_id)} onKeyDown={event => { if ((event.key === 'Enter' || event.key === ' ') && !disabled && (checked || selected.length < 16)) { event.preventDefault(); onToggle(site.site_id); } }} className="cat-site-point"><circle cx={p[0]} cy={p[1]} r={checked ? 11 : 7} fill={checked ? colors[site.kind] : 'var(--wf-field)'} stroke={colors[site.kind]} strokeWidth={2.5} />{(checked || sites.length <= 20) && <text x={p[0] + 12} y={p[1] - 10} fill="var(--wf-text)" fontSize={12}>S{i + 1}</text>}<title>S{i + 1} {kindLabels[site.kind]} · u/v {vector(site.fractional.slice(0, 2))}</title></g>; })}
    {['a', 'b'].map((label, i) => { const p = screen(corners[i === 0 ? 1 : 3]); return <text key={label} x={p[0] + 12} y={p[1] + 20} fill="var(--wf-muted)" fontSize={14}>{label}</text>; })}
    <text x={30} y={365} fill="var(--wf-muted)" fontSize={12}>沿 +n 看向表面 · 灰点为顶层原子 · 标记为几何位置</text>
  </svg><div className="cat-site-legend">{(['ontop', 'bridge', 'hollow', 'manual'] as const).filter(kind => sites.some(site => site.kind === kind)).map(kind => <span key={kind}><i style={{ background: colors[kind] }} />{kindLabels[kind]} {sites.filter(site => site.kind === kind).length}</span>)}</div></div>;
}

export default function AdsorptionBuilder({ doc, upstreamDirty, parentBusy, parentConflict, surfaceGeometry, onUpdate, onBusy }: { doc: CatalysisDraft; upstreamDirty: boolean; parentBusy: boolean; parentConflict: boolean; surfaceGeometry?: StructureGeometry; onUpdate: (draft: CatalysisDraft) => void; onBusy: (busy: boolean) => void }) {
  const recovery = useRef(readAdsorptionRecovery(doc));
  const [editor, setEditor] = useState<AdsorptionEditor>(() => recovery.current?.revision === doc.revision ? recovery.current.editor : adsorptionEditorFor(doc));
  const [recovered, setRecovered] = useState(!!recovery.current), [busy, setBusy] = useState(''), [error, setError] = useState(''), [notice, setNotice] = useState(''), [conflict, setConflict] = useState(false), [storageFailed, setStorageFailed] = useState(false), [previewId, setPreviewId] = useState('');
  const version = useRef(0), live = useRef(true), handledRevision = useRef(doc.revision), upstream = useRef(upstreamDirty);
  upstream.current = upstreamDirty;
  useEffect(() => { live.current = true; return () => { live.current = false; onBusy(false); }; }, [onBusy]);
  const surface = activeSurface(doc), state = doc.adsorption, saved = adsorptionEditorFor(doc);
  const source = adsorbateInput(editor), placement = placementInput(editor), siteSettings = siteInput(editor);
  const sourceDirty = !state?.adsorbate || !sourceEqual(editor, saved), placementDirty = !placementEqual(editor, saved), siteSettingsDirty = !same(siteFields(editor), siteFields(saved));
  const selectedSitesDirty = !same(editor.siteIds, state?.selected_site_ids ?? []), selectionDirty = !same(editor.candidateIds, state?.selected_candidate_ids ?? []);
  const pending = !same(editor, saved), staleRecovery = !!recovery.current && recovery.current.revision !== doc.revision;
  const blocked = upstreamDirty || parentBusy || parentConflict || conflict || !!busy;
  const sitesMatch = !!surface && state?.site_parent_surface_id === surface.surface_id && state.site_parent_snapshot_sha256 === surface.snapshot.sha256;
  const visibleSites = sitesMatch && !upstreamDirty && !sourceDirty && !siteSettingsDirty ? state!.sites : [];
  const siteMarkers = visibleSites.map((site, i) => ({ id: site.site_id, label: `S${i + 1}`, cartesian: site.cartesian, selected: editor.siteIds.includes(site.site_id) }));
  const selectedSiteMarkers = siteMarkers.filter(marker => editor.siteIds.includes(marker.id));
  const validCandidates = (state?.candidates ?? []).filter(candidate => candidate.status === 'valid' && candidate.parent_surface_id === surface?.surface_id && candidate.parent_snapshot_sha256 === surface?.snapshot.sha256 && candidate.adsorbate_source_id === state?.adsorbate?.source_id);
  const candidatesCurrent = !upstreamDirty && !sourceDirty && !placementDirty && !siteSettingsDirty && !selectedSitesDirty;
  const chosen = validCandidates.find(candidate => candidate.candidate_id === previewId);
  const geometry = useQuery({ queryKey: ['cat-candidate-geometry', doc.draft_id, doc.revision, chosen?.candidate_id], queryFn: ({ signal }) => catalysisApi.candidateGeometry(doc.draft_id, chosen!.candidate_id, doc.revision, signal), enabled: !!chosen && candidatesCurrent && !blocked, retry: false });
  const geometryMatches = !!chosen && geometry.data?.revision === doc.revision && geometry.data.candidate_id === chosen.candidate_id;
  const canExport = candidatesCurrent && !blocked && !selectionDirty && editor.candidateIds.length > 0 && editor.candidateIds.every(id => validCandidates.some(c => c.candidate_id === id));
  const parentIdentity = `${surface?.surface_id}:${surface?.snapshot.sha256}`;
  const identity = useRef(parentIdentity);
  useEffect(() => {
    if (handledRevision.current === doc.revision) return;
    handledRevision.current = doc.revision;
    if (identity.current !== parentIdentity) {
      identity.current = parentIdentity; version.current++; setPreviewId('');
      setEditor(previous => ({ ...previous, siteIds: [], candidateIds: [] }));
      setNotice('清洁表面快照已变化，位点与候选选择已清除；请重新生成。');
    } else if (!pending) { setEditor(adsorptionEditorFor(doc)); setConflict(false); }
    else setEditor(previous => ({ ...previous, siteIds: previous.siteIds.filter(id => state?.sites.some(s => s.site_id === id)), candidateIds: previous.candidateIds.filter(id => validCandidates.some(c => c.candidate_id === id)) }));
  }, [doc, parentIdentity, pending, state, validCandidates]);
  useEffect(() => { if (!staleRecovery || !recovered) setStorageFailed(!storeAdsorptionRecovery(doc, editor, pending)); }, [doc, editor, pending, staleRecovery, recovered]);
  useEffect(() => { const before = (event: BeforeUnloadEvent) => { if (pending) { event.preventDefault(); event.returnValue = ''; } }; window.addEventListener('beforeunload', before); return () => window.removeEventListener('beforeunload', before); }, [pending]);
  function edit(patch: Partial<AdsorptionEditor>) { version.current++; setEditor(previous => ({ ...previous, ...patch })); setNotice(''); }
  async function action(label: string, mode: 'source' | 'sites' | 'candidates' | 'selection', work: () => Promise<{ draft: CatalysisDraft }>) {
    const atVersion = version.current; setBusy(label); onBusy(true); setError(''); setNotice('');
    try {
      const next = (await work()).draft; handledRevision.current = next.revision;
      onUpdate(next); if (!live.current) return;
      const restored = adsorptionEditorFor(next); identity.current = `${activeSurface(next)?.surface_id}:${activeSurface(next)?.snapshot.sha256}`;
      setConflict(false); setRecovered(false); recovery.current = null;
      setEditor(previous => {
        if (atVersion !== version.current) return { ...previous, siteIds: mode === 'selection' ? previous.siteIds : [], candidateIds: [] };
        if (mode === 'source') return { ...previous, ...sourceFields(restored), siteIds: [], candidateIds: [] };
        if (mode === 'sites') return { ...previous, ...siteFields(restored), siteIds: restored.siteIds, candidateIds: [] };
        if (mode === 'candidates') return { ...previous, ...placementFields(restored), siteIds: restored.siteIds, candidateIds: restored.candidateIds };
        return { ...previous, candidateIds: restored.candidateIds };
      });
      if (mode !== 'selection') setPreviewId(atVersion === version.current && mode === 'candidates' ? next.adsorption?.candidates.find(c => c.status === 'valid')?.candidate_id ?? '' : '');
      setNotice(atVersion !== version.current ? '请求已完成，期间的新输入已保留；旧位点和候选选择已清除，请重新应用。' : mode === 'source' ? '吸附物与锚点已保存。' : mode === 'sites' ? '几何位点已保存，请选择生成位置。' : mode === 'candidates' ? '独立吸附候选已生成并保存。' : '候选导出选择已保存。');
    } catch (e) { if (live.current) { setError(errorText(e)); setConflict(e instanceof ApiError && e.status === 409); } }
    finally { if (live.current) { setBusy(''); onBusy(false); } }
  }
  async function importXYZ(file?: File) {
    if (!file) return;
    if (!file.size || file.size > 65536) { setError('请选择非空、最多 64 KiB 的单构型 XYZ 文件。'); return; }
    const atVersion = version.current;
    try { const content = await file.text(); if (!live.current) return; if (version.current !== atVersion) { setNotice('读取文件期间输入已变化，请重新选择 XYZ 文件。'); return; } edit({ kind: 'xyz', content, sourceName: file.name, anchor: '0', siteIds: [], candidateIds: [] }); setError(''); }
    catch (e) { setError(errorText(e)); }
  }
  async function exportZip() {
    if (!canExport) return;
    const atVersion = version.current, atRevision = doc.revision; setBusy('准备候选导出'); onBusy(true); setError('');
    try { const blob = await catalysisApi.exportCandidates(doc, editor.candidateIds); if (!live.current) return; if (version.current !== atVersion || upstream.current || handledRevision.current !== atRevision) { setNotice('导出期间输入已变化，已取消旧候选下载。'); return; } const url = URL.createObjectURL(blob), a = document.createElement('a'); a.href = url; a.download = `${doc.name || 'adsorption'}-candidates.zip`; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); setNotice('已导出所选独立候选、约束与来源清单。'); }
    catch (e) { if (live.current) { setError(errorText(e)); setConflict(e instanceof ApiError && e.status === 409); } }
    finally { if (live.current) { setBusy(''); onBusy(false); } }
  }
  async function refreshAfterConflict() {
    setBusy('读取最新草稿'); onBusy(true); setError('');
    try {
      const next = (await catalysisApi.get(doc.draft_id)).draft; if (!live.current) return;
      handledRevision.current = next.revision; identity.current = `${activeSurface(next)?.surface_id}:${activeSurface(next)?.snapshot.sha256}`; onUpdate(next);
      version.current++; setEditor(previous => ({ ...previous, siteIds: [], candidateIds: [] })); setPreviewId(''); setConflict(false);
      setRecovered(false); recovery.current = null; setNotice('已读取最新草稿并保留吸附输入；旧位点和候选选择已清除，请重新应用并生成。');
    } catch (e) { if (live.current) setError(errorText(e)); }
    finally { if (live.current) { setBusy(''); onBusy(false); } }
  }
  const imported = editor.kind === 'xyz' ? parseXYZ(editor.content) : editor.kind === 'co_example' ? parseXYZ(coXYZ) : [{ element: editor.element, xyz: [0, 0, 0] as Vec3 }];
  const anchorOptions = typeof imported === 'string' ? [] : imported.map((atom, i) => ({ value: String(i), label: `#${i + 1} ${atom.element} · ${vector(atom.xyz)} Å` }));
  const visiblePreview = candidatesCurrent && !blocked && geometryMatches && geometry.data;
  const overlayMatchesRawAtoms = !state?.site_surface_atoms || state.site_surface_atoms.every(atom => { const original = surface?.snapshot.atoms.find(a => a.atom_id === atom.atom_id); return !!original && atom.cartesian.every((n, i) => Math.abs(n - original.cartesian[i]) < 1e-6); });
  return <div className="cat-adsorption" data-testid="cat-adsorption">
    <Card title="吸附物与锚点" extra={<Tag>每候选一个吸附物</Tag>}>
      <Typography.Paragraph type="secondary">沿用已保存的清洁表面和约束。选择一个原子，或导入一个 XYZ 分子；锚点是用于放置的指定原子。</Typography.Paragraph>
      {upstreamDirty && <Alert type="info" title="清洁表面或草稿名称有未应用输入，请先保存／生成／应用约束。" />}
      {recovered && !staleRecovery && <Alert type="info" title="已恢复本机尚未应用的吸附输入" />}
      {recovered && staleRecovery && <Alert type="warning" title="发现旧 revision 的吸附输入，未自动套用" action={<Button size="small" onClick={() => { const old = recovery.current; if (old) edit({ ...old.editor, siteIds: [], candidateIds: [] }); setRecovered(false); recovery.current = null; }}>恢复旧吸附参数</Button>} />}
      {storageFailed && <Alert type="warning" title="浏览器无法保存吸附输入，请在离开前应用。" />}
      <div className="cat-controls"><label>吸附物类型<Select aria-label="吸附物类型" value={editor.kind} onChange={kind => edit({ kind, anchor: '0', siteIds: [], candidateIds: [] })} options={[{ value: 'co_example', label: 'CO 合成示例' }, { value: 'atom', label: '单原子' }, { value: 'xyz', label: 'XYZ 分子' }]} /></label>{editor.kind === 'atom' && <label>元素符号<Input aria-label="吸附原子元素" value={editor.element} maxLength={2} onChange={e => edit({ element: e.target.value, anchor: '0', siteIds: [], candidateIds: [] })} placeholder="例如 H" /></label>}<label>锚定原子<Select aria-label="锚定原子" value={editor.anchor} options={anchorOptions} disabled={!anchorOptions.length} onChange={anchor => edit({ anchor })} /></label></div>
      {editor.kind === 'xyz' && <><label className="cat-file-picker">导入单构型 XYZ · 最多 128 原子、64 KiB<input aria-label="选择 XYZ 分子文件" type="file" accept=".xyz" onChange={e => void importXYZ(e.target.files?.[0])} /></label><label className="cat-label">XYZ 内容<Input.TextArea aria-label="XYZ 分子内容" value={editor.content} rows={5} onChange={e => edit({ content: e.target.value, sourceName: '', anchor: '0', siteIds: [], candidateIds: [] })} placeholder={coXYZ} /></label>{editor.sourceName && <p className="cat-note">来源文件：{editor.sourceName}；内容已保存在草稿，重开无需原文件。</p>}</>}
      {editor.kind === 'co_example' && <p className="cat-note">C 为默认锚点；O 沿 +n，合成键长 1.15 Å。仅用于几何演示。</p>}
      {typeof imported !== 'string' && <details className="cat-molecule-preview"><summary>吸附物局部坐标 · {imported.length} 原子 · Å</summary><div className="cat-molecule-atoms">{imported.map((atom, i) => <span key={i}>#{i + 1} {atom.element}{String(i) === editor.anchor ? ' · 锚点' : ''}<code>{vector(atom.xyz)}</code></span>)}</div></details>}
      {typeof source === 'string' && <Alert type="warning" title={source} />}
      <Button disabled={blocked || typeof source === 'string' || !sourceDirty} onClick={() => { if (typeof source !== 'string') void action('保存吸附物', 'source', () => catalysisApi.adsorbate(doc, source.source, source.anchor)); }}>应用并保存吸附物</Button>
      <div className="cat-save-status" role="status">{busy ? <Space><Spin size="small" />{busy}…</Space> : notice || (pending ? '有尚未应用的吸附输入，候选导出已暂停。' : state?.adsorbate ? '吸附配置已保存。' : '请应用所选吸附物。')}</div>
      {error && <Alert type={conflict ? 'warning' : 'error'} title={conflict ? '草稿 revision 已变化，吸附输入已保留' : '吸附操作失败，已保存快照保持可恢复'} description={error} />}
      {conflict && <Button disabled={!!busy} onClick={() => void refreshAfterConflict()}>保留输入并读取最新草稿</Button>}
    </Card>
    <Card title="放置参数">
      <Typography.Paragraph type="secondary">锚点位于所选表面位置 + 高度 × n。x 沿 a，z 沿外法向 n，y = z × x；XYZ 坐标映射到此正交参考系，不自动推断分子主轴。</Typography.Paragraph>
      <div className="cat-controls">{([['height', '初始锚点高度 (Å)'], ['rx', '绕固定 X 旋转 (°)'], ['ry', '绕固定 Y 旋转 (°)'], ['rz', '绕固定 Z 旋转 (°)'], ['screening', '近距离提示阈值 (Å)']] as const).map(([key, label]) => <label key={key}>{label}<Input aria-label={label} inputMode="decimal" value={editor[key]} onChange={e => edit({ [key]: e.target.value })} /></label>)}</div>
      <p className="cat-note">绕锚点依次旋转 X → Y → Z，均为固定表面轴。0 Å 阈值关闭距离提示；重合原子仍会被拒绝。阈值仅用于几何检查。</p>
      {state?.frame && <details className="cat-frame"><summary>当前表面参考系</summary><p>x：{vector(state.frame.x)}<br />y：{vector(state.frame.y)}<br />z：{vector(state.frame.z)}</p></details>}
      {typeof placement === 'string' && <Alert type="warning" title={placement} />}
    </Card>
    <Card title="几何位点与独立候选">
      <Typography.Paragraph type="secondary">自动候选按顶层几何识别顶位、桥位和空位；手动位置 u、v 是沿 a、b 的面内分数坐标，基准为最高原子核平面。</Typography.Paragraph>
      <Space wrap>{(['ontop', 'bridge', 'hollow'] as const).map(kind => <Checkbox key={kind} checked={editor.kinds.includes(kind)} onChange={() => edit({ kinds: editor.kinds.includes(kind) ? editor.kinds.filter(k => k !== kind) : [...editor.kinds, kind] })}>{kindLabels[kind]}</Checkbox>)}<Checkbox checked={editor.manualEnabled} onChange={e => edit({ manualEnabled: e.target.checked })}>添加手动位置</Checkbox></Space>
      <div className="cat-controls">{editor.manualEnabled && <><label>手动 u<Input aria-label="手动位置 u" value={editor.u} inputMode="decimal" onChange={e => edit({ u: e.target.value })} /></label><label>手动 v<Input aria-label="手动位置 v" value={editor.v} inputMode="decimal" onChange={e => edit({ v: e.target.value })} /></label><label>手动位置名称<Input aria-label="手动位置名称" value={editor.manualLabel} maxLength={80} onChange={e => edit({ manualLabel: e.target.value })} placeholder="例如 自定义位置" /></label></>}<label>位点去重容差 (Å)<Input aria-label="位点去重容差 (Å)" value={editor.tolerance} inputMode="decimal" onChange={e => edit({ tolerance: e.target.value })} /></label></div>
      {typeof siteSettings === 'string' && <Alert type="warning" title={siteSettings} />}
      <Button disabled={blocked || sourceDirty || typeof siteSettings === 'string'} onClick={() => { if (typeof siteSettings !== 'string') void action('生成几何位点', 'sites', () => catalysisApi.sites(doc, siteSettings)); }}>生成／更新几何位点</Button>
      {surface && visibleSites.length > 0 ? <><SiteMap surface={surface} sites={visibleSites} selected={editor.siteIds} disabled={blocked} origin={state?.site_map_origin_cartesian ?? undefined} topAtoms={state?.site_surface_atoms} onToggle={id => edit({ siteIds: toggle(editor.siteIds, id) })} /><p className="cat-note">平面图按建位点时的周期展开显示顶层原子与位置；不修改原始结构坐标。</p>{surfaceGeometry && overlayMatchesRawAtoms && <><CrystalGeometryViewer data={surfaceGeometry} markers={selectedSiteMarkers} /><p className="cat-note">{selectedSiteMarkers.length ? `三维预览仅显示已选 ${selectedSiteMarkers.length} 个位点，全部位点请在平面图或列表中选择。` : '三维预览仅显示已选 0 个位点，请先在平面图或列表中选择位点。'}</p></>}<div className="cat-site-list" aria-label="位点选择">{visibleSites.map((site, i) => <Checkbox key={site.site_id} checked={editor.siteIds.includes(site.site_id)} disabled={blocked || (!editor.siteIds.includes(site.site_id) && editor.siteIds.length >= 16)} onChange={() => edit({ siteIds: toggle(editor.siteIds, site.site_id) })}><strong>S{i + 1} · {kindLabels[site.kind]}</strong><small>{site.label} · u/v {vector(site.fractional.slice(0, 2))} · xyz {vector(site.cartesian)} Å</small></Checkbox>)}</div></> : <Empty className="cat-compact-empty" image={Empty.PRESENTED_IMAGE_SIMPLE} description={sourceDirty ? '先保存吸附物，再生成几何位点。' : siteSettingsDirty || upstreamDirty ? '参数尚未应用，旧位点预览已暂停。' : '生成位点后可在平面图中选择。'} />}
      <Space wrap><Button type="primary" disabled={blocked || sourceDirty || siteSettingsDirty || !sitesMatch || typeof placement === 'string' || editor.siteIds.length < 1 || editor.siteIds.length > 16 || editor.siteIds.some(id => !visibleSites.some(site => site.site_id === id))} onClick={() => { if (typeof placement !== 'string') void action('生成吸附候选', 'candidates', () => catalysisApi.candidates(doc, editor.siteIds, placement)); }}>生成 {editor.siteIds.length} 个独立候选</Button><span className="cat-note">最多 16 个位点；每个候选只放置一个吸附物。</span></Space>
      <p className="cat-note">位点是几何候选，未作能量排序；空位不默认标为 fcc／hcp。较大表面受原子数量与候选总量限制。</p>
    </Card>
    <Card title="候选预览与多选导出">
      {!!validCandidates.length && !candidatesCurrent && <Alert type="info" title="吸附或上游输入尚未应用，旧候选预览与导出已暂停。" />}
      {(state?.candidates ?? []).length ? <div className="cat-candidate-list">{state!.candidates.map((candidate, i) => { const valid = validCandidates.some(c => c.candidate_id === candidate.candidate_id); return <div key={candidate.candidate_id} className={`cat-candidate-row${chosen?.candidate_id === candidate.candidate_id ? ' cat-candidate-active' : ''}`}><Checkbox aria-label={`导出候选 ${i + 1}`} checked={editor.candidateIds.includes(candidate.candidate_id)} disabled={blocked || !valid || !candidatesCurrent} onChange={() => edit({ candidateIds: toggle(editor.candidateIds, candidate.candidate_id) })} /><div><strong>候选 {i + 1} · {candidate.label}</strong><small>{candidate.snapshot.atoms.length} 原子 · 高度 {candidate.placement.height_angstrom} Å · 旋转 {vector(candidate.placement.rotation_degrees)}°</small>{valid ? <small>距表面最近 {candidate.validation.minimum_adsorbate_surface_distance_angstrom.toFixed(3)} Å · {candidate.validation.screening_passed ? '几何筛查通过' : '有近距离提示'}</small> : <small>已失效：{candidate.invalidation_reason ?? '源表面或吸附物已改变，请重新生成。'}</small>}</div><Button size="small" disabled={blocked || !valid || !candidatesCurrent} onClick={() => setPreviewId(candidate.candidate_id)}>预览候选 {i + 1}</Button></div>; })}</div> : <Empty className="cat-compact-empty" image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未生成吸附候选。" />}
      {chosen && candidatesCurrent && !blocked && (geometry.isPending ? <Spin /> : geometry.error ? <Alert type="warning" title="候选预览读取失败" description={errorText(geometry.error)} action={<Button onClick={() => void geometry.refetch()}>重试候选预览</Button>} /> : visiblePreview && <div data-testid="cat-candidate-geometry" data-revision={doc.revision} data-candidate-id={chosen.candidate_id}><CrystalGeometryViewer data={visiblePreview.geometry as StructureGeometry} />{chosen.validation.warnings.map(w => <Alert key={w} type="warning" title={w} />)}</div>)}
      <Space wrap><Button disabled={blocked || !candidatesCurrent || !selectionDirty} onClick={() => void action('保存候选选择', 'selection', () => catalysisApi.selection(doc, editor.candidateIds))}>保存候选导出选择</Button><Button type="primary" disabled={!canExport} onClick={() => void exportZip()}>导出所选 {editor.candidateIds.length} 个候选 ZIP</Button></Space>
      {selectionDirty && <p className="cat-note">候选导出选择尚未保存。保存后可导出所选结构。</p>}
      <p className="cat-note">ZIP 包含各候选 POSCAR、元数据和来源清单；表面原有约束保留，新增吸附物自由。几何构建与筛查不代表吸附稳定、松弛或能量验证。</p>
      {chosen && <><CatalysisWorkflowHandoff doc={doc} target={{ candidate_id: chosen.candidate_id }} snapshot={chosen.snapshot}
        identity={`${doc.draft_id}:${doc.revision}:${chosen.candidate_id}:${version.current}`} disabled={!visiblePreview || geometry.isFetching || !!geometry.error || selectionDirty}
        label="将当前预览候选传入 Workflow" onConflict={() => { setConflict(true); setError('草稿 revision 或候选已变化，请读取最新草稿后重新预览并确认。'); }} /><p className="cat-note">仅传入当前预览的一个候选；上方多选用于 ZIP 导出。</p></>}
    </Card>
  </div>;
}
