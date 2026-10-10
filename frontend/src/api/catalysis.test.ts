import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';
import { catalysisApi } from './catalysis';
import type { CatalysisDraft } from '../types/catalysis';

it('omits empty names so the server default applies, and retains bulk/slab source semantics', async () => {
  const bodies: unknown[] = [];
  server.use(http.post('/api/v1/toolbox/catalysis/drafts', async ({ request }) => { bodies.push(await request.json()); return HttpResponse.json({ mode: 'toolbox', draft: { draft_id: 'cat-new' } }); }));
  await catalysisApi.create('', { kind: 'example', role: 'bulk' });
  await catalysisApi.create(' slab ', { kind: 'poscar', role: 'slab', content: 'synthetic POSCAR', name: 'POSCAR' });
  expect(bodies).toEqual([{ source: { kind: 'example', role: 'bulk' } }, { name: 'slab', source: { kind: 'poscar', role: 'slab', content: 'synthetic POSCAR', name: 'POSCAR' } }]);
});

it('pins export and mutations to the current draft revision and surface', async () => {
  const doc = { draft_id: 'cat-test', revision: 9 } as CatalysisDraft;
  const calls: unknown[] = [];
  server.use(http.patch('/api/v1/toolbox/catalysis/drafts/:id', async ({ request }) => { calls.push(await request.json()); return HttpResponse.json({ mode: 'toolbox', draft: doc }); }), http.get('/api/v1/toolbox/catalysis/drafts/:id/geometry', ({ request }) => { const url = new URL(request.url); calls.push(Object.fromEntries(url.searchParams)); return HttpResponse.json({ mode: 'toolbox', revision: 9, surface_id: 'surface-2', geometry: {} }); }), http.get('/api/v1/toolbox/catalysis/drafts/:id/export', ({ request }) => { const url = new URL(request.url); calls.push(Object.fromEntries(url.searchParams)); return new HttpResponse('ZIP', { headers: { 'Content-Type': 'application/zip' } }); }));
  await catalysisApi.save(doc, { active_surface_id: 'surface-2' });
  await catalysisApi.geometry(doc.draft_id, 'surface-2', doc.revision);
  await catalysisApi.export(doc, 'surface-2');
  expect(calls).toEqual([{ revision: 9, active_surface_id: 'surface-2' }, { revision: '9', surface_id: 'surface-2' }, { revision: '9', surface_id: 'surface-2' }]);
});

it('preserves revision-conflict errors, including during ZIP download', async () => {
  server.use(http.get('/api/v1/toolbox/catalysis/drafts/:id/export', () => HttpResponse.json({ error: { code: 'CAT_REVISION_CONFLICT', message: '草稿已变化' } }, { status: 409 })));
  await expect(catalysisApi.export({ draft_id: 'cat-test', revision: 1 } as CatalysisDraft, 'surface-1')).rejects.toEqual(expect.objectContaining({ code: 'CAT_REVISION_CONFLICT', status: 409 }));
});
