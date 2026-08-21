// ============================================================
// VASP-Copilot / VASP-Doctor+ — 分层状态枚举与常量
// 严格遵守 MVP_ARCHITECTURE_DESIGN.md §7.22
// ============================================================

// ---- 任务状态 ----
export type TaskStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';

// ---- 文件状态 ----
export type FileStatus = 'uploaded' | 'scanning' | 'ready' | 'rejected' | 'expired';

// ---- 工作流状态 ----
export type WorkflowStatus =
  | 'draft'
  | 'needs_confirmation'
  | 'planned'
  | 'generated'
  | 'ready_to_download'
  | 'failed';

export const WORKFLOW_TERMINAL: WorkflowStatus[] = [
  'ready_to_download',
  'failed',
];

// ---- 诊断状态 ----
export type DiagnosisStatus =
  | 'uploaded'
  | 'queued'
  | 'running'
  | 'succeeded'
  | 'failed';

export const DIAGNOSIS_TERMINAL: DiagnosisStatus[] = ['succeeded', 'failed'];

// ---- HPC 作业状态 ----
export type HpcJobStatus =
  | 'draft'
  | 'ready_for_confirmation'
  | 'authorized'
  | 'submitting'
  | 'submitted'
  | 'pending'
  | 'running'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'timeout'
  | 'out_of_memory'
  | 'unknown';

export const HPC_JOB_TERMINAL: HpcJobStatus[] = [
  'completed',
  'failed',
  'cancelled',
  'timeout',
  'out_of_memory',
];

// ---- 部署状态 ----
export type DeploymentStatus =
  | 'draft'
  | 'preflight_failed'
  | 'ready_for_confirmation'
  | 'authorized'
  | 'deploying'
  | 'deployed'
  | 'failed';

export const DEPLOYMENT_TERMINAL: DeploymentStatus[] = [
  'deployed',
  'failed',
];

// ---- Recipe 状态 ----
export type RecipeStatus = 'draft' | 'published' | 'deprecated';
export type RecipeCompositionStatus =
  | 'draft'
  | 'needs_confirmation'
  | 'confirmed'
  | 'invalid';
export type ConfirmationStatus = 'pending' | 'confirmed' | 'rejected';
export type FixStatus = 'proposed' | 'generated' | 'unavailable';
export type CollectionStatus =
  | 'draft'
  | 'authorized'
  | 'collecting'
  | 'succeeded'
  | 'partial'
  | 'failed';
export type CheckStatus = 'passed' | 'warning' | 'failed' | 'not_run';

// ---- 严重度 ----
export type Severity = 'info' | 'low' | 'medium' | 'high' | 'critical';

// ---- 磁矩分析模式 ----
export type MagnetizationAnalysisMode =
  | 'collinear'
  | 'unsupported_noncollinear_or_soc'
  | 'unavailable';

// ---- HPC Capability 枚举 ----
export type HpcCapability = 'deploy' | 'submit' | 'status' | 'collect';
export type GrantCapability = 'HPC_DEPLOY' | 'HPC_SUBMIT' | 'HPC_COLLECT';

// ---- 参数来源类型 ----
export type ProvenanceSourceType =
  | 'recipe'
  | 'derived_function'
  | 'user_patch'
  | 'rule_fix'
  | 'scheduler_profile';

// ---- Recipe Layer ----
export type RecipeLayer =
  | 'base'
  | 'task'
  | 'electronic_type'
  | 'modifier'
  | 'precision'
  | 'user_patch';

// ---- 文件类型 ----
export type FileKind =
  | 'poscar'
  | 'cif'
  | 'incar'
  | 'kpoints'
  | 'potcar'
  | 'oszicar'
  | 'outcar'
  | 'submit_script'
  | 'readme'
  | 'markdown_report'
  | 'vasprun_xml'
  | 'job_log'
  | 'unknown';

// ---- 工作流任务类型 ----
export type WorkflowTask = 'relax' | 'static' | 'dos' | 'band';

// ---- 电子类型 ----
export type ElectronicType = 'metal' | 'semiconductor' | 'unknown';

// ---- 精度档位 ----
export type PrecisionLevel = 'quick' | 'standard' | 'high';

// ---- 坐标模式 ----
export type CoordinateMode = 'direct' | 'cartesian';

