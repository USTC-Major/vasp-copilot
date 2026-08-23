// ============================================================
// VASP-Copilot / VASP-Doctor+ — Generated API Types
// 基于 MVP_ARCHITECTURE_DESIGN.md §6-7 所有 Schema 定义
// 后端 snake_case → 前端直接映射
// ============================================================

import type {
  FileStatus, DiagnosisStatus,
  HpcJobStatus, DeploymentStatus, RecipeStatus, RecipeCompositionStatus,
  ConfirmationStatus, FixStatus,
  Severity, MagnetizationAnalysisMode, HpcCapability, GrantCapability,
  ProvenanceSourceType, RecipeLayer, FileKind, WorkflowTask, ElectronicType,
  PrecisionLevel, CoordinateMode, MagnetismHint, SchedulerType,
  ConnectorStatus, RemoteExecutionMode, DeploymentOperationType,
  IssueCategory, FixStrategy, PatchOperation, RecommendationAction,
} from './enums';

export type { Severity, WorkflowStatus, DiagnosisStatus, HpcJobStatus, DeploymentStatus } from './enums';

// ============================================================
// 通用
// ============================================================

export interface ApiError {
  code: string;
  message: string;
  details?: Record<string, unknown>;
  field_errors: FieldError[];
  retryable: boolean;
  help?: string;
}

export interface FieldError {
  field: string;
  code: string;
  message: string;
}

export interface ApiResponse<T = unknown> {
  request_id: string;
  error?: ApiError;
  data?: T;
}

// ============================================================
// 文件 (files)
// ============================================================

export interface UploadedFile {
  file_id: string;
  name: string;
  kind: FileKind;
  size_bytes: number;
  sha256: string;
  file_status: FileStatus;
  expires_at: string;
}

export interface FilePreview {
  content: string;
  start_line: number;
  end_line: number;
  total_lines: number;
  returned_bytes: number;
  truncated: boolean;
  next_cursor: string | null;
}

export interface PreviewPolicy {
  max_preview_bytes: number;
  max_preview_lines: number;
  binary_rejected: boolean;
  sensitive_content_redacted: boolean;
}

export interface FilePreviewResponse {
  request_id: string;
  file_id: string;
  name: string;
  kind: FileKind;
  mime_type: string;
  encoding: string;
  sha256: string;
  preview: FilePreview;
  policy: PreviewPolicy;
}

// ============================================================
// 结构 (structure)
// ============================================================

export interface LatticeInfo {
  matrix?: number[][];
  a: number;
  b: number;
  c: number;
  alpha: number;
  beta: number;
  gamma: number;
  volume: number;
}

export interface StructureWarning {
  code: string;
  message: string;
  severity: Severity;
}

export interface StructureSummary {
  structure_id: string;
  formula: string;
  reduced_formula?: string;
  elements: string[];
  counts: number[];
  atom_count: number;
  lattice: LatticeInfo;
  coordinate_mode: CoordinateMode;
  selective_dynamics: boolean;
  transition_metals: string[];
  magnetism_hint: MagnetismHint;
  source_format: string;
  source_sha256: string;
  standardized?: boolean;
  warnings: StructureWarning[];
}

export interface StructureAnalysisResponse {
  request_id: string;
  structure_id: string;
  summary: StructureSummary;
  normalized_poscar_file_id: string;
}

// ============================================================
// 工作流 (workflow)
// ============================================================

// ============ Materials Project ============
export interface MaterialCandidate {
  material_id: string;
  formula: string;
  elements: string[];
  n_elements?: number;
  spacegroup?: { symbol?: string; number?: number; point_group?: string; crystal_system?: string };
  lattice?: { a?: number; b?: number; c?: number; alpha?: number; beta?: number; gamma?: number; volume?: number };
  density?: number;
  band_gap?: number;
  is_metal?: boolean;
  is_stable?: boolean;
  formation_energy_per_atom?: number;
  energy_above_hull?: number;
  total_magnetization?: number;
  ordering?: string;
}

export interface MaterialsSearchResponse {
  request_id: string;
  query: string;
  criteria: Record<string, unknown>;
  llm_used: boolean;
  count: number;
  materials: MaterialCandidate[];
}

export interface MaterialsImportResponse {
  request_id: string;
  structure_id: string;
  normalized_poscar_file_id: string;
  file_id: string;
  material_id: string;
  summary: string;
}
export interface WorkflowGoal {
  original_text: string;
  requested_tasks: WorkflowTask[];
}

export interface WorkflowAssumptions {
  electronic_type: ElectronicType;
  magnetic: boolean;
  soc: boolean;
  precision: PrecisionLevel;
}

export interface DftuEntry {
  element: string;
  l: number;
  u_ev: number;
  j_ev: number;
  source_note: string;
  confirmed_by_user: boolean;
}

