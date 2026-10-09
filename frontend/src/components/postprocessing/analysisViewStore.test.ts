import { vi } from 'vitest';
import { ApiError } from '../../api/client';
import type { PPDataset, PPView } from '../../api/postprocessing';
import { AnalysisViewStore, NUMERIC_SAVE_DELAY_MS } from './analysisViewStore';

const view: PPView = { version: 'pp.view.v2', reference: 'fermi', reference_ev: 0, mirror_down: false, atoms: [], elements: [], orbitals: ['s', 'px'], projection_grouping: 'element', energy_min_ev: -5, energy_max_ev: 3, band_start: 1, band_end: 2 };
const doc: PPDataset = { id: 'queue-a', title: 'A', kind: 'dos', status: 'ready', revision: 4, files: [], error: null, view, summary: { spin_mode: 'collinear', efermi_ev: 7, convergence: 'unknown', warnings: [], atoms: [[1, 'Fe'], [2, 'O']], orbitals: ['s', 'px'], band_count: 0 } };
const stores: AnalysisViewStore[] = [];
function deferred<T>() { let resolve!: (value: T) => void; let reject!: (reason: unknown) => void; const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
function makeStore(record = doc) {
  let persisted = record;
  const transport = {
    get: vi.fn(async () => ({ dataset: persisted })),
    save: vi.fn(async (base: PPDataset, intent: PPView, _signal?: AbortSignal) => ({ dataset: persisted = { ...persisted, revision: base.revision + 1, view: intent } })),
  };
  const store = new AnalysisViewStore(record, transport); stores.push(store);
  return { store, transport };
}
beforeEach(() => { vi.useFakeTimers(); });
afterEach(() => { stores.forEach(store => store.dispose()); stores.length = 0; vi.useRealTimers(); });

it('debounces complete numeric edits and sends no request for empty, invalid or overflowing ranges', async () => {
  const { store, transport } = makeStore();
  store.patch({ energy_min_ev: -4 }, true);
  await vi.advanceTimersByTimeAsync(NUMERIC_SAVE_DELAY_MS - 1);
  expect(transport.save).not.toHaveBeenCalled();
  store.patch({ energy_min_ev: Number.NaN }, true);
  await vi.advanceTimersByTimeAsync(1000);
  expect(transport.save).not.toHaveBeenCalled();
  store.patch({ energy_min_ev: 3, energy_max_ev: 3 }, true);
  await vi.advanceTimersByTimeAsync(1000);
  expect(transport.save).not.toHaveBeenCalled();
  store.patch({ energy_min_ev: -Number.MAX_VALUE, energy_max_ev: Number.MAX_VALUE }, true);
  await vi.advanceTimersByTimeAsync(1000);
  expect(transport.save).not.toHaveBeenCalled();
  store.patch({ energy_min_ev: -4, energy_max_ev: 2 }, true);
  await vi.advanceTimersByTimeAsync(NUMERIC_SAVE_DELAY_MS);
  expect(transport.save).toHaveBeenCalledOnce();
  expect(store.getSnapshot()).toMatchObject({ status: 'saved', view: { energy_min_ev: -4, energy_max_ev: 2 } });
});

it('serializes edits made during a save and never replaces the newer draft or plot with its old response', async () => {
  const { store, transport } = makeStore();
  const first = deferred<{ dataset: PPDataset }>();
  transport.save.mockImplementationOnce(() => first.promise);
  store.patch({ mirror_down: true });
  await vi.advanceTimersByTimeAsync(0);
  store.patch({ mirror_down: false, elements: ['Fe'] });
  await vi.advanceTimersByTimeAsync(0);
  expect(transport.save).toHaveBeenCalledOnce();
  first.resolve({ dataset: { ...doc, revision: 5, view: { ...view, mirror_down: true } } });
  await vi.advanceTimersByTimeAsync(0);
  expect(transport.save).toHaveBeenCalledTimes(2);
  expect(transport.save.mock.calls[1][0].revision).toBe(5);
  expect(transport.save.mock.calls[1][1]).toMatchObject({ mirror_down: false, elements: ['Fe'] });
  expect(store.getSnapshot()).toMatchObject({ status: 'saved', savedDoc: { revision: 6 }, view: { mirror_down: false, elements: ['Fe'] } });
});

it('pauses after CAS conflict without overwriting an external field and requires an explicit reapply', async () => {
  const { store, transport } = makeStore();
  const reload = deferred<{ dataset: PPDataset }>();
  transport.save.mockRejectedValueOnce(new ApiError('PP_CONFLICT', '版本变化', false, 409));
  transport.get.mockImplementationOnce(() => reload.promise);
  store.patch({ energy_min_ev: -4 });
  await vi.advanceTimersByTimeAsync(0);
  store.patch({ elements: ['O'] });
  reload.resolve({ dataset: { ...doc, revision: 17, view: { ...view, mirror_down: true } } });
  await vi.advanceTimersByTimeAsync(0);
  expect(transport.save).toHaveBeenCalledOnce();
  expect(store.getSnapshot()).toMatchObject({ status: 'conflict', view: { mirror_down: false, elements: ['O'], energy_min_ev: -4 } });
  store.patch({ energy_max_ev: 2 });
  await vi.advanceTimersByTimeAsync(1000);
  expect(transport.save).toHaveBeenCalledOnce();
  transport.get.mockResolvedValueOnce({ dataset: { ...doc, revision: 17, view: { ...view, mirror_down: true } } });
  store.retry();
  await vi.advanceTimersByTimeAsync(0);
  expect(transport.save.mock.calls[1][0].revision).toBe(17);
  expect(transport.save.mock.calls[1][1]).toMatchObject({ mirror_down: false, elements: ['O'], energy_min_ev: -4, energy_max_ev: 2 });
  expect(store.getSnapshot().savedDoc.revision).toBe(18);
});

it('loads the latest external configuration after conflict without saving over it', async () => {
  const { store, transport } = makeStore();
  const latest = { ...doc, revision: 8, view: { ...view, mirror_down: true } };
  transport.save.mockRejectedValueOnce(new ApiError('PP_CONFLICT', '版本变化', false, 409));
  transport.get.mockResolvedValue({ dataset: latest });
  store.patch({ energy_min_ev: -4 });
  await vi.advanceTimersByTimeAsync(0);
  expect(store.getSnapshot().status).toBe('conflict');
  await store.loadLatest();
  expect(transport.save).toHaveBeenCalledOnce();
  expect(store.getSnapshot()).toMatchObject({ status: 'saved', error: '', view: { mirror_down: true, energy_min_ev: -5 }, savedDoc: { revision: 8 } });
});

it('pauses a queued draft when a newer external refetch changes an untouched field', async () => {
  const { store, transport } = makeStore();
  store.patch({ energy_min_ev: -4 }, true);
  store.receive({ ...doc, revision: 7, view: { ...view, mirror_down: true } });
  await vi.advanceTimersByTimeAsync(1000);
  expect(transport.save).not.toHaveBeenCalled();
  expect(store.getSnapshot()).toMatchObject({ status: 'conflict', view: { mirror_down: false, energy_min_ev: -4 } });
});

it('accepts an equal-revision successful response already received from refetch and pauses for a newer external revision', async () => {
  const a = makeStore();
  const pending = deferred<{ dataset: PPDataset }>();
  a.transport.save.mockImplementationOnce(() => pending.promise);
  a.store.patch({ mirror_down: true });
  await vi.advanceTimersByTimeAsync(0);
  const saved = { ...doc, revision: 5, view: { ...view, mirror_down: true } };
  a.store.receive(saved);
  pending.resolve({ dataset: saved });
  await vi.advanceTimersByTimeAsync(0);
  expect(a.store.getSnapshot()).toMatchObject({ status: 'saved', error: '', savedDoc: { revision: 5 } });
  const b = makeStore({ ...doc, id: 'queue-b' });
  const late = deferred<{ dataset: PPDataset }>();
  b.transport.save.mockImplementationOnce(() => late.promise);
  b.store.patch({ energy_min_ev: -4 });
  await vi.advanceTimersByTimeAsync(0);
  b.store.receive({ ...doc, id: 'queue-b', revision: 6, view: { ...view, mirror_down: true } });
  late.resolve({ dataset: { ...doc, id: 'queue-b', revision: 5, view: { ...view, energy_min_ev: -4 } } });
  await vi.advanceTimersByTimeAsync(0);
  expect(b.store.getSnapshot()).toMatchObject({ status: 'conflict', view: { energy_min_ev: -4, mirror_down: false } });
  expect(b.transport.save).toHaveBeenCalledOnce();
});

it('keeps a failed draft and recovers an ambiguously successful save by reading the current server revision', async () => {
  const { store, transport } = makeStore();
  transport.save.mockRejectedValueOnce(new Error('网络断开'));
  store.patch({ mirror_down: true });
  await vi.advanceTimersByTimeAsync(0);
  expect(store.getSnapshot()).toMatchObject({ status: 'failed', error: '网络断开', view: { mirror_down: true }, savedDoc: { revision: 4 } });
  await vi.advanceTimersByTimeAsync(1000);
  expect(transport.save).toHaveBeenCalledOnce();
  transport.get.mockResolvedValueOnce({ dataset: { ...doc, revision: 5, view: { ...view, mirror_down: true } } });
  store.retry();
  await vi.advanceTimersByTimeAsync(0);
  expect(transport.save).toHaveBeenCalledOnce();
  expect(store.getSnapshot()).toMatchObject({ status: 'saved', error: '', savedDoc: { revision: 5 } });
});

it('continues owned saves without subscribers and keeps concurrent datasets independent', async () => {
  const a = makeStore();
  const b = makeStore({ ...doc, id: 'queue-b', title: 'B' });
  const listener = vi.fn();
  const unsubscribe = a.store.subscribe(listener);
  a.store.patch({ energy_min_ev: -4 }, true);
  unsubscribe();
  b.store.patch({ elements: ['O'] });
  await vi.advanceTimersByTimeAsync(NUMERIC_SAVE_DELAY_MS);
  expect(a.transport.save.mock.calls[0][0].id).toBe('queue-a');
  expect(b.transport.save.mock.calls[0][0].id).toBe('queue-b');
  expect(a.store.getSnapshot().view.energy_min_ev).toBe(-4);
  expect(b.store.getSnapshot().view.energy_min_ev).toBe(-5);
  expect(listener).toHaveBeenCalledOnce();
});

it('retains an invalid edit made during a save without enqueueing it', async () => {
  const { store, transport } = makeStore();
  const first = deferred<{ dataset: PPDataset }>();
  transport.save.mockImplementationOnce(() => first.promise);
  store.patch({ mirror_down: true });
  await vi.advanceTimersByTimeAsync(0);
  store.patch({ energy_max_ev: Number.NaN }, true);
  first.resolve({ dataset: { ...doc, revision: 5, view: { ...view, mirror_down: true } } });
  await vi.advanceTimersByTimeAsync(1000);
  expect(transport.save).toHaveBeenCalledOnce();
  expect(store.getSnapshot().status).toBe('invalid');
  expect(store.getSnapshot().view.energy_max_ev).toBeNaN();
  store.patch({ energy_max_ev: 2 }, true);
  await vi.advanceTimersByTimeAsync(NUMERIC_SAVE_DELAY_MS);
  expect(transport.save.mock.calls[1][0].revision).toBe(5);
  expect(store.getSnapshot().view.energy_max_ev).toBe(2);
});

it('aborts a disposed dataset and ignores its eventual response', async () => {
  const { store, transport } = makeStore();
  const pending = deferred<{ dataset: PPDataset }>();
  transport.save.mockImplementationOnce(() => pending.promise);
  store.patch({ mirror_down: true });
  await vi.advanceTimersByTimeAsync(0);
  store.dispose();
  expect(transport.save.mock.calls[0][2]?.aborted).toBe(true);
  pending.resolve({ dataset: { ...doc, revision: 5, view: { ...view, mirror_down: true } } });
  await vi.advanceTimersByTimeAsync(0);
  expect(store.getSnapshot().savedDoc.revision).toBe(4);
});
