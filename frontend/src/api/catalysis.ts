import { request } from './client';
import type { AdsorbateSource, AdsorptionPlacement, AdsorptionSiteKind, AtomOverride, CatalysisDraft, CatalysisDraftSummary, CatalysisSource, CatalysisWorkflowBindingResponse, CatalysisWorkflowTarget, SurfaceParams } from '../types/catalysis';
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
  adsorbate: (draft: CatalysisDraft, source: AdsorbateSource, anchor_index: number) => request<{ draft: CatalysisDraft }>(`${path(draft.draft_id)}/adsorbate`, { method: 'POST', body: { revision: draft.revision, source, anchor_index } }),
  sites: (draft: CatalysisDraft, settings: { kinds: Exclude<AdsorptionSiteKind, 'manual'>[]; manual_sites: { label?: string; uv: [number, number] }[]; dedup_tolerance_angstrom: number }) => request<{ draft: CatalysisDraft }>(`${path(draft.draft_id)}/adsorption/sites`, { method: 'POST', body: { revision: draft.revision, ...settings } }),
  candidates: (draft: CatalysisDraft, site_ids: string[], placement: AdsorptionPlacement) => request<{ draft: CatalysisDraft }>(`${path(draft.draft_id)}/adsorption/candidates`, { method: 'POST', body: { revision: draft.revision, site_ids, placement } }),
  selection: (draft: CatalysisDraft, selected_candidate_ids: string[]) => request<{ draft: CatalysisDraft }>(`${path(draft.draft_id)}/adsorption/selection`, { method: 'PATCH', body: { revision: draft.revision, selected_candidate_ids } }),
  candidateGeometry: (id: string, candidateId: string, revision: number, signal?: AbortSignal) => request<{ revision: number; candidate_id: string; geometry: StructureGeometry }>(`${path(id)}/geometry`, { params: { revision, candidate_id: candidateId }, signal }),
  exportCandidates: (draft: CatalysisDraft, candidate_ids: string[]) => request<Blob>(`${path(draft.draft_id)}/adsorption/export`, { method: 'POST', body: { revision: draft.revision, candidate_ids }, responseType: 'blob' }),
  workflowBinding: (draft: CatalysisDraft, target: CatalysisWorkflowTarget) => request<CatalysisWorkflowBindingResponse>(`${path(draft.draft_id)}/workflow-binding`, { method: 'POST', body: { revision: draft.revision, ...target } }),
};