export interface DftuSettings {
  enabled: boolean;
  entries: DftuEntry[];
}

/** 响应侧 scheduler 块（后端 SchedulerBlock）。
 * 请求侧请改用 types/workflow-contract.ts 的 SchedulerRequest（字段为 type）。 */
export interface SchedulerSettings {
  scheduler_type: string;
  scheduler_profile_id?: string | null;
  nodes: number;
  tasks_per_node: number;
  walltime: string;
  vasp_binary_hint: string;
}

export interface RemoteExecution {
  enabled: boolean;
  mode: RemoteExecutionMode;
  cluster_profile_id: string | null;
  deploy_requires_confirmation: boolean;
  submit_requires_confirmation: boolean;
  auto_resubmit: boolean;
}

export interface WorkflowStep {
  step_id: string;
  task: WorkflowTask;
  label: string;
  directory: string;
  depends_on: string[];
  runnable: boolean;
  blocked_by: string[];
  requires_runtime_outputs: string[];
  produces: string[];
  parameters: Record<string, Record<string, unknown>>;
}

export interface FileInheritanceDependency {
  dependency_id: string;
  dependency_type?: string;
  from_step_id: string;
  source_file: string;
  to_step_id: string;
  target_file: string;
  required: boolean;
  satisfied: boolean;
  requires_upstream_diagnosis_pass: boolean;
  validation?: {
    checks: string[];
    passed: boolean;
  };
  evidence?: unknown[];
  blocking_codes?: string[];
}

export interface FileInheritancePlan {
  plan_id: string;
  workflow_id: string;
  revision: number;
  dependencies: FileInheritanceDependency[];
  evaluated_at: string | null;
}

export interface SelectedRecipe {
  recipe_id?: string;
  recipe_ref?: string;
  version: string;
  layer: RecipeLayer;
  order: number;
  sha256?: string;
  selection_reason: string;
  matched_context?: Record<string, unknown>;
}

export interface RecipeComposition {
  composition_id: string;
  revision: number;
  composition_status?: RecipeCompositionStatus;
  step_id: string;
  recipe_pack: {
    pack_id: string;
    version: string;
    sha256: string;
  };
  selected: SelectedRecipe[];
  resolved_parameters?: Record<string, unknown>;
  provenance?: ParameterProvenance[];
  patches?: ParameterPatch[];
  confirmations?: WorkflowConfirmation[];
  conflicts?: unknown[];
  warnings?: unknown[];
  composition_sha256: string;
}

export interface WorkflowConfirmation {
  key: string;
  prompt: string;
  confirmation_status: ConfirmationStatus;
  confirmed_at?: string;
}

export interface WorkflowPlan {
  schema_version: string;
  workflow_id: string;
  revision: number;
  created_at: string;
  structure: {
    structure_id: string;
    formula: string;
    elements: string[];
    counts: number[];
    source_sha256: string;
  };
  goal: WorkflowGoal;
  assumptions: WorkflowAssumptions;
  dftu: DftuSettings;
  scheduler: SchedulerSettings;
  remote_execution: RemoteExecution;
  steps: WorkflowStep[];
  file_inheritance_plan: FileInheritancePlan;
  recipe_compositions: RecipeComposition[];
  confirmations: WorkflowConfirmation[];
  warnings: unknown[];
  template_versions: Record<string, string>;
}

// ============================================================
// 文件树
// ============================================================

export interface FileTreeNode {
  name: string;
  type: 'file' | 'directory';
  relative_path: string;
  children?: FileTreeNode[];
  file_id?: string;
  mime_type?: string;
  size_bytes?: number;
  sha256?: string;
  preview_available?: boolean;
  generated_by?: string;
}

// ============================================================
// Recipe
// ============================================================

export interface RecipeManifest {
  schema_version: string;
  recipe_id: string;
  version: string;
  kind: string;
  recipe_status: RecipeStatus;
  display_name: string;
  description: string;
  scope: {
    tasks?: WorkflowTask[];
    electronic_types?: ElectronicType[];
    vasp_versions?: string[];
  };
  parameters: Record<string, unknown>;
  derived_parameters: unknown[];
  requires: string[];
  conflicts: unknown[];
  confirmations: {
    key: string;
    required: boolean;
    prompt: string;
  }[];
  warnings: {
    code: string;
    severity: Severity;
    message: string;
  }[];
  allowed_overrides: Record<string, { type: string; minimum?: number; exclusive_minimum?: number }>;
  provenance: {
    source_type: string;
    source_note: string;
    reviewed_by: string;
    reviewed_at: string;
  };
  tests: {
    test_status: string;
    case_count: number;
    last_tested_at: string;
  };
  sha256: string;
}

// ============================================================
// 参数 (parameter)
// ============================================================

