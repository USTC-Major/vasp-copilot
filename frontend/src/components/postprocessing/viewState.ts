import type { PPDataset, PPView } from '../../api/postprocessing';

export function parseNumericDraft(text: string): number {
  const clean = text.trim();
  if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(clean)) return Number.NaN;
  const value = Number(clean);
  return Number.isFinite(value) ? value : Number.NaN;
}

/** The backend migrates persisted v1 views; this also keeps old cached responses readable. */
export function normalizeView(doc: PPDataset): PPView {
  const source = doc.view;
  const energyAxis = doc.kind === 'dos' ? 'x' : 'y';
  const legacy = source.version !== 'pp.view.v2';
  const atoms = doc.summary?.atoms ?? [];
  const allElements = [...new Set(atoms.map(([, element]) => element))];
  let reference = source.reference;
  let referenceEv = source.reference_ev;
  if (legacy && reference === 'custom') {
    const ef = doc.summary?.efermi_ev;
    if (ef == null) reference = 'legacy_absolute';
    else referenceEv -= ef;
  }
  return {
    ...source, version: 'pp.view.v2', reference, reference_ev: referenceEv,
    elements: source.elements ?? (legacy && !source.atoms.length && source.orbitals.length ? allElements : []),
    orbitals: legacy && !source.orbitals.length ? [...(doc.summary?.orbitals ?? [])] : source.orbitals,
    projection_grouping: source.projection_grouping ?? (legacy ? 'combined' : 'element'),
    energy_min_ev: source.energy_min_ev ?? source.axes?.[`${energyAxis}_min`] ?? (doc.kind === 'dos' ? -5 : -3),
    energy_max_ev: source.energy_max_ev ?? source.axes?.[`${energyAxis}_max`] ?? (doc.kind === 'dos' ? 3 : 2),
  };
}

export function validateView(view: PPView, doc: PPDataset): string {
  const min = view.energy_min_ev, max = view.energy_max_ev;
  if (!Number.isFinite(min) || !Number.isFinite(max)) return '请输入完整的能量起点和终点';
  if (!Number.isFinite(max! - min!) || min! >= max!) return '能量起点必须小于终点，跨度须为有限数值';
  if (view.reference === 'fermi' || view.reference === 'custom') {
    if (doc.summary?.efermi_ev == null) return '文件缺少费米能级，无法使用该能量参考';
  }
  if (!Number.isFinite(view.reference_ev)) return '请输入有限的零点偏移';
  if (doc.kind === 'band' && (!Number.isInteger(view.band_start) || !Number.isInteger(view.band_end)
      || view.band_start < 1 || view.band_start > view.band_end || view.band_end > (doc.summary?.band_count ?? 0))) return '能带范围须为有效整数，起始能带不能大于结束能带';
  return '';
}

/** axes are server-derived, and do not belong to the user's editable intent. */
export function editableView(view: PPView): PPView {
  const { axes: _axes, ...intent } = view;
  return intent;
}

export function viewKey(view: PPView): string {
  return JSON.stringify({
    version: view.version, reference: view.reference, reference_ev: view.reference_ev,
    energy_min_ev: view.energy_min_ev, energy_max_ev: view.energy_max_ev,
    mirror_down: view.mirror_down, band_start: view.band_start, band_end: view.band_end,
    atoms: [...view.atoms].sort((a, b) => a - b), elements: [...(view.elements ?? [])].sort(),
    orbitals: [...view.orbitals].sort(), projection_grouping: view.projection_grouping,
  });
}

export function selectedAtoms(view: PPView, atoms: [number, string][]): number[] {
  const elements = new Set(view.elements ?? []);
  const ids = new Set(view.atoms);
  return atoms.filter(([id, element]) => ids.size ? ids.has(id) && (!elements.size || elements.has(element)) : elements.has(element)).map(([id]) => id);
}

/** A partial element needs an exact global list, including atoms of fully selected elements. */
export function atomSelectionPatch(ids: number[], atoms: [number, string][]): Partial<PPView> {
  const selected = new Set(ids);
  const elements = [...new Set(atoms.filter(([id]) => selected.has(id)).map(([, element]) => element))];
  const allSelected = atoms.filter(([, element]) => elements.includes(element)).every(([id]) => selected.has(id));
  return { elements, atoms: allSelected ? [] : atoms.filter(([id]) => selected.has(id)).map(([id]) => id), projection_grouping: 'element' };
}

export function orbitalGroups(orbitals: string[]): { name: string; components: string[] }[] {
  return ['s', 'p', 'd', 'f'].map(name => ({ name, components: orbitals.filter(orbital => orbital.toLowerCase().startsWith(name)) })).filter(group => group.components.length);
}

export function selectedOrbitals(view: PPView, available: string[]): string[] {
  const choices = new Set(view.orbitals);
  return available.filter(component => choices.has(component) || choices.has(component[0].toLowerCase()));
}

export function effectiveReference(view: PPView, efermi: number | null): number | null {
  if (view.reference === 'raw') return 0;
  if (view.reference === 'legacy_absolute') return view.reference_ev;
  if (efermi == null) return null;
  return efermi + (view.reference === 'custom' ? view.reference_ev : 0);
}
