import { ApiError, request } from './client';
import type { PPTaskIdentity } from './postprocessing';

export type EnergyBasis = 'sigma_to_zero_ev' | 'without_entropy_ev' | 'free_energy_toten_ev';
export type EnergyAnalysisKind = 'adsorption' | 'formation';
export type EnergyAssignmentOrigin = 'manual' | 'auto' | null;
export type EnergyFields = Record<EnergyBasis, number | null>;
export type EnergyRole = 'clean_slab' | 'adsorbate' | 'adsorbed' | 'material' | 'element_reference';
export type EnergyComposition = Record<string, number>;
export type EnergyStatus = { completion: string; electronic_converged: boolean | null; ionic_converged: boolean | null; ionic_applicability: string };
export type EnergyOverride = { composition?: EnergyComposition; energy_fields?: Partial<EnergyFields>; energy_basis?: EnergyBasis; unit: 'eV'; note: string };
export type EnergyCsvMetadata = { source_notes: string[]; override_notes: string[]; original_energies: { energy_basis: EnergyBasis; energy_ev: number }[]; value_source?: 'original' | 'effective' | null };
export type EnergySource = {
  kind: 'local_upload' | 'manual' | 'csv' | 'task_result'; original_name?: string; relative_path?: string;
  sha256?: string; size_bytes?: number; imported_at?: string; energy_basis?: EnergyBasis; reference_note?: string;
  project_id?: string; task_id?: string; job_key?: string; attempt_id?: string;
  submission_action_id?: string; slurm_id?: string | number; snapshot_sha256?: string; cached_at?: string;
  csv_metadata?: EnergyCsvMetadata;
  [key: string]: unknown;
};
export type EnergySample = {
  id: string; name: string; revision: number; source: EnergySource;
  energy_basis?: EnergyBasis;
  parsed: {
    energy_fields: EnergyFields; composition: EnergyComposition | null; status: EnergyStatus;
    metadata: { parameters?: Record<string, unknown>; support?: { automatic_comparison: boolean; reasons: string[] }; [key: string]: unknown };
    provenance: { run_segment?: number | null; selected_ionic_step?: number | null; field_lines?: Record<string, unknown>; [key: string]: unknown };
    errors: string[]; warnings: string[];
    issues?: { code: string; severity: string; message: string; recoverable_by_manual: boolean }[];
  };
  override: EnergyOverride | null;
  role_suggestion: { role: EnergyRole | null; reasons: string[]; confidence: string };
  role: EnergyRole | null; included: boolean; confirmed: boolean; accepted_warnings: boolean; confirmation_fingerprint: string | null;
  warning_acceptance_fingerprint?: string | null;
  role_origin?: EnergyAssignmentOrigin; included_origin?: EnergyAssignmentOrigin; assignment_reasons?: string[];
};
type EnergyGroupCommon = { id: string; name: string; energy_basis: EnergyBasis; basis_confirmed: boolean; reference_note: string; reference_origins?: Record<string, Exclude<EnergyAssignmentOrigin, null>> };
export type AdsorptionEnergyGroup = EnergyGroupCommon & {
  kind: 'adsorption'; clean_sample_id: string; adsorbate_sample_id: string; reference_units: number;
  targets: { sample_id: string; adsorbate_count: number }[];
};
export type FormationEnergyGroup = EnergyGroupCommon & { kind: 'formation'; element_references: Record<string, string>; targets: { sample_id: string }[] };
export type EnergyGroup = AdsorptionEnergyGroup | FormationEnergyGroup;
export type EnergyResultRow = {
  sample_id: string; name: string; delta_ev: number; normalized_ev: number; normalization: 'per_adsorbate' | 'per_atom'; unit: string;
  formula: string; terms: { sample_id: string; coefficient: number; energy_ev: number; contribution_ev: number; element?: string; count?: number; [key: string]: unknown }[];
  warnings: string[];
};
export type EnergyResult = {
  schema_version: 'pp.energy.result.v1'; input_fingerprint: string; calculated_at: string; warnings: string[];
  groups: { id: string; kind: 'adsorption' | 'formation'; name: string; energy_basis: EnergyBasis; rows: EnergyResultRow[]; warnings: string[] }[];
};
export type EnergyCollection = {
  id: string; schema_version: 'pp.energy.v1'; title: string; revision: number; created_at: string; updated_at: string;
  samples: EnergySample[]; groups: EnergyGroup[]; result: EnergyResult | null;
  analysis_kind?: EnergyAnalysisKind | null; legacy_mode?: boolean; locked?: boolean; lock_fingerprint?: string | null;
  assignment_report?: { state: 'ready' | 'pending' | 'ambiguous'; issues: { code: string; message: string; sample_ids: string[] }[] } | null;
  limits: { max_file_bytes: number; max_collection_bytes: number; max_samples: number };
};
export type EnergyConfiguration = {
  expected_revision: number; title: string;
  samples: { sample_id: string; name: string; role: EnergyRole | null; included: boolean; confirmed: boolean; accepted_warnings: boolean; override: EnergyOverride | null; role_origin?: EnergyAssignmentOrigin; included_origin?: EnergyAssignmentOrigin }[];
  groups: EnergyGroup[];
};
export type EnergyManualInput = { name: string; composition: EnergyComposition; energy_fields: Partial<EnergyFields>; energy_basis: EnergyBasis; unit: 'eV'; reference_note: string };
export type EnergyTaskPreview = { id: string; source: EnergySource; files: { name: string; available: boolean; size_bytes: number | null; reason?: string }[]; expires_at: string; warnings: string[] };
const base = '/toolbox/postprocessing/energy';
const collectionPath = (id: string) => `${base}/collections/${encodeURIComponent(id)}`;
type CollectionResponse = { mode: 'toolbox'; collection: EnergyCollection };
export type EnergyRemovalRequest = { sample_ids: string[]; clear_all?: never } | { clear_all: true; sample_ids?: never };
export type EnergyRemoval = { sample_ids: string[]; removed_count: number; affected_target_ids: string[]; cleared_reference_keys: string[] };
export type EnergyCsvPreview = { row_count: number; valid_count: number; can_import: boolean; issues: { code: string; message: string }[]; rows: { row_number: number; name: string; composition: EnergyComposition | null; energy_basis: EnergyBasis | null; energy_ev: number | null; unit: string; relative_path: string; reference_note: string; csv_metadata?: EnergyCsvMetadata; issues: { code: string; message: string }[] }[] };
export type EnergySampleValueSource = 'original' | 'effective';