export interface ParameterProvenance {
  parameter: string;
  value: unknown;
  source_type: ProvenanceSourceType;
  source_id: string;
  source_revision: string;
  overrode?: {
    source_type: ProvenanceSourceType;
    source_id: string;
    value: unknown;
  } | null;
  derived_by: string | null;
  requires_confirmation: boolean;
  confirmed: boolean;
}

export interface ParameterPatch {
  patch_id: string;
  composition_id: string;
  expected_revision: number;
  parameter: string;
  operation: PatchOperation;
  value: unknown;
  source: string;
  reason: string;
  confirmed_by_user: boolean;
  validation: {
    allowed: boolean;
    rule_ids: string[];
    warnings: unknown[];
  };
}

// ============================================================
// 诊断 (diagnosis)
// ============================================================

export interface DetectedFile {
  name: string;
  kind: FileKind;
  size_bytes: number;
  sha256: string;
}

export interface DetectedRun {
  root: string;
  run_type: WorkflowTask | 'unknown';
  files: DetectedFile[];
  missing_recommended: string[];
  candidate_job_logs?: string[];
}

export interface Evidence {
  evidence_id: string;
  file: string;
  line: number | null;
  line_end?: number | null;
  message: string;
  excerpt?: string;
  data_ref?: string;
}

export interface Recommendation {
  recommendation_id: string;
  action: RecommendationAction;
  target: string;
  parameter?: string;
  old_value?: unknown;
  new_value?: unknown;
  rationale: string;
  requires_user_confirmation: boolean;
  priority: number;
}

export interface DiagnosisIssue {
  issue_id: string;
  rule_id: string;
  severity: Severity;
  category: IssueCategory;
  title: string;
  summary: string;
  evidence: Evidence[];
  possible_causes: string[];
  recommendations: Recommendation[];
  auto_fixable: boolean;
  confidence: number;
  blocking: boolean;
  tags: string[];
}

export interface ScfSeries {
  ionic_step: number;
  electronic_step: number;
  energy: number;
}

export interface ScfPlotData {
  x_label: string;
  y_label: string;
  series: ScfSeries[];
}

export interface MagnetizationSeriesPoint {
  atom_index: number;
  element: string;
  initial_moment: number;
  final_moment: number;
}

export interface MagnetizationPlotData {
  x_label: string;
  y_label: string;
  series: MagnetizationSeriesPoint[];
}

export interface RecommendedFix {
  fix_id: string;
  issue_ids: string[];
  target_file: string;
  strategy: FixStrategy;
  fix_status: FixStatus;
  safe_to_generate: boolean;
  requires_user_confirmation: boolean;
  changes: {
    parameter: string;
    operation: PatchOperation;
    old_value: unknown;
    new_value: unknown;
    reason: string;
  }[];
  diff: string;
  generated_file_id: string;
  warnings: string[];
}

export interface CalculationMode {
  is_spin_polarized: boolean;
  is_dftu: boolean;
  is_soc: boolean;
  is_noncollinear: boolean;
  magnetization_analysis_mode: MagnetizationAnalysisMode;
}

export interface DiagnosisProvenance {
  parser_version: string;
  rule_set_version: string;
  recipe_pack_version: string;
  composition_sha256: string;
  vasp_version: string | null;
  vasp_binary_hint: string | null;
  calculation_mode: CalculationMode;
  llm_used: boolean;
  mode: string;
}

export interface DiagnosisSummary {
  headline: string;
  highest_severity: Severity;
  issue_count: Record<Severity, number>;
}

export interface NextStep {
  allowed: boolean;
  suggested_task: string | null;
  reason: string;
}

export interface DiagnosisResult {
  schema_version: string;
  diagnosis_id: string;
  diagnosis_status: DiagnosisStatus;
  summary: DiagnosisSummary;
  detected_run: DetectedRun;
  issues: DiagnosisIssue[];
  plots: {
    scf: ScfPlotData;
    magnetization: MagnetizationPlotData;
  };
  recommended_fixes: RecommendedFix[];
  missing_evidence: unknown[];
  next_step: NextStep;
  report: {
    report_id: string;
    format: string;
    ready: boolean;
    download_url: string;
  };
  provenance: DiagnosisProvenance;
}

// ============================================================
// 报告 (report)
// ============================================================

export interface ReportMetadata {
  report_id: string;
  diagnosis_id?: string;
  workflow_id?: string;
  format: string;
  language?: string;
  title: string;
  generated_at: string;
  size_bytes: number;
  sha256: string;
  sections: string[];
  download_url: string;
  generator_version: string;
}

// ============================================================
// HPC
// ============================================================

