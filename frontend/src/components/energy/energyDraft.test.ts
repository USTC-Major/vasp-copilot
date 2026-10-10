import { describe, expect, it } from 'vitest';
import { calculationIssues, configurationFromDraft, draftFromCollection, effectiveEnergy, invalidateConfirmations, parseComposition, parseEnergy } from './energyDraft';
import { energyFixture } from './energyTestFixtures';

describe('energy confirmation and scientific input drafts', () => {
  it('preserves source IDs and clears confirmations after a scientific definition changes', () => {
    const collection = energyFixture(true);
    const draft = invalidateConfirmations(draftFromCollection(collection));
    expect(draft.rows.map(row => row.sample_id)).toEqual(['es_clean', 'es_reference', 'es_target']);
    expect(draft.rows.every(row => !row.confirmed && !row.accepted_warnings)).toBe(true);
    expect(draft.groups.every(group => !group.basis_confirmed)).toBe(true);
    const saved = configurationFromDraft(collection, draft);
    expect(saved.expected_revision).toBe(4);
    expect(saved.samples.every(row => !row.confirmed)).toBe(true);
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
