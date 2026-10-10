import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Card, Empty, Input, Modal, Radio, Space, Spin, Tag, Typography } from 'antd';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useSearchParams } from 'react-router-dom';
import { ApiError } from '../api/client';
import { energyApi, type EnergyAnalysisKind, type EnergyBasis, type EnergyCollection, type EnergyRemoval, type EnergyRemovalRequest, type EnergySampleValueSource } from '../api/energy';
import EnergyImports, { type LocalEnergyFile } from '../components/energy/EnergyImports';
import EnergyTaskSource from '../components/energy/EnergyTaskSource';
import EnergyConfirmationTable from '../components/energy/EnergyConfirmationTable';
import EnergyGroups from '../components/energy/EnergyGroups';
import EnergyResults from '../components/energy/EnergyResults';
import EnergySampleExport from '../components/energy/EnergySampleExport';
import { analysisKind, analysisLabels, calculationDraftKey, calculationIssues, configurationFromDraft, confirmedDraft, draftFromCollection, energyError, legacyReadOnly, organizeTargets, reconcileConfirmations, removalImpact, riskRequired, saveEnergyBlob, type EnergyDraft, type EnergyRowDraft } from '../components/energy/energyDraft';
import { energyDraftSessions as sessions, type EnergyEditor as Editor } from '../components/energy/energyDraftSessions';
import './energy.css';

