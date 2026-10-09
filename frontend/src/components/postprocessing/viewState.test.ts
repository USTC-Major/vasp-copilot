import type { PPDataset, PPView } from '../../api/postprocessing';
import { normalizeView, parseNumericDraft, selectedAtoms, selectedOrbitals, validateView, viewKey } from './viewState';

const legacy: PPView = { reference: 'custom', reference_ev: 5, mirror_down: false, atoms: [1], orbitals: [], band_start: 1, band_end: 2, axes: { version: 'pp.axes.v1', x_min: -8, x_max: 4, x_interval: 2, y_min: 0, y_max: 5, y_interval: 1 } };
const doc: PPDataset = { id: 'old', title: 'old', kind: 'dos', status: 'ready', revision: 2, files: [], error: null, view: legacy, summary: { spin_mode: 'none', efermi_ev: 3, convergence: 'unknown', warnings: [], atoms: [[1, 'Fe'], [2, 'O']], orbitals: ['s', 'px'], band_count: 2 } };

it('preserves old custom graph reference and energy range while making its relative offset explicit', () => {
  const migrated = normalizeView(doc);
  expect(migrated).toMatchObject({ version: 'pp.view.v2', reference: 'custom', reference_ev: 2, energy_min_ev: -8, energy_max_ev: 4, orbitals: ['s', 'px'], projection_grouping: 'combined' });
  expect(selectedAtoms(migrated, doc.summary!.atoms)).toEqual([1]);
});

it('keeps a missing-EF old custom record in the explicit legacy absolute reference', () => {
  const missing = { ...doc, summary: { ...doc.summary!, efermi_ev: null } };
  expect(normalizeView(missing)).toMatchObject({ reference: 'legacy_absolute', reference_ev: 5, energy_min_ev: -8, energy_max_ev: 4 });
  expect(validateView({ ...normalizeView(missing), reference: 'custom' }, missing)).toContain('缺少费米能级');
});

it('treats explicit v2 empty orbital and site selections as empty and expands group/component choices without duplicates', () => {
  const current = { ...normalizeView(doc), elements: [], atoms: [], orbitals: [] };
  expect(selectedAtoms(current, doc.summary!.atoms)).toEqual([]);
  expect(selectedOrbitals(current, ['s', 'px', 'py'])).toEqual([]);
  expect(selectedOrbitals({ ...current, orbitals: ['p', 'px'] }, ['s', 'px', 'py'])).toEqual(['px', 'py']);
});

it('preserves unfinished numeric text as invalid rather than coercing it to zero', () => {
  for (const text of ['', ' ', '-', '+', 'abc', '1e', 'Infinity', '1e9999']) expect(parseNumericDraft(text)).toBeNaN();
  expect(parseNumericDraft('-3.5')).toBe(-3.5);
  expect(parseNumericDraft('+.2')).toBe(.2);
});

it('compares equivalent saved selections independently of response property and selection ordering', () => {
  const a = { ...normalizeView(doc), elements: ['Fe', 'O'], atoms: [1, 2], orbitals: ['s', 'px'] };
  const b = Object.fromEntries(Object.entries(a).reverse()) as PPView;
  expect(viewKey(a)).toBe(viewKey({ ...b, elements: ['O', 'Fe'], atoms: [2, 1], orbitals: ['px', 's'] }));
});
