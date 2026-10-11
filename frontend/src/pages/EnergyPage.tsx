import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Button, Card, Checkbox, Drawer, Empty, Input, Modal, notification, Radio, Space, Spin, Tag, Typography } from 'antd';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useSearchParams } from 'react-router-dom';
import { ApiError } from '../api/client';
import { energyApi, type EnergyAnalysisKind, type EnergyBasis, type EnergyCollection, type EnergyFieldError, type EnergyGroup, type EnergyImpact, type EnergyRemoval, type EnergyRemovalRequest, type EnergySamplePatch, type EnergySampleValueSource } from '../api/energy';
import EnergyImports, { type LocalEnergyFile } from '../components/energy/EnergyImports';
import EnergyTaskSource from '../components/energy/EnergyTaskSource';
import EnergyConfirmationTable from '../components/energy/EnergyConfirmationTable';
import EnergyGroups from '../components/energy/EnergyGroups';
import EnergyResults from '../components/energy/EnergyResults';
import EnergySampleExport from '../components/energy/EnergySampleExport';
import { analysisKind, analysisLabels, draftFromCollection, energyError, newEnergyGroup, riskRequired, saveEnergyBlob, type EnergyDraft, type EnergyRowDraft } from '../components/energy/energyDraft';
import { cardConfiguration, cardKey, cardSampleIds, cardsReadOnly, cardScienceKey, collectionCards, energyFieldKey, quantityIssue, samplePatches, validateCard, validateSharedDraft } from '../components/energy/energyCards';
import { energyDraftSessions as sessions, type EnergyEditor as Editor } from '../components/energy/energyDraftSessions';
import './energy.css';

type SharedReview = { collection: EnergyCollection; impact: EnergyImpact; patches?: EnergySamplePatch[]; title?: string; request?: EnergyRemovalRequest; removal?: EnergyRemoval; error?: string; draftKey: string };
type ErrorNotice = { fields: Set<string>; show: (remaining: EnergyFieldError[]) => void };
const rowKey = (row: EnergyRowDraft | undefined) => JSON.stringify({ name: row?.name, override: row?.override });
const sharedKey = (draft: EnergyDraft) => JSON.stringify({ title: draft.title, rows: draft.rows.map(row => ({ id: row.sample_id, key: rowKey(row) })) });
function serverDraft(collection: EnergyCollection): EnergyDraft { return { ...draftFromCollection(collection), groups: structuredClone(collectionCards(collection)) }; }
function cardDirty(editor: Editor, cardId: string) { const draft = editor.draft.groups.find(card => card.id === cardId), saved = collectionCards(editor.collection).find(card => card.id === cardId); return !!draft && (!saved || cardKey(draft) !== cardKey(saved)); }
function sharedDirty(editor: Editor) { return sharedKey(editor.draft) !== sharedKey(serverDraft(editor.collection)); }
function anyDirty(editor: Editor) { return sharedDirty(editor) || editor.draft.groups.some(card => cardDirty(editor, card.id)); }
function referencedSampleDirty(editor: Editor, group: EnergyGroup) { const saved = serverDraft(editor.collection); return editor.draft.rows.some(row => cardSampleIds(group).includes(row.sample_id) && JSON.stringify(row.override) !== JSON.stringify(saved.rows.find(item => item.sample_id === row.sample_id)?.override)); }
const stateLabel = (card: EnergyGroup, dirty = false) => dirty ? '未保存草稿' : card.status === 'stale' ? '需重新确认' : card.result ? '有结果' : card.locked ? '已锁定' : card.confirmed ? '已确认 · 可编辑' : '草稿';