// ---- 磁性提示 ----
export type MagnetismHint = 'possible' | 'unlikely' | 'unknown';

// ---- 调度器类型 ----
export type SchedulerType = 'slurm' | 'cbatch' | 'custom' | 'fake';

// ---- 连接器状态 ----
export type ConnectorStatus = 'available' | 'unavailable' | 'error';

// ---- 远程执行模式 ----
export type RemoteExecutionMode = 'disabled' | 'fake' | 'real';

// ---- 远程部署操作类型 ----
export type DeploymentOperationType =
  | 'create_directory'
  | 'upload_file'
  | 'atomic_rename';

// ---- Issue 类别 ----
export type IssueCategory =
  | 'electronic_convergence'
  | 'ionic_convergence'
  | 'parameter_consistency'
  | 'file_missing'
  | 'magnetic'
  | 'job_resource'
  | 'job_configuration'
  | 'remote_operation'
  | 'recipe_validation'
  | 'unknown';

// ---- 修复策略 ----
export type FixStrategy = 'parameter_patch' | 'scheduler_patch' | 'manual_only';

// ---- Patch 操作 ----
export type PatchOperation = 'add' | 'replace' | 'remove';

// ---- 推荐动作 ----
export type RecommendationAction =
  | 'review_and_set_parameter'
  | 'review_structure'
  | 'review_magnetic'
  | 'review_resource'
  | 'review_file'
  | 'manual_intervention';

// ---- 状态标签配置 ----
export interface StatusConfig {
  color: string;
  label: string;
}

export const WORKFLOW_STATUS_MAP: Record<WorkflowStatus, StatusConfig> = {
  draft: { color: 'default', label: '草稿' },
  needs_confirmation: { color: 'warning', label: '待确认' },
  planned: { color: 'processing', label: '已规划' },
  generated: { color: 'cyan', label: '已生成' },
  ready_to_download: { color: 'success', label: '可下载' },
  failed: { color: 'error', label: '失败' },
};

export const DIAGNOSIS_STATUS_MAP: Record<DiagnosisStatus, StatusConfig> = {
  uploaded: { color: 'default', label: '已上传' },
  queued: { color: 'processing', label: '排队中' },
  running: { color: 'processing', label: '诊断中' },
  succeeded: { color: 'success', label: '诊断完成' },
  failed: { color: 'error', label: '诊断失败' },
};

export const HPC_JOB_STATUS_MAP: Record<HpcJobStatus, StatusConfig> = {
  draft: { color: 'default', label: '草稿' },
  ready_for_confirmation: { color: 'warning', label: '待确认' },
  authorized: { color: 'cyan', label: '已授权' },
  submitting: { color: 'processing', label: '提交中' },
  submitted: { color: 'blue', label: '已提交' },
  pending: { color: 'processing', label: '排队中' },
  running: { color: 'processing', label: '运行中' },
  completed: { color: 'success', label: '已完成' },
  failed: { color: 'error', label: '失败' },
  cancelled: { color: 'default', label: '已取消' },
  timeout: { color: 'warning', label: '超时' },
  out_of_memory: { color: 'error', label: '内存不足' },
  unknown: { color: 'default', label: '未知' },
};

export const DEPLOYMENT_STATUS_MAP: Record<DeploymentStatus, StatusConfig> = {
  draft: { color: 'default', label: '草稿' },
  preflight_failed: { color: 'error', label: '预检失败' },
  ready_for_confirmation: { color: 'warning', label: '待确认' },
  authorized: { color: 'cyan', label: '已授权' },
  deploying: { color: 'processing', label: '部署中' },
  deployed: { color: 'success', label: '已部署' },
  failed: { color: 'error', label: '失败' },
};

export const SEVERITY_MAP: Record<Severity, StatusConfig> = {
  info: { color: 'default', label: '信息' },
  low: { color: 'cyan', label: '低' },
  medium: { color: 'warning', label: '中' },
  high: { color: 'volcano', label: '高' },
  critical: { color: 'error', label: '严重' },
};

export const RECIPE_STATUS_MAP: Record<RecipeStatus, StatusConfig> = {
  draft: { color: 'default', label: '草稿' },
  published: { color: 'success', label: '已发布' },
  deprecated: { color: 'error', label: '已弃用' },
};