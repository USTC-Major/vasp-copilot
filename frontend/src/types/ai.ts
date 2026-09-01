// ============================================================
// AI 全流程作业 — 类型与状态映射（第 1 步准备 + 第 3 步质检）
// 状态串联：planned -> generated -> submitted -> collected
//           -> inspecting -> done（failed 任意阶段可到达）
// ============================================================

import type { WorkflowStep, FileInheritanceDependency } from './generated-api';

export type AiJobStatus =
  | 'idle'
  | 'planned'
  | 'generated'
  | 'submitted'
  | 'collected'
  | 'inspecting'
  | 'done'
  | 'failed';

export interface AiTimelineEntry {
  status: AiJobStatus;
  note: string;
  at: number;
}

export interface AiStepExplanation {
  step: string;
  label: string;
  explanation: string;
}

export interface AiPlanData {
  requested_tasks: string[];
  assumptions: Record<string, unknown>;
  patches: { parameter: string; op: string; value: unknown; reason?: string }[];
  step_explanations: AiStepExplanation[];
  user_needs: string;
}

export interface AiReportSummary {
  report_ready: boolean;
  report_id: string;
  download_url: string;
}

export interface AiWorkflowWarning {
  code: string;
  message: string;
  severity: string;
}

export interface AiWorkflowPreview {
  status: string;
  steps: WorkflowStep[];
  file_inheritance_plan: {
    plan_id?: string;
    workflow_id?: string;
    revision?: number;
    dependencies: FileInheritanceDependency[];
    evaluated_at?: string | null;
  };
  warnings: AiWorkflowWarning[];
}

export interface AiJobRecord {
  job_id: string;
  goal_text: string;
  status: AiJobStatus;
  degraded: boolean;
  structure_id?: string;
  workflow_id?: string;
  plan?: AiPlanData;
  download_url?: string;
  submit_instructions?: string;
  diagnosis_id?: string;
  report_summary?: AiReportSummary;
  timeline: AiTimelineEntry[];
  coverage?: { start: string; end: string };
  current_step?: string;
}

export interface AiJobDetail extends AiJobRecord {
  workflow_preview?: AiWorkflowPreview;
  diagnosis?: { report_ready?: boolean; report_id?: string; download_url?: string } | { error_code: string; error_message: string } | null;
  run_messages?: string[];
}

// ---- 状态展示配置 ----
export interface AiStatusConfig {
  color: string;
  label: string;
}

export const AI_JOB_STATUS_MAP: Record<AiJobStatus, AiStatusConfig> = {
  idle: { color: 'default', label: '新建 · 等待需求' },
  planned: { color: 'processing', label: 'AI 已规划 · 待你批准' },
  generated: { color: 'cyan', label: '生成包就绪 · 待提交' },
  submitted: { color: 'blue', label: '已提交 · 运行中' },
  collected: { color: 'warning', label: '已回收 · 待质检' },
  inspecting: { color: 'processing', label: 'AI 质检中' },
  done: { color: 'success', label: '已完成 · 报告就绪' },
  failed: { color: 'error', label: '失败' },
};

// 流程步骤展示顺序（fail 不参与主流程）
export const AI_JOB_FLOW: AiJobStatus[] = [
  'planned',
  'generated',
  'submitted',
  'collected',
  'inspecting',
  'done',
];

// 8 工序固定顺序（对应 backend/ai_mode/workflow/steps.py）
export const AI_WORKFLOW_STEPS: { key: string; label: string }[] = [
  { key: 'understand', label: '理解需求' },
  { key: 'plan', label: '规划作业' },
  { key: 'prepare_input', label: '准备输入' },
  { key: 'setup', label: '连接超算搭建' },
  { key: 'precheck', label: '提交前检查' },
  { key: 'submit_monitor', label: '提交与监控' },
  { key: 'finish', label: '作业结束确认' },
  { key: 'report', label: '结果与报告' },
];


// ============================================================
// M12 前端整合类型 — 项目 / 计算任务 / 设置 / 上下文 / 等待队列
// （项目/任务/上下文在 M13 对接真实后端前由演示数据后端提供）
// ============================================================

export interface AiMessage {
  role: 'user' | 'assistant';
  content: string;
  thinking?: string;
  at?: string;
}

export interface AiSettingsLlm {
  base_url: string;
  model: string;
  provider?: string;
  enable_thinking?: boolean;
  api_key: string; // ""（未配置）或 "<redacted>"
}

export interface AiSettingsOut {
  enabled: boolean;
  data_dir?: string;
  max_jobs: number;
  poll_interval_seconds?: number;
  billing_estimate_enabled?: boolean;
  llm: AiSettingsLlm;
  ssh: { name: string; host: string; port: number; username: string };
  materials_project: { api_key: string };
}

