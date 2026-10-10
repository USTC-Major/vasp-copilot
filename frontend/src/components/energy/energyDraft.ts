import type { EnergyBasis, EnergyCollection, EnergyComposition, EnergyConfiguration, EnergyGroup, EnergyOverride, EnergyRole, EnergySample } from '../../api/energy';

export const basisOptions: { value: EnergyBasis; label: string }[] = [
  { value: 'sigma_to_zero_ev', label: 'energy(sigma→0)' },
  { value: 'without_entropy_ev', label: 'energy without entropy' },
  { value: 'free_energy_toten_ev', label: 'free energy TOTEN' },
];
export const roleLabels: Record<EnergyRole, string> = { clean_slab: '清洁表面', adsorbate: '吸附物参考', adsorbed: '吸附构型', material: '材料目标', element_reference: '元素参考' };
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
export type EnergyRowDraft = { sample_id: string; name: string; role: EnergyRole | null; included: boolean; confirmed: boolean; accepted_warnings: boolean; override: OverrideDraft | null };
export type EnergyDraft = { title: string; rows: EnergyRowDraft[]; groups: EnergyGroup[] };
export function draftFromCollection(collection: EnergyCollection): EnergyDraft {
  return {
    title: collection.title,
    rows: collection.samples.map(sample => {
      const basis = sample.override?.energy_basis ?? 'sigma_to_zero_ev';
      return { sample_id: sample.id, name: sample.name, role: sample.role, included: sample.included, confirmed: sample.confirmed, accepted_warnings: sample.accepted_warnings,
        override: sample.override ? { composition: sample.override.composition ? compositionLabel(sample.override.composition) : '', energy: sample.override.energy_fields?.[basis]?.toString() ?? '', energy_basis: basis, note: sample.override.note } : null };
    }),
    groups: structuredClone(collection.groups),
  };
}
export function invalidateConfirmations(draft: EnergyDraft): EnergyDraft {
  return { ...draft, rows: draft.rows.map(row => ({ ...row, confirmed: false, accepted_warnings: false })), groups: draft.groups.map(group => ({ ...group, basis_confirmed: false })) };
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
  return { expected_revision: collection.revision, title: draft.title.trim(), samples: draft.rows.map(row => ({ sample_id: row.sample_id, name: row.name, role: row.role, included: row.included, confirmed: row.confirmed, accepted_warnings: row.accepted_warnings, override: rowOverride(row) })), groups: draft.groups };
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
  if (!draft.groups.length) return ['添加一个吸附能或形成能比较组，并绑定参考与目标。'];
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
