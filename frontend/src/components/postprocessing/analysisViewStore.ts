import { useEffect, useSyncExternalStore } from 'react';
import { ApiError } from '../../api/client';
import { ppApi, type PPDataset, type PPView } from '../../api/postprocessing';
import { editableView, normalizeView, validateView, viewKey } from './viewState';

export const NUMERIC_SAVE_DELAY_MS = 400;
type SaveStatus = 'saved' | 'scheduled' | 'saving' | 'invalid' | 'failed' | 'conflict';
type Snapshot = { view: PPView; savedDoc: PPDataset; status: SaveStatus; error: string };
type Transport = Pick<typeof ppApi, 'save' | 'get'>;

/** One serial CAS queue owns one dataset, including while its page is unmounted. */
export class AnalysisViewStore {
  private base: PPDataset;
  private snapshot: Snapshot;
  private listeners = new Set<() => void>();
  private timer: ReturnType<typeof setTimeout> | undefined;
  private request: AbortController | undefined;
  private inFlightIntent: PPView | undefined;
  private inFlightRevision: number | undefined;
  private inFlight = false;
  private disposed = false;
  private version = 0;
  private readyAt = 0;
  private reloadNeeded = false;
  private overwriteApproved = false;
  private transport: Transport;

  constructor(doc: PPDataset, transport: Transport = ppApi) {
    this.transport = transport;
    const view = normalizeView(doc);
    this.base = { ...doc, view };
    this.snapshot = { view, savedDoc: this.base, status: 'saved', error: '' };
  }

