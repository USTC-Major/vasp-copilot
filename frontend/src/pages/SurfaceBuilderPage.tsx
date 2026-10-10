import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Card, Checkbox, Empty, Input, Select, Space, Spin, Tag, Typography } from 'antd';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import { catalysisApi } from '../api/catalysis';
import { ApiError } from '../api/client';
import { CrystalGeometryViewer } from '../components/structure/CrystalViewer';
import type { CatalysisDraft, CatalysisSource } from '../types/catalysis';
import { activeSurface, editorFor, parsedParameters, readRecovery, same, storeRecovery, type SurfaceEditor, type SurfaceFields } from './surfaceDraftState';
import './surface-builder.css';

const errorText = (e: unknown) => e instanceof Error ? e.message : '操作失败，请重试。';
const fieldLabels: Record<keyof SurfaceFields, string> = { h: '晶面 h', k: '晶面 k', l: '晶面 l', thickness: '最小 slab 厚度 (Å)', vacuum: '最小真空 (Å)', nx: '面内 a 倍数', ny: '面内 b 倍数', tolerance: '分层容差 (Å)' };
const download = (blob: Blob, filename: string) => { const url = URL.createObjectURL(blob); const a = document.createElement('a'); a.href = url; a.download = filename; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); };

function DraftEditor({ initial, onUpdate }: { initial: CatalysisDraft; onUpdate: (doc: CatalysisDraft) => void }) {
  const [doc, setDoc] = useState(initial);
  const recovery = useRef(readRecovery(initial));
  const [editor, setEditor] = useState<SurfaceEditor>(() => recovery.current?.revision === initial.revision ? recovery.current.editor : editorFor(initial));
  const [recovered, setRecovered] = useState(!!recovery.current);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [conflict, setConflict] = useState(false);
  const [notice, setNotice] = useState('');
  const [storageFailed, setStorageFailed] = useState(false);
  const version = useRef(0);
  const live = useRef(true);
  useEffect(() => { live.current = true; return () => { live.current = false; }; }, []);
  const saved = editorFor(doc);
  const paramsDirty = !same(editor.fields, saved.fields);
  const constraintsDirty = editor.bottom !== saved.bottom || !same(editor.overrides, saved.overrides) || editor.reset !== saved.reset;
  const dirty = paramsDirty || constraintsDirty || editor.name !== doc.name;
  const nameError = !editor.name.trim() ? '草稿名称不能为空。' : '';
  const staleRecovery = recovery.current && recovery.current.revision !== initial.revision;
  const surface = activeSurface(doc);
  const parameters = parsedParameters(editor.fields);
  const bottom = editor.bottom.trim() ? Number(editor.bottom) : NaN;
  const constraintsError = !Number.isInteger(bottom) || bottom < 0 || bottom > (surface?.surface.layers.length ?? 0) ? '固定层数须为 0 到当前总层数的整数。' : '';
  useEffect(() => { if (!staleRecovery || !recovered) setStorageFailed(!storeRecovery(doc, editor, dirty)); }, [doc, editor, dirty, staleRecovery, recovered]);
  useEffect(() => {
    if (initial.revision <= doc.revision) return;
    if (dirty) { setConflict(true); setError('服务器上已有更新，载入最新草稿会放弃本页输入。'); }
    else { setDoc(initial); setEditor(editorFor(initial)); }
  }, [initial, doc.revision, dirty]);
  useEffect(() => {
    const before = (event: BeforeUnloadEvent) => { if (dirty) { event.preventDefault(); event.returnValue = ''; } };
    window.addEventListener('beforeunload', before); return () => window.removeEventListener('beforeunload', before);
  }, [dirty]);
  const geometry = useQuery({ queryKey: ['cat-geometry', doc.draft_id, doc.revision, surface?.surface_id], queryFn: ({ signal }) => catalysisApi.geometry(doc.draft_id, surface!.surface_id, doc.revision, signal), enabled: !!surface && !paramsDirty && !constraintsDirty && !busy, retry: false });
  const edit = (patch: Partial<SurfaceEditor>) => { version.current++; setEditor(previous => ({ ...previous, ...patch })); setNotice(''); };
  const update = (next: CatalysisDraft) => { setDoc(previous => next.revision >= previous.revision ? next : previous); onUpdate(next); };
  async function action(label: string, work: () => Promise<{ draft: CatalysisDraft }>, mode: 'save' | 'build' | 'constraints' | 'selection') {
    const atVersion = version.current; setBusy(label); setError(''); setNotice('');
    try {
      const next = (await work()).draft;
      if (!live.current) { onUpdate(next); return; }
      update(next); setConflict(false);
      if (atVersion === version.current) {
        const restored = editorFor(next);
        setEditor(previous => mode === 'save' ? (paramsDirty ? restored : { ...previous, name: restored.name, fields: restored.fields }) : mode === 'selection' ? restored : { ...restored, name: previous.name });
        setRecovered(false); recovery.current = null;
        setNotice(mode === 'build' ? '表面已生成并保存。' : mode === 'constraints' ? '约束已应用并保存。' : '草稿已保存。');
      } else {
        if (mode === 'build' || mode === 'selection' || (mode === 'save' && paramsDirty)) {
          const restored = editorFor(next);
          // Atom exceptions belong to the applied snapshot, while typed name
          // and parameter edits remain local if they changed during the call.
          setEditor(previous => ({ ...previous, bottom: restored.bottom, overrides: restored.overrides, reset: restored.reset }));
        }
        setNotice('请求已完成，期间的新输入已保留；请重新应用后预览或导出。');
      }
    } catch (e) { if (live.current) { setError(errorText(e)); setConflict(e instanceof ApiError && (e.status === 409 || e.code === 'CAT_REVISION_CONFLICT')); } }
    finally { if (live.current) setBusy(''); }
  }
  async function loadLatest() {
    setBusy('载入最新草稿'); setError('');
    try { const next = (await catalysisApi.get(doc.draft_id)).draft; if (!live.current) return; update(next); setEditor(editorFor(next)); version.current++; setConflict(false); setRecovered(false); recovery.current = null; setNotice('已载入最新草稿。'); }
    catch (e) { setError(errorText(e)); } finally { if (live.current) setBusy(''); }
  }
  async function exportZip() {
    if (!surface || dirty || busy || conflict || geometry.isFetching || geometry.data?.revision !== doc.revision || geometry.data.surface_id !== surface.surface_id) return;
    setBusy('准备导出'); setError(''); const atVersion = version.current;
    try { const blob = await catalysisApi.export(doc, surface.surface_id); if (live.current && atVersion === version.current) { download(blob, `${doc.name || 'surface'}-${doc.draft_id}.zip`); setNotice('已导出 POSCAR 与建模元数据。'); } else if (live.current) setNotice('导出期间输入已变化，已取消旧结构下载。'); }
    catch (e) { if (live.current) { setError(errorText(e)); setConflict(e instanceof ApiError && e.status === 409); } } finally { if (live.current) setBusy(''); }
  }
  const geometryMatches = !!surface && geometry.data?.revision === doc.revision && geometry.data.surface_id === surface.surface_id;
  const ready = !!surface && !dirty && !busy && !conflict && geometryMatches && !geometry.isFetching && !geometry.error;
  return <div className="cat-editor">
    <Card title="建模草稿" extra={<Tag>revision {doc.revision}</Tag>}>
      <label className="cat-label">草稿名称<Input aria-label="草稿名称" value={editor.name} maxLength={120} onChange={e => edit({ name: e.target.value })} /></label>
      <div className="cat-source"><Tag>{doc.source.role === 'bulk' ? '体相来源' : '已有 slab'}</Tag><span>{doc.input_snapshot.atoms.length} 个输入原子 · {doc.input_snapshot.coordinate_mode === 'direct' ? 'Direct' : 'Cartesian'}</span><small>{doc.source.name ?? (doc.source.kind === 'example' ? '合成理想 fcc Pt，a = 3.92 Å' : '本地结构')}</small></div>
      {recovered && !staleRecovery && <Alert type="info" title="已恢复本机尚未保存的输入" description="这些输入尚未应用到已保存快照；完成保存或重新生成后即可导出。" />}
      {staleRecovery && recovered && <Alert type="warning" title="发现旧 revision 的本机输入" description="服务器草稿已更新，旧输入未自动套用。可恢复旧参数后审阅并重新生成；逐原子例外将不恢复。" action={<Button size="small" onClick={() => { const old = recovery.current; if (old) edit({ name: old.editor.name, fields: old.editor.fields }); setRecovered(false); recovery.current = null; }}>恢复旧参数</Button>} />}
      {storageFailed && <Alert type="warning" title="浏览器无法保存未提交输入，请在离开前保存草稿。" />}
      {nameError && <Alert type="warning" title={nameError} />}
      <Space wrap><Button disabled={!!busy || conflict || !dirty || !!nameError || constraintsDirty || (doc.source.role === 'bulk' && typeof parameters === 'string')} onClick={() => void action('保存草稿', () => catalysisApi.save(doc, { name: editor.name.trim(), ...(doc.source.role === 'bulk' && paramsDirty && typeof parameters !== 'string' ? { parameters } : {}) }), 'save')}>保存草稿</Button><Button disabled={!!busy || (!dirty && !conflict)} onClick={() => void loadLatest()}>放弃本页输入并载入最新</Button></Space>
      <div className="cat-save-status" role="status">{busy ? <Space><Spin size="small" />{busy}…</Space> : <>{notice && <span>{notice}</span>}{dirty ? <span> 有尚未应用的输入，导出已暂停。</span> : !notice && '已保存，可跨页面或关闭后恢复。'}</>}</div>
      {error && <Alert type={conflict ? 'warning' : 'error'} title={conflict ? '草稿 revision 已变化，本页输入已保留' : '操作失败，本页输入已保留'} description={error} />}
      {doc.warnings.map(w => <Alert key={w} type="warning" title={w} showIcon />)}
    </Card>
    {doc.source.role === 'bulk' && <Card title="晶面与表面尺寸">
      <Typography.Paragraph type="secondary">hkl 相对于导入的输入晶胞。保留该晶胞定义，不隐式转为常规胞或原胞。</Typography.Paragraph>
      <div className="cat-controls">{(Object.keys(fieldLabels) as (keyof SurfaceFields)[]).map(key => <label key={key}>{fieldLabels[key]}<Input aria-label={fieldLabels[key]} inputMode={['h', 'k', 'l', 'nx', 'ny'].includes(key) ? 'numeric' : 'decimal'} value={editor.fields[key]} onChange={e => edit({ fields: { ...editor.fields, [key]: e.target.value } })} /></label>)}</div>
      {typeof parameters === 'string' && <Alert type="warning" title={parameters} />}
      <Button type="primary" disabled={!!busy || conflict || typeof parameters === 'string'} onClick={() => { if (typeof parameters !== 'string') void action('生成表面', () => catalysisApi.build(doc, parameters), 'build'); }}>{surface ? '重新生成表面' : '生成表面'}</Button>
      <Typography.Paragraph className="cat-note" type="secondary">厚度和真空是生成下限，实际原子核跨度及周期空隙由离散晶层决定。重新生成会替换终止面和其约束。</Typography.Paragraph>
    </Card>}
    {surface && <Card title="终止面与实际几何">
      <label className="cat-label">终止面<Select aria-label="终止面" value={surface.surface_id} disabled={!!busy || dirty || conflict} options={doc.surfaces.map((s, i) => ({ value: s.surface_id, label: `${doc.source.role === 'slab' ? '导入表面' : `终止面 ${i + 1}`} · ${s.termination_shift === null ? '原始晶胞' : `shift ${s.termination_shift.toFixed(4)}`} · ${s.snapshot.atoms.length} 原子` }))} onChange={active_surface_id => void action('保存终止面', () => catalysisApi.save(doc, { active_surface_id }), 'selection')} /></label>
      <dl className="cat-metrics"><div><dt>实际原子核跨度</dt><dd>{surface.surface.actual_nuclei_span_angstrom.toFixed(4)} Å</dd></div><div><dt>周期无核空隙</dt><dd>{surface.surface.periodic_vacuum_gap_angstrom.toFixed(4)} Å</dd></div><div><dt>法向周期</dt><dd>{surface.surface.normal_period_angstrom.toFixed(4)} Å</dd></div><div><dt>实际面内尺寸</dt><dd>{surface.surface.in_plane_lengths_angstrom.map(v => v.toFixed(4)).join(' × ')} Å</dd></div></dl>
      {doc.parameters && <Typography.Paragraph type="secondary">已应用的生成下限：slab {doc.parameters.min_slab_size} Å，真空 {doc.parameters.min_vacuum_size} Å。</Typography.Paragraph>}
      <Typography.Paragraph type="secondary">法向 n = a × b 归一化：{surface.surface.normal.map(v => v.toFixed(4)).join(' / ')}。底层按沿 n 的投影分组；无核空隙是周期几何量。</Typography.Paragraph>
      {paramsDirty || constraintsDirty || busy ? <Alert type="info" title="输入尚未应用，结构预览与导出已暂停。" /> : geometry.isPending ? <Spin /> : geometry.error ? <Alert type="warning" title="结构预览读取失败" description={errorText(geometry.error)} action={<Button onClick={() => void geometry.refetch()}>重试预览</Button>} /> : geometryMatches && geometry.data && <div data-testid="cat-geometry" data-revision={doc.revision}><CrystalGeometryViewer data={geometry.data.geometry} /></div>}
    </Card>}
    {surface && <Card title="固定层与逐原子例外">
      <Typography.Paragraph type="secondary">按 a × b 的法向，从底部起固定。FFF 为全固定，TTT 为全自由；混合 T/F 按直接晶格 a/b/c 基矢解释，与屏幕 x/y/z 无关。</Typography.Paragraph>
      <div className="cat-controls"><label>底部固定层数<Input aria-label="底部固定层数" inputMode="numeric" value={editor.bottom} disabled={!!busy || paramsDirty || conflict} onChange={e => edit({ bottom: e.target.value })} /></label><label>分层信息<span>{surface.surface.layers.length} 层 · 容差 {surface.surface.layer_tolerance} Å</span></label></div>
      <Checkbox checked={editor.reset} disabled={!!busy || paramsDirty || conflict} onChange={e => edit({ reset: e.target.checked })}>先释放所有已有约束，再应用本页固定层和例外</Checkbox>
      <div className="cat-layer-list">{surface.surface.layers.map((layer, layerNumber) => <details key={layer.layer_index}><summary>底部第 {layerNumber + 1} 层 · {layer.atom_ids.length} 原子 · 投影 {layer.projection_angstrom.toFixed(4)} Å</summary><div className="cat-atom-list">{layer.atom_ids.map(atomId => { const index = surface.snapshot.atoms.findIndex(a => a.atom_id === atomId); const atom = surface.snapshot.atoms[index]; return atom && <label key={atomId}><span title={`稳定原子 ID: ${atomId}`}>#{index + 1} {atom.element} · {atom.selective_dynamics.map(f => f ? 'T' : 'F').join('')}</span><Select aria-label={`原子 ${index + 1} ${atom.element} 约束例外`} value={editor.overrides[atomId] ?? 'inherit'} disabled={!!busy || paramsDirty || conflict} options={[{ value: 'inherit', label: '跟随层／原有约束' }, { value: 'fixed', label: '例外：固定 FFF' }, { value: 'free', label: '例外：自由 TTT' }]} onChange={(value: 'fixed' | 'free' | 'inherit') => { const overrides = { ...editor.overrides }; if (value === 'inherit') delete overrides[atomId]; else overrides[atomId] = value; edit({ overrides }); }} /></label>; })}</div></details>)}</div>
      {constraintsError && <Alert type="warning" title={constraintsError} />}
      <Button disabled={!!busy || paramsDirty || conflict || !!constraintsError || !constraintsDirty} onClick={() => void action('应用约束', () => catalysisApi.constraints(doc, { bottom_fixed_layers: bottom, atom_overrides: editor.overrides, reset_existing: editor.reset }), 'constraints')}>应用并保存约束</Button>
    </Card>}
    <Card title="独立导出"><Space wrap><Button type="primary" disabled={!ready} onClick={() => void exportZip()}>导出 POSCAR + metadata ZIP</Button><span className="cat-note">{surface ? '保存当前输入并完成预览后可导出。' : '生成表面后可导出。'}</span></Space><Typography.Paragraph type="secondary" className="cat-note">几何生成不代表稳定性、松弛或收敛验证。极性、磁性、带电与重构表面仍须单独判断。本阶段提供表面建模与文件导出。</Typography.Paragraph></Card>
  </div>;
}

export default function SurfaceBuilderPage() {
  const client = useQueryClient();
  const [params, setParams] = useSearchParams();
  const id = params.get('draft') ?? '';
  const [name, setName] = useState('');
  const [role, setRole] = useState<'bulk' | 'slab'>('bulk');
  const [file, setFile] = useState<File>();
  const [structureId, setStructureId] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const list = useQuery({ queryKey: ['cat-drafts'], queryFn: ({ signal }) => catalysisApi.list(signal), retry: false });
  const current = useQuery({ queryKey: ['cat-draft', id], queryFn: ({ signal }) => catalysisApi.get(id, signal), enabled: !!id, retry: false });
  const update = (doc: CatalysisDraft) => { const previous = client.getQueryData<{ draft: CatalysisDraft }>(['cat-draft', doc.draft_id]); if (!previous || previous.draft.revision <= doc.revision) client.setQueryData(['cat-draft', doc.draft_id], { draft: doc }); void client.invalidateQueries({ queryKey: ['cat-drafts'] }); };
  async function create(example = false, existing = false) {
    setError('');
    if (existing && !structureId.trim()) { setError('请输入已有导入记录的 structure_id。'); return; }
    if (!example && !existing && (!file || !file.size || file.size > 2 * 1024 ** 2)) { setError('请选择非空的本地 POSCAR／CONTCAR／CIF，最多 2 MiB。'); return; }
    setBusy(true);
    try {
      const source: CatalysisSource = example ? { kind: 'example', role: 'bulk' } : existing ? { kind: 'structure_id', role, structure_id: structureId.trim() } : { kind: file!.name.toLowerCase().endsWith('.cif') ? 'cif' : 'poscar', role, content: await file!.text(), name: file!.name };
      const doc = (await catalysisApi.create(name, source)).draft; update(doc); setParams({ draft: doc.draft_id }); setFile(undefined); setName('');
    } catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  }
  return <div className="wf-page cat-page"><div className="wf-page-heading"><div><Typography.Title level={3}>表面构建</Typography.Title><p>从体相切出指定晶面，或导入已有 slab；选择终止面、设置约束并保存建模草稿。</p></div><Tag>Toolbox · 独立几何工具</Tag></div>
    <div className="cat-grid"><aside><Card title="结构来源"><div className="cat-controls cat-single"><label>新草稿名称<Input aria-label="新草稿名称" value={name} maxLength={120} disabled={busy} onChange={e => setName(e.target.value)} placeholder="例如 Pt(111) 清洁表面" /></label><label>输入用途<Select aria-label="输入用途" value={role} disabled={busy} onChange={setRole} options={[{ value: 'bulk', label: '体相：切出表面' }, { value: 'slab', label: '已有 slab：分层与约束' }]} /></label></div><label className="cat-file-picker">选择本地 POSCAR／CIF<input aria-label="选择本地 POSCAR／CIF" type="file" disabled={busy} onChange={e => { setFile(e.target.files?.[0]); e.target.value = ''; }} /></label>{file && <p className="cat-note">已选择 {file.name}</p>}<Space orientation="vertical" style={{ width: '100%' }}><Button block disabled={busy || !file} onClick={() => void create()} loading={busy}>导入并创建草稿</Button><Button block disabled={busy} onClick={() => void create(true)}>使用合成 Pt 示例</Button></Space><details className="cat-existing-source"><summary>使用已有结构记录</summary><label className="cat-label">已有结构 ID<Input aria-label="已有结构 ID" value={structureId} maxLength={100} disabled={busy} onChange={e => setStructureId(e.target.value)} placeholder="structure_id" /></label><Typography.Paragraph type="secondary" className="cat-note">从本机已有导入记录读取结构；输入用途沿用上方选择。</Typography.Paragraph><Button block disabled={busy || !structureId.trim()} onClick={() => void create(false, true)}>从已有记录创建草稿</Button></details><Typography.Paragraph type="secondary" className="cat-note">示例为理想 fcc Pt，a = 3.92 Å；仅用于演示几何，不是实验或 DFT 优化结构。CIF 输入不携带 POSCAR 逐原子约束。</Typography.Paragraph>{error && <Alert type="error" title={error} />}</Card>
    <Card title="已保存的草稿" extra={<Button size="small" disabled={list.isFetching} onClick={() => void list.refetch()}>刷新</Button>}>{list.isPending ? <Spin /> : list.error ? <Alert type="error" title={errorText(list.error)} /> : !list.data?.drafts.length ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有建模草稿" /> : <ul className="cat-history">{list.data.drafts.map(d => <li key={d.draft_id}><button aria-current={id === d.draft_id} onClick={() => setParams({ draft: d.draft_id })}>{d.name}<small>{d.source_role === 'bulk' ? '体相' : 'slab'} · {d.atom_count} 原子 · {d.surface_count} 终止面 · r{d.revision}</small><small>{new Date(d.updated_at).toLocaleString()}</small></button></li>)}</ul>}</Card></aside>
    <main aria-label="表面建模编辑区">{!id ? <Card><Empty description="选择已保存草稿，或导入结构开始建模。" /></Card> : current.isPending ? <Card><Spin /> 正在恢复草稿…</Card> : current.error ? <Card><Alert type="error" title="草稿恢复失败" description={errorText(current.error)} action={<Button onClick={() => void current.refetch()}>重试恢复</Button>} /></Card> : current.data && <DraftEditor key={id} initial={current.data.draft} onUpdate={update} />}</main></div>
  </div>;
}
