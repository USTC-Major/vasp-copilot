import type { EnergyAnalysisKind, EnergyAssignmentOrigin, EnergyBasis, EnergyCollection, EnergyComposition, EnergyConfiguration, EnergyGroup, EnergyOverride, EnergyRole, EnergySample } from '../../api/energy';

export const basisOptions: { value: EnergyBasis; label: string }[] = [
  { value: 'sigma_to_zero_ev', label: 'energy(sigma→0)' },
  { value: 'without_entropy_ev', label: 'energy without entropy' },
  { value: 'free_energy_toten_ev', label: 'free energy TOTEN' },
];
export const roleLabels: Record<EnergyRole, string> = { clean_slab: '清洁表面', adsorbate: '吸附物参考', adsorbed: '吸附构型', material: '材料目标', element_reference: '元素参考' };
export const analysisLabels: Record<EnergyAnalysisKind, string> = { adsorption: '吸附能', formation: '材料形成能' };
export function analysisKind(collection: EnergyCollection): EnergyAnalysisKind | null {
  const kinds = [...new Set(collection.groups.map(group => group.kind))];
  return collection.analysis_kind ?? (kinds.length === 1 ? kinds[0] : null);
}
export function legacyReadOnly(collection: EnergyCollection): boolean { return collection.groups.length > 1 || !analysisKind(collection); }
export const basisLabel = (basis: EnergyBasis) => basisOptions.find(option => option.value === basis)!.label;
export const compositionLabel = (composition: EnergyComposition | null | undefined) => composition ? Object.entries(composition).map(([element, count]) => `${element}:${count}`).join(' ') : '';
export const energyNumber = (value: number | null | undefined) => value == null ? '缺失' : value.toLocaleString('en-US', { useGrouping: false, maximumFractionDigits: 8 });
export const energyError = (error: unknown) => error instanceof Error ? error.message : '操作失败，请重试';

export function parseComposition(text: string): EnergyComposition {
  const value = text.trim();
  if (!value) throw new Error('请填写明确的元素计数，例如 Pt:4 C:1 O:1。');
  let composition: EnergyComposition;
  if (value.startsWith('{')) {
    try { composition = JSON.parse(value) as EnergyComposition; } catch { throw new Error('组成 JSON 格式不正确。'); }
  } else {
    composition = {};
    for (const token of value.split(/[\s,，;；]+/).filter(Boolean)) {
      const match = /^([A-Z][a-z]?)[:：=](\d+)$/.exec(token);
      if (!match || match[1] in composition) throw new Error('组成使用不重复的元素及正整数计数，例如 Pt:4 C:1 O:1。');
      composition[match[1]] = Number(match[2]);
    }
  }
  if (!composition || Array.isArray(composition) || typeof composition !== 'object' || !Object.keys(composition).length || Object.entries(composition).some(([element, count]) => !/^[A-Z][a-z]?$/.test(element) || !Number.isSafeInteger(count) || count <= 0)) throw new Error('组成必须包含明确元素和正整数原子计数。');
  return composition;
}
export function parseEnergy(text: string): number {
  if (!text.trim()) throw new Error('请填写能量；缺失能量不能补零。');
  const value = Number(text);
  if (!Number.isFinite(value)) throw new Error('能量必须为有限数值，单位 eV。');
  return value;
}