  getSnapshot = () => this.snapshot;
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => this.listeners.delete(listener); };
  private emit(value: Partial<Snapshot>) {
    if (this.disposed) return;
    this.snapshot = { ...this.snapshot, ...value };
    this.listeners.forEach(listener => listener());
  }
  private dirty() { return viewKey(this.snapshot.view) !== viewKey(this.base.view); }
  private schedule() {
    clearTimeout(this.timer);
    if (this.disposed || this.snapshot.status === 'failed' || this.snapshot.status === 'conflict' || validateView(this.snapshot.view, this.base)) return;
    this.timer = setTimeout(() => void this.pump(), Math.max(0, this.readyAt - Date.now()));
  }
  receive(doc: PPDataset) {
    if (doc.id !== this.base.id || doc.revision <= this.base.revision) return;
    const hadDraft = this.dirty() || this.inFlight;
    const previousView = this.base.view;
    const next = normalizeView(doc);
    const ownSave = this.inFlightIntent && doc.revision === this.inFlightRevision! + 1 && viewKey(next) === viewKey(this.inFlightIntent);
    this.base = { ...doc, view: next };
    if (!hadDraft) this.emit({ view: this.base.view, savedDoc: this.base, status: 'saved', error: '' });
    else if (!ownSave && viewKey(next) !== viewKey(previousView) && viewKey(next) !== viewKey(this.snapshot.view)) this.conflict();
  }
  patch = (patch: Partial<PPView>, numeric = false) => {
    const view = { ...this.snapshot.view, ...patch };
    if (viewKey(view) === viewKey(this.snapshot.view)) return;
    this.version += 1;
    this.readyAt = Date.now() + (numeric ? NUMERIC_SAVE_DELAY_MS : 0);
    const invalid = validateView(view, this.base);
    const conflicted = this.snapshot.status === 'conflict';
    this.emit({ view, status: conflicted ? 'conflict' : invalid ? 'invalid' : this.inFlight ? 'saving' : 'scheduled', error: conflicted ? this.snapshot.error : '' });
    this.schedule();
  };
  retry = () => {
    if (this.disposed || validateView(this.snapshot.view, this.base)) return;
    this.reloadNeeded = true;
    this.overwriteApproved = this.snapshot.status === 'conflict';
    this.readyAt = Date.now();
    this.emit({ status: 'scheduled', error: '' });
    this.schedule();
  };
  loadLatest = async () => {
    if (this.disposed || this.inFlight) return;
    clearTimeout(this.timer);
    this.inFlight = true;
    const requestedVersion = ++this.version;
    const controller = new AbortController();
    this.request = controller;
    this.emit({ status: 'saving', error: '' });
    try {
      await this.refresh(controller.signal);
      if (this.disposed) return;
      if (requestedVersion === this.version) this.emit({ view: this.base.view, savedDoc: this.base, status: 'saved', error: '' });
      else this.conflict('载入期间当前选择已变化，请选择载入最新配置或重新应用当前选择。');
    } catch (error) {
      if (!this.disposed) this.conflict(error instanceof Error ? error.message : '无法载入最新配置，请重试');
    } finally { this.inFlight = false; this.request = undefined; }
  };
  private conflict(message = '该分析已在其他页面修改。当前选择已保留，自动保存已暂停。') {
    clearTimeout(this.timer);
    this.emit({ status: 'conflict', error: message });
  }
  dispose() {
    this.disposed = true;
    clearTimeout(this.timer);
    this.request?.abort();
    this.listeners.clear();
  }
  private async refresh(signal: AbortSignal) {
    const doc = (await this.transport.get(this.base.id, signal)).dataset;
    if (doc.id !== this.base.id || doc.status !== 'ready') throw new Error('该分析已变化，当前草稿尚未保存，请重新打开分析');
    if (doc.revision >= this.base.revision) this.base = { ...doc, view: normalizeView(doc) };
  }
  private async pump() {
    if (this.disposed || this.inFlight || this.snapshot.status === 'conflict' || this.snapshot.status === 'failed' || validateView(this.snapshot.view, this.base)) return;
    if (Date.now() < this.readyAt) { this.schedule(); return; }
    if (!this.dirty() && !this.reloadNeeded) { this.emit({ savedDoc: this.base, status: 'saved' }); return; }
    this.inFlight = true;
    const controller = new AbortController();
    this.request = controller;
    this.emit({ status: 'saving', error: '' });
    let failedVersion: number | undefined;
    try {
      if (this.reloadNeeded) {
        const previous = this.base;
        await this.refresh(controller.signal);
        this.reloadNeeded = false;
        if (!this.overwriteApproved && viewKey(this.base.view) !== viewKey(previous.view) && viewKey(this.base.view) !== viewKey(this.snapshot.view)) {
          this.conflict();
          return;
        }
      }
      this.overwriteApproved = false;
      if (!this.disposed) {
        if (validateView(this.snapshot.view, this.base) || Date.now() < this.readyAt) return;
        if (!this.dirty()) { this.emit({ view: this.base.view, savedDoc: this.base, status: 'saved', error: '' }); return; }
        const requestedVersion = this.version;
        const requestedRevision = this.base.revision;
        const intent = editableView(this.snapshot.view);
        this.inFlightIntent = intent;
        this.inFlightRevision = requestedRevision;
        try {
          const { dataset } = await this.transport.save(this.base, intent, controller.signal);
          if (this.disposed) return;
          if (dataset.id !== this.base.id || dataset.revision <= requestedRevision) throw new Error('保存响应版本无效，当前草稿尚未保存');
          if (dataset.revision < this.base.revision) { this.conflict('保存期间该分析已有较新的外部修改。当前选择已保留，自动保存已暂停。'); return; }
          const normalized = normalizeView(dataset);
          if (dataset.revision === this.base.revision && viewKey(normalized) !== viewKey(this.base.view)) { this.conflict(); return; }
          this.base = { ...dataset, view: normalized };
          // A late successful save advances CAS, but never replaces a newer draft or plot.
          if (requestedVersion === this.version) this.emit({ view: this.base.view, savedDoc: this.base, status: 'saved', error: '' });
        } catch (error) {
          if (this.disposed) return;
          if (error instanceof ApiError && error.code === 'PP_CONFLICT') {
            await this.refresh(controller.signal);
            this.conflict();
            return;
          }
          failedVersion = requestedVersion;
          throw error;
        }
      }
    } catch (error) {
      if (!this.disposed) {
        failedVersion ??= this.version;
        this.emit({ status: 'failed', error: error instanceof Error ? error.message : '自动保存失败，请重试' });
      }
    } finally {
      this.inFlight = false;
      this.request = undefined;
      this.inFlightIntent = undefined;
      this.inFlightRevision = undefined;
      if (!this.disposed) {
        const invalid = validateView(this.snapshot.view, this.base);
        if (this.getSnapshot().status === 'conflict') { /* An explicit choice is required before overwriting remote settings. */ }
        else if (invalid) this.emit({ status: 'invalid' });
        else if (failedVersion !== undefined && failedVersion === this.version) { /* Preserve draft until retry or another edit. */ }
        else if (this.dirty()) { this.emit({ status: 'scheduled', error: '' }); this.schedule(); }
        else this.emit({ savedDoc: this.base, status: 'saved', error: '' });
      }
    }
  }
}

const sessions = new Map<string, AnalysisViewStore>();
function getAnalysisViewSession(doc: PPDataset): AnalysisViewStore {
  let existing = sessions.get(doc.id);
  if (!existing) { existing = new AnalysisViewStore(doc); sessions.set(doc.id, existing); }
  return existing;
}
export function forgetAnalysisViewSession(id: string) { sessions.get(id)?.dispose(); sessions.delete(id); }
export function clearAnalysisViewSessions() { sessions.forEach(store => store.dispose()); sessions.clear(); }

export function useAnalysisView(doc: PPDataset, update: (doc: PPDataset) => void) {
  const store = getAnalysisViewSession(doc);
  const snapshot = useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
  useEffect(() => { store.receive(doc); }, [doc, store]);
  useEffect(() => { update(snapshot.savedDoc); }, [snapshot.savedDoc, update]);
  return { ...snapshot, patch: store.patch, retry: store.retry, loadLatest: store.loadLatest };
}
