import type { EnergyCollection, EnergyFieldError, EnergyGroup, EnergySamplePatch } from '../../api/energy';
import { basisLabel, effectiveComposition, effectiveEnergy, parseComposition, parseEnergy, rowOverride, type EnergyDraft } from './energyDraft';

export const collectionCards = (collection: EnergyCollection) => collection.card_projection ?? collection.groups;
export const cardsReadOnly = (collection: EnergyCollection) => collection.card_migration?.read_only ?? (!collection.analysis_kind && new Set(collection.groups.map(group => group.kind)).size !== 1);
export function cardConfiguration(group: EnergyGroup): EnergyGroup {
  const common = { id: group.id, name: group.name, kind: group.kind, energy_basis: group.energy_basis, basis_confirmed: group.basis_confirmed, reference_note: group.reference_note, ...(group.reference_origins ? { reference_origins: group.reference_origins } : {}) };
  return group.kind === 'adsorption' ? { ...common, kind: 'adsorption', clean_sample_id: group.clean_sample_id, adsorbate_sample_id: group.adsorbate_sample_id, reference_units: group.reference_units, targets: group.targets.map(target => ({ sample_id: target.sample_id, adsorbate_count: target.adsorbate_count })) } : { ...common, kind: 'formation', element_references: group.element_references, targets: group.targets.map(target => ({ sample_id: target.sample_id })) };
}
export const cardKey = (group: EnergyGroup) => JSON.stringify(cardConfiguration(group));
export const cardScienceKey = (group: EnergyGroup) => { const { name: _name, basis_confirmed: _basis, reference_origins: _origin, ...configuration } = cardConfiguration(group); return JSON.stringify(configuration); };
export function cardSampleIds(group: EnergyGroup): string[] {
  return [...new Set([...(group.kind === 'adsorption' ? [group.clean_sample_id, group.adsorbate_sample_id] : Object.values(group.element_references)), ...group.targets.map(target => target.sample_id)].filter(Boolean))];
}
export function cardUse(group: EnergyGroup, sampleId: string): string[] {
  const uses: string[] = [];
  if (group.targets.some(target => target.sample_id === sampleId)) uses.push(group.kind === 'adsorption' ? '目标构型' : '材料目标');
  if (group.kind === 'adsorption') { if (group.clean_sample_id === sampleId) uses.push('清洁表面参考'); if (group.adsorbate_sample_id === sampleId) uses.push('吸附物参考'); }
  else Object.entries(group.element_references).forEach(([element, id]) => { if (id === sampleId) uses.push(`${element} 元素参考`); });
  return uses;
}
export const energyFieldKey = (cardId: string | null | undefined, field: string) => `${cardId ?? 'shared'}:${field}`;
export function quantityIssue(group: EnergyGroup, field: string): EnergyFieldError[] {
  const sampleId = field.startsWith('targets.') ? field.split('.')[1] : null;
  const value = field === 'reference_units' && group.kind === 'adsorption' ? group.reference_units : group.kind === 'adsorption' ? group.targets.find(target => target.sample_id === sampleId)?.adsorbate_count : undefined;
  return Number.isSafeInteger(value) && Number(value) > 0 ? [] : [{ card_id: group.id, sample_id: sampleId, field, code: 'ENERGY_INVALID_QUANTITY', message: '请填写正整数，空值、零、负数或小数不能用于计算。' }];
}
export function validateCard(collection: EnergyCollection, draft: EnergyDraft, group: EnergyGroup): EnergyFieldError[] {
  const errors: EnergyFieldError[] = [];
  const add = (field: string, message: string, sampleId: string | null = null) => errors.push({ card_id: group.id, sample_id: sampleId, field, code: 'ENERGY_CARD_INPUT', message });
  if (!group.name.trim()) add('name', '请填写计算卡名称。');
  if (!group.targets.length) add('targets', '至少选择一个目标样本。');
  if (group.kind === 'adsorption') {
    if (!group.clean_sample_id) add('clean_sample_id', '请选择清洁表面参考。');
    if (!group.adsorbate_sample_id) add('adsorbate_sample_id', '请选择吸附物参考。');
    errors.push(...quantityIssue(group, 'reference_units'));
    group.targets.forEach(target => errors.push(...quantityIssue(group, `targets.${target.sample_id}.adsorbate_count`)));
  }
  for (const sampleId of cardSampleIds(group)) {
    const sample = collection.samples.find(item => item.id === sampleId);
    const row = draft.rows.find(item => item.sample_id === sampleId);
    if (!sample || !row) { add('targets', '引用的样本已不存在，请重新配置。', sampleId); continue; }
    const composition = effectiveComposition(sample, row);
    if (!composition) add(`samples.${sampleId}.override.composition`, `${row.name}：组成缺失或无效。`, sampleId);
    if (effectiveEnergy(sample, row, group.energy_basis) == null) add('energy_basis', `${row.name}：缺少 ${basisLabel(group.energy_basis)}，请补充该字段或选择一致口径。`, sampleId);
    if ((row.override?.energy.trim() ? row.override.energy_basis : sample.energy_basis) && (row.override?.energy.trim() ? row.override.energy_basis : sample.energy_basis) !== group.energy_basis) add('energy_basis', `${row.name}：人工能量口径与当前卡不一致。`, sampleId);
    const repaired = !!row.override?.composition.trim() && !!composition;
    if (sample.parsed.issues?.some(issue => issue.severity === 'error' && !(issue.recoverable_by_manual && repaired)) || (!sample.parsed.issues?.length && sample.parsed.errors.length)) add('targets', `${row.name}：存在不可豁免的解析或计算类型错误。`, sampleId);
    errors.push(...validateSharedDraft({ ...draft, rows: [row] }).map(error => ({ ...error, card_id: group.id })));
    if (group.kind === 'formation' && group.targets.some(target => target.sample_id === sampleId)) Object.keys(composition ?? {}).forEach(element => { if (!group.element_references[element]) add(`element_references.${element}`, `${element} 元素参考缺失。`, sampleId); });
  }
  if (group.kind === 'adsorption' && !errors.some(error => ['reference_units', 'clean_sample_id', 'adsorbate_sample_id'].includes(error.field))) {
    const compositionFor = (id: string) => { const sample = collection.samples.find(item => item.id === id); const row = draft.rows.find(item => item.sample_id === id); return sample && row ? effectiveComposition(sample, row) : null; };
    const clean = compositionFor(group.clean_sample_id), reference = compositionFor(group.adsorbate_sample_id);
    if (clean && reference) for (const target of group.targets) {
      if (quantityIssue(group, `targets.${target.sample_id}.adsorbate_count`).length) continue;
      const composition = compositionFor(target.sample_id);
      if (composition && [...new Set([...Object.keys(clean), ...Object.keys(reference), ...Object.keys(composition)])].some(element => (composition[element] ?? 0) * group.reference_units !== (clean[element] ?? 0) * group.reference_units + (reference[element] ?? 0) * target.adsorbate_count)) add(`targets.${target.sample_id}.adsorbate_count`, '目标组成与清洁表面及 n/m 参考计量不一致，请核对数量、参考单元与组成。', target.sample_id);
    }
  }
  return errors;
}
export function validateSharedDraft(draft: EnergyDraft): EnergyFieldError[] {
  const errors: EnergyFieldError[] = [];
  for (const row of draft.rows) {
    const add = (field: string, message: string) => errors.push({ card_id: null, sample_id: row.sample_id, field: `samples.${row.sample_id}.${field}`, code: 'ENERGY_SAMPLE_INPUT', message: `${row.name || row.sample_id}：${message}` });
    if (!row.name.trim()) add('name', '请填写样本名称。');
    if (!row.override) continue;
    if (row.override.composition.trim()) try { parseComposition(row.override.composition); } catch (cause) { add('override.composition', cause instanceof Error ? cause.message : '组成无效。'); }
    if (row.override.energy.trim()) try { parseEnergy(row.override.energy); } catch (cause) { add('override.energy', cause instanceof Error ? cause.message : '能量无效。'); }
    if (!row.override.note.trim()) add('override.note', '请注明人工补充／覆盖依据。');
    if (!row.override.composition.trim() && !row.override.energy.trim()) add('override.energy', '请填写人工能量或组成，或取消人工覆盖。');
  }
  return errors;
}
export function samplePatches(collection: EnergyCollection, draft: EnergyDraft): EnergySamplePatch[] {
  return draft.rows.flatMap(row => {
    const sample = collection.samples.find(item => item.id === row.sample_id);
    if (!sample) return [];
    const override = rowOverride(row);
    const patch: EnergySamplePatch = { sample_id: row.sample_id };
    if (row.name !== sample.name) patch.name = row.name;
    if (JSON.stringify(override) !== JSON.stringify(sample.override)) patch.override = override;
    return Object.keys(patch).length > 1 ? [patch] : [];
  });
}
