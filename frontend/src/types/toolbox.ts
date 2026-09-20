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
  precheck: { ok: boolean; issues: Record<string, unknown>[]; digest?: string };
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
  binding?: Record<string, unknown>;
}

export interface ToolboxTaskDetail {
  mode: 'toolbox';
  task_id: string;
  task: ToolboxTask;
  flow: ToolboxFlow;
  consents: ToolboxConsentCard[];
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
