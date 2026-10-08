export type PotcarDatasetStatus = 'ready' | 'unsupported' | 'invalid' | 'ambiguous';
export interface PotcarIssue { code: string; message: string }
export interface PotcarScan {
  scan_id: string;
  library_id: string;
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';
  scanned_count: number;
  candidate_count: number;
  failed_count: number;
  created_at: string;
  finished_at: string | null;
  error: (PotcarIssue & { retryable: boolean }) | null;
  index_revision: number | null;
}
export interface PotcarLibrary {
  library_id: string;
  display_name: string;
  root_path: string;
  version_note: string | null;
  revision: number;
  index_revision: number | null;
  source_ack: { confirmed: true; confirmed_at: string };
  source_fingerprint: string | null;
  created_at: string;
  updated_at: string;
  is_default: boolean;
  reachable: boolean;
  scan: PotcarScan | null;
  summary: ({ total: number } & Record<PotcarDatasetStatus, number>) | null;
}
export interface PotcarDataset {
  dataset_id: string;
  library_id: string;
  relative_path: string;
  compression: 'raw' | 'gzip' | 'Z' | null;
  element: string | null;
  variant: string | null;
  family: string | null;
  lexch: string | null;
  zval: number | null;
  enmax_ev: number | null;
  dataset_date: string | null;
  title: string | null;
  decoded_sha256: string | null;
  source_sha256: string | null;
  status: PotcarDatasetStatus;
  issues: PotcarIssue[];
  duplicate_of: string | null;
}
export interface PotcarDiscovery {
  root_path: string;
  requires_selection: boolean;
  collections: { root_path: string; display_name: string; candidate_count: number }[];
  candidate_count: number;
}
export interface PotcarLibrariesResponse {
  mode: 'toolbox'; libraries: PotcarLibrary[]; default_library_id: string | null; revision: number;
}
export interface PotcarLibraryResponse { mode: 'toolbox'; library: PotcarLibrary; revision: number }
export interface PotcarDatasetsResponse {
  mode: 'toolbox'; index_revision: number | null; datasets: PotcarDataset[]; next_cursor: string | null; total: number;
}
export interface PotcarPreviewRequest {
  library_id: string;
  index_revision: number;
  poscar_text: string;
  dataset_ids?: (string | null)[];
  legacy_species?: string[];
  context?: PotcarContext;
}
export interface PotcarContext {
  purpose: 'unknown' | 'regular' | 'special';
  functional: 'unknown' | 'PBE' | 'PBE+U' | 'HSE06';
  spin_polarized?: boolean | null;
  short_bonds?: boolean | null;
  high_pressure?: boolean | null;
  high_unoccupied?: boolean | null;
  magnetic_energy?: boolean | null;
}
export interface PotcarAdvice {
  code: string; message: string; rule_ids: string[]; source_ids: string[]; target_variants: string[];
}
export interface PotcarWorkflowBinding {
  workflow_id: string; revision: number; request_sha256: string; step_ids: string[];
}
export interface PotcarSelectionReason {
  code: 'USER_SELECTED' | 'RULE_RECOMMENDED' | 'UNIQUE_COMPATIBLE' | 'SELECTION_REQUIRED' | 'NO_COMPATIBLE_DATASET';
  message: string;
}
export interface PotcarPreviewRow {
  position: number;
  element: string;
  atom_count: number;
  dataset_id: string | null;
  candidates: PotcarDataset[];
  reason: PotcarSelectionReason;
  selection_reason?: PotcarSelectionReason;
  advice?: PotcarAdvice[];
}
export interface PotcarPreview {
  mode: 'toolbox';
  preview_id: string;
  selection_digest: string;
  structure_sha256: string;
  library: { library_id: string; display_name: string; version_note: string | null; index_revision: number };
  rows: PotcarPreviewRow[];
  blockers: { code: string; message: string; position: number | null }[];
  expires_at: string;
  rule_version?: string;
  rule_sources?: { source_id: string; title: string; url: string }[];
  context?: PotcarContext;
  context_source?: 'user' | 'unknown' | 'workflow';
  workflow_binding?: PotcarWorkflowBinding | null;
}
export interface PotcarArtifact {
  artifact_id: string;
  preview_id: string;
  selection_digest: string;
  structure_sha256: string;
  library_id: string;
  index_revision: number;
  status: 'ready';
  rule_version?: string;
  context?: PotcarContext;
  context_source?: 'user' | 'unknown' | 'workflow';
  workflow_binding?: PotcarWorkflowBinding | null;
  size_bytes: number;
  sha256: string;
  created_at: string;
  expires_at: string;
  rows: { position: number; element: string; atom_count: number; dataset_id: string; variant: string | null; title: string | null; decoded_sha256: string; source_sha256: string }[];
}
export interface PotcarArtifactRequest {
  preview_id: string;
  selection_digest: string;
  confirmed_order_and_variants: true;
  idempotency_key: string;
}
export interface PotcarArtifactResponse { mode: 'toolbox'; artifact: PotcarArtifact }
export interface WorkflowPotcarChoice { mode: 'omit' | 'include'; artifact_id?: string | null }
export interface WorkflowPotcarState extends WorkflowPotcarChoice {
  status: 'omitted' | 'pending_confirmation' | 'generating' | 'generated' | 'failed';
  steps: { step_id: string; status: WorkflowPotcarState['status']; artifact_id?: string | null; sha256?: string | null; size_bytes?: number | null }[];
  error?: { code: string; message: string } | null;
}
