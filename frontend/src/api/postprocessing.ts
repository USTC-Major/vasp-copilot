import { ApiError, request } from './client';

export type PPAxes = { version: 'pp.axes.v1'; x_min: number; x_max: number; x_interval: number; y_min: number; y_max: number; y_interval: number };
export type PPView = { version?: 'pp.view.v2'; reference: 'raw' | 'fermi' | 'custom' | 'legacy_absolute'; reference_ev: number; mirror_down: boolean; atoms: number[]; orbitals: string[]; elements?: string[]; projection_grouping?: 'element' | 'combined'; energy_min_ev?: number; energy_max_ev?: number; band_start: number; band_end: number; axes?: PPAxes };
export type PPDataset = {
  id: string; title: string; kind: 'dos' | 'band'; status: 'draft' | 'processing' | 'ready' | 'failed' | 'cancelled'; revision: number;
  files: { name: string; size_bytes: number; sha256: string }[]; view: PPView;
  error: { code: string; message: string } | null;
  summary: null | { spin_mode: string; efermi_ev: number | null; convergence: string; warnings: string[]; atoms: [number, string][]; orbitals: string[]; band_count: number };
};
export type PPCurves = { id: string; kind: 'dos' | 'band'; revision: number; reference_ev: number; effective_reference_ev?: number; energy_bounds_ev?: { min_ev: number; max_ev: number }; units?: { energy: string; dos_total: string; dos_projection: string; kpath: string; reciprocal_convention: string }; view: PPView; ticks: { x: number; label: string }[]; plot_warnings?: string[]; curves: { id?: string; name: string; channel: string; element?: string | null; element_index?: number | null; atom_ids?: number[]; normalization?: string; density_unit?: string; x: number[]; y: number[] }[] };
const base = '/toolbox/postprocessing/datasets';
const path = (id: string) => `${base}/${encodeURIComponent(id)}`;
export const ppApi = {
  list: (signal?: AbortSignal) => request<{ datasets: PPDataset[] }>(base, { signal }),
  get: (id: string, signal?: AbortSignal) => request<{ dataset: PPDataset }>(path(id), { signal }),
  create: (kind: 'dos' | 'band', title: string) => request<{ dataset: PPDataset }>(base, { method: 'POST', body: { kind, title } }),
  upload: async (id: string, file: File, signal: AbortSignal) => {
    const response = await fetch(`/api/v1${path(id)}/files/${encodeURIComponent(file.name)}`, { method: 'PUT', headers: { 'Content-Type': 'application/octet-stream' }, body: file, signal });
    const data = await response.json();
    if (!response.ok) throw new ApiError(data.error?.code ?? 'PP_UPLOAD_FAILED', data.error?.message ?? '文件上传失败');
    return data as { dataset: PPDataset };
  },
  start: (id: string) => request<{ dataset: PPDataset }>(`${path(id)}/analyses`, { method: 'POST' }),
  cancel: (id: string) => request<{ dataset: PPDataset }>(`${path(id)}/cancel`, { method: 'POST' }),
  remove: (id: string) => request(`${path(id)}`, { method: 'DELETE' }),
  save: (doc: PPDataset, view: PPView, signal?: AbortSignal) => request<{ dataset: PPDataset }>(`${path(doc.id)}/view`, { method: 'PATCH', body: { expected_revision: doc.revision, view }, signal }),
  fitAxes: (doc: PPDataset, view: PPView) => request<{ axes: PPAxes; revision: number }>(`${path(doc.id)}/fit-axes`, { method: 'POST', body: { expected_revision: doc.revision, view: { ...view, axes: undefined } } }),
  curves: (id: string, signal?: AbortSignal) => request<PPCurves>(`${path(id)}/curves`, { signal }),
  download: (id: string, format: 'csv' | 'json') => request<Blob>(`${path(id)}/export?format=${format}`, { responseType: 'blob' }),
};