async function uploadRaw<T = CollectionResponse>(path: string, body: File, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`/api/v1${path}`, { method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body, signal });
  const data = await response.json();
  if (!response.ok || data.error) throw new ApiError(data.error?.code ?? 'ENERGY_UPLOAD_FAILED', data.error?.message ?? '能量来源导入失败', data.error?.retryable ?? false, response.status, data.error?.field_errors ?? []);
  return data as T;
}

export const energyApi = {
  list: (signal?: AbortSignal) => request<{ mode: 'toolbox'; collections: EnergyCollection[] }>(`${base}/collections`, { signal }),
  get: (id: string, signal?: AbortSignal) => request<CollectionResponse>(collectionPath(id), { signal }),
  create: (title: string, analysisKind: EnergyAnalysisKind) => request<CollectionResponse>(`${base}/collections`, { method: 'POST', body: { title, analysis_kind: analysisKind } }),
  remove: (id: string) => request<{ deleted: true }>(collectionPath(id), { method: 'DELETE' }),
  upload: (collection: EnergyCollection, file: File, relativePath: string, signal?: AbortSignal) => {
    const query = new URLSearchParams({ name: file.name, expected_revision: String(collection.revision) });
    if (relativePath) query.set('relative_path', relativePath);
    return uploadRaw(`${collectionPath(collection.id)}/outcar?${query}`, file, signal);
  },
  template: (basis?: EnergyBasis) => request<Blob>(`${base}/csv-template${basis ? `?energy_basis=${basis}` : ''}`, { responseType: 'blob' }),
  previewCsv: (collection: EnergyCollection, file: File, basis?: EnergyBasis, signal?: AbortSignal) => {
    const query = new URLSearchParams({ expected_revision: String(collection.revision) }); if (basis) query.set('energy_basis', basis);
    return uploadRaw<{ mode: 'toolbox'; preview: EnergyCsvPreview }>(`${collectionPath(collection.id)}/csv/preview?${query}`, file, signal);
  },
  importCsv: (collection: EnergyCollection, file: File, basis?: EnergyBasis, signal?: AbortSignal) => {
    const query = new URLSearchParams({ expected_revision: String(collection.revision) }); if (basis) query.set('energy_basis', basis);
    return uploadRaw(`${collectionPath(collection.id)}/csv?${query}`, file, signal);
  },
  addManual: (collection: EnergyCollection, input: EnergyManualInput) => request<CollectionResponse>(`${collectionPath(collection.id)}/manual`, { method: 'POST', body: { expected_revision: collection.revision, ...input } }),
  save: (id: string, configuration: EnergyConfiguration) => request<CollectionResponse>(`${collectionPath(id)}/configuration`, { method: 'PUT', body: configuration }),
  calculate: (collection: EnergyCollection) => request<CollectionResponse>(`${collectionPath(collection.id)}/calculate`, { method: 'POST', body: { expected_revision: collection.revision } }),
  lock: (collection: EnergyCollection) => request<CollectionResponse>(`${collectionPath(collection.id)}/lock`, { method: 'POST', body: { expected_revision: collection.revision } }),
  unlock: (collection: EnergyCollection) => request<CollectionResponse>(`${collectionPath(collection.id)}/unlock`, { method: 'POST', body: { expected_revision: collection.revision } }),
  copy: (collection: EnergyCollection, analysisKind: EnergyAnalysisKind, groupId?: string) => request<CollectionResponse>(`${collectionPath(collection.id)}/copy`, { method: 'POST', body: { expected_revision: collection.revision, analysis_kind: analysisKind, ...(groupId ? { group_id: groupId } : {}) } }),
  autofill: (collection: EnergyCollection) => request<CollectionResponse>(`${collectionPath(collection.id)}/autofill`, { method: 'POST', body: { expected_revision: collection.revision } }),
  previewRemoval: (collection: EnergyCollection, input: EnergyRemovalRequest) => request<{ mode: 'toolbox'; removal: EnergyRemoval }>(`${collectionPath(collection.id)}/samples/removal-preview`, { method: 'POST', body: { expected_revision: collection.revision, ...input } }),
  removeSamples: (collection: EnergyCollection, input: EnergyRemovalRequest) => request<CollectionResponse & { removal: EnergyRemoval }>(`${collectionPath(collection.id)}/samples/remove`, { method: 'POST', body: { expected_revision: collection.revision, ...input } }),
  previewTask: (identity: PPTaskIdentity, signal?: AbortSignal) => request<{ mode: 'toolbox'; preview: EnergyTaskPreview }>(`${base}/task-sources/preview`, { method: 'POST', body: identity, signal }),
  importTask: (collection: EnergyCollection, previewId: string, name?: string) => request<CollectionResponse>(`${collectionPath(collection.id)}/task-sources/import`, { method: 'POST', body: { expected_revision: collection.revision, preview_id: previewId, name } }),
  reuse: (collection: EnergyCollection, sourceCollectionId: string, sampleId: string) => request<CollectionResponse>(`${collectionPath(collection.id)}/reuse`, { method: 'POST', body: { expected_revision: collection.revision, source_collection_id: sourceCollectionId, sample_id: sampleId } }),
  download: (id: string, format: 'csv' | 'json') => request<Blob>(`${collectionPath(id)}/export?format=${format}`, { responseType: 'blob' }),
  downloadSamples: (collection: EnergyCollection, basis: EnergyBasis, valueSource: EnergySampleValueSource) => request<Blob>(`${collectionPath(collection.id)}/samples.csv?${new URLSearchParams({ energy_basis: basis, value_source: valueSource })}`, { responseType: 'blob' }),
};
