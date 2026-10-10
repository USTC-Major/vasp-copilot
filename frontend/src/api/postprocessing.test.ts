import { vi } from 'vitest';
import { ppApi } from './postprocessing';

afterEach(() => vi.restoreAllMocks());

it('sends exact identity to the task preview and a confirmed subset to import', async () => {
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ preview: { id: 'synthetic-preview' } }), { status: 200 }));
  const signal = new AbortController().signal;
  const identity = { project_id: 'p', task_id: 't', job_key: 'dos', attempt_id: 'a', kind: 'dos' as const };
  await ppApi.previewTaskSource(identity, signal);
  expect(fetch).toHaveBeenLastCalledWith('/api/v1/toolbox/postprocessing/task-sources/preview', expect.objectContaining({ method: 'POST', body: JSON.stringify(identity), signal }));
  fetch.mockResolvedValueOnce(new Response(JSON.stringify({ dataset: { id: 'synthetic-dataset', status: 'downloading' } }), { status: 202 }));
  const selection = { preview_id: 'synthetic-preview', files: ['vasprun.xml'], title: '合成分析' };
  await expect(ppApi.importTaskSource(selection)).resolves.toMatchObject({ dataset: { status: 'downloading' } });
  expect(fetch).toHaveBeenLastCalledWith('/api/v1/toolbox/postprocessing/task-sources/import', expect.objectContaining({ method: 'POST', body: JSON.stringify(selection) }));
});

it('uses offline reuse independently of remote preview and encodes retry download IDs', async () => {
  const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(JSON.stringify({ dataset: { id: 'cached' } }), { status: 200 }));
  await ppApi.importTaskSource({ reuse_dataset_id: 'cached' });
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(fetch).toHaveBeenCalledWith('/api/v1/toolbox/postprocessing/task-sources/import', expect.objectContaining({ body: '{"reuse_dataset_id":"cached"}' }));
  await ppApi.retryDownload('dataset/1');
  expect(fetch).toHaveBeenLastCalledWith('/api/v1/toolbox/postprocessing/datasets/dataset%2F1/retry-download', expect.objectContaining({ method: 'POST' }));
});
