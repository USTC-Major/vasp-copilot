// ============================================================
// workflow-contract — 工作流生成请求/响应契约类型（与后端对齐）
//
// 后端契约来源：
// - 请求侧 SchedulerSettings（backend/app/schemas/generation.py）：字段名为 `type`
// - 响应侧 SchedulerBlock（backend/app/schemas/workflow.py）：字段名为
//   `scheduler_type` + 可选 `scheduler_profile_id`
// 两者必须用独立类型表示，禁止同一 interface 双用。
// 工作流生成请求的 scheduler 类型单独限定为 slurm|cbatch|generic；
// HPC 侧的 custom/fake 枚举（types/enums.ts SchedulerType）保持不变。
// ============================================================

export type WorkflowSchedulerType = 'slurm' | 'cbatch' | 'generic';

export type WorkflowPrecision = 'quick' | 'standard' | 'high';

export type WorkflowElectronicType = 'metal' | 'semiconductor' | 'unknown';

/** 请求侧：嵌套在 workflow.scheduler 中提交给后端。 */
export interface SchedulerRequest {
  type: WorkflowSchedulerType;
  nodes: number;
  tasks_per_node: number;
  walltime: string;
  partition?: string | null;
  account?: string | null;
  job_name?: string | null;
  vasp_binary_hint: string;
  module_loads?: string[];
  parallel_defaults?: Record<string, number>;
}

/** 响应侧：后端回显/计划文件中的 scheduler 块。 */
export interface SchedulerResponseBlock {
  scheduler_type: string;
  scheduler_profile_id?: string | null;
  nodes: number;
  tasks_per_node: number;
  walltime: string;
  vasp_binary_hint: string;
}

/** 请求侧：单条 DFT+U 条目（U/J/L 均为用户输入，系统不保证科研正确性）。 */
export interface DftuEntryRequest {
  element: string;
  l: number;
  u_eff_ev?: number | null;
  u_ev?: number | null;
  j_ev?: number | null;
  source_note?: string | null;
  confirmed_by_user: boolean;
}

/** 请求侧：DFT+U 设置。 */
export interface DftuSettingsRequest {
  enabled: boolean;
  form?: 'dudarev' | 'liechtenstein' | null;
  input_mode?: 'u_eff' | 'u_j' | null;
  entries: DftuEntryRequest[];
}

export interface MaterialAssumptionsRequest {
  electronic_type: WorkflowElectronicType;
  magnetic: boolean;
  soc: boolean;
  precision: WorkflowPrecision;
}

/** POST /api/v1/workflows/plan 的嵌套请求体（后端 WorkflowApiRequest.workflow）。 */
export interface WorkflowPlanRequestBody {
  structure_id: string;
  patches?: import('./generated-api').ParameterPatch[];
  workflow: {
    sample_name?: string;
    requested_tasks: string[];
    goal_text?: string | null;
    material_assumptions: MaterialAssumptionsRequest;
    precision: WorkflowPrecision;
    dftu: DftuSettingsRequest;
    scheduler: SchedulerRequest;
    confirm: boolean;
    potcar?: import('./potcar').WorkflowPotcarChoice;
  };
}

/** 最终确认摘要的不可变快照：Modal 展示与实际发送 payload 同源。 */
export interface WorkflowConfirmSnapshot {
  patches?: import('./generated-api').ParameterPatch[];
  catalysis_binding?: import('./catalysis').CatalysisWorkflowBinding | null;
  surface_policy?: import('./catalysis').CatalysisSurfacePolicy | null;
  structure: { formula: string; elements: string[] };
  sample_name: string;
  poscar_comment: string;
  requested_tasks: string[];
  electronic_type: WorkflowElectronicType;
  magnetic: boolean;
  soc: boolean;
  precision: WorkflowPrecision;
  dftu: DftuSettingsRequest;
  scheduler: SchedulerRequest;
}
