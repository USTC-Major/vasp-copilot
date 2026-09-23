export type ToolboxExecutionMode = 'None' | 'Fake' | 'Real';

export interface ToolboxProject {
  id: string;
  name: string;
  description?: string;
  created_at?: string;
  updated_at?: string;
}

export interface ToolboxTask {
  id: string;
  project_id: string;
  title: string;
  goal: string;
  local_workspace: string | null;
  hpc_workspace: string | null;
  status: string;
  updated_at: string;
  execution_mode?: ToolboxExecutionMode;
}

export interface ToolboxJob {
  key: string;
  /** Opaque, current identity for this calculation attempt. */
  attempt_id?: string;
  label: string;
  kind: string;
  requires: string[];
  status: string;
  slurm_id?: string | number | null;
  submission_state?: string;
  description?: string;
  attempts?: Record<string, unknown>[];
  attempt_history?: Record<string, unknown>[];
  diagnosis?: Record<string, unknown>;
  precheck?: ToolboxJobPrecheck;
  draft?: ToolboxJobDraft | null;
}

export interface ToolboxJobPrecheck {
  attempt_id?: string;
  ok: boolean;
  hard?: boolean;
  digest?: string;
  snapshot?: Record<string, unknown>;
  issues?: Record<string, unknown>[];
}