export interface AiSecretStatus {
  llm: boolean;
  mp: boolean;
  ssh: boolean;
}

export interface AiSecretReveal {
  mode: string;
  kind: "llm" | "mp" | "ssh";
  value: string;
}

export interface AiProject {
  id: string;
  name: string;
  description?: string;
  created_at: string;
  updated_at?: string;
  job_count: number;
  context_ratio: number; // 上下文占有率 0..1
}

export interface AiProjectSettings {
  project_id: string;
  // 额外设置条目：纯内容（无名字，只有内容）；AI 控制本任务运行时遵循，实时注入不进聊天记录
  accuracy: string[];
}

export interface AiTask {
  id: string;
  project_id: string;
  title: string;
  goal: string;
  local_workspace?: string;
  hpc_workspace?: string;
  status: AiJobStatus;
  job?: AiJobRecord;
  updated_at: string;
  context_ratio?: number;
  last_message?: string;
}

export interface AiTaskPatch {
  title?: string;
  goal?: string;
  local_workspace?: string;
  hpc_workspace?: string;
}

export interface AiWaitQueueEntry {
  task_title?: string;
  queued_at: string;
  reason: string;
}

export interface AiContextSummary {
  ratio: number;
  used: number;
  capacity: number;
}
// ---- 目录浏览（M032：本地/超算工作区图形化点选）----
export interface AiBrowseEntry {
  name: string;
  is_dir: boolean;
  size?: number;
}

export interface AiBrowseResponse {
  mode: string;
  kind: 'local' | 'hpc';
  path?: string;
  parent?: string | null;
  exists?: boolean;
  is_dir?: boolean;
  roots?: AiBrowseEntry[];
  entries: AiBrowseEntry[];
  notice?: string;
}
export interface AiMkdirResponse {
  ok: boolean;
  path?: string;
  notice?: string;
}

export interface AiPickResponse {
  ok: boolean;
  mode?: string;
  kind?: 'local' | 'hpc';
  path?: string;
  notice?: string;
}

// ---- 流式聊天事件（SSE）----
export interface AiConsentCard {
  card_id: string;
  tool: string;
  args: Record<string, unknown>;
  risk: 'high' | 'medium' | 'low';
  reason: string;
  options: string[];
  batch_key: string;
  kind: 'workspace' | 'submit';
  summary: string;
}

export type AiStreamEvent =
  | { type: "thinking"; text: string }
  | { type: "answer"; text: string }
  | { type: "status"; text: string }
  | { type: "card"; card: AiConsentCard }
  | { type: "done"; answer: string }
  | { type: "stopped"; answer: string }
  | { type: "error"; message: string };

// ---- 任务详情（GET /tasks/{tid}/detail，智能模式真实 flow 概要）----
export interface AiFlowJob {
  key: string;
  label: string;
  kind: string;
  requires: string[];
  status: string;
  slurm_id: number | string | null;
  description: string;
}

export interface AiFlowDetail {
  phase: string;
  goal: string;
  strategy: string;
  local_dir: string;
  hpc_dir: string;
  waiting: string[];
  precheck: { ok: boolean; issues: { job: string; file: string; level: string; message: string }[] };
  report: string;
  jobs: AiFlowJob[];
}

// flow phase 展示映射
export const AI_FLOW_PHASE_MAP: Record<string, { color: string; label: string }> = {
  running: { color: 'processing', label: '准备输入 · 待提交' },
  await_submit: { color: 'warning', label: '待你确认提交' },
  monitoring: { color: 'processing', label: '监控计算中' },
  blocked: { color: 'error', label: '已阻塞/取消' },
  done: { color: 'success', label: '已完成' },
};

// flow 作业状态展示映射（对齐 backend/ai_mode/jobs/state.py）
export const AI_FLOW_JOB_STATUS_MAP: Record<string, { color: string; label: string }> = {
  draft: { color: 'default', label: '准备输入' },
  waiting: { color: 'gold', label: '等待前置' },
  submitted: { color: 'blue', label: '已提交' },
  queued: { color: 'blue', label: '排队中' },
  running: { color: 'processing', label: '运行中' },
  completed: { color: 'success', label: '已完成' },
  failed: { color: 'error', label: '失败' },
  not_converged: { color: 'error', label: '未收敛' },
  not_found: { color: 'error', label: '未找到' },
  canceled: { color: 'default', label: '已取消' },
  blocked: { color: 'red', label: '已阻断' },
  skipped: { color: 'default', label: '已跳过' },
};
