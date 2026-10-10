import { request } from './client';
import type { AtomOverride, CatalysisDraft, CatalysisDraftSummary, CatalysisSource, SurfaceParams } from '../types/catalysis';
import type { StructureGeometry } from '../types/structure-geometry';

const base = '/toolbox/catalysis/drafts';
const path = (id: string) => `${base}/${encodeURIComponent(id)}`;
export const catalysisApi = {
  list: (signal?: AbortSignal) => request<{ drafts: CatalysisDraftSummary[] }>(base, { signal }),
  create: (name: string, source: CatalysisSource) => request<{ draft: CatalysisDraft }>(base, { method: 'POST', body: { ...(name.trim() ? { name: name.trim() } : {}), source } }),
  get: (id: string, signal?: AbortSignal) => request<{ draft: CatalysisDraft }>(path(id), { signal }),
  save: (draft: CatalysisDraft, changes: { name?: string; parameters?: SurfaceParams | null; active_surface_id?: string }) =>
    request<{ draft: CatalysisDraft }>(path(draft.draft_id), { method: 'PATCH', body: { revision: draft.revision, ...changes } }),
  build: (draft: CatalysisDraft, parameters: SurfaceParams) =>
    request<{ draft: CatalysisDraft }>(`${path(draft.draft_id)}/surfaces`, { method: 'POST', body: { revision: draft.revision, ...parameters } }),
  constraints: (draft: CatalysisDraft, settings: { bottom_fixed_layers: number; atom_overrides: Record<string, AtomOverride>; reset_existing: boolean }) =>
    request<{ draft: CatalysisDraft }>(`${path(draft.draft_id)}/constraints`, { method: 'POST', body: { revision: draft.revision, ...settings } }),
  geometry: (id: string, surfaceId: string, revision: number, signal?: AbortSignal) => request<{ revision: number; surface_id: string; geometry: StructureGeometry }>(`${path(id)}/geometry`, { params: { revision, surface_id: surfaceId }, signal }),
  export: (draft: CatalysisDraft, surfaceId: string) => request<Blob>(`${path(draft.draft_id)}/export`, { params: { revision: draft.revision, surface_id: surfaceId }, responseType: 'blob' }),
};
