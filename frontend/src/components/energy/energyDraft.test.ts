import { describe, expect, it } from 'vitest';
import { calculationIssues, configurationFromDraft, confirmedDraft, draftFromCollection, effectiveEnergy, organizeTargets, reconcileConfirmations, parseComposition, parseEnergy } from './energyDraft';
import { energyFixture } from './energyTestFixtures';

describe('energy confirmation and scientific input drafts', () => {
  it('preserves unrelated confirmations and all risk acceptance when a target coefficient changes', () => {
    const collection = energyFixture(true);
    const previous = draftFromCollection(collection);
    const changed = structuredClone(previous);
    if (changed.groups[0].kind === 'adsorption') changed.groups[0].targets[0].adsorbate_count = 2;
    const draft = reconcileConfirmations(collection, previous, changed);
    expect(draft.rows.map(row => row.sample_id)).toEqual(['es_clean', 'es_reference', 'es_target']);
    expect(draft.rows.map(row => row.confirmed)).toEqual([true, true, false]);
    expect(draft.rows.every(row => row.accepted_warnings)).toBe(true);
    expect(draft.groups[0].basis_confirmed).toBe(true);
    const saved = configurationFromDraft(collection, draft);
    expect(saved.expected_revision).toBe(4);
    expect(saved.samples[2].confirmed).toBe(false);
  });
  it('automatically includes a new target without clearing existing row or reference confirmations', () => {
    const collection = energyFixture(true);
    const sample = structuredClone(collection.samples[2]); sample.id = 'es_second'; sample.confirmed = false;
    collection.samples.push(sample);
    const previous = draftFromCollection(collection);
    previous.rows[3].included = false;
    const changed = organizeTargets({ ...previous, rows: previous.rows.map(row => row.sample_id === sample.id ? { ...row, included: true } : row) });
    const draft = reconcileConfirmations(collection, previous, changed);
    expect(draft.groups[0].targets).toHaveLength(2);
    expect(draft.rows.slice(0, 3).every(row => row.confirmed && row.accepted_warnings)).toBe(true);
    expect(draft.groups[0].basis_confirmed).toBe(true);
  });
  it('invalidates only users of a changed reference and never grants risk acceptance through uniform confirmation', () => {
    const collection = energyFixture(true); const previous = draftFromCollection(collection);
    const changed = structuredClone(previous);
    changed.rows[0].override = { composition: '', energy: '-101', energy_basis: 'sigma_to_zero_ev', note: '合成修订' };
    const draft = reconcileConfirmations(collection, previous, changed);
    expect(draft.rows.map(row => row.confirmed)).toEqual([false, true, false]);
    expect(draft.groups[0].basis_confirmed).toBe(false);
    expect(draft.rows.every(row => row.accepted_warnings)).toBe(true);
    const unknown = draftFromCollection(energyFixture(false));
    expect(confirmedDraft(unknown).rows.every(row => !row.accepted_warnings)).toBe(true);
  });
  it('keeps presentation names and manual notes from invalidating scientific confirmations', () => {
    const collection = energyFixture(true); const previous = draftFromCollection(collection);
    previous.rows[0].override = { composition: '', energy: '-101', energy_basis: 'sigma_to_zero_ev', note: '旧说明' };
    const changed = structuredClone(previous); changed.rows[0].name = '新名称'; changed.rows[0].override!.note = '补充来源说明'; changed.rows[0].role_origin = 'manual'; changed.rows[0].included_origin = 'manual'; changed.groups[0].name = '新条件名称'; changed.groups[0].reference_origins = { clean_sample_id: 'manual' };
    const draft = reconcileConfirmations(collection, previous, changed);
    expect(draft.rows.every(row => row.confirmed && row.accepted_warnings)).toBe(true);
    expect(draft.groups[0].basis_confirmed).toBe(true);
    expect(configurationFromDraft(collection, draft).samples[0]).toMatchObject({ role_origin: 'manual', included_origin: 'manual' });
  });
  it('keeps formation targets confirmed when only an unused element reference changes', () => {
    const collection = energyFixture(true);
    collection.samples[0].role = 'element_reference';
    collection.samples[1].role = 'element_reference'; collection.samples[1].parsed.composition = { O: 2 };
    collection.samples[2].role = 'material'; collection.samples[2].parsed.composition = { Pt: 4 };
    const oxygenTarget = structuredClone(collection.samples[2]); oxygenTarget.id = 'es_oxygen'; oxygenTarget.parsed.composition = { O: 4 }; collection.samples.push(oxygenTarget);
    collection.groups = [{ id: 'g_f', kind: 'formation', name: '合成形成能', energy_basis: 'sigma_to_zero_ev', basis_confirmed: true, reference_note: '合成参考', element_references: { Pt: 'es_clean', O: 'es_reference' }, targets: [{ sample_id: 'es_target' }, { sample_id: 'es_oxygen' }] }];
    const previous = draftFromCollection(collection); const changed = structuredClone(previous);
    changed.rows[1].override = { composition: '', energy: '-11', energy_basis: 'sigma_to_zero_ev', note: '合成参考修订' };
    const draft = reconcileConfirmations(collection, previous, changed);
    expect(draft.rows.find(row => row.sample_id === 'es_target')?.confirmed).toBe(true);
    expect(draft.rows.find(row => row.sample_id === 'es_oxygen')?.confirmed).toBe(false);
    expect(draft.groups[0].basis_confirmed).toBe(false);
  });
  it('requires explicit unknown-state acceptance and refuses missing selected fields', () => {
    const collection = energyFixture(false);
    const draft = draftFromCollection(collection);
    draft.rows.forEach(row => { row.confirmed = true; }); draft.groups.forEach(group => { group.basis_confirmed = true; });
    expect(calculationIssues(collection, draft).some(issue => issue.includes('尚未明确接受'))).toBe(true);
    draft.rows.forEach(row => { row.accepted_warnings = true; });
    expect(calculationIssues(collection, draft)).toEqual([]);
    draft.groups[0].energy_basis = 'free_energy_toten_ev';
    expect(calculationIssues(collection, draft).some(issue => issue.includes('不会回退'))).toBe(true);
  });
  it('keeps manual overrides separate and never reuses another field', () => {
    const collection = energyFixture(true); const draft = draftFromCollection(collection);
    draft.rows[2].override = { composition: 'Pt:4 C:1 O:1', energy: '-113', energy_basis: 'sigma_to_zero_ev', note: '合成手填覆盖' };
    const saved = configurationFromDraft(collection, draft);
    expect(saved.samples[2].override?.energy_fields).toEqual({ sigma_to_zero_ev: -113 });
    expect(collection.samples[2].parsed.energy_fields.sigma_to_zero_ev).toBe(-112);
    expect(effectiveEnergy(collection.samples[2], draft.rows[2], 'free_energy_toten_ev')).toBeNull();
    draft.rows[2].override.energy = 'NaN';
    expect(() => configurationFromDraft(collection, draft)).toThrow('有限数值');
    expect(draft.rows[2].override.energy).toBe('NaN');
  });
  it('allows draft groups without targets while calculation remains blocked', () => {
    const collection = energyFixture(); const draft = draftFromCollection(collection); draft.groups[0].targets = [];
    expect(configurationFromDraft(collection, draft).groups[0].targets).toEqual([]);
    expect(calculationIssues(collection, draft).some(issue => issue.includes('尚未绑定目标'))).toBe(true);
  });
  it('rejects blank/nonfinite energy and ambiguous or noninteger composition', () => {
    expect(parseComposition('Pt:4 C:1 O:1')).toEqual({ Pt: 4, C: 1, O: 1 });
    expect(parseComposition('{"Al":2,"O":3}')).toEqual({ Al: 2, O: 3 });
    for (const input of ['', 'NaN', 'Infinity']) expect(() => parseEnergy(input)).toThrow();
    for (const input of ['Pt:0', 'Pt:4 Pt:4', '{"O":1.5}', 'CO']) expect(() => parseComposition(input)).toThrow();
  });
});
