import { afterEach, expect, it } from 'vitest';
import { cardConfiguration, cardsReadOnly, cardScienceKey, validateCard, validateSharedDraft } from './energyCards';
import { draftFromCollection } from './energyDraft';
import { energyFixture } from './energyTestFixtures';
import { clearEnergyDraftSessions, energyDraftSessions } from './energyDraftSessions';

afterEach(clearEnergyDraftSessions);
it('strips state and every response extension from the outgoing card configuration', () => {
  const group = { ...energyFixture(true).groups[0], locked: true, confirmed: true, risks: [{ sample_id: 'es_target', name: 'risk', warnings: ['unknown'] }], assignment_report: { state: 'pending' as const, issues: [] }, result: energyFixture(true).result, status: 'result' as const };
  expect(cardConfiguration(group)).toEqual(energyFixture(true).groups[0]);
  expect(cardScienceKey(group)).toBe(cardScienceKey({ ...group, name: 'new display name' }));
  expect(cardScienceKey(group)).not.toBe(cardScienceKey({ ...group, reference_note: 'new scientific unit definition' }));
});
it('validates only actual card references regardless of legacy role, inclusion or unrelated cards', () => {
  const collection = energyFixture(); collection.samples.forEach(sample => { sample.role = null; sample.included = false; });
  collection.groups.push({ ...collection.groups[0], id: 'g_other', targets: [] });
  const draft = draftFromCollection(collection);
  expect(validateCard(collection, draft, draft.groups[0])).toEqual([]);
  expect(cardsReadOnly({ ...collection, analysis_kind: 'adsorption', card_migration: { required: true, read_only: false, reason: null } })).toBe(false);
});
it('locates quantity and composition mismatches by stable target id and never accepts hard errors', () => {
  const collection = energyFixture(true), draft = draftFromCollection(collection), group = draft.groups[0];
  if (group.kind !== 'adsorption') throw new Error('fixture kind');
  group.targets[0].adsorbate_count = 2;
  expect(validateCard(collection, draft, group)).toContainEqual(expect.objectContaining({ card_id: 'g_ads', sample_id: 'es_target', field: 'targets.es_target.adsorbate_count', message: expect.stringContaining('计量不一致') }));
  group.targets[0].adsorbate_count = 0;
  expect(validateCard(collection, draft, group)).toContainEqual(expect.objectContaining({ field: 'targets.es_target.adsorbate_count', code: 'ENERGY_INVALID_QUANTITY' }));
  collection.samples[0].parsed.issues = [{ code: 'UNSUPPORTED', severity: 'error', message: 'unsupported', recoverable_by_manual: false }];
  expect(validateCard(collection, draft, group)).toContainEqual(expect.objectContaining({ sample_id: 'es_clean', message: expect.stringContaining('不可豁免') }));
});
it('maps invalid shared values to their actual fields instead of a generic card quantity or note', () => {
  const draft = draftFromCollection(energyFixture());
  draft.rows[0].override = { composition: 'Pt:not-a-count', energy: 'not-energy', note: '', energy_basis: 'sigma_to_zero_ev' };
  expect(validateSharedDraft(draft).map(error => error.field)).toEqual(['samples.es_clean.override.composition', 'samples.es_clean.override.energy', 'samples.es_clean.override.note']);
});
it('restores per-card drafts and active card from browser session storage after the memory cache is recreated', () => {
  const collection = energyFixture(), draft = draftFromCollection(collection);
  draft.groups[0].reference_note = 'unsaved scientific definition';
  energyDraftSessions.set(collection.id, { collection, draft, dirty: true, conflict: false, activeCardId: 'g_ads', acceptedRisks: { g_ads: false } });
  Map.prototype.clear.call(energyDraftSessions);
  expect(energyDraftSessions.get(collection.id)).toMatchObject({ activeCardId: 'g_ads', dirty: true, draft: { groups: [{ reference_note: 'unsaved scientific definition' }] } });
});
