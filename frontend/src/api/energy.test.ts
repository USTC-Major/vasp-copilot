import { expect, it, afterEach, vi } from 'vitest';
import { ApiError } from './client';
import { energyApi } from './energy';
import { energyFixture } from '../components/energy/energyTestFixtures';

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