export default function EnergyPage() {
  const [params, setParams] = useSearchParams();
  const id = params.get('collection') ?? '';
  const routeId = useRef(id);
  routeId.current = id;
  const client = useQueryClient();
  const [editor, setEditor] = useState<Editor>();
  const current = useRef<Editor | undefined>(undefined);
  const mounted = useRef(false);
  const [newTitle, setNewTitle] = useState('');
  const [newKind, setNewKind] = useState<EnergyAnalysisKind>();
  const [newTaskOpen, setNewTaskOpen] = useState(false);
  const [renameTask, setRenameTask] = useState<EnergyCollection>();
  const [renameTitle, setRenameTitle] = useState('');
  const [deletingTask, setDeletingTask] = useState<EnergyCollection>();
  const [busy, setBusy] = useState(false);
  const working = useRef(false);
  const [actionError, setActionError] = useState('');
  const [errors, setErrors] = useState<EnergyFieldError[]>([]);
  const [status, setStatusState] = useState('');
  const operationTask = useRef<string | undefined>(undefined);
  const deletedTasks = useRef(new Set<string>());
  function setStatus(value: string) { if (operationTask.current === undefined || operationTask.current === routeId.current) setStatusState(value); }
  const [poolOpen, setPoolOpen] = useState(false);
  const [mobileCardsOpen, setMobileCardsOpen] = useState(false);
  const [sourceMode, setSourceMode] = useState<'local' | 'task'>('local');
  const [taskSourceMounted, setTaskSourceMounted] = useState(false);
  const [selection, setSelection] = useState<{ collectionId: string; ids: string[] }>({ collectionId: '', ids: [] });
  const [review, setReview] = useState<SharedReview>();
  const [deleteCardId, setDeleteCardId] = useState<string>();
  const [notice, noticeHolder] = notification.useNotification();
  const errorNotice = useRef<ErrorNotice | undefined>(undefined);
  const listing = useQuery({ queryKey: ['energy-collections'], queryFn: ({ signal }) => energyApi.list(signal), retry: false, refetchOnWindowFocus: false });
  const detail = useQuery({ queryKey: ['energy-collection', id], queryFn: ({ signal }) => energyApi.get(id, signal), enabled: !!id, retry: false, refetchOnWindowFocus: false });
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const apply = useCallback((next: Editor | undefined) => {
    if (next) { next = { ...next, dirty: anyDirty(next) }; sessions.set(next.collection.id, next); }
    current.current = next; setEditor(next);
  }, []);
  const installed = useCallback((collection: EnergyCollection, savedCards: string[] = [], savedShared = false, fresh = false, invalidatedCards: string[] = [], savedTitle = false) => {
    if (deletedTasks.current.has(collection.id)) return;
    collection = { ...collection, sample_count: collection.samples.length, card_count: collectionCards(collection).length };
    const previous = fresh ? undefined : current.current?.collection.id === collection.id ? current.current : sessions.get(collection.id);
    const draft = serverDraft(collection);
    if (previous) {
      if (!savedShared) {
        draft.title = savedTitle || previous.draft.title === previous.collection.title ? collection.title : previous.draft.title;
        const oldServer = serverDraft(previous.collection);
        draft.rows = draft.rows.map(row => { const local = previous.draft.rows.find(item => item.sample_id === row.sample_id), old = oldServer.rows.find(item => item.sample_id === row.sample_id); return local && old && rowKey(local) !== rowKey(old) ? { ...row, name: local.name, override: local.override } : row; });
      }
      draft.groups = draft.groups.map(card => { const local = previous.draft.groups.find(item => item.id === card.id); return local && !savedCards.includes(card.id) && cardDirty(previous, card.id) ? { ...card, ...cardConfiguration(local) } : card; });
    }
    const acceptedRisks = { ...previous?.acceptedRisks };
    for (const card of draft.groups) {
      const old = previous && collectionCards(previous.collection).find(item => item.id === card.id);
      const local = previous?.draft.groups.find(item => item.id === card.id);
      if (invalidatedCards.includes(card.id) || card.risk_accepted || local && cardScienceKey(local) !== cardScienceKey(card) || savedShared && (old?.confirmation_fingerprint !== card.confirmation_fingerprint || old?.risk_acceptance_fingerprint !== card.risk_acceptance_fingerprint)) delete acceptedRisks[card.id];
    }
    const next = { collection, draft, dirty: false, conflict: false, activeCardId: draft.groups.some(card => card.id === previous?.activeCardId) ? previous?.activeCardId : draft.groups[0]?.id, acceptedRisks };
    if (routeId.current === collection.id) apply(next);
    else sessions.set(collection.id, { ...next, dirty: anyDirty(next) });
    client.setQueryData(['energy-collection', collection.id], { mode: 'toolbox', collection });
    client.setQueryData<{ collections: EnergyCollection[] }>(['energy-collections'], old => ({ collections: [...(old?.collections ?? []).filter(item => item.id !== collection.id), collection].sort((a, b) => b.updated_at.localeCompare(a.updated_at)) }));
  }, [apply, client]);
  function openTask(collectionId: string) {
    routeId.current = collectionId;
    apply(collectionId ? sessions.get(collectionId) : undefined);
    setParams(collectionId ? { collection: collectionId } : {});
    setErrors([]); setActionError(''); setStatusState(''); setReview(undefined); setDeleteCardId(undefined); setPoolOpen(false); setMobileCardsOpen(false);
    errorNotice.current = undefined; notice.destroy('energy-operation-error');
  }
  useEffect(() => {
    if (current.current?.collection.id !== id) { apply(id ? sessions.get(id) : undefined); setErrors([]); setActionError(''); setStatusState(''); setReview(undefined); setDeleteCardId(undefined); setPoolOpen(false); setMobileCardsOpen(false); errorNotice.current = undefined; notice.destroy('energy-operation-error'); }
  }, [id, apply, notice]);
  useEffect(() => {
    const server = detail.data?.collection;
    if (!server || server.id !== id) return;
    const cached = current.current?.collection.id === server.id ? current.current : sessions.get(server.id);
    if (cached && server.revision < cached.collection.revision) return;
    if (cached?.dirty) apply({ ...cached, conflict: cached.conflict || cached.collection.revision !== server.revision });
    else if (!cached || server.revision > cached.collection.revision) installed(server);
  }, [detail.data, id, apply, installed]);
  useEffect(() => {
    if (id && detail.error instanceof ApiError && detail.error.status === 404) { sessions.delete(id); if (current.current?.collection.id === id) apply(undefined); }
  }, [detail.error, id, apply]);
  useEffect(() => {
    if (!editor?.dirty) return;
    const handler = (event: BeforeUnloadEvent) => { event.preventDefault(); };
    window.addEventListener('beforeunload', handler); return () => window.removeEventListener('beforeunload', handler);
  }, [editor?.dirty]);
  useEffect(() => {
    const pending = errorNotice.current;
    if (!pending?.fields.size) return;
    const remaining = errors.filter(error => pending.fields.has(energyFieldKey(error.card_id, error.field)));
    const fields = new Set(remaining.map(error => energyFieldKey(error.card_id, error.field)));
    if (!fields.size) { errorNotice.current = undefined; notice.destroy('energy-operation-error'); }
    else if (fields.size < pending.fields.size) { pending.fields = fields; pending.show(remaining); }
  }, [errors, notice]);
  function selectCard(cardId: string, collectionId = routeId.current) { const previous = current.current?.collection.id === collectionId ? current.current : sessions.get(collectionId); if (!previous || !previous.draft.groups.some(card => card.id === cardId)) return; const next = { ...previous, activeCardId: cardId }; if (routeId.current !== collectionId) { sessions.set(collectionId, next); return; } apply(next); setMobileCardsOpen(false); setActionError(''); setStatus(''); }
  function locate(error: EnergyFieldError) {
    if (error.card_id && current.current?.draft.groups.some(card => card.id === error.card_id)) selectCard(error.card_id);
    const isSample = error.field.startsWith('samples.'); setPoolOpen(isSample);
    setTimeout(() => {
      const key = energyFieldKey(isSample ? null : error.card_id, error.field);
      const element = [...document.querySelectorAll<HTMLElement>('[data-energy-field]')].find(item => item.dataset.energyField === key) ?? (isSample ? [...document.querySelectorAll<HTMLElement>('tr[data-sample-id]')].find(item => item.dataset.sampleId === error.sample_id)?.querySelector<HTMLElement>('.energy-override-details') : null) ?? document.getElementById('energy-card-actions');
      if (!element) return;
      for (let parent: HTMLElement | null = element; parent; parent = parent.parentElement) if (parent instanceof HTMLDetailsElement) parent.open = true;
      element.scrollIntoView?.({ behavior: 'smooth', block: 'center' });
      (element.querySelector<HTMLElement>('input:not([disabled]),textarea:not([disabled]),button:not([disabled]),[tabindex="0"]') ?? element).focus({ preventScroll: true });
    }, 50);
  }
  function report(cause: unknown, fieldErrors?: EnergyFieldError[]) {
    const previous = current.current;
    const reported = fieldErrors ?? (cause instanceof ApiError ? cause.fieldErrors as EnergyFieldError[] : []);
    const mapped = reported.map(error => ({ ...error, card_id: error.card_id ?? (!error.field.startsWith('samples.') && !['expected_revision', 'preview_id', 'card'].includes(error.field) ? previous?.activeCardId : null) }));
    setErrors(old => [...old.filter(error => !mapped.some(item => energyFieldKey(item.card_id, item.field) === energyFieldKey(error.card_id, error.field))), ...mapped]);
    const message = energyError(cause);
    if (cause instanceof ApiError && cause.code === 'ENERGY_REVISION_CONFLICT' && previous) { apply({ ...previous, conflict: true }); setActionError(`${message} 本地输入已保留；读取最新版本后明确选择重新保存。`); }
    else setActionError(message);
    const pending: ErrorNotice = { fields: new Set(mapped.map(error => energyFieldKey(error.card_id, error.field))), show: remaining => {
      const first = remaining[0], card = current.current?.draft.groups.find(item => item.id === first?.card_id), sample = current.current?.draft.rows.find(item => item.sample_id === first?.sample_id);
      notice.error({ key: 'energy-operation-error', title: '当前操作未完成', placement: 'bottomRight', duration: 8, onClose: () => { if (errorNotice.current === pending) errorNotice.current = undefined; }, description: <><p>{card ? `${card.name} · ` : ''}{sample ? `${sample.name}：` : ''}{first?.message ?? message}{remaining.length > 1 ? `（共 ${remaining.length} 项，请查看字段说明）` : ''}</p><Button size="small" onClick={() => first ? locate(first) : document.getElementById('energy-card-actions')?.scrollIntoView?.({ block: 'center' })}>{first ? '定位并修正' : '查看操作说明'}</Button></> });
    } };
    errorNotice.current = pending; pending.show(mapped);
  }
  async function run(work: () => Promise<void>) {
    if (working.current) return false;
    const previousNotice = errorNotice.current;
    operationTask.current = routeId.current;
    working.current = true; setBusy(true); setActionError(''); setStatus('');
    try { await work(); if (previousNotice && errorNotice.current === previousNotice && !previousNotice.fields.size) { errorNotice.current = undefined; notice.destroy('energy-operation-error'); } return true; } catch (cause) { if (operationTask.current === routeId.current) report(cause); else if (cause instanceof ApiError && cause.code === 'ENERGY_REVISION_CONFLICT') { const previous = sessions.get(operationTask.current ?? ''); if (previous) sessions.set(previous.collection.id, { ...previous, conflict: true }); } return false; } finally { operationTask.current = undefined; working.current = false; setBusy(false); }
  }
  function requireEditor() { const previous = current.current; if (!previous) throw new Error('先创建或打开一个计算任务。'); if (previous.conflict) throw new Error('存在修订冲突，请先读取最新版本；本地输入保留。'); return previous; }
  async function migrateForImport() {
    const previous = requireEditor();
    if (previous.collection.schema_version === 'pp.energy.v2') return previous.collection;
    const preview = await energyApi.previewSampleChange(previous.collection, []);
    const saved = await energyApi.saveSamples(previous.collection, [], preview.impact); installed(saved.collection); return saved.collection;
  }
  function changeCard(card: EnergyGroup, scientific = true) {
    const previous = current.current, saved = previous && collectionCards(previous.collection).find(item => item.id === card.id);
    if (!previous || working.current || previous.conflict || cardsReadOnly(previous.collection) || scientific && saved?.locked) return;
    const old = previous.draft.groups.find(item => item.id === card.id)!, changedScience = cardScienceKey(old) !== cardScienceKey(card);
    apply({ ...previous, draft: { ...previous.draft, groups: previous.draft.groups.map(item => item.id === card.id ? card : item) }, acceptedRisks: changedScience ? { ...previous.acceptedRisks, [card.id]: false } : previous.acceptedRisks });
    setStatus(''); setActionError('');
    setErrors(existing => existing.filter(error => {
      if (error.card_id !== card.id || error.field.startsWith('samples.')) return true;
      if (error.field === 'accepted_warnings') return !changedScience;
      if (error.field.includes('adsorbate_count') || error.field === 'reference_units') return true;
      return validateCard(previous.collection, { ...previous.draft, groups: [card] }, card).some(item => item.field === error.field);
    }));
  }
  function changeRow(sampleId: string, patch: Partial<EnergyRowDraft>) {
    const previous = current.current; if (!previous || working.current || previous.conflict || cardsReadOnly(previous.collection)) return;
    const acceptedRisks = { ...previous.acceptedRisks };
    if ('override' in patch) for (const card of previous.draft.groups) if (cardSampleIds(card).includes(sampleId)) acceptedRisks[card.id] = false;
    apply({ ...previous, acceptedRisks, draft: { ...previous.draft, rows: previous.draft.rows.map(row => row.sample_id === sampleId ? { ...row, ...patch } : row) } }); setStatus('');
  }
  function validateQuantity(card: EnergyGroup, field: string) {
    const collectionId = current.current?.collection.id;
    // InputNumber flushes its controlled value later in the same blur event.
    // Read the synchronous editor ref after bubbling, rather than render props.
    queueMicrotask(() => {
      const previous = current.current;
      if (!mounted.current || !previous || previous.collection.id !== collectionId) return;
      const latest = previous.draft.groups.find(item => item.id === card.id);
      if (!latest) return;
      const next = [...quantityIssue(latest, field), ...validateCard(previous.collection, previous.draft, latest).filter(error => error.field === field && error.code !== 'ENERGY_INVALID_QUANTITY')];
      setErrors(old => [...old.filter(error => !(error.card_id === latest.id && error.field === field)), ...next]);
    });
  }
  function validateSharedField(sampleId: string, field: string) {
    const draft = current.current?.draft; if (!draft) return;
    const path = `samples.${sampleId}.${field}`, found = validateSharedDraft({ ...draft, rows: draft.rows.filter(row => row.sample_id === sampleId) }).filter(error => error.field === path);
    setErrors(old => [...old.filter(error => !(error.sample_id === sampleId && error.field === path)), ...found]);
  }
  async function persistCard(cardId: string) {
    const previous = requireEditor(), card = previous.draft.groups.find(item => item.id === cardId);
    if (!card) throw new Error('计算卡不存在。');
    if (!cardDirty(previous, cardId)) return previous.collection;
    const saved = await energyApi.saveCard(previous.collection, cardConfiguration(card)); installed(saved.collection, [cardId]); return saved.collection;
  }
  async function create() {
    if (!newKind) return;
    await run(async () => {
      const response = await energyApi.create(newTitle.trim() || `新的${analysisLabels[newKind]}计算任务`, newKind);
      installed(response.collection, [], false, true); setNewTaskOpen(false); setNewTitle('');
      if (routeId.current === operationTask.current) { operationTask.current = response.collection.id; openTask(response.collection.id); }
      const added = await energyApi.addCard(response.collection, newEnergyGroup(newKind, 1)); installed(added.collection);
      if (routeId.current === response.collection.id) setPoolOpen(true);
      setStatus('计算任务已创建。导入任务内共享样本后，可添加多张独立计算卡。');
    });
  }
  async function addCard() { await run(async () => { const previous = requireEditor(), kind = analysisKind(previous.collection); if (!kind) throw new Error('旧混合记录需先按类型复制。'); const added = await energyApi.addCard(previous.collection, newEnergyGroup(kind, previous.draft.groups.length + 1)); installed(added.collection); selectCard(added.collection.groups.at(-1)!.id, added.collection.id); setStatus('已添加独立计算卡，其他卡与草稿保留。'); }); }
  async function saveCard() { await run(async () => { const cardId = requireEditor().activeCardId!; await persistCard(cardId); setStatus('当前卡草稿已保存，未执行计算。'); }); }
  async function operateCard(operation: 'copy' | 'unlock' | 'autofill' | 'calculate' | 'lock') {
    const previous = current.current, cardId = previous?.activeCardId, card = previous?.draft.groups.find(item => item.id === cardId);
    if (!previous || !cardId || !card) return;
    if (operation === 'lock') {
      const found = validateCard(previous.collection, previous.draft, card);
      const risky = cardSampleIds(card).filter(sampleId => { const sample = previous.collection.samples.find(item => item.id === sampleId); return sample && riskRequired(sample); });
      if (risky.length && !(previous.acceptedRisks?.[cardId] ?? card.risk_accepted)) found.push({ card_id: cardId, field: 'accepted_warnings', code: 'ENERGY_RISK_ACCEPTANCE_REQUIRED', message: '请明确接受当前卡实际引用样本的未完成／未知状态及警告。' });
      setErrors(old => old.filter(error => error.card_id !== cardId || error.field.startsWith('samples.')));
      if (found.length) { report(new Error('当前卡仍有待处理输入。'), found); return; }
    }
    if ((operation === 'lock' || operation === 'calculate') && referencedSampleDirty(previous, card)) { report(new Error('当前卡引用的共享科学值有未保存修改。先打开共享样本，预览影响并确认保存，再确认或计算当前卡。')); return; }
    await run(async () => {
      const acceptedRisk = previous.acceptedRisks?.[cardId] ?? card.risk_accepted ?? false;
      const collection = operation === 'unlock' ? requireEditor().collection : await persistCard(cardId);
      const response = operation === 'copy' ? await energyApi.copyCard(collection, cardId) : operation === 'unlock' ? await energyApi.unlockCard(collection, cardId) : operation === 'autofill' ? await energyApi.autofillCard(collection, cardId) : operation === 'calculate' ? await energyApi.calculateCard(collection, cardId) : await energyApi.lockCard(collection, cardId, acceptedRisk);
      installed(response.collection, operation === 'unlock' ? [] : [cardId]);
      if (operation === 'copy') selectCard(response.collection.groups.at(-1)!.id, response.collection.id);
      setStatus({ copy: '已复制当前卡。新卡需独立确认，其他卡与共享样本保留。', unlock: '当前卡已解锁；仍有效的确认、风险依据和结果保留。', autofill: '仅当前卡的规则建议已更新；已有人工选择保留。', calculate: '当前卡结果已计算并保存。', lock: '当前卡已确认并锁定，其他卡可继续独立编辑。' }[operation]);
    });
  }
  async function deleteCard() { const cardId = deleteCardId; if (!cardId) return; await run(async () => { const response = await energyApi.deleteCard(requireEditor().collection, cardId); installed(response.collection, [cardId]); setDeleteCardId(undefined); setErrors(old => old.filter(error => error.card_id !== cardId)); setStatus('计算卡已删除，共享样本和其他卡保留。'); }); }
  async function copyAnalysis(kind: EnergyAnalysisKind) { await run(async () => { const response = await energyApi.copy(requireEditor().collection, kind); installed(response.collection, [], false, true); if (routeId.current === operationTask.current) { operationTask.current = response.collection.id; openTask(response.collection.id); } setStatus('已按所选类型复制到独立计算任务，原始混合记录保留。新卡需重新确认。'); }); }
  async function saveTaskTitle() {
    const task = renameTask, title = renameTitle.trim(); if (!task || !title) return;
    await run(async () => { const response = await energyApi.rename(task, title); installed(response.collection, [], false, false, [], true); setRenameTask(undefined); });
  }
  async function deleteTask() {
    const task = deletingTask; if (!task) return;
    await run(async () => { await energyApi.remove(task.id, task.revision); deletedTasks.current.add(task.id); sessions.delete(task.id); client.removeQueries({ queryKey: ['energy-collection', task.id], exact: true }); client.setQueryData<{ collections: EnergyCollection[] }>(['energy-collections'], old => ({ collections: (old?.collections ?? []).filter(item => item.id !== task.id) })); setDeletingTask(undefined); if (routeId.current === task.id) openTask(''); });
  }
  async function importMutation(work: (collection: EnergyCollection) => Promise<{ collection: EnergyCollection }>, message: string) {
    return run(async () => { const collection = await migrateForImport(); const response = await work(collection); installed(response.collection); setStatus(message); });
  }
  async function importFiles(files: LocalEnergyFile[], onImported: (id: string) => void) {
    await run(async () => { let collection = await migrateForImport(), completed = 0; for (const item of files) { setStatus(`正在读取 ${completed + 1}/${files.length}：${item.relativePath || item.file.name}`); const response = await energyApi.upload(collection, item.file, item.relativePath); collection = response.collection; installed(collection); onImported(item.id); completed++; } setStatus(`已加入 ${completed} 个共享样本。打开计算卡选择用途，或仅对当前卡应用规则建议。`); });
  }
  async function previewCsv(file: File, basis?: EnergyBasis) {
    if (working.current) throw new Error('当前操作尚未完成，请稍后预览。');
    operationTask.current = routeId.current; working.current = true; setBusy(true);
    try { const collection = await migrateForImport(); const response = await energyApi.previewCsv(collection, file, basis); return { preview: response.preview, revision: collection.revision }; }
    catch (cause) { if (operationTask.current === routeId.current) report(cause); throw cause; } finally { operationTask.current = undefined; working.current = false; setBusy(false); }
  }
  async function importCsv(file: File, basis: EnergyBasis | undefined, revision: number) {
    const previous = current.current; if (!previous || previous.collection.revision !== revision) { report(new Error('CSV 预览对应的修订已变化，请重新预览。')); return false; }
    return importMutation(collection => energyApi.importCsv(collection, file, basis), 'CSV 样本已加入共享池，卡片配置保留；未知状态在卡片确认时接受。');
  }
  async function exportSamples(basis: EnergyBasis, source: EnergySampleValueSource) {
    if (working.current) throw new Error('当前操作尚未完成，请稍后导出。');
    if (current.current && sharedDirty(current.current)) { const error = new Error('共享样本有未保存修改，请先预览影响并保存后导出。'); report(error); throw error; }
    const success = await run(async () => { const collection = requireEditor().collection; saveEnergyBlob(await energyApi.downloadSamples(collection, basis, source), `${collection.id}.samples.${source}.csv`); setStatus('共享样本表已导出；回导不会继承卡片锁定或确认。'); });
    if (!success) throw new Error('样本导出未完成，请查看操作说明。');
  }
  async function previewShared(request?: EnergyRemovalRequest) {
    const previousDraft = current.current?.draft;
    if (!request && previousDraft) { const found = validateSharedDraft(previousDraft); if (found.length) { report(new Error('共享样本仍有待处理输入。'), found); return; } }
    await run(async () => {
      const previous = requireEditor();
      if (request && sharedDirty(previous)) throw new Error('先保存共享样本修改，再读取删除影响；当前输入保留。');
      if (request) { const response = await energyApi.previewRemoval(previous.collection, request); if (!response.removal.impact) throw new Error('服务器未提供计算卡删除影响，请刷新后重试。'); if (routeId.current === previous.collection.id) setReview({ collection: previous.collection, impact: response.removal.impact, request, removal: response.removal, draftKey: sharedKey(previous.draft) }); }
      else { const patches = samplePatches(previous.collection, previous.draft); const title = previous.draft.title !== previous.collection.title ? previous.draft.title.trim() : undefined; if (title === '') throw new Error('请填写计算任务名称。'); const response = await energyApi.previewSampleChange(previous.collection, patches, title); if (routeId.current === previous.collection.id) setReview({ collection: previous.collection, impact: response.impact, patches, title, draftKey: sharedKey(previous.draft) }); }
    });
  }
  async function applyShared() {
    const preview = review; if (!preview) return;
    const previous = current.current;
    if (!previous || previous.collection.id !== preview.collection.id || previous.collection.revision !== preview.collection.revision || previous.conflict || sharedKey(previous.draft) !== preview.draftKey) { setReview({ ...preview, error: '修订或共享输入已变化，尚未保存。请取消后重新预览。' }); return; }
    const success = await run(async () => { const response = preview.request ? await energyApi.removeSamplesWithImpact(preview.collection, preview.request, preview.impact) : await energyApi.saveSamples(preview.collection, preview.patches ?? [], preview.impact, preview.title); installed(response.collection, [], true, false, preview.impact.affected_cards.map(card => card.card_id)); if (routeId.current === preview.collection.id) { setReview(undefined); setSelection(selected => ({ ...selected, ids: selected.ids.filter(sampleId => response.collection.samples.some(sample => sample.id === sampleId)) })); setErrors(old => old.filter(error => !error.field.startsWith('samples.'))); } setStatus(preview.request ? `已移除 ${preview.removal?.removed_count ?? 0} 个共享样本；相关卡需重新确认。` : '共享修改已保存，实际引用卡需重新确认；未受影响卡保留有效状态。'); });
    if (!success && routeId.current === preview.collection.id) setReview({ ...preview, error: '保存未完成，所有本地输入保留；请查看操作说明。' });
  }
  async function reload(keepDraft: boolean) {
    await run(async () => { const previous = current.current!; const response = await energyApi.get(previous.collection.id); if (!keepDraft) installed(response.collection, [], false, true); else { installed(response.collection); const merged = sessions.get(previous.collection.id); if (merged) { const next = { ...merged, conflict: false }; if (routeId.current === previous.collection.id) apply(next); else sessions.set(previous.collection.id, next); } } setStatus(keepDraft ? '已读取最新版本并保留本地输入。请核对变化，锁定卡需先解锁才能修改科学配置。' : '已载入服务器保存的版本。'); });
  }
  async function exportCard(format: 'csv' | 'json') {
    await run(async () => { const previous = requireEditor(), cardId = previous.activeCardId!, activeCard = previous.draft.groups.find(card => card.id === cardId)!; if (cardDirty(previous, cardId) || referencedSampleDirty(previous, activeCard)) throw new Error('当前卡或实际引用样本有未保存修改，请保存并重新核验结果。'); const blob = await energyApi.downloadCard(previous.collection.id, cardId, format); saveEnergyBlob(blob, `${previous.collection.id}.${cardId}.${format}`); setStatus('结果已导出，卡片参考、计量与实际来源保留。'); });
  }
  const active = editor?.collection.id === id && !(detail.error instanceof ApiError && detail.error.status === 404) ? editor : undefined;
  const card = active?.draft.groups.find(item => item.id === active.activeCardId) ?? active?.draft.groups[0];
  const savedCard = active && card ? collectionCards(active.collection).find(item => item.id === card.id) : undefined;
  const readOnly = !!active && cardsReadOnly(active.collection);
  const kind = active ? analysisKind(active.collection) : null;
  const disabled = busy || !!active?.conflict || readOnly;
  const dirtyCard = !!active && !!card && cardDirty(active, card.id);
  const dirtyShared = !!active && sharedDirty(active);
  const scienceChanged = !!card && !!savedCard && cardScienceKey(card) !== cardScienceKey(savedCard);
  const sampleChanged = !!active && !!card && referencedSampleDirty(active, card);
  const result = readOnly ? active?.collection.result : savedCard?.result;
  const resultValid = !!result && !scienceChanged && !sampleChanged && !active?.conflict;
  const referenced = active && card ? cardSampleIds(card).flatMap(sampleId => { const sample = active.collection.samples.find(item => item.id === sampleId); return sample && riskRequired(sample) ? [sample] : []; }) : [];
  const accepted = !!card && (active?.acceptedRisks?.[card.id] ?? card.risk_accepted ?? false);
  const selectedIds = active && selection.collectionId === active.collection.id ? selection.ids.filter(sampleId => active.draft.rows.some(row => row.sample_id === sampleId)) : [];
  const activeErrors = errors.filter(error => !error.card_id || error.card_id === card?.id);
  return <div className="wf-page energy-page">
    {noticeHolder}
    <div className="wf-page-heading"><div><Typography.Title level={3}>基础能量</Typography.Title><p>{id ? '在当前计算任务内复用样本，按计算卡独立配置参考、确认与计算。' : '分别建立吸附能或形成能计算任务，每个任务管理自己的样本、参考、计算卡和结果。'}</p></div><Space wrap><Link to="/toolbox/postprocessing">DOS／能带后处理</Link>{id ? <Button onClick={() => openTask('')}>返回计算任务列表</Button> : <Button type="primary" disabled={busy} onClick={() => { setNewKind(undefined); setNewTitle(''); setActionError(''); setNewTaskOpen(true); }}>新建计算任务</Button>}{active && <Button onClick={() => setPoolOpen(true)}>本任务样本与导入 · {active.collection.samples.length}</Button>}</Space></div>
    {detail.error && <Alert type="error" title={energyError(detail.error)} action={<Button onClick={() => void detail.refetch()}>重试打开</Button>} />}
    {id && !active && detail.isFetching && <Spin />}
    {!id && <Card title="计算任务列表" extra={<Button size="small" disabled={busy} onClick={() => void listing.refetch()}>刷新列表</Button>}>
      {listing.isLoading ? <Spin /> : <ul className="energy-task-list" aria-label="已保存计算任务">{listing.data?.collections.map(collection => <li key={collection.id}>
        <div className="energy-task-description"><Typography.Title level={4}>{collection.title}</Typography.Title><Space wrap><Tag>{analysisKind(collection) ? analysisLabels[analysisKind(collection)!] : '旧混合类型 · 只读'}</Tag><span>{collection.sample_count ?? collection.samples.length} 个样本 · {collection.card_count ?? collectionCards(collection).length} 张计算卡</span>{sessions.get(collection.id)?.dirty && <Tag>有本地草稿</Tag>}</Space><small>更新于 {new Date(collection.updated_at).toLocaleString('zh-CN', { hour12: false })}</small></div>
        <Space wrap><Button type="primary" aria-label={`打开计算任务 ${collection.title}`} onClick={() => openTask(collection.id)}>打开任务</Button><Button disabled={busy} aria-label={`重命名计算任务 ${collection.title}`} onClick={() => { setRenameTask(collection); setRenameTitle(collection.title); setActionError(''); }}>重命名</Button><Button danger disabled={busy} aria-label={`删除计算任务 ${collection.title}`} onClick={() => { setDeletingTask(collection); setActionError(''); }}>删除</Button></Space>
      </li>)}</ul>}
      {listing.data?.collections.length === 0 && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚无计算任务。新建任务时选择固定类型，再导入来源并配置计算卡。" />}{listing.error && <Alert type="error" title={energyError(listing.error)} />}
      {actionError && !newTaskOpen && !renameTask && !deletingTask && <Alert type="error" title={actionError} />}
    </Card>}
    {id && !active && !detail.isFetching && !detail.error && <Card><Empty description="计算任务尚未打开，请重试或返回任务列表。" /></Card>}
    {active && <>
      <div className="energy-analysis-heading"><div><Typography.Title level={4}>{active.draft.title}</Typography.Title><Space wrap><Tag>{kind ? analysisLabels[kind] : '旧混合类型'}</Tag><Tag>{active.draft.groups.length} 张计算卡</Tag><Tag>{active.dirty ? '有本地未保存输入' : '已保存'} · 修订 {active.collection.revision}</Tag></Space></div><Space wrap><Button className="energy-mobile-toggle" onClick={() => setMobileCardsOpen(open => !open)}>{mobileCardsOpen ? '返回当前卡编辑' : '选择计算卡'}</Button><Button onClick={() => setPoolOpen(true)}>管理本任务样本</Button></Space></div>
      {active.conflict && <Alert type="warning" title="保存版本已变化，本地输入保留。" action={<Space wrap><Button disabled={busy} onClick={() => void reload(true)}>读取最新并保留本地输入</Button><Button disabled={busy} onClick={() => void reload(false)}>重新载入已保存版本</Button></Space>} />}
      {readOnly && <Alert type="info" title="旧混合计算任务完整保留，以只读方式打开。" description={<><p>按类型显式复制到独立计算任务后编辑，原始组、来源与结果保留。</p><Space wrap>{(['adsorption', 'formation'] as EnergyAnalysisKind[]).filter(type => !active.draft.groups.length || active.draft.groups.some(item => item.kind === type)).map(type => <Button key={type} disabled={busy} onClick={() => void copyAnalysis(type)}>复制为{analysisLabels[type]}计算任务</Button>)}</Space></>} />}
      <div className={`energy-card-workspace${mobileCardsOpen ? ' mobile-cards-open' : ''}`}>
        <aside className="energy-card-list" aria-label="计算卡列表"><div className="energy-card-list-heading"><strong>计算卡</strong><Button size="small" disabled={disabled} onClick={() => void addCard()}>新增计算卡</Button></div><p className="energy-note">每张卡独立保存和锁定；切换保留输入。</p><ul className="energy-history">{active.draft.groups.map(item => <li key={item.id}><button disabled={busy} aria-current={item.id === card?.id} aria-label={`打开计算卡 ${item.name}`} onClick={() => selectCard(item.id)}><strong>{item.name}</strong><small>{item.targets.length} 个目标 · {stateLabel(item, cardDirty(active, item.id))}</small>{errors.some(error => error.card_id === item.id) && <small className="energy-field-error">有待修正项</small>}</button></li>)}</ul>{!active.draft.groups.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚无计算卡" />}</aside>
        <main className="energy-card-editor">
          {card ? <>
            <div id="energy-card-actions" className="energy-card-actions" tabIndex={-1} aria-label="当前卡固定操作区">
              <div className="energy-card-action-title"><strong>{card.name}</strong><Space wrap><Tag color={savedCard?.locked ? 'green' : undefined}>{stateLabel(card, dirtyCard)}</Tag><span className="energy-save-status" role="status">{status || (dirtyCard ? '当前卡输入已在本地保留，尚未保存。' : savedCard?.locked ? '当前卡已锁定，可独立计算；解锁入口始终可达。' : '可自由配置当前卡，其他卡不受影响。')}</span></Space></div>
              <Space wrap><Button disabled={disabled || !dirtyCard} onClick={() => void saveCard()}>保存当前卡草稿</Button>{savedCard?.locked ? <Button disabled={busy || readOnly || active.conflict} onClick={() => void operateCard('unlock')}>解锁当前卡</Button> : <Button type="primary" disabled={disabled} onClick={() => void operateCard('lock')}>确认并锁定当前卡</Button>}<Button type="primary" disabled={disabled || !savedCard?.locked || dirtyCard || sampleChanged} onClick={() => void operateCard('calculate')}>计算当前卡</Button><Button disabled={busy || readOnly || !resultValid || dirtyCard || sampleChanged} onClick={() => void exportCard('csv')}>导出当前卡 CSV</Button><Button disabled={busy || readOnly || !resultValid || dirtyCard || sampleChanged} onClick={() => void exportCard('json')}>导出当前卡 JSON</Button></Space>
              {actionError && <div className="energy-action-error" role="status">{actionError}</div>}
              {!!activeErrors.length && <details className="energy-details"><summary>待处理输入 {activeErrors.length} 项 · 可定位</summary><ul>{activeErrors.map((error, index) => <li key={`${energyFieldKey(error.card_id, error.field)}-${index}`}><button className="energy-error-link" onClick={() => locate(error)}>{error.message} · 定位</button></li>)}</ul></details>}
            </div>
            <Card title={`${analysisLabels[card.kind]} · 参考与目标`} extra={<Space wrap><Button size="small" disabled={disabled || !!savedCard?.locked} onClick={() => void operateCard('autofill')}>应用当前卡规则建议</Button><Button size="small" disabled={disabled} onClick={() => void operateCard('copy')}>复制当前卡</Button><Button size="small" danger disabled={disabled} onClick={() => setDeleteCardId(card.id)}>删除当前卡</Button></Space>}>
              {savedCard?.locked && <Alert type="success" title="当前卡已锁定。解锁后可修改参考、目标及计量；名称仍可修改。" />}
              {(scienceChanged || sampleChanged || card.status === 'stale') && <Alert type="warning" title="当前卡科学输入变化，需重新确认；旧结果不能作为当前结果导出。" />}
              <EnergyGroups collection={active.collection} draft={{ ...active.draft, groups: [card] }} disabled={disabled || !!savedCard?.locked} nameDisabled={disabled} errors={errors} onValidate={validateQuantity} onGroup={changeCard} />
            </Card>
            <Card title="当前卡集中确认" className="energy-risk-card"><p className="energy-note">确认目标、参考、数量、能量口径与来源后锁定当前卡。风险接受不能放行缺少来源、计量冲突等硬错误。</p>{referenced.length ? <><ul className="energy-risk-list">{referenced.map(sample => <li key={sample.id}><strong>{active.draft.rows.find(row => row.sample_id === sample.id)?.name}</strong><p>{sample.parsed.warnings.join('；') || '运行未完成、收敛或状态未知。'}</p><small>运行：{sample.parsed.status.completion} · 电子收敛：{sample.parsed.status.electronic_converged === true ? '已收敛' : sample.parsed.status.electronic_converged === false ? '未收敛' : '未知'} · 离子：{sample.parsed.status.ionic_applicability === 'not_applicable' ? '静态，不适用' : sample.parsed.status.ionic_converged === true ? '已收敛' : sample.parsed.status.ionic_converged === false ? '未收敛' : '未知'}</small></li>)}</ul><div data-energy-field={energyFieldKey(card.id, 'accepted_warnings')}><Checkbox aria-label={`接受当前卡风险 ${card.id}`} disabled={disabled || !!savedCard?.locked} checked={accepted} onChange={event => { apply({ ...active, acceptedRisks: { ...active.acceptedRisks, [card.id]: event.target.checked } }); if (event.target.checked) setErrors(old => old.filter(error => !(error.card_id === card.id && error.field === 'accepted_warnings'))); }}>明确接受上述实际引用样本的未完成／未知状态及警告</Checkbox>{errors.filter(error => error.card_id === card.id && error.field === 'accepted_warnings').map((error, index) => <p key={index} className="energy-field-error" role="status">{error.message}</p>)}</div></> : <p>当前卡实际引用来源未发现需明确接受的状态风险。</p>}</Card>
            <Card title="当前卡结果"><EnergyResults collection={{ ...active.collection, result: result ?? null }} valid={resultValid} expired={scienceChanged || sampleChanged || card.status === 'stale'} showExports={false} /></Card>
          </> : <Card><Empty description="新增一张计算卡，选择共享样本作为参考和目标。" /></Card>}
        </main>
      </div>
    </>}
    <Modal open={newTaskOpen} title="新建计算任务 · 先选类型" footer={null} onCancel={() => { if (!busy) setNewTaskOpen(false); }}>
      <div className="energy-import-form"><Radio.Group aria-label="新计算任务类型" className="energy-type-options" value={newKind} disabled={busy} onChange={event => setNewKind(event.target.value)}><Radio value="adsorption">吸附能</Radio><Radio value="formation">材料形成能</Radio></Radio.Group><label className="energy-field">计算任务名称<Input aria-label="新计算任务名称" value={newTitle} maxLength={120} disabled={busy} onChange={event => setNewTitle(event.target.value)} /></label><Button type="primary" disabled={busy || !newKind} onClick={() => void create()}>创建计算任务并导入数据</Button><p className="energy-note">创建后类型固定。任务拥有自己的样本和计算卡；同一任务的多张卡可复用样本，并独立保存参考、确认和结果。</p>{actionError && <Alert type="error" title={actionError} />}</div>
    </Modal>
    <Modal open={!!renameTask} title="重命名计算任务" okText="保存任务名称" cancelText="取消" confirmLoading={busy} okButtonProps={{ disabled: !renameTitle.trim() }} onCancel={() => { if (!busy) setRenameTask(undefined); }} onOk={() => void saveTaskTitle()}>
      <label className="energy-field">计算任务名称<Input aria-label="重命名计算任务名称" value={renameTitle} maxLength={120} disabled={busy} onChange={event => setRenameTitle(event.target.value)} /></label><p className="energy-note">仅修改任务名称，已有样本、计算卡确认与结果保持有效。</p>{actionError && <Alert type="error" title={actionError} />}
    </Modal>
    <Modal open={!!deletingTask} title="删除计算任务" okText="确认删除计算任务" cancelText="取消" okButtonProps={{ danger: true }} confirmLoading={busy} onCancel={() => { if (!busy) setDeletingTask(undefined); }} onOk={() => void deleteTask()}>
      {deletingTask && <><p>将删除“{deletingTask.title}”的 {deletingTask.sample_count ?? deletingTask.samples.length} 个样本、{deletingTask.card_count ?? collectionCards(deletingTask).length} 张计算卡，以及该任务的配置、结果与本地草稿。</p><p className="energy-note">删除只清理应用保存的当前任务数据。原始文件、来源计算任务和其他计算任务保留。</p>{actionError && <Alert type="error" title={actionError} />}</>}
    </Modal>
    <Drawer open={poolOpen && !!active} title={`本任务样本与导入 · ${active?.draft.title ?? ''}`} size="large" className="energy-pool-drawer" onClose={() => setPoolOpen(false)} extra={<Button type="primary" disabled={disabled || !dirtyShared} onClick={() => void previewShared()}>预览影响并保存共享修改</Button>}>
      {active && <>
        <div className="energy-pool-actions"><p className="energy-note">当前任务：{active.draft.title} · {kind ? analysisLabels[kind] : '旧混合类型'}。这里的样本仅供当前任务的计算卡选择与复用。</p><span role="status" className="energy-save-status">{status || (dirtyShared ? '共享修改尚未保存。预览影响后明确确认。' : '共享来源已保存。卡片用途在各卡独立选择。')}</span>{actionError && <div role="status" className="energy-action-error">{actionError}</div>}</div>
        <Card title="导入来源" extra={<Space wrap><Button size="small" type={sourceMode === 'local' ? 'primary' : 'default'} onClick={() => setSourceMode('local')}>本地／手填／CSV</Button><Button size="small" type={sourceMode === 'task' ? 'primary' : 'default'} onClick={() => { setTaskSourceMounted(true); setSourceMode('task'); }}>已有任务／缓存</Button></Space>}><div hidden={sourceMode !== 'local'}><EnergyImports key={active.collection.id} collection={active.collection} disabled={disabled} draftDirty={dirtyShared} onFiles={importFiles} onManual={input => importMutation(collection => energyApi.addManual(collection, input), '手填样本已加入共享池，来源状态保留未知。')} onCsvPreview={previewCsv} onCsv={importCsv} /></div>{taskSourceMounted && <div hidden={sourceMode !== 'task'}><EnergyTaskSource key={active.collection.id} disabled={disabled} collections={listing.data?.collections ?? []} onImport={previewId => importMutation(collection => energyApi.importTask(collection, previewId), '稳定任务快照已加入共享池。')} onReuse={(sourceId, sampleId) => importMutation(collection => energyApi.reuse(collection, sourceId, sampleId), '缓存样本已复用到共享池。')} /></div>}</Card>
        <Card title="共享样本 · 参与卡片与用途">{active.collection.samples.length ? <EnergyConfirmationTable collection={active.collection} draft={active.draft} disabled={disabled} selectedIds={selectedIds} onSelection={ids => setSelection({ collectionId: active.collection.id, ids })} onDelete={() => void previewShared({ sample_ids: selectedIds })} onClear={() => void previewShared({ clear_all: true })} onRow={changeRow} errors={errors} onValidate={validateSharedField} /> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="导入来源后，在各计算卡选择参考和目标。" />}<EnergySampleExport key={active.collection.id} initialBasis={card?.energy_basis ?? 'sigma_to_zero_ev'} dirty={dirtyShared} disabled={busy || active.conflict || dirtyShared} onExport={exportSamples} /></Card>
      </>}
    </Drawer>
    <Modal open={!!review} title={review?.request ? '确认共享样本删除影响' : '确认共享修改影响'} okText={review?.request ? '确认删除并更新相关卡' : '确认保存并更新相关卡'} cancelText="取消，保留现状" okButtonProps={{ danger: !!review?.request }} confirmLoading={busy} onCancel={() => { if (!busy) setReview(undefined); }} onOk={() => void applyShared()}>
      {review && <><p>{review.request ? `将移除 ${review.removal?.removed_count ?? 0} 个共享样本。` : `将保存 ${review.patches?.length ?? 0} 个共享样本修改${review.title ? '及计算任务名称' : ''}。`}以下实际引用卡将需要重新确认：</p>{review.impact.affected_cards.length ? <ul>{review.impact.affected_cards.map(item => <li key={item.card_id}><strong>{item.name}</strong>{item.locked ? ' · 当前已锁定，将解除锁定并清除过期结果' : ' · 确认与结果将失效'}</li>)}</ul> : <p>没有卡片科学输入受影响。</p>}<p className="energy-note">取消不保存任何共享修改。删除只移除当前计算任务的样本与关联，原始文件、任务来源和其他计算任务保留；不会自动改绑参考。</p>{review.error && <Alert type="error" title={review.error} />}</>}
    </Modal>
    <Modal open={!!deleteCardId} title="删除计算卡" okText="确认删除计算卡" cancelText="取消" okButtonProps={{ danger: true }} confirmLoading={busy} onCancel={() => { if (!busy) setDeleteCardId(undefined); }} onOk={() => void deleteCard()}><p>删除“{active?.draft.groups.find(item => item.id === deleteCardId)?.name}”及该卡配置与结果。共享样本、其他卡和原始来源保留。</p></Modal>
  </div>;
}