export interface ClusterProfile {
  cluster_profile_id: string;
  display_name: string;
  scheduler_profile_id: string;
  scheduler_type: SchedulerType;
  connector_status: ConnectorStatus;
  capabilities: HpcCapability[];
  limits: {
    max_nodes: number;
    max_tasks: number;
    max_walltime: string;
    max_upload_bytes: number;
  };
  allowed_partitions: string[];
  pseudopotential_mode: string;
  parallel_policy: {
    defaults: Record<string, number>;
    editable: boolean;
    scope: string;
    provenance: string;
    disclaimer: string;
  };
  security: {
    host_key_verification: boolean;
    credential_location: string;
    arbitrary_shell: boolean;
  };
}

export interface SchedulerProfile {
  scheduler_profile_id: string;
  profile_version: string;
  scheduler_type: SchedulerType;
  submit_command: {
    argv_template: string[];
    job_id_parser: string;
  };
  status_command: {
    argv_template: string[];
  };
  accounting_command: {
    argv_template: string[];
  };
  launcher_command: {
    argv_template: string[];
  };
  script_template: string;
  allow_user_command_override: boolean;
}

export interface DeploymentOperation {
  operation_id: string;
  type: DeploymentOperationType;
  relative_path: string;
  source_file_id?: string;
  size_bytes?: number;
  sha256?: string;
}

export interface PreflightResult {
  passed: boolean;
  checks: unknown[];
  warnings: unknown[];
  expires_at?: string;
}

export interface RemoteDeploymentPlan {
  schema_version: string;
  deployment_id: string;
  deployment_status: DeploymentStatus;
  workflow_id: string;
  workflow_revision: number;
  cluster_profile_id: string;
  bundle_sha256: string;
  target_relative_path: string;
  overwrite: boolean;
  file_count: number;
  total_bytes: number;
  operations: DeploymentOperation[];
  preflight: PreflightResult;
  required_capability: GrantCapability;
}

export interface CapabilityGrant {
  grant_id: string;
  capability: GrantCapability;
  session_id: string;
  subject_id: string;
  single_use: boolean;
  used: boolean;
  constraints: {
    cluster_profile_id: string;
    idempotency_key: string;
    max_nodes: number;
    max_tasks: number;
    max_walltime: string;
  };
  issued_at: string;
  expires_at: string;
}

export interface RemoteManifest {
  manifest_id: string;
  deployment_id: string;
  cluster_profile_id: string;
  workflow_id: string;
  workflow_revision: number;
  bundle_sha256: string;
  target_relative_path: string;
  verified: boolean;
  immutable: boolean;
  files: {
    relative_path: string;
    size_bytes: number;
    sha256: string;
    mode: string;
  }[];
  pseudopotential: {
    included: boolean;
    download_allowed: boolean;
    llm_visible: boolean;
    mode: string;
  };
  deployed_at: string;
}

export interface SubmissionDraft {
  submission_draft_id: string;
  hpc_job_status: HpcJobStatus;
  manifest_id: string;
  step_id: string;
  script_sha256: string;
  resources: {
    partition: string;
    nodes: number;
    tasks: number;
    memory_gb: number;
    walltime: string;
  };
  idempotency_key: string;
  preflight: PreflightResult;
}

export interface RemoteJobState {
  normalized: HpcJobStatus;
  scheduler_state: string;
  reason: string | null;
  exit_code: number | null;
}

export interface RemoteJob {
  remote_job_id: string;
  scheduler_type: SchedulerType;
  scheduler_profile_id: string;
  scheduler_job_id: string;
  cluster_profile_id: string;
  manifest_id: string;
  step_id: string;
  hpc_job_status: HpcJobStatus;
  state: RemoteJobState;
  submitted_at: string;
  last_synced_at: string;
  collectable: boolean;
}

export interface ResultCollection {
  files: {
    relative_path: string;
    size_bytes: number;
    sha256: string;
  }[];
  excluded: {
    relative_path: string;
    reason: string;
  }[];
  partial: boolean;
  diagnosis_id?: string;
}

// ============================================================
// Feature Flags
// ============================================================

export interface FeatureFlags {
  ENABLE_LLM: boolean;
  ENABLE_HPC_BRIDGE: boolean;
  ENABLE_FAKE_HPC: boolean;
  ENABLE_POTCAR_ASSEMBLY: boolean;
  ENABLE_BAND_WORKFLOW: boolean;
  MAX_UPLOAD_SIZE_MB: number;
  MAX_TEXT_PREVIEW_BYTES: number;
  MAX_OUTCAR_PREVIEW_LINES: number;
}

// ============================================================
// Audit
// ============================================================

export interface AuditEvent {
  audit_event_id: string;
  request_id: string;
  session_id: string;
  actor: string;
  action: string;
  subject_type: string;
  subject_id: string;
  cluster_profile_id: string;
  workflow_id: string;
  workflow_revision: number;
  bundle_sha256: string;
  grant_id: string;
  result: string;
  created_at: string;
  redactions: string[];
}
