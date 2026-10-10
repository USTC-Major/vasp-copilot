import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';
import { catalysisApi } from './catalysis';
import type { CatalysisDraft } from '../types/catalysis';
import { catalysisWorkflowResponseFixture } from '../mocks/catalysisWorkflowFixture';

it('omits empty names so the server default applies, and retains bulk/slab source semantics', async () => {
  const bodies: unknown[] = [];
  server.use(http.post('/api/v1/toolbox/catalysis/drafts', async ({ request }) => { bodies.push(await request.json()); return HttpResponse.json({ mode: 'toolbox', draft: { draft_id: 'cat-new' } }); }));
  await catalysisApi.create('', { kind: 'example', role: 'bulk' });
  await catalysisApi.create(' slab ', { kind: 'poscar', role: 'slab', content: 'synthetic POSCAR', name: 'POSCAR' });
  expect(bodies).toEqual([{ source: { kind: 'example', role: 'bulk' } }, { name: 'slab', source: { kind: 'poscar', role: 'slab', content: 'synthetic POSCAR', name: 'POSCAR' } }]);
});

it('binds exactly one explicit model at the current revision and retains the complete response', async () => {
  const bodies: unknown[] = [], response = catalysisWorkflowResponseFixture();
  server.use(http.post('/api/v1/toolbox/catalysis/drafts/:id/workflow-binding', async ({ request }) => { bodies.push(await request.json()); return HttpResponse.json(response); }));
  const doc = { draft_id: 'cat-test', revision: 9 } as CatalysisDraft;
  expect(await catalysisApi.workflowBinding(doc, { surface_id: 'surface-1' })).toEqual(response);
  await catalysisApi.workflowBinding(doc, { candidate_id: 'candidate-1' });
  expect(bodies).toEqual([{ revision: 9, surface_id: 'surface-1' }, { revision: 9, candidate_id: 'candidate-1' }]);
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

it('pins adsorption requests, candidate previews and multi-export to revision and explicit IDs', async () => {
  const doc = { draft_id: 'cat-ads', revision: 12 } as CatalysisDraft, calls: unknown[] = [];
  server.use(http.post('/api/v1/toolbox/catalysis/drafts/:id/adsorbate', async ({ request }) => { calls.push(await request.json()); return HttpResponse.json({ draft: doc }); }), http.post('/api/v1/toolbox/catalysis/drafts/:id/adsorption/candidates', async ({ request }) => { calls.push(await request.json()); return HttpResponse.json({ draft: doc }); }), http.patch('/api/v1/toolbox/catalysis/drafts/:id/adsorption/selection', async ({ request }) => { calls.push(await request.json()); return HttpResponse.json({ draft: doc }); }), http.get('/api/v1/toolbox/catalysis/drafts/:id/geometry', ({ request }) => { calls.push(Object.fromEntries(new URL(request.url).searchParams)); return HttpResponse.json({ revision: 12, candidate_id: 'c2', geometry: {} }); }), http.post('/api/v1/toolbox/catalysis/drafts/:id/adsorption/export', async ({ request }) => { calls.push(await request.json()); return new HttpResponse('ZIP'); }));
  await catalysisApi.adsorbate(doc, { kind: 'xyz', content: '2\nCO\nC 0 0 0\nO 0 0 1.15', name: 'CO 分子.xyz' }, 1);
  await catalysisApi.candidates(doc, ['s1', 's2'], { height_angstrom: 2, rotation_degrees: [0, 90, 0], screening_distance_angstrom: .8 });
  await catalysisApi.selection(doc, ['c2']); await catalysisApi.candidateGeometry(doc.draft_id, 'c2', 12); await catalysisApi.exportCandidates(doc, ['c1', 'c2']);
  expect(calls).toEqual([{ revision: 12, source: { kind: 'xyz', content: '2\nCO\nC 0 0 0\nO 0 0 1.15', name: 'CO 分子.xyz' }, anchor_index: 1 }, { revision: 12, site_ids: ['s1', 's2'], placement: { height_angstrom: 2, rotation_degrees: [0, 90, 0], screening_distance_angstrom: .8 } }, { revision: 12, selected_candidate_ids: ['c2'] }, { revision: '12', candidate_id: 'c2' }, { revision: 12, candidate_ids: ['c1', 'c2'] }]);
});
