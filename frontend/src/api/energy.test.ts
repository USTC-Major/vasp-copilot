import { expect, it, afterEach, vi } from 'vitest';
import { ApiError } from './client';
import { energyApi } from './energy';
import { energyFixture } from '../components/energy/energyTestFixtures';
import { cardConfiguration } from '../components/energy/energyCards';

afterEach(() => vi.restoreAllMocks());
it('uses independent collection/sample identities and encodes duplicate filename metadata', async () => {
  const collection = energyFixture();
  const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(JSON.stringify({ mode: 'toolbox', collection }), { status: 201 }));
  const first = new File(['synthetic-1'], 'OUTCAR'); const second = new File(['synthetic-2'], 'OUTCAR');
  await energyApi.upload(collection, first, '中文 表面/位点 A/OUTCAR');
  await energyApi.upload({ ...collection, revision: 5 }, second, '中文 表面/位点 B/OUTCAR');
  expect(fetch.mock.calls[0][0]).toContain('/energy/collections/ec_fixture/outcar?');
  expect(new URL(`https://fixture.invalid${fetch.mock.calls[0][0]}`).searchParams.get('relative_path')).toBe('中文 表面/位点 A/OUTCAR');
  expect(new URL(`https://fixture.invalid${fetch.mock.calls[1][0]}`).searchParams.get('expected_revision')).toBe('5');
  expect(fetch.mock.calls[0][1]?.body).toBe(first); expect(fetch.mock.calls[1][1]?.body).toBe(second);
});
it('retains exact controlled task identity and supports offline cache reuse', async () => {
  const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response('{}', { status: 200 }));
  const identity = { project_id: 'p', task_id: 't', job_key: 'relax', attempt_id: 'a' };
  await energyApi.previewTask(identity);
  expect(fetch).toHaveBeenLastCalledWith('/api/v1/toolbox/postprocessing/energy/task-sources/preview', expect.objectContaining({ body: JSON.stringify(identity) }));
  await energyApi.reuse(energyFixture(), 'ec_cached', 'es_cached');
  expect(fetch).toHaveBeenLastCalledWith('/api/v1/toolbox/postprocessing/energy/collections/ec_fixture/reuse', expect.objectContaining({ body: JSON.stringify({ expected_revision: 4, source_collection_id: 'ec_cached', sample_id: 'es_cached' }) }));
});
it('propagates raw-upload revision conflicts with their API code and status', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ error: { code: 'ENERGY_REVISION_CONFLICT', message: '版本已变化' } }), { status: 409 }));
  const error = await energyApi.upload(energyFixture(), new File(['fixture'], 'OUTCAR'), '').catch(cause => cause);
  expect(error).toBeInstanceOf(ApiError); expect(error.code).toBe('ENERGY_REVISION_CONFLICT'); expect(error.status).toBe(409);
});
it('sends explicit analysis types and revisions for create, lock, unlock and per-group legacy copy', async () => {
  const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response('{}', { status: 200 }));
  await energyApi.create('形成能分析', 'formation');
  expect(fetch).toHaveBeenLastCalledWith('/api/v1/toolbox/postprocessing/energy/collections', expect.objectContaining({ body: JSON.stringify({ title: '形成能分析', analysis_kind: 'formation', workflow: 'cards' }) }));
  await energyApi.lock(energyFixture());
  expect(fetch).toHaveBeenLastCalledWith('/api/v1/toolbox/postprocessing/energy/collections/ec_fixture/lock', expect.objectContaining({ body: JSON.stringify({ expected_revision: 4 }) }));
  await energyApi.unlock(energyFixture());
  expect(fetch).toHaveBeenLastCalledWith('/api/v1/toolbox/postprocessing/energy/collections/ec_fixture/unlock', expect.objectContaining({ body: JSON.stringify({ expected_revision: 4 }) }));
  await energyApi.copy(energyFixture(), 'adsorption', 'g_ads');
  expect(fetch).toHaveBeenLastCalledWith('/api/v1/toolbox/postprocessing/energy/collections/ec_fixture/copy', expect.objectContaining({ body: JSON.stringify({ expected_revision: 4, analysis_kind: 'adsorption', workflow: 'cards', group_id: 'g_ads' }) }));
});
it('revises batch assignment and keeps removal preview separate from atomic removal', async () => {
  const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response('{}', { status: 200 }));
  await energyApi.autofill(energyFixture());
  expect(fetch).toHaveBeenLastCalledWith('/api/v1/toolbox/postprocessing/energy/collections/ec_fixture/autofill', expect.objectContaining({ body: JSON.stringify({ expected_revision: 4 }) }));
  await energyApi.previewRemoval(energyFixture(), { sample_ids: ['es_clean'] });
  expect(fetch).toHaveBeenLastCalledWith('/api/v1/toolbox/postprocessing/energy/collections/ec_fixture/samples/removal-preview', expect.objectContaining({ body: JSON.stringify({ expected_revision: 4, sample_ids: ['es_clean'], workflow: 'cards' }) }));
  await energyApi.removeSamples({ ...energyFixture(), revision: 5 }, { clear_all: true });
  expect(fetch).toHaveBeenLastCalledWith('/api/v1/toolbox/postprocessing/energy/collections/ec_fixture/samples/remove', expect.objectContaining({ body: JSON.stringify({ expected_revision: 5, clear_all: true }) }));
});
it('keeps file-declared CSV basis optional and sends identical raw bytes and basis through preview and import', async () => {
  const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response('{}', { status: 200 }));
  const file = new File(['name,composition,energy_basis,energy_ev\n合成样本,Pt:4,sigma_to_zero_ev,-100'], '中文样本.csv');
  await energyApi.previewCsv(energyFixture(), file);
  expect(fetch).toHaveBeenLastCalledWith('/api/v1/toolbox/postprocessing/energy/collections/ec_fixture/csv/preview?expected_revision=4', expect.objectContaining({ body: file, headers: { 'Content-Type': 'application/octet-stream' } }));
  await energyApi.previewCsv(energyFixture(), file, 'without_entropy_ev');
  expect(fetch).toHaveBeenLastCalledWith('/api/v1/toolbox/postprocessing/energy/collections/ec_fixture/csv/preview?expected_revision=4&energy_basis=without_entropy_ev', expect.objectContaining({ body: file }));
  await energyApi.importCsv(energyFixture(), file, 'without_entropy_ev');
  expect(fetch).toHaveBeenLastCalledWith('/api/v1/toolbox/postprocessing/energy/collections/ec_fixture/csv?expected_revision=4&energy_basis=without_entropy_ev', expect.objectContaining({ body: file }));
});
it('downloads a blank template and saved sample values independently of result export', async () => {
  const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response('name,composition,energy_basis,energy_ev,unit,relative_path,reference_note\r\n', { status: 200, headers: { 'Content-Type': 'text/csv' } }));
  expect(await energyApi.template()).toBeInstanceOf(Blob);
  expect(fetch).toHaveBeenLastCalledWith('/api/v1/toolbox/postprocessing/energy/csv-template', expect.objectContaining({ method: 'GET' }));
  await energyApi.template('free_energy_toten_ev');
  expect(fetch).toHaveBeenLastCalledWith('/api/v1/toolbox/postprocessing/energy/csv-template?energy_basis=free_energy_toten_ev', expect.objectContaining({ method: 'GET' }));
  expect(await energyApi.downloadSamples(energyFixture(), 'sigma_to_zero_ev', 'effective')).toBeInstanceOf(Blob);
  expect(fetch).toHaveBeenLastCalledWith('/api/v1/toolbox/postprocessing/energy/collections/ec_fixture/samples.csv?energy_basis=sigma_to_zero_ev&value_source=effective', expect.objectContaining({ method: 'GET' }));
  await energyApi.downloadSamples(energyFixture(), 'without_entropy_ev', 'original');
  expect(fetch).toHaveBeenLastCalledWith('/api/v1/toolbox/postprocessing/energy/collections/ec_fixture/samples.csv?energy_basis=without_entropy_ev&value_source=original', expect.objectContaining({ method: 'GET' }));
});
it('uses per-card revision and explicit risk acceptance plus the exact shared-impact token', async () => {
  const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response('{}', { status: 200 }));
  const collection = energyFixture(), card = cardConfiguration(collection.groups[0]);
  await energyApi.saveCard(collection, card);
  expect(fetch).toHaveBeenLastCalledWith('/api/v1/toolbox/postprocessing/energy/collections/ec_fixture/cards/g_ads', expect.objectContaining({ method: 'PUT', body: JSON.stringify({ expected_revision: 4, card }) }));
  await energyApi.lockCard(collection, 'g_ads', true);
  expect(fetch).toHaveBeenLastCalledWith('/api/v1/toolbox/postprocessing/energy/collections/ec_fixture/cards/g_ads/lock', expect.objectContaining({ body: JSON.stringify({ expected_revision: 4, accepted_warnings: true }) }));
  await energyApi.calculateCard(collection, 'g_ads');
  expect(fetch).toHaveBeenLastCalledWith('/api/v1/toolbox/postprocessing/energy/collections/ec_fixture/cards/g_ads/calculate', expect.objectContaining({ body: JSON.stringify({ expected_revision: 4 }) }));
  const patches = [{ sample_id: 'es_clean', override: null }];
  const impact = { preview_id: 'bound-token', expected_revision: 4, sample_ids: ['es_clean'], affected_cards: [{ card_id: 'g_ads', name: 'A', locked: true }], requires_acknowledgement: true };
  await energyApi.saveSamples(collection, patches, impact);
  expect(fetch).toHaveBeenLastCalledWith('/api/v1/toolbox/postprocessing/energy/collections/ec_fixture/samples/configuration', expect.objectContaining({ body: JSON.stringify({ expected_revision: 4, samples: patches, preview_id: 'bound-token', acknowledge_locked_cards: true }) }));
});
