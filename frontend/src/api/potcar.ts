import { request } from './client';
import type { PotcarDatasetStatus, PotcarDatasetsResponse, PotcarDiscovery, PotcarLibrariesResponse, PotcarLibraryResponse, PotcarScan } from '../types/potcar';

const BASE = '/toolbox/potcar';
const libraryPath = (id: string) => `/libraries/${encodeURIComponent(id)}`;
export const potcarApi = {
  libraries: (signal?: AbortSignal) => request<PotcarLibrariesResponse>(`${BASE}/libraries`, { signal }),
  library: (id: string, signal?: AbortSignal) => request<PotcarLibraryResponse>(`${BASE}${libraryPath(id)}`, { signal }),
  discover: (root_path: string, signal?: AbortSignal) => request<{ mode: 'toolbox' } & PotcarDiscovery>(`${BASE}/discover`, { method: 'POST', body: { root_path }, signal }),
  register: (body: { display_name: string; root_path: string; version_note: string | null; source_ack: { confirmed: true }; expected_registry_revision: number }) => request<PotcarLibraryResponse>(`${BASE}/libraries`, { method: 'POST', body }),
  edit: (id: string, expected_revision: number, display_name: string, version_note: string | null) => request<PotcarLibraryResponse>(`${BASE}${libraryPath(id)}`, { method: 'PATCH', body: { expected_revision, display_name, version_note } }),
  setDefault: (library_id: string | null, expected_registry_revision: number) => request<{ mode: 'toolbox'; default_library_id: string | null; revision: number }>(`${BASE}/default-library`, { method: 'PUT', body: { library_id, expected_registry_revision } }),
  relink: (id: string, root_path: string, expected_revision: number) => request<PotcarLibraryResponse>(`${BASE}${libraryPath(id)}/relink`, { method: 'POST', body: { root_path, expected_revision } }),
  replace: (id: string, root_path: string, expected_revision: number) => request<PotcarLibraryResponse>(`${BASE}${libraryPath(id)}/replace-source`, { method: 'POST', body: { root_path, expected_revision, source_ack: { confirmed: true } } }),
  remove: (id: string, expected_revision: number) => request<{ mode: 'toolbox'; deleted: true; library_id: string; revision: number }>(`${BASE}${libraryPath(id)}`, { method: 'DELETE', params: { expected_revision } }),
  startScan: (id: string, expected_revision: number) => request<PotcarLibraryResponse & { scan: PotcarScan }>(`${BASE}${libraryPath(id)}/scans`, { method: 'POST', body: { expected_revision } }),
  scan: (id: string, signal?: AbortSignal) => request<{ mode: 'toolbox'; scan: PotcarScan }>(`${BASE}/scans/${encodeURIComponent(id)}`, { signal }),
  cancelScan: (id: string) => request<{ mode: 'toolbox'; scan: PotcarScan }>(`${BASE}/scans/${encodeURIComponent(id)}/cancel`, { method: 'POST', body: {} }),
  datasets: (id: string, filters: { element?: string; status?: PotcarDatasetStatus; cursor?: string }, signal?: AbortSignal) => {
    const params = new URLSearchParams({ limit: '50' });
    Object.entries(filters).forEach(([key, value]) => { if (value) params.set(key, value); });
    return request<PotcarDatasetsResponse>(`${BASE}${libraryPath(id)}/datasets?${params}`, { signal });
  },
};