export interface ToolboxJobDraft {
  attempt_id?: string;
  job_key?: string;
  dir?: string;
  directory?: string;
  script_name?: string;
  script_sha256?: string;
  scheduler_target?: string | Record<string, unknown>;
  resources?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface ToolboxArtifact {
  name: string;
  path: string;
  size: number;
  sha256: string;
}

export interface ToolboxFlow {
  execution_mode: ToolboxExecutionMode;
  phase: string;
  goal: string;
  strategy: string;
  local_dir: string;
  hpc_dir: string;
  waiting: string[];
  // In a multi-job task this is an overview only. Submission evidence belongs
  // to the selected job's `precheck` and `draft` above.
  precheck: { ok: boolean; issues: Record<string, unknown>[]; digest?: string; per_job?: boolean };
  report: string;
  jobs: ToolboxJob[];
  draft: Record<string, unknown>[];
  artifacts: Record<string, ToolboxArtifact>;
}

export interface ToolboxExecutionEvent {
  id: number;
  project_id: string;
  task_id: string;
  kind: string;
  at: string;
  message: string;
}

export interface ToolboxMonitorState {
  state: 'idle' | 'monitoring' | 'recovering' | 'error' | 'stopped';
  last_attempt_at?: string;
  last_success_at?: string;
  last_error?: string;
  interval_seconds: number;
  remote_cancelled: false;
}

export type ToolboxConsentState =
  | 'pending' | 'approved' | 'executing' | 'executed'
  | 'rejected' | 'expired' | 'failed' | 'unknown';

export interface ToolboxConsentCard {
  card_id: string;
  action_id: string;
  kind: string;
  summary: string;
  state: ToolboxConsentState;
  reason?: string;
  expires_at?: string;
  result?: string;
  options?: string[];
  args?: Record<string, unknown>;
  binding?: Record<string, unknown>;
  receipt?: ToolboxFileReceipt;
}

export type ToolboxFileOperation = 'copy' | 'symlink' | 'write_text' | 'mkdir';
export type ToolboxFileApprovalMode = 'human' | 'reviewer';
export interface ToolboxFileReview {
  protocol_version?: string;
  state: 'queued' | 'reviewing' | 'approved' | 'rejected' | 'needs_human';
  requested_at?: string | null;
  finished_at?: string | null;
  decision?: 'approve' | 'reject' | 'needs_human' | null;
  reason_code?: string | null;
  reason?: string | null;
  checks?: Record<string, boolean | 'unknown'>;
  reviewer_model?: string | null;
  decided_by?: 'human' | 'reviewer' | null;
}
export interface ToolboxFileRoot {
  root_id: string;
  version: number;
  requested_path: string;
  canonical_path: string;
  endpoint_digest: string;
  identity?: Record<string, unknown>;
}
export interface ToolboxFileScope {
  scope_id: string;
  version: number;
  kind: 'file';
  state: 'proposed' | 'active' | 'revoked' | 'expired';
  approval_mode?: ToolboxFileApprovalMode;
  activated_by?: 'human' | null;
  activated_at?: string | null;
  job_key: string;
  attempt_id: string;
  endpoint?: Record<string, unknown> & { host_key?: Record<string, unknown> };
  endpoint_digest?: string;
  source_policy?: string;
  submit_limit?: number;
  root_bindings: { root_id: string; version: number; destination_prefixes: string[] }[];
  allowed_operations: ToolboxFileOperation[];
  source_bindings: {
    requested_path: string; canonical_path: string; type?: string; size?: number; mtime_ns?: number;
    sha256?: string; content_class?: string; identity?: Record<string, unknown>;
  }[];
  max_operations: number;
  max_total_bytes: number;
  expires_at: string;
  reason?: string;
}
export interface ToolboxFileItemInput {
  item_id: string;
  op: ToolboxFileOperation;
  destination: { root_id: string; relative_path: string };
  source?: { absolute_path: string };
  text?: string;
  on_conflict: 'fail';
}
export interface ToolboxFileManifestItem {
  item_id: string;
  op: ToolboxFileOperation;
  destination: Record<string, unknown>;
  source?: Record<string, unknown> | null;
  text?: string | null;
  text_sha256?: string;
  bytes: number;
  content_class?: string;
  on_conflict: 'fail';
}
export interface ToolboxFileReceipt {
  phase: string;
  items: { item_id: string; state?: string; [key: string]: unknown }[];
  item_outcomes?: Record<string, { state: string; evidence?: string }>;
  spent?: { operations: number; bytes: number };
  held_unknown?: { operations: number; bytes: number };
  released?: { operations: number; bytes: number };
  leftovers?: string[];
  error?: ToolboxErrorBody | null;
  cancel_requested_at?: string;
}
export interface ToolboxFileAction extends ToolboxConsentCard {
  kind: 'remote_file';
  binding_hash?: string;
  review?: ToolboxFileReview | null;
  binding: Record<string, unknown> & {
    scope_id: string;
    scope_version: number;
    job_key: string;
    attempt_id: string;
    manifest: { endpoint: Record<string, unknown>; roots: ToolboxFileRoot[]; items: ToolboxFileManifestItem[]; manifest_digest: string };
  };
  receipt: ToolboxFileReceipt;
}

export interface ToolboxTaskDetail {
  mode: 'toolbox';
  task_id: string;
  task: ToolboxTask;
  flow: ToolboxFlow;
  consents: ToolboxConsentCard[];
  file_roots?: ToolboxFileRoot[];
  file_roots_version?: number;
  file_scopes?: ToolboxFileScope[];
  file_actions?: ToolboxFileAction[];
  events: ToolboxExecutionEvent[];
  monitor: ToolboxMonitorState;
  backend_mode: ToolboxExecutionMode | string;
}

export interface ToolboxErrorBody {
  code: string;
  message: string;
  retryable: boolean;
}

export interface ToolboxToolResult {
  mode: 'toolbox';
  task_id: string;
  ok: boolean;
  error: ToolboxErrorBody | null;
  result: string;
  pending: ToolboxConsentCard | null;
  flow: ToolboxFlow;
}

export interface ToolboxConsentResult {
  mode: 'toolbox';
  task_id: string;
  ok: boolean;
  error: ToolboxErrorBody | null;
  card: ToolboxConsentCard;
  result: string;
  flow: ToolboxFlow;
}

export interface ToolboxBrowseEntry {
  name: string;
  is_dir: boolean;
}

export interface ToolboxBrowseResponse {
  mode: 'toolbox';
  kind: 'local' | 'hpc';
  path: string;
  parent: string | null;
  exists: boolean;
  is_dir: boolean;
  entries: ToolboxBrowseEntry[];
  roots?: ToolboxBrowseEntry[];
  notice?: string;
}

export interface ToolboxSettings {
  max_jobs: number;
  poll_interval_seconds: number;
  ssh: {
    name: string;
    host: string;
    port: number;
    username: string;
    known_hosts_path: string;
    identity_file: string;
    scheduler_backend: string;
  };
  materials_project: { configured: boolean };
}

export interface ToolboxSettingsResponse {
  mode: 'toolbox';
  settings: ToolboxSettings;
  backend_mode: ToolboxExecutionMode | string;
}

export interface ToolboxHistoryItem {
  id: string;
  kind?: string;
  title: string;
  status: string;
  updated_at: string;
  project_id: string;
  task_id: string;
  project_name?: string;
  execution_mode?: ToolboxExecutionMode;
}

export interface ToolboxPlanJobInput {
  key: string;
  label: string;
  kind: string;
  requires?: string[];
  description?: string;
}

export interface ToolboxIncarEntry {
  tag: string;
  value: string | number | boolean | number[] | string[];
}