export type OverrideDraft = { composition: string; energy: string; energy_basis: EnergyBasis; note: string };
export type EnergyRowDraft = { sample_id: string; name: string; role: EnergyRole | null; included: boolean; confirmed: boolean; accepted_warnings: boolean; override: OverrideDraft | null; role_origin?: EnergyAssignmentOrigin; included_origin?: EnergyAssignmentOrigin };
export type EnergyDraft = { title: string; rows: EnergyRowDraft[]; groups: EnergyGroup[] };
export function draftFromCollection(collection: EnergyCollection): EnergyDraft {
  return {
    title: collection.title,
    rows: collection.samples.map(sample => {
      const basis = sample.override?.energy_basis ?? 'sigma_to_zero_ev';
      return { sample_id: sample.id, name: sample.name, role: sample.role, included: sample.included, confirmed: sample.confirmed, accepted_warnings: sample.accepted_warnings,
        role_origin: sample.role_origin ?? null, included_origin: sample.included_origin ?? null,
        override: sample.override ? { composition: sample.override.composition ? compositionLabel(sample.override.composition) : '', energy: sample.override.energy_fields?.[basis]?.toString() ?? '', energy_basis: basis, note: sample.override.note } : null };
    }),
    groups: collection.groups.length || !collection.analysis_kind || collection.legacy_mode !== false ? structuredClone(collection.groups) : [newEnergyGroup(collection.analysis_kind, 1)],
  };
}
function stable(value: unknown): string {
  return JSON.stringify(value, (_, item: unknown) => item && typeof item === 'object' && !Array.isArray(item) ? Object.fromEntries(Object.entries(item).sort(([a], [b]) => a.localeCompare(b))) : item);
}
function rowFact(collection: EnergyCollection, row: EnergyRowDraft | undefined): unknown {
  const sample = collection.samples.find(item => item.id === row?.sample_id);
  if (!row || !sample) return null;
  let composition: unknown = row.override?.composition.trim() || null;
  if (composition) { try { composition = parseComposition(String(composition)); } catch { /* Invalid drafts remain editable. */ } }
  const energy = row.override?.energy.trim();
  return { source: sample.source, parsed: sample.parsed, energy_basis: sample.energy_basis, role: row.role, included: row.included, override: row.override ? { composition, energy: energy ? Number.isFinite(Number(energy)) ? Number(energy) : energy : null, basis: energy ? row.override.energy_basis : null, unit: 'eV' } : null };
}
function referenceIds(group: EnergyGroup): string[] { return group.kind === 'adsorption' ? [group.clean_sample_id, group.adsorbate_sample_id].filter(Boolean) : Object.values(group.element_references); }
function referenceDefinition(collection: EnergyCollection, draft: EnergyDraft, group: EnergyGroup, elements?: string[]): unknown {
  const references = group.kind === 'formation' && elements ? Object.fromEntries(Object.entries(group.element_references).filter(([element]) => elements.includes(element))) : group.kind === 'formation' ? group.element_references : null;
  const ids = references ? Object.values(references) : referenceIds(group);
  return { kind: group.kind, basis: group.energy_basis, references: group.kind === 'adsorption' ? [group.clean_sample_id, group.adsorbate_sample_id] : references, units: group.kind === 'adsorption' ? group.reference_units : null, note: group.reference_note, facts: Object.fromEntries(ids.map(id => [id, rowFact(collection, draft.rows.find(row => row.sample_id === id))])) };
}
function rowDependencies(collection: EnergyCollection, draft: EnergyDraft, row: EnergyRowDraft): unknown {
  return { fact: rowFact(collection, row), bindings: draft.groups.flatMap<unknown>(group => {
    const target = group.targets.find(item => item.sample_id === row.sample_id);
    if (target) { const sample = collection.samples.find(item => item.id === row.sample_id); return [{ reference: referenceDefinition(collection, draft, group, group.kind === 'formation' ? Object.keys(sample ? effectiveComposition(sample, row) ?? {} : {}) : undefined), count: 'adsorbate_count' in target ? target.adsorbate_count : null }]; }
    if (referenceIds(group).includes(row.sample_id)) return [{ kind: group.kind, basis: group.energy_basis, units: group.kind === 'adsorption' ? group.reference_units : null, elements: group.kind === 'formation' ? Object.entries(group.element_references).filter(([, id]) => id === row.sample_id).map(([element]) => element).sort() : null }];
    return [];
  }) };
}
export function reconcileConfirmations(previousCollection: EnergyCollection, previous: EnergyDraft, next: EnergyDraft, nextCollection = previousCollection): EnergyDraft {
  return { ...next, rows: next.rows.map(row => {
    const old = previous.rows.find(item => item.sample_id === row.sample_id);
    const source = previousCollection.samples.find(item => item.id === row.sample_id);
    const updated = nextCollection.samples.find(item => item.id === row.sample_id);
    return { ...row, confirmed: old && stable(rowDependencies(previousCollection, previous, old)) === stable(rowDependencies(nextCollection, next, row)) ? row.confirmed : false,
      accepted_warnings: source && updated && stable({ source: source.source, parsed: source.parsed }) === stable({ source: updated.source, parsed: updated.parsed }) ? row.accepted_warnings : false };
  }), groups: next.groups.map(group => {
    const old = previous.groups.find(item => item.id === group.id);
    return { ...group, basis_confirmed: old && stable({ reference: referenceDefinition(previousCollection, previous, old), note: old.reference_note }) === stable({ reference: referenceDefinition(nextCollection, next, group), note: group.reference_note }) ? group.basis_confirmed : false };
  }) };
}
export function organizeTargets(draft: EnergyDraft): EnergyDraft {
  if (draft.groups.length !== 1) return draft;
  const group = draft.groups[0];
  const rows = draft.rows.filter(row => row.included && row.role === (group.kind === 'adsorption' ? 'adsorbed' : 'material'));
  const validReference = (id: string, role: EnergyRole) => draft.rows.some(row => row.sample_id === id && row.role === role);
  const next: EnergyGroup = group.kind === 'adsorption' ? { ...group,
    clean_sample_id: validReference(group.clean_sample_id, 'clean_slab') ? group.clean_sample_id : '', adsorbate_sample_id: validReference(group.adsorbate_sample_id, 'adsorbate') ? group.adsorbate_sample_id : '',
    targets: rows.map(row => ({ sample_id: row.sample_id, adsorbate_count: group.targets.find(target => target.sample_id === row.sample_id)?.adsorbate_count ?? 1 }))
  } : { ...group, element_references: Object.fromEntries(Object.entries(group.element_references).filter(([, id]) => validReference(id, 'element_reference'))), targets: rows.map(row => ({ sample_id: row.sample_id })) };
  return { ...draft, groups: [next] };
}
export function confirmedDraft(draft: EnergyDraft): EnergyDraft {
  return { ...draft, rows: draft.rows.map(row => ({ ...row, confirmed: row.included && !!row.role })), groups: draft.groups.map(group => ({ ...group, basis_confirmed: true })) };
}
export function calculationDraftKey(collection: EnergyCollection, draft: EnergyDraft): string {
  return stable({ rows: draft.rows.map(row => ({ id: row.sample_id, fact: rowFact(collection, row) })), groups: draft.groups.map(({ name: _name, basis_confirmed: _confirmed, reference_origins: _origins, ...group }) => group) });
}
export function removalImpact(draft: EnergyDraft, sampleIds: string[]) {
  const removed = new Set(sampleIds);
  return draft.groups.flatMap(group => referenceIds(group).filter(id => removed.has(id)).map(id => ({
    sample_id: id, name: draft.rows.find(row => row.sample_id === id)?.name ?? id, reference_name: group.name,
    affected_targets: group.targets.filter(target => !removed.has(target.sample_id)).map(target => ({ sample_id: target.sample_id, name: draft.rows.find(row => row.sample_id === target.sample_id)?.name ?? target.sample_id })),
  })));
}
export function rowOverride(row: EnergyRowDraft): EnergyOverride | null {
  if (!row.override) return null;
  if (!row.override.note.trim()) throw new Error(`${row.name}：请注明人工补充／覆盖的依据。`);
  const override: EnergyOverride = { unit: 'eV', note: row.override.note.trim() };
  if (row.override.composition.trim()) override.composition = parseComposition(row.override.composition);
  if (row.override.energy.trim()) { override.energy_basis = row.override.energy_basis; override.energy_fields = { [row.override.energy_basis]: parseEnergy(row.override.energy) }; }
  if (!override.composition && !override.energy_fields) throw new Error(`${row.name}：请填写人工能量或组成，或取消人工覆盖。`);
  return override;
}
export function configurationFromDraft(collection: EnergyCollection, draft: EnergyDraft): EnergyConfiguration {
  if (!draft.title.trim()) throw new Error('请填写比较集名称。');
  return { expected_revision: collection.revision, title: draft.title.trim(), samples: draft.rows.map(row => ({ sample_id: row.sample_id, name: row.name, role: row.role, included: row.included, confirmed: row.confirmed, accepted_warnings: row.accepted_warnings, override: rowOverride(row), role_origin: row.role_origin ?? null, included_origin: row.included_origin ?? null })), groups: draft.groups };
}
export function riskRequired(sample: EnergySample): boolean {
  const status = sample.parsed.status;
  return status.completion !== 'completed' || status.electronic_converged !== true || (status.ionic_applicability !== 'not_applicable' && status.ionic_converged !== true) || sample.parsed.warnings.length > 0;
}
export function effectiveComposition(sample: EnergySample, row: EnergyRowDraft): EnergyComposition | null {
  if (row.override?.composition.trim()) { try { return parseComposition(row.override.composition); } catch { return null; } }
  return sample.parsed.composition && Object.keys(sample.parsed.composition).length ? sample.parsed.composition : null;
}
export function effectiveEnergy(sample: EnergySample, row: EnergyRowDraft, basis: EnergyBasis): number | null {
  if (row.override?.energy.trim()) { if (row.override.energy_basis !== basis) return null; try { return parseEnergy(row.override.energy); } catch { return null; } }
  return sample.parsed.energy_fields[basis];
}
export function newEnergyGroup(kind: EnergyGroup['kind'], index: number): EnergyGroup {
  const common = { id: `g_${crypto.randomUUID()}`, name: `${kind === 'adsorption' ? '吸附比较' : '形成能比较'} ${index}`, energy_basis: 'sigma_to_zero_ev' as const, basis_confirmed: false, reference_note: '' };
  return kind === 'adsorption' ? { ...common, kind, clean_sample_id: '', adsorbate_sample_id: '', reference_units: 1, targets: [] } : { ...common, kind, element_references: {}, targets: [] };
}
export function calculationIssues(collection: EnergyCollection, draft: EnergyDraft): string[] {
  const issues: string[] = [];
  if (!draft.groups.length) return ['选择分析类型并配置参考与目标。'];
  for (const row of draft.rows.filter(item => item.included)) {
    if (!row.role) issues.push(`${row.name}：尚未指定角色。`);
    if (!draft.groups.some(group => group.targets.some(target => target.sample_id === row.sample_id) || referenceIds(group).includes(row.sample_id))) issues.push(`${row.name}：尚未用于目标或参考；请配置关联或取消纳入。`);
  }
  for (const group of draft.groups) {
    if (!group.basis_confirmed) issues.push(`${group.name}：能量字段尚未确认。`);
    if (!group.targets.length) issues.push(`${group.name}：尚未绑定目标样本。`);
    const referenced = group.kind === 'adsorption' ? [group.clean_sample_id, group.adsorbate_sample_id] : Object.values(group.element_references);
    if (group.kind === 'adsorption' && (!group.clean_sample_id || !group.adsorbate_sample_id || !Number.isSafeInteger(group.reference_units) || group.reference_units <= 0 || group.targets.some(target => !Number.isSafeInteger(target.adsorbate_count) || target.adsorbate_count <= 0))) issues.push(`${group.name}：清洁表面、吸附物参考及正整数单元数必须明确。`);
    if (group.kind === 'formation') {
      const elements = new Set(group.targets.flatMap(target => {
        const sample = collection.samples.find(item => item.id === target.sample_id);
        const row = draft.rows.find(item => item.sample_id === target.sample_id);
        return sample && row ? Object.keys(effectiveComposition(sample, row) ?? {}) : [];
      }));
      if (!elements.size || [...elements].some(element => !group.element_references[element])) issues.push(`${group.name}：目标组成或所需元素参考缺失。`);
    }
    for (const id of new Set([...referenced, ...group.targets.map(target => target.sample_id)].filter(Boolean))) {
      const sample = collection.samples.find(item => item.id === id);
      const row = draft.rows.find(item => item.sample_id === id);
      if (!sample || !row) { issues.push(`${group.name}：引用的来源不存在。`); continue; }
      if (!row.included || !row.confirmed) issues.push(`${row.name}：需要纳入并人工确认。`);
      if (riskRequired(sample) && !row.accepted_warnings) issues.push(`${row.name}：未完成／未知状态或警告尚未明确接受。`);
      const repaired = !!row.override?.composition.trim() && !!effectiveComposition(sample, row);
      if (sample.parsed.issues?.some(issue => issue.severity === 'error' && !(issue.recoverable_by_manual && repaired)) || (!sample.parsed.issues?.length && sample.parsed.errors.length)) issues.push(`${row.name}：存在不可豁免的解析／计算类型错误。`);
      if (!effectiveComposition(sample, row)) issues.push(`${row.name}：组成缺失或无效。`);
      if (effectiveEnergy(sample, row, group.energy_basis) == null) issues.push(`${row.name}：缺少 ${basisLabel(group.energy_basis)}，不会回退到其他字段。`);
      const manualBasis = row.override?.energy.trim() ? row.override.energy_basis : sample.energy_basis;
      if (manualBasis && manualBasis !== group.energy_basis) issues.push(`${row.name}：人工能量口径与组字段不一致。`);
    }
  }
  return [...new Set(issues)];
}
export function saveEnergyBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a'); link.href = url; link.download = name; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