export default function EnergyPage() {
  const [params, setParams] = useSearchParams();
  const id = params.get('collection') ?? '';
  const client = useQueryClient();
  const [editor, setEditor] = useState<Editor>();
  const current = useRef<Editor | undefined>(undefined);
  const [newTitle, setNewTitle] = useState('');
  const [newKind, setNewKind] = useState<EnergyAnalysisKind>();
  const [busy, setBusy] = useState(false);
  const working = useRef(false);
  const [error, setError] = useState('');
  const [status, setStatus] = useState('');
  const [sourceMode, setSourceMode] = useState<'local' | 'task'>('local');
  const [taskSourceMounted, setTaskSourceMounted] = useState(false);
  const [selection, setSelection] = useState<{ collectionId: string; ids: string[] }>({ collectionId: '', ids: [] });
  const [removal, setRemoval] = useState<{ collection: EnergyCollection; request: EnergyRemovalRequest; preview: EnergyRemoval; error?: string }>();
  const listing = useQuery({ queryKey: ['energy-collections'], queryFn: ({ signal }) => energyApi.list(signal), retry: false, refetchOnWindowFocus: false });
  const detail = useQuery({ queryKey: ['energy-collection', id], queryFn: ({ signal }) => energyApi.get(id, signal), enabled: !!id, retry: false, refetchOnWindowFocus: false });
  function apply(next: Editor | undefined) { current.current = next; setEditor(next); if (next) sessions.set(next.collection.id, next); }
  function installed(collection: EnergyCollection) {
    const draft = draftFromCollection(collection);
    const previous = current.current;
    apply({ collection, draft, dirty: draft.groups.length !== collection.groups.length, conflict: false, resultExpired: !collection.result && previous?.collection.id === collection.id && (previous.resultExpired || !!previous.collection.result) });
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
    else if (!cached || server.revision >= cached.collection.revision) { const draft = draftFromCollection(server); apply({ collection: server, draft, dirty: draft.groups.length !== server.groups.length, conflict: false, resultExpired: cached?.resultExpired && !server.result }); }
  }, [detail.data, id]);
  useEffect(() => {
    if (!editor?.dirty) return;
    const handler = (event: BeforeUnloadEvent) => { event.preventDefault(); };
    window.addEventListener('beforeunload', handler); return () => window.removeEventListener('beforeunload', handler);
  }, [editor?.dirty]);

  function edit(transform: (draft: EnergyDraft) => EnergyDraft, scientific = true) {
    const previous = current.current;
    if (!previous || working.current || previous.collection.locked || legacyReadOnly(previous.collection)) return;
    const transformed = transform(previous.draft);
    const changed = scientific ? organizeTargets(transformed) : transformed;
    const draft = scientific ? reconcileConfirmations(previous.collection, previous.draft, changed) : changed;
    const changedScience = calculationDraftKey(previous.collection, previous.draft) !== calculationDraftKey(previous.collection, draft);
    apply({ ...previous, draft, dirty: true, resultExpired: previous.resultExpired || (!!previous.collection.result && changedScience) }); setStatus(''); setError('');
  }
  function rowChange(sampleId: string, patch: Partial<EnergyRowDraft>, scientific = true) {
    edit(draft => ({ ...draft, rows: draft.rows.map(row => row.sample_id === sampleId ? { ...row, ...patch } : row) }), scientific);
  }
  function failed(cause: unknown) {
    if (cause instanceof ApiError && cause.code === 'ENERGY_REVISION_CONFLICT') {
      const previous = current.current; if (previous) apply({ ...previous, dirty: true, conflict: true });
      setError(`${energyError(cause)} 本地输入已保留；请读取最新版本后明确选择重新保存。`);
    } else setError(energyError(cause));
  }
  async function persist(): Promise<EnergyCollection> {
    const previous = current.current;
    if (!previous) throw new Error('先创建或打开分析记录。');
    if (previous.conflict) throw new Error('分析记录存在版本冲突。本地输入已保留，请先读取最新版本并重新核对。');
    if (!previous.dirty) return previous.collection;
    const response = await energyApi.save(previous.collection.id, configurationFromDraft(previous.collection, previous.draft));
    installed(response.collection); return response.collection;
  }
  async function mutate(work: (collection: EnergyCollection) => Promise<{ collection: EnergyCollection }>, success: string, autofill = false): Promise<boolean> {
    if (working.current) return false;
    working.current = true; setBusy(true); setError(''); setStatus('');
    let imported = false;
    try { const collection = await persist(); const response = await work(collection); installed(response.collection); imported = true; if (autofill) { const assigned = await energyApi.autofill(response.collection); installed(assigned.collection); } setStatus(success); return true; }
    catch (cause) { failed(cause); if (autofill && imported) setStatus('来源已加入，规则分配尚未完成，可重试更新规则分配。'); return autofill && imported; }
    finally { working.current = false; setBusy(false); }
  }
  async function create() {
    if (working.current || !newKind) return;
    working.current = true; setBusy(true); setError('');
    try { const response = await energyApi.create(newTitle.trim() || `新的${analysisLabels[newKind]}分析`, newKind); installed(response.collection); setParams({ collection: response.collection.id }); setNewTitle(''); setStatus('分析类型已确定，可导入多份来源并配置参考。更换类型请创建另一份分析，当前记录保留。'); }
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
      const assigned = await energyApi.autofill(collection); installed(assigned.collection);
      setStatus(`已读取 ${completed} 份来源，明确角色与参考已按规则填入。请处理待核对项并独立接受风险。`);
    } catch (cause) { failed(cause); setStatus(completed === files.length ? `已导入 ${completed} 份；规则分配尚未完成，可重试更新规则分配。` : `已导入 ${completed} 份；未成功的文件保留，可修正后继续。`); }
    finally { working.current = false; setBusy(false); }
  }
  async function calculate() {
    if (!current.current || working.current || !current.current.collection.locked) return;
    const issues = calculationIssues(current.current.collection, current.current.draft);
    if (issues.length) { setError(issues.slice(0, 6).join(' ')); return; }
    await mutate(collection => energyApi.calculate(collection), '已按当前确认输入计算并保存。所有状态标记保留在结果与导出中。');
  }
  async function confirmAndLock() {
    const previous = current.current;
    if (!previous || working.current || previous.collection.locked || legacyReadOnly(previous.collection)) return;
    const pending = calculationIssues(previous.collection, confirmedDraft(previous.draft));
    if (pending.length) { setError(pending.slice(0, 6).join(' ')); return; }
    working.current = true; setBusy(true); setError(''); setStatus('');
    try {
      let collection = await persist();
      const draft = confirmedDraft(draftFromCollection(collection));
      const issues = calculationIssues(collection, draft);
      if (issues.length) throw new Error(issues.slice(0, 6).join(' '));
      const saved = await energyApi.save(collection.id, configurationFromDraft(collection, draft));
      collection = saved.collection; installed(collection);
      const response = await energyApi.lock(collection); installed(response.collection);
      setStatus('当前分析已确认并锁定。可计算和导出；需要修改时先解锁。');
    } catch (cause) { failed(cause); }
    finally { working.current = false; setBusy(false); }
  }
  async function unlock() {
    const previous = current.current;
    if (!previous || working.current || !previous.collection.locked) return;
    working.current = true; setBusy(true); setError(''); setStatus('');
    try {
      const response = await energyApi.unlock(previous.collection);
      if (previous.dirty) apply({ ...previous, collection: response.collection, conflict: false }); else installed(response.collection);
      setStatus('已解锁编辑，已有核对与风险接受保留。科学输入变更后仅相关核对失效，旧结果过期。');
    } catch (cause) { failed(cause); }
    finally { working.current = false; setBusy(false); }
  }
  async function copyAnalysis(kind: EnergyAnalysisKind, groupId: string) {
    const previous = current.current;
    if (!previous || working.current) return;
    working.current = true; setBusy(true); setError(''); setStatus('');
    try { const response = await energyApi.copy(previous.collection, kind, groupId); installed(response.collection); setParams({ collection: response.collection.id }); setStatus('已复制为新的单类型分析，原记录保留。新分析需重新核对并锁定。'); }
    catch (cause) { failed(cause); }
    finally { working.current = false; setBusy(false); }
  }
  async function previewRemoval(input: EnergyRemovalRequest) {
    const previous = current.current;
    if (!previous || working.current || previous.collection.locked || legacyReadOnly(previous.collection)) return;
    working.current = true; setBusy(true); setError(''); setStatus('');
    try {
      const collection = await persist();
      const response = await energyApi.previewRemoval(collection, input);
      setRemoval({ collection, request: input, preview: response.removal });
      setStatus('当前草稿已保存并读取删除影响，尚未删除任何样本。');
    } catch (cause) { failed(cause); }
    finally { working.current = false; setBusy(false); }
  }
  async function previewCsv(file: File, basis?: EnergyBasis) {
    if (working.current) throw new Error('当前分析正在操作，请稍后重新预览。');
    working.current = true; setBusy(true); setError(''); setStatus('');
    try { const collection = await persist(); const response = await energyApi.previewCsv(collection, file, basis); return { preview: response.preview, revision: collection.revision }; }
    catch (cause) { failed(cause); throw cause; }
    finally { working.current = false; setBusy(false); }
  }
  async function importPreviewedCsv(file: File, basis: EnergyBasis | undefined, revision: number) {
    const previous = current.current;
    if (!previous || previous.dirty || previous.collection.revision !== revision) { setError('CSV 预览已失效，请保存当前输入后重新预览。'); return false; }
    return mutate(collection => energyApi.importCsv(collection, file, basis), 'CSV 已按预览导入并完成规则分配；来源状态为未知，请重新核对与接受风险。', true);
  }
  async function exportSamples(basis: EnergyBasis, source: EnergySampleValueSource) {
    if (working.current) throw new Error('当前分析正在操作，请稍后重新下载。');
    working.current = true; setBusy(true); setError(''); setStatus('');
    try { const collection = await persist(); const blob = await energyApi.downloadSamples(collection, basis, source); saveEnergyBlob(blob, `${collection.id}.samples.${source}.csv`); setStatus(`已导出${source === 'effective' ? '包含人工修订的有效值' : '原始值'}样本表，可作为新的 CSV 来源回导。`); }
    catch (cause) { failed(cause); throw cause; }
    finally { working.current = false; setBusy(false); }
  }
  async function performRemoval() {
    const preview = removal;
    const previous = current.current;
    if (!preview || !previous || working.current) return;
    if (previous.collection.id !== preview.collection.id || previous.collection.revision !== preview.collection.revision || previous.dirty || previous.collection.locked || previous.conflict) {
      setRemoval({ ...preview, error: '分析版本或输入已变化，样本尚未删除。请取消后重新读取删除影响。' }); return;
    }
    working.current = true; setBusy(true); setError(''); setStatus('');
    try {
      const response = await energyApi.removeSamples(preview.collection, preview.request);
      installed(response.collection);
      setSelection(selected => selected.collectionId === response.collection.id ? { ...selected, ids: selected.ids.filter(id => response.collection.samples.some(sample => sample.id === id)) } : selected);
      setRemoval(undefined); setStatus(`已从当前分析移除 ${response.removal.removed_count} 个样本。原始文件、任务来源和其他分析记录保留；缺少的参考需要明确配置。`);
    } catch (cause) { failed(cause); setRemoval({ ...preview, error: `${energyError(cause)} 样本与管理选择保留，未完成删除。` }); }
    finally { working.current = false; setBusy(false); }
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
        const sourceChecked = reconcileConfirmations(previous.collection, previous.draft, { ...previous.draft, rows }, response.collection);
        const draft = reconcileConfirmations(response.collection, serverDraft, sourceChecked);
        apply({ collection: response.collection, draft, dirty: true, conflict: false, resultExpired: previous.resultExpired || calculationDraftKey(response.collection, serverDraft) !== calculationDraftKey(response.collection, draft) }); setStatus(response.collection.locked ? '已读取服务器锁定状态并保留本地输入；先解锁才能继续编辑与保存。' : '已读取最新版本并保留本地输入；只需重新核对受影响项。');
      } else { installed(response.collection); setStatus('已重新载入保存的比较集。'); }
    } catch (cause) { failed(cause); } finally { working.current = false; setBusy(false); }
  }
  const active = editor?.collection.id === id ? editor : undefined;
  const issues = active ? calculationIssues(active.collection, active.draft) : [];
  const pendingIssues = active ? calculationIssues(active.collection, confirmedDraft(active.draft)) : [];
  const readOnly = !!active && legacyReadOnly(active.collection);
  const editingDisabled = busy || !!active?.conflict || !!active?.collection.locked || readOnly;
  const kind = active ? analysisKind(active.collection) : null;
  const selectedIds = active && selection.collectionId === active.collection.id ? selection.ids.filter(id => active.draft.rows.some(row => row.sample_id === id)) : [];
  const assignmentReport = active?.collection.assignment_report;
  const referenceImpact = removal ? removalImpact(draftFromCollection(removal.collection), removal.preview.sample_ids) : [];
  const remainingAffectedTargetIds = removal && !removal.request.clear_all ? removal.preview.affected_target_ids.filter(sampleId => !removal.preview.sample_ids.includes(sampleId)) : [];
  return <div className="wf-page energy-page">
    <div className="wf-page-heading"><div><Typography.Title level={3}>基础能量</Typography.Title><p>先选分析类型，再导入、配置参考、核对并锁定。每份分析支持多个目标。</p></div><Space wrap><Link to="/toolbox/postprocessing">DOS／能带后处理</Link><Tag>确定性计算 · eV</Tag></Space></div>
    <div className="energy-layout">
      <aside className="energy-sidebar">
        <Card title="新建分析 · 先选类型"><div className="energy-import-form"><Radio.Group aria-label="新分析类型" className="energy-type-options" value={newKind} disabled={busy} onChange={event => setNewKind(event.target.value)}><Radio value="adsorption">吸附能</Radio><Radio value="formation">材料形成能</Radio></Radio.Group><label className="energy-field">分析名称<Input aria-label="新能量比较名称" value={newTitle} maxLength={120} disabled={busy} placeholder="例如 CO 位点能量比较" onChange={event => setNewTitle(event.target.value)} /></label><Button type="primary" disabled={busy || !newKind} onClick={() => void create()}>创建分析并导入数据</Button><p className="energy-note">每份分析使用一种类型和一套参考条件。更换类型或参考条件可新建另一份分析，已有记录保留。</p></div></Card>
        <Card title="已保存分析" extra={<Button size="small" type="text" disabled={busy} onClick={() => void listing.refetch()}>刷新列表</Button>}>
          {listing.isLoading ? <Spin /> : <ul className="energy-history">{listing.data?.collections.map(collection => <li key={collection.id}><button disabled={busy} aria-current={collection.id === id} onClick={() => setParams({ collection: collection.id })}>{collection.title}<small>{analysisKind(collection) ? analysisLabels[analysisKind(collection)!] : '旧混合类型'} · {collection.samples.length} 个样本 · {collection.locked ? '已锁定' : collection.result ? '已有结果' : '草稿'}{collection.groups.length > 1 ? ` · ${collection.groups.length} 套旧参考` : ''}</small></button></li>)}</ul>}
          {listing.error && <Alert type="error" title={energyError(listing.error)} />}
          {listing.data?.collections.length === 0 && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚无能量分析" />}
        </Card>
      </aside>
      <div className="energy-main">
        {error && <Alert showIcon type="error" title={error} />}
        {detail.error && <Alert type="error" title={energyError(detail.error)} action={<Button onClick={() => void detail.refetch()}>重试打开</Button>} />}
        {id && !active && detail.isFetching && <Spin />}
        {!active && !detail.isFetching && <Card><Empty description="先选择吸附能或材料形成能创建分析，或打开已保存记录。" /></Card>}
        {active && <>
          <div className="energy-toolbar"><label className="energy-field" style={{ flex: 1 }}>分析名称<Input aria-label="当前能量比较名称" value={active.draft.title} disabled={editingDisabled} onChange={event => edit(draft => ({ ...draft, title: event.target.value }), false)} /></label><Space wrap><Tag color={active.collection.locked ? 'green' : undefined}>{active.collection.locked ? '已确认并锁定' : '编辑中'}</Tag><Tag>{kind ? analysisLabels[kind] : '旧混合类型'}</Tag><Tag>{active.dirty ? '存在未保存输入' : '已保存'} · 修订 {active.collection.revision}</Tag><Button disabled={editingDisabled || !active.dirty} onClick={() => void saveDraft()}>保存草稿</Button>{active.collection.locked && !readOnly && <Button disabled={busy || active.conflict} onClick={() => void unlock()}>解锁编辑</Button>}</Space></div>
          {active.collection.locked && <Alert type="success" title="当前分析已锁定，来源、角色、参考与计量不能修改。" description="结果可继续计算与导出。解锁本身保留已有核对及风险接受；后续修改只使受影响项重新核对，并使旧结果过期。" />}
          {readOnly && <Alert type="info" title="旧分析完整保留，当前以只读方式打开。" description={<div>已有组、来源与结果均保留。选择一套参考条件复制为新的单类型分析，原记录不被覆盖。<div className="energy-legacy-copies">{active.collection.groups.map(group => <div key={group.id}><span>{group.name} · {analysisLabels[group.kind]} · {group.targets.length} 个目标</span><Button disabled={busy || active.conflict || active.dirty} onClick={() => void copyAnalysis(group.kind, group.id)}>复制为{analysisLabels[group.kind]}分析</Button></div>)}</div></div>} />}
          {active.resultExpired && <Alert type="warning" title="科学输入已变化，旧结果已过期。" description="已有无关核对与风险接受保留。完成受影响项核对，重新确认锁定并计算后才能导出当前结果。" />}
          {active.conflict && <Alert type="warning" title="保存版本已变化，本地未保存输入保留，计算与导出已暂停。" action={<Space wrap><Button disabled={busy} onClick={() => void reload(true)}>读取最新并保留本地输入</Button><Button disabled={busy} onClick={() => void reload(false)}>重新载入已保存版本</Button></Space>} />}
          <Card title="1 · 导入能量来源" extra={<Space wrap><Button size="small" type={sourceMode === 'local' ? 'primary' : 'default'} disabled={busy} onClick={() => setSourceMode('local')}>本地／手填／CSV</Button><Button size="small" type={sourceMode === 'task' ? 'primary' : 'default'} disabled={busy} onClick={() => { setTaskSourceMounted(true); setSourceMode('task'); }}>已有任务／缓存</Button></Space>}>
            <div hidden={sourceMode !== 'local'}><EnergyImports collection={active.collection} disabled={editingDisabled} draftDirty={active.dirty} onFiles={importFiles} onManual={input => mutate(collection => energyApi.addManual(collection, input), '手填样本已加入并完成规则分配，状态保留未知。', true)} onCsvPreview={previewCsv} onCsv={importPreviewedCsv} /></div>
            {taskSourceMounted && <div hidden={sourceMode !== 'task'}><EnergyTaskSource disabled={editingDisabled} collections={listing.data?.collections ?? []} onImport={previewId => mutate(collection => energyApi.importTask(collection, previewId), '稳定任务快照已缓存并完成规则分配，等待人工确认。', true)} onReuse={(sourceCollectionId, sampleId) => mutate(collection => energyApi.reuse(collection, sourceCollectionId, sampleId), '已离线复用缓存并完成规则分配；新样本仍需确认。', true)} /></div>}
          </Card>
          <Card title="2 · 集中核对样本">
            {!!active.collection.samples.length && !readOnly && <section className="energy-assignment-review" aria-label="规则分配与待处理歧义"><Alert type={assignmentReport?.state === 'ambiguous' || assignmentReport?.issues.length ? 'warning' : 'info'} title={assignmentReport?.issues.length ? `待处理歧义与缺口 · ${assignmentReport.issues.length} 项` : assignmentReport?.state === 'ready' ? '明确角色与参考已填入，等待科学核对' : assignmentReport ? '待处理角色与参考关系' : '规则分配尚未更新'} description={<><p>人工指定的角色、排除和参考会保留。吸附单元数按后端返回的规则依据填写；请核对参考文件代表的单元，规则不证明参考态正确，也不会代替风险接受。</p>{assignmentReport?.issues.length ? <ul>{assignmentReport.issues.map((issue, index) => <li key={`${issue.code}-${index}`}><strong>{issue.sample_ids.map(sampleId => active.draft.rows.find(row => row.sample_id === sampleId)?.name ?? sampleId).join('、') || '参考与目标关系'}</strong>：{issue.message}</li>)}</ul> : <p>在表中修正角色、纳入状态及参考配置后，可更新规则分配。</p>}</>} action={<Button disabled={editingDisabled} onClick={() => void mutate(collection => energyApi.autofill(collection), '规则分配已更新，人工指定与排除保留。')}>更新规则分配</Button>} /></section>}
            {active.collection.samples.length ? <EnergyConfirmationTable collection={active.collection} draft={active.draft} disabled={editingDisabled} selectedIds={selectedIds} onSelection={ids => setSelection({ collectionId: active.collection.id, ids })} onDelete={() => void previewRemoval({ sample_ids: selectedIds })} onClear={() => void previewRemoval({ clear_all: true })} onRow={rowChange} onIncludeAll={included => edit(draft => ({ ...draft, rows: draft.rows.map(row => ({ ...row, included, included_origin: 'manual' })) }))} onAcceptRisks={accepted => edit(draft => ({ ...draft, rows: draft.rows.map(row => { const sample = active.collection.samples.find(item => item.id === row.sample_id)!; return row.included && riskRequired(sample) ? { ...row, accepted_warnings: accepted } : row; }) }), false)} /> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="导入来源后，在同一张表核对组成、能量、角色及状态。" />}
            {!!active.collection.samples.length && <EnergySampleExport key={active.collection.id} initialBasis={active.draft.groups[0]?.energy_basis ?? 'sigma_to_zero_ev'} dirty={active.dirty} disabled={busy || active.conflict || (active.dirty && (!!active.collection.locked || readOnly))} onExport={exportSamples} />}
          </Card>
          <Card title={`3 · ${kind ? analysisLabels[kind] : '旧分析'}参考与目标`}><EnergyGroups collection={active.collection} draft={active.draft} disabled={editingDisabled} onGroup={(group, scientific = true) => edit(draft => ({ ...draft, groups: draft.groups.map(item => item.id === group.id ? group : item) }), scientific)} />
            {!readOnly && <div className="energy-review-footer"><div className="energy-save-status" role="status">{status || (active.collection.locked ? '当前分析已锁定，可计算与导出。' : active.dirty ? '输入尚未保存；请完成核对后确认并锁定。' : '草稿与来源已保存，可继续核对。')}</div><Space wrap><Button disabled={editingDisabled || !active.dirty} onClick={() => void saveDraft()}>仅保存草稿</Button><Button type="primary" loading={busy && !active.collection.locked} disabled={editingDisabled || !!pendingIssues.length} onClick={() => void confirmAndLock()}>确认并锁定当前分析</Button><Button type="primary" loading={busy && !!active.collection.locked} disabled={busy || active.conflict || !active.collection.locked || !!issues.length || active.dirty} onClick={() => void calculate()}>计算能量</Button></Space></div>}
            {!readOnly && !!pendingIssues.length && <details className="energy-details"><summary>确认锁定前待处理 {pendingIssues.length} 项</summary><ul>{pendingIssues.map(issue => <li key={issue}>{issue}</li>)}</ul></details>}
          </Card>
          <Card title="4 · 结果与导出"><EnergyResults collection={active.collection} expired={active.resultExpired} valid={!active.dirty && !active.conflict && !busy && !!active.collection.result && (active.collection.legacy_mode !== false || !!active.collection.locked)} /></Card>
        </>}
      </div>
    </div>
    <Modal open={!!removal} destroyOnHidden title={removal?.request.clear_all ? '确认清空当前分析样本' : '确认删除所选样本'} okText={removal?.request.clear_all ? '确认清空样本' : '确认删除样本'} cancelText="取消删除" okButtonProps={{ danger: true }} confirmLoading={busy} closable={!busy} mask={{ closable: !busy }} cancelButtonProps={{ disabled: busy }} onCancel={() => { if (!busy) { setRemoval(undefined); setStatus('已取消删除，当前分析样本与关联保留。'); } }} onOk={() => void performRemoval()}>
      {removal && <><p>将从“{removal.collection.title}”移除 <strong>{removal.preview.removed_count}</strong> 个样本。当前草稿已保存；取消将保留全部样本。</p><ul>{removal.preview.sample_ids.map(sampleId => <li key={sampleId}>{removal.collection.samples.find(sample => sample.id === sampleId)?.name ?? sampleId}</li>)}</ul>{!!referenceImpact.length && <p>将清除以下参考关联：{[...new Set(referenceImpact.map(impact => impact.name))].join('、')}。</p>}{!!remainingAffectedTargetIds.length && <Alert type="warning" title={`${remainingAffectedTargetIds.length} 个保留目标将缺少参考，相关计算会被阻止。`} description={remainingAffectedTargetIds.map(sampleId => removal.collection.samples.find(sample => sample.id === sampleId)?.name ?? sampleId).join('、')} />}<p className="energy-note">只移除当前分析中的样本与关联。原始 OUTCAR、任务来源和其他分析记录保留；不会自动改绑另一份参考。</p>{removal.error && <Alert type="error" title={removal.error} />}</>}
    </Modal>
  </div>;
}
