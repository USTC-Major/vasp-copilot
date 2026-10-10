import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Card, Empty, Input, Space, Spin, Tag, Typography } from 'antd';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useSearchParams } from 'react-router-dom';
import { ApiError } from '../api/client';
import { energyApi, type EnergyCollection, type EnergyGroup } from '../api/energy';
import EnergyImports, { type LocalEnergyFile } from '../components/energy/EnergyImports';
import EnergyTaskSource from '../components/energy/EnergyTaskSource';
import EnergyConfirmationTable from '../components/energy/EnergyConfirmationTable';
import EnergyGroups from '../components/energy/EnergyGroups';
import EnergyResults from '../components/energy/EnergyResults';
import { calculationIssues, configurationFromDraft, draftFromCollection, energyError, invalidateConfirmations, newEnergyGroup, riskRequired, type EnergyDraft, type EnergyRowDraft } from '../components/energy/energyDraft';
import { energyDraftSessions as sessions, type EnergyEditor as Editor } from '../components/energy/energyDraftSessions';
import './energy.css';

export default function EnergyPage() {
  const [params, setParams] = useSearchParams();
  const id = params.get('collection') ?? '';
  const client = useQueryClient();
  const [editor, setEditor] = useState<Editor>();
  const current = useRef<Editor | undefined>(undefined);
  const [newTitle, setNewTitle] = useState('');
  const [busy, setBusy] = useState(false);
  const working = useRef(false);
  const [error, setError] = useState('');
  const [status, setStatus] = useState('');
  const [sourceMode, setSourceMode] = useState<'local' | 'task'>('local');
  const [taskSourceMounted, setTaskSourceMounted] = useState(false);
  const listing = useQuery({ queryKey: ['energy-collections'], queryFn: ({ signal }) => energyApi.list(signal), retry: false, refetchOnWindowFocus: false });
  const detail = useQuery({ queryKey: ['energy-collection', id], queryFn: ({ signal }) => energyApi.get(id, signal), enabled: !!id, retry: false, refetchOnWindowFocus: false });
  function apply(next: Editor | undefined) { current.current = next; setEditor(next); if (next) sessions.set(next.collection.id, next); }
  function installed(collection: EnergyCollection) {
    apply({ collection, draft: draftFromCollection(collection), dirty: false, conflict: false });
    client.setQueryData<{ collections: EnergyCollection[] }>(['energy-collections'], previous => ({ collections: [...(previous?.collections ?? []).filter(item => item.id !== collection.id), collection].sort((a, b) => b.updated_at.localeCompare(a.updated_at)) }));
  }
  useEffect(() => {
    if (current.current?.collection.id !== id) { apply(sessions.get(id)); setError(''); setStatus(''); }
  }, [id]);
  useEffect(() => {
    const server = detail.data?.collection;
    if (!server || server.id !== id) return;
    const cached = current.current?.collection.id === server.id ? current.current : sessions.get(server.id);
    if (cached?.dirty) apply({ ...cached, conflict: cached.conflict || cached.collection.revision !== server.revision });
    else if (!cached || server.revision >= cached.collection.revision) apply({ collection: server, draft: draftFromCollection(server), dirty: false, conflict: false });
  }, [detail.data, id]);
  useEffect(() => {
    if (!editor?.dirty) return;
    const handler = (event: BeforeUnloadEvent) => { event.preventDefault(); };
    window.addEventListener('beforeunload', handler); return () => window.removeEventListener('beforeunload', handler);
  }, [editor?.dirty]);

  function edit(transform: (draft: EnergyDraft) => EnergyDraft, scientific = true) {
    const previous = current.current;
    if (!previous || working.current) return;
    const changed = transform(previous.draft);
    apply({ ...previous, draft: scientific ? invalidateConfirmations(changed) : changed, dirty: true }); setStatus(''); setError('');
  }
  function rowChange(sampleId: string, patch: Partial<EnergyRowDraft>, scientific = true) {
    edit(draft => ({ ...draft, rows: draft.rows.map(row => row.sample_id === sampleId ? { ...row, ...patch } : row), groups: patch.role !== undefined ? draft.groups.map(group => {
      if (group.kind === 'adsorption') return { ...group, clean_sample_id: group.clean_sample_id === sampleId && patch.role !== 'clean_slab' ? '' : group.clean_sample_id, adsorbate_sample_id: group.adsorbate_sample_id === sampleId && patch.role !== 'adsorbate' ? '' : group.adsorbate_sample_id, targets: patch.role === 'adsorbed' ? group.targets : group.targets.filter(target => target.sample_id !== sampleId) };
      return { ...group, element_references: Object.fromEntries(Object.entries(group.element_references).filter(([, referenceId]) => referenceId !== sampleId || patch.role === 'element_reference')), targets: patch.role === 'material' ? group.targets : group.targets.filter(target => target.sample_id !== sampleId) };
    }) : draft.groups }), scientific);
  }
  function assign(sampleId: string, groupIds: string[]) {
    edit(draft => ({ ...draft, groups: draft.groups.map(group => {
      const targets = group.targets.filter(target => target.sample_id !== sampleId);
      if (!groupIds.includes(group.id)) return { ...group, targets } as EnergyGroup;
      if (group.kind === 'adsorption') { const existing = group.targets.find(target => target.sample_id === sampleId); return { ...group, targets: [...targets, existing ?? { sample_id: sampleId, adsorbate_count: 1 }] } as EnergyGroup; }
      return { ...group, targets: [...targets, { sample_id: sampleId }] };
    }) }));
  }
  function failed(cause: unknown) {
    if (cause instanceof ApiError && cause.code === 'ENERGY_REVISION_CONFLICT') {
      const previous = current.current; if (previous) apply({ ...previous, dirty: true, conflict: true });
      setError(`${energyError(cause)} 本地输入已保留；请读取最新版本后明确选择重新保存。`);
    } else setError(energyError(cause));
  }
  async function persist(): Promise<EnergyCollection> {
    const previous = current.current;
    if (!previous) throw new Error('先创建或打开比较集。');
    if (previous.conflict) throw new Error('比较集存在版本冲突。本地输入已保留，请先读取最新版本并重新核对。');
    if (!previous.dirty) return previous.collection;
    const response = await energyApi.save(previous.collection.id, configurationFromDraft(previous.collection, previous.draft));
    installed(response.collection); return response.collection;
  }
  async function mutate(work: (collection: EnergyCollection) => Promise<{ collection: EnergyCollection }>, success: string): Promise<boolean> {
    if (working.current) return false;
    working.current = true; setBusy(true); setError(''); setStatus('');
    try { const collection = await persist(); const response = await work(collection); installed(response.collection); setStatus(success); return true; }
    catch (cause) { failed(cause); return false; }
    finally { working.current = false; setBusy(false); }
  }
  async function create() {
    if (working.current) return;
    working.current = true; setBusy(true); setError('');
    try { const response = await energyApi.create(newTitle.trim() || '新的能量比较'); installed(response.collection); setParams({ collection: response.collection.id }); setNewTitle(''); setStatus('比较集已创建，可以先保存草稿，再确认计算。'); }
    catch (cause) { failed(cause); } finally { working.current = false; setBusy(false); }
  }
  async function saveDraft() {
    if (working.current) return;
    working.current = true; setBusy(true); setError(''); setStatus('');
    try { await persist(); setStatus('草稿已保存，未执行计算。'); } catch (cause) { failed(cause); } finally { working.current = false; setBusy(false); }
  }
  async function importFiles(files: LocalEnergyFile[], onImported: (id: string) => void) {
    if (working.current) return;
    working.current = true; setBusy(true); setError(''); setStatus('');
    let completed = 0;
    try {
      let collection = await persist();
      for (const item of files) {
        setStatus(`正在读取 ${completed + 1}/${files.length}：${item.relativePath || item.file.name}`);
        const response = await energyApi.upload(collection, item.file, item.relativePath);
        collection = response.collection; installed(collection); onImported(item.id); completed++;
      }
      setStatus(`已读取 ${completed} 份来源，推荐角色仍需人工核对。`);
    } catch (cause) { failed(cause); setStatus(`已导入 ${completed} 份；未成功的文件保留，可修正后继续。`); }
    finally { working.current = false; setBusy(false); }
  }
  async function calculate() {
    if (!current.current || working.current) return;
    const issues = calculationIssues(current.current.collection, current.current.draft);
    if (issues.length) { setError(issues.slice(0, 6).join(' ')); return; }
    await mutate(collection => energyApi.calculate(collection), '已按当前确认输入计算并保存。所有状态标记保留在结果与导出中。');
  }
  async function reload(keepDraft: boolean) {
    if (!current.current || working.current) return;
    working.current = true; setBusy(true); setError('');
    const previous = current.current;
    try {
      const response = await energyApi.get(previous.collection.id);
      if (keepDraft) {
        const serverDraft = draftFromCollection(response.collection);
        const rows = serverDraft.rows.map(row => previous.draft.rows.find(item => item.sample_id === row.sample_id) ?? row);
        apply({ collection: response.collection, draft: invalidateConfirmations({ ...previous.draft, rows }), dirty: true, conflict: false }); setStatus('已读取最新版本并保留本地输入；确认已清除，请重新核对后保存。');
      } else { installed(response.collection); setStatus('已重新载入保存的比较集。'); }
    } catch (cause) { failed(cause); } finally { working.current = false; setBusy(false); }
  }
  const active = editor?.collection.id === id ? editor : undefined;
  const issues = active ? calculationIssues(active.collection, active.draft) : [];
  return <div className="wf-page energy-page">
    <div className="wf-page-heading"><div><Typography.Title level={3}>基础能量</Typography.Title><p>批量读取、集中核对，再比较吸附能与材料形成能。所有参考由你明确确认。</p></div><Space wrap><Link to="/toolbox/postprocessing">DOS／能带后处理</Link><Tag>确定性计算 · eV</Tag></Space></div>
    <div className="energy-layout">
      <aside className="energy-sidebar">
        <Card title="新的比较集"><div className="energy-import-form"><label className="energy-field">比较名称<Input aria-label="新能量比较名称" value={newTitle} maxLength={120} disabled={busy} placeholder="例如 CO 位点能量比较" onChange={event => setNewTitle(event.target.value)} /></label><Button type="primary" disabled={busy} onClick={() => void create()}>创建能量比较集</Button><p className="energy-note">来源读取、草稿保存与科学计算分开进行，无需创建计算任务或配置 AI。</p></div></Card>
        <Card title="已保存比较集" extra={<Button size="small" type="text" disabled={busy} onClick={() => void listing.refetch()}>刷新列表</Button>}>
          {listing.isLoading ? <Spin /> : <ul className="energy-history">{listing.data?.collections.map(collection => <li key={collection.id}><button disabled={busy} aria-current={collection.id === id} onClick={() => setParams({ collection: collection.id })}>{collection.title}<small>{collection.samples.length} 个样本 · {collection.groups.length} 个比较组 · {collection.result ? '已计算' : '草稿'}</small></button></li>)}</ul>}
          {listing.error && <Alert type="error" title={energyError(listing.error)} />}
          {listing.data?.collections.length === 0 && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚无能量比较集" />}
        </Card>
      </aside>
      <div className="energy-main">
        {error && <Alert showIcon type="error" title={error} />}
        {detail.error && <Alert type="error" title={energyError(detail.error)} action={<Button onClick={() => void detail.refetch()}>重试打开</Button>} />}
        {id && !active && detail.isFetching && <Spin />}
        {!active && !detail.isFetching && <Card><Empty description="创建一个比较集，或重开已保存记录。" /></Card>}
        {active && <>
          <div className="energy-toolbar"><label className="energy-field" style={{ flex: 1 }}>比较集名称<Input aria-label="当前能量比较名称" value={active.draft.title} disabled={busy} onChange={event => edit(draft => ({ ...draft, title: event.target.value }), false)} /></label><Space wrap><Tag>{active.dirty ? '存在未保存输入' : '已保存'} · 修订 {active.collection.revision}</Tag><Button disabled={busy || !active.dirty || active.conflict} onClick={() => void saveDraft()}>保存草稿</Button></Space></div>
          {active.conflict && <Alert type="warning" title="保存版本已变化，本地未保存输入保留，计算与导出已暂停。" action={<Space wrap><Button disabled={busy} onClick={() => void reload(true)}>读取最新并保留本地输入</Button><Button disabled={busy} onClick={() => void reload(false)}>重新载入已保存版本</Button></Space>} />}
          <Card title="1 · 导入能量来源" extra={<Space wrap><Button size="small" type={sourceMode === 'local' ? 'primary' : 'default'} disabled={busy} onClick={() => setSourceMode('local')}>本地／手填／CSV</Button><Button size="small" type={sourceMode === 'task' ? 'primary' : 'default'} disabled={busy} onClick={() => { setTaskSourceMounted(true); setSourceMode('task'); }}>已有任务／缓存</Button></Space>}>
            <div hidden={sourceMode !== 'local'}><EnergyImports collection={active.collection} disabled={busy || active.conflict} onFiles={importFiles} onManual={input => mutate(collection => energyApi.addManual(collection, input), '手填样本已加入，状态保留未知。')} onCsv={file => mutate(collection => energyApi.importCsv(collection, file), 'CSV 样本已加入，逐项核对角色与状态。')} /></div>
            {taskSourceMounted && <div hidden={sourceMode !== 'task'}><EnergyTaskSource disabled={busy || active.conflict} collections={listing.data?.collections ?? []} onImport={previewId => mutate(collection => energyApi.importTask(collection, previewId), '稳定任务快照已缓存，等待人工确认。')} onReuse={(sourceCollectionId, sampleId) => mutate(collection => energyApi.reuse(collection, sourceCollectionId, sampleId), '已离线复用缓存；新样本仍需确认。')} /></div>}
          </Card>
          <Card title="2 · 集中核对样本">
            {active.collection.samples.length ? <EnergyConfirmationTable collection={active.collection} draft={active.draft} disabled={busy || active.conflict} onRow={rowChange} onAssign={assign} onIncludeAll={included => edit(draft => ({ ...draft, rows: draft.rows.map(row => ({ ...row, included })) }))} onConfirmAll={() => edit(draft => ({ ...draft, rows: draft.rows.map(row => ({ ...row, confirmed: row.included && !!row.role })), groups: draft.groups.map(group => ({ ...group, basis_confirmed: true })) }), false)} onAcceptRisks={accepted => edit(draft => ({ ...draft, rows: draft.rows.map(row => { const sample = active.collection.samples.find(item => item.id === row.sample_id)!; return row.included && riskRequired(sample) ? { ...row, accepted_warnings: accepted } : row; }) }), false)} /> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="导入来源后，在同一张表核对组成、能量、角色及状态。" />}
          </Card>
          <Card title="3 · 参考定义与比较组"><EnergyGroups collection={active.collection} draft={active.draft} disabled={busy || active.conflict} onGroup={(group, scientific = true) => edit(draft => ({ ...draft, groups: draft.groups.map(item => item.id === group.id ? group : item) }), scientific)} onAdd={kind => edit(draft => ({ ...draft, groups: [...draft.groups, newEnergyGroup(kind, draft.groups.length + 1)] }))} onRemove={groupId => edit(draft => ({ ...draft, groups: draft.groups.filter(group => group.id !== groupId) }))} />
            <div className="energy-review-footer"><div className="energy-save-status" role="status">{status || (active.dirty ? '输入尚未保存；旧结果已隐藏。' : '草稿与来源已保存，可继续核对。')}</div><Space wrap><Button disabled={busy || !active.dirty || active.conflict} onClick={() => void saveDraft()}>仅保存草稿</Button><Button type="primary" loading={busy} disabled={busy || active.conflict || !!issues.length} onClick={() => void calculate()}>保存并计算能量</Button></Space></div>
            {!!issues.length && <details className="energy-details"><summary>计算前待处理 {issues.length} 项</summary><ul>{issues.map(issue => <li key={issue}>{issue}</li>)}</ul></details>}
          </Card>
          <Card title="4 · 结果与导出"><EnergyResults collection={active.collection} valid={!active.dirty && !active.conflict && !busy && !!active.collection.result} /></Card>
        </>}
      </div>
    </div>
  </div>;
}
