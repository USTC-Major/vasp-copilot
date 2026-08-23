// ============================================================
// MSW Fixtures — 为所有 P0 端点准备 success/error/empty fixtures
// ============================================================

import type {
  UploadedFile, StructureAnalysisResponse, WorkflowPlan,
  FilePreviewResponse, FileTreeNode, DiagnosisResult,
  FeatureFlags, ClusterProfile, DeploymentOperation,
} from '../types/generated-api';

// ---- Feature Flags ----
export const featureFlagsFixture: FeatureFlags = {
  ENABLE_LLM: false,
  ENABLE_HPC_BRIDGE: false,
  ENABLE_FAKE_HPC: true,
  ENABLE_POTCAR_ASSEMBLY: false,
  ENABLE_BAND_WORKFLOW: true,
  MAX_UPLOAD_SIZE_MB: 100,
  MAX_TEXT_PREVIEW_BYTES: 524288,
  MAX_OUTCAR_PREVIEW_LINES: 500,
};

// ---- 文件上传 ----
export const uploadSuccessFixture: UploadedFile = {
  file_id: 'file_01',
  name: 'POSCAR',
  kind: 'poscar',
  size_bytes: 842,
  sha256: 'ab12def456...',
  file_status: 'ready',
  expires_at: '2026-08-03T02:00:00Z',
};

// ---- 结构分析 ----
export const structureAnalysisFixture: StructureAnalysisResponse = {
  request_id: 'req_02',
  structure_id: 'str_01',
  summary: {
    structure_id: 'str_01',
    formula: 'Fe2O3',
    reduced_formula: 'Fe2O3',
    elements: ['Fe', 'O'],
    counts: [2, 3],
    atom_count: 5,
    lattice: {
      matrix: [[5.03, 0, 0], [-2.515, 4.356, 0], [0, 0, 13.75]],
      a: 5.03, b: 5.03, c: 13.75,
      alpha: 90, beta: 90, gamma: 120,
      volume: 301.2,
    },
    coordinate_mode: 'direct',
    selective_dynamics: false,
    transition_metals: ['Fe'],
    magnetism_hint: 'possible',
    source_format: 'poscar',
    source_sha256: 'ab12...',
    warnings: [
      {
        code: 'MAGNETISM_REQUIRES_CONFIRMATION',
        message: '含 Fe；是否磁性需用户确认。',
        severity: 'medium',
      },
    ],
  },
  normalized_poscar_file_id: 'file_02',
};

// ---- 工作流计划 ----
export const workflowPlanFixture: WorkflowPlan = {
  schema_version: '1.0',
  workflow_id: 'wf_01',
  revision: 1,
  created_at: '2026-08-02T02:00:00Z',
  structure: {
    structure_id: 'str_01',
    formula: 'Fe2O3',
    elements: ['Fe', 'O'],
    counts: [2, 3],
    source_sha256: 'ab12...',
  },
  goal: {
    original_text: '做优化、静态和 DOS',
    requested_tasks: ['relax', 'static', 'dos'],
  },
  assumptions: {
    electronic_type: 'unknown',
    magnetic: true,
    soc: false,
    precision: 'standard',
  },
  dftu: {
    enabled: true,
    entries: [
      {
        element: 'Fe',
        l: 2,
        u_ev: 5.3,
        j_ev: 0.0,
        source_note: 'user_input',
        confirmed_by_user: true,
      },
    ],
  },
  scheduler: {
    scheduler_type: 'slurm',
    scheduler_profile_id: 'scheduler_demo_slurm',
    nodes: 1,
    tasks_per_node: 32,
    walltime: '12:00:00',
    vasp_binary_hint: 'vasp_std',
  },
  remote_execution: {
    enabled: false,
    mode: 'disabled',
    cluster_profile_id: null,
    deploy_requires_confirmation: true,
    submit_requires_confirmation: true,
    auto_resubmit: false,
  },
  steps: [
    {
      step_id: '01_relax',
      task: 'relax',
      label: '结构优化',
      directory: '01_relax',
      depends_on: [],
      runnable: true,
      blocked_by: [],
      requires_runtime_outputs: [],
      produces: ['CONTCAR', 'OUTCAR', 'OSZICAR'],
      parameters: {
        INCAR: { ENCUT: 520, EDIFF: 1e-5 },
        KPOINTS: { mode: 'automatic_density', kppa: 1000 },
      },
    },
    {
      step_id: '02_static',
      task: 'static',
      label: '静态计算',
      directory: '02_static',
      depends_on: ['01_relax'],
      runnable: false,
      blocked_by: ['POTCAR_NOT_PREPARED'],
      requires_runtime_outputs: ['CONTCAR'],
      produces: ['CHGCAR', 'OUTCAR', 'OSZICAR'],
      parameters: {
        INCAR: { ENCUT: 520, EDIFF: 1e-6 },
        KPOINTS: { mode: 'automatic_density', kppa: 2000 },
      },
    },
    {
      step_id: '03_dos',
      task: 'dos',
      label: '态密度计算',
      directory: '03_dos',
      depends_on: ['02_static'],
      runnable: false,
      blocked_by: ['CONTCAR_NOT_READY'],
      requires_runtime_outputs: ['CHGCAR'],
      produces: ['DOSCAR', 'OUTCAR'],
      parameters: {
        INCAR: { ICHARG: 11, LORBIT: 11, NEDOS: 2000 },
      },
    },
  ],
  file_inheritance_plan: {
    plan_id: 'inherit_wf_01',
    workflow_id: 'wf_01',
    revision: 1,
    dependencies: [
      {
        dependency_id: 'dep_relax_static_contcar',
        from_step_id: '01_relax',
        source_file: 'CONTCAR',
        to_step_id: '02_static',
        target_file: 'POSCAR',
        required: true,
        satisfied: false,
        requires_upstream_diagnosis_pass: true,
      },
      {
        dependency_id: 'dep_static_dos_chgcar',
        from_step_id: '02_static',
        source_file: 'CHGCAR',
        to_step_id: '03_dos',
        target_file: 'CHGCAR',
        required: true,
        satisfied: false,
        requires_upstream_diagnosis_pass: true,
      },
    ],
    evaluated_at: null,
  },
  recipe_compositions: [
    {
      composition_id: 'composition_01',
      revision: 2,
      step_id: '01_relax',
      recipe_pack: {
        pack_id: 'vasp-mvp-core',
        version: '1.0.0',
        sha256: 'pack-a1...',
      },
      selected: [
        {
          recipe_ref: 'base.vasp@1.0.0',
          version: '1.0.0',
          layer: 'base',
          order: 10,
          selection_reason: '所有 VASP 任务共享的受审查基础参数',
        },
        {
          recipe_ref: 'task.relax.standard@1.0.0',
          version: '1.0.0',
          layer: 'task',
          order: 20,
          selection_reason: '目标包含 relax，精度等级为 standard',
        },
        {
          recipe_ref: 'electronic.unknown@1.0.0',
          version: '1.0.0',
          layer: 'electronic_type',
          order: 30,
          selection_reason: '用户将体系电子类型标记为不确定',
        },
        {
          recipe_ref: 'modifier.magnetic@1.0.0',
          version: '1.0.0',
          layer: 'modifier',
          order: 40,
          selection_reason: '用户确认磁性，结构含 Fe',
        },
        {
          recipe_ref: 'modifier.dftu@1.0.0',
          version: '1.0.0',
          layer: 'modifier',
          order: 41,
          selection_reason: '用户启用 DFT+U 且已确认 Fe 的 U/J',
        },
      ],
      composition_sha256: 'composition-c1...',
    },
  ],
  confirmations: [
    {
      key: 'dftu.entries.Fe.u_ev',
      prompt: '确认 Fe 的 U 值',
      confirmation_status: 'confirmed',
      confirmed_at: '2026-08-02T02:02:00Z',
    },
  ],
  warnings: [],
  template_versions: {
    'scheduler.slurm': '1.0.0',
    'readme': '1.0.0',
  },
};

// ---- 文件树 ----
export const fileTreeFixture: FileTreeNode = {
  name: 'vasp_workflow_wf_01',
  type: 'directory',
  relative_path: '.',
  children: [
    {
      name: '01_relax',
      type: 'directory',
      relative_path: '01_relax',
      children: [
        {
          name: 'INCAR',
          type: 'file',
          relative_path: '01_relax/INCAR',
          file_id: 'file_11',
          mime_type: 'text/plain',
          size_bytes: 421,
          sha256: 'incar-hash...',
          preview_available: true,
          generated_by: 'IncarGenerator',
        },
        {
          name: 'KPOINTS',
          type: 'file',
          relative_path: '01_relax/KPOINTS',
          file_id: 'file_12',
          mime_type: 'text/plain',
          size_bytes: 120,
          sha256: 'kpoints-hash...',
          preview_available: true,
          generated_by: 'KpointsGenerator',
        },
        {
          name: 'POSCAR',
          type: 'file',
          relative_path: '01_relax/POSCAR',
          file_id: 'file_13',
          mime_type: 'text/plain',
          size_bytes: 842,
          sha256: 'poscar-hash...',
          preview_available: true,
          generated_by: 'PoscarCopier',
        },
      ],
    },
    {
      name: 'submit.sh',
      type: 'file',
      relative_path: 'submit.sh',
      file_id: 'file_14',
      mime_type: 'text/plain',
      size_bytes: 256,
      sha256: 'script-hash...',
      preview_available: true,
      generated_by: 'ScriptGenerator',
    },
  ],
};

// ---- 文件预览 ----
export const filePreviewFixture: FilePreviewResponse = {
  request_id: 'req_preview_01',
  file_id: 'file_11',
  name: 'INCAR',
  kind: 'incar',
  mime_type: 'text/plain',
  encoding: 'utf-8',
  sha256: 'incar-hash...',
  preview: {
    content: 'SYSTEM = Fe2O3_relax\nENCUT = 520\nPREC = Accurate\nEDIFF = 1E-5\nLREAL = Auto\nLWAVE = .FALSE.\nLCHARG = .FALSE.\nIBRION = 2\nNSW = 100\nISIF = 3\nEDIFFG = -0.02\nISMEAR = 0\nSIGMA = 0.05\nALGO = Normal\nISPIN = 2\nMAGMOM = 5.0 5.0 0.0 0.0 0.0\nLDAU = .TRUE.\nLDAUTYPE = 2\nLDAUL = 2 -1\nLDAUU = 4.0 0.0\nLDAUJ = 0.0 0.0\nLMAXMIX = 4\n',
    start_line: 1,
    end_line: 22,
    total_lines: 22,
    returned_bytes: 421,
    truncated: false,
    next_cursor: null,
  },
  policy: {
    max_preview_bytes: 524288,
    max_preview_lines: 1000,
    binary_rejected: true,
    sensitive_content_redacted: false,
  },
};

// ---- 诊断结果 ----
export const diagnosisResultFixture: DiagnosisResult = {
  schema_version: '1.0',
  diagnosis_id: 'diag_01',
  diagnosis_status: 'succeeded',
  summary: {
    headline: '发现 1 个高严重度问题和 1 个中等问题',
    highest_severity: 'high',
    issue_count: { critical: 0, high: 1, medium: 1, low: 0, info: 0 },
  },
  detected_run: {
    root: 'failed_relax',
    run_type: 'relax',
    files: [
      { name: 'INCAR', kind: 'incar', size_bytes: 500, sha256: '...' },
      { name: 'POSCAR', kind: 'poscar', size_bytes: 842, sha256: '...' },
      { name: 'OSZICAR', kind: 'oszicar', size_bytes: 2048, sha256: '...' },
      { name: 'OUTCAR', kind: 'outcar', size_bytes: 50000, sha256: '...' },
    ],
    missing_recommended: ['vasprun.xml'],
  },
  issues: [
    {
      issue_id: 'SCF_NOT_CONVERGED_001',
      rule_id: 'SCF_REACHED_NELM',
      severity: 'high',
      category: 'electronic_convergence',
      title: '电子步达到 NELM 仍未收敛',
      summary: '最后一个离子步达到 NELM=200，未观察到电子收敛标志。',
      evidence: [
        {
          evidence_id: 'ev_01',
          file: 'OSZICAR',
          line: 201,
          message: '最后一个离子步记录到 200 个电子步。',
          excerpt: 'RMM: 200 ...',
          data_ref: 'parsed.oszicar.ionic_steps[-1].electronic_steps[-1]',
        },
      ],
      possible_causes: ['电荷混合震荡', '初始磁矩不合适', '展宽设置与体系类型不匹配'],
      recommendations: [
        {
          recommendation_id: 'rec_01',
          action: 'review_and_set_parameter',
          target: 'INCAR',
          parameter: 'ALGO',
          old_value: 'Fast',
          new_value: 'Normal',
          rationale: '作为更稳健的初始排错尝试；仍需结合体系复核。',
          requires_user_confirmation: true,
          priority: 1,
        },
      ],
      auto_fixable: true,
      confidence: 0.92,
      blocking: true,
      tags: ['scf', 'nelm'],
    },
    {
      issue_id: 'MAGMOM_SIGN_FLIP_001',
      rule_id: 'MAGMOM_SIGN_FLIP',
      severity: 'medium',
      category: 'magnetic',
      title: '提示：磁矩符号翻转',
      summary: 'Fe 原子的最终磁矩符号与初始设置相反。',
      evidence: [
        {
          evidence_id: 'ev_02',
          file: 'OUTCAR',
          line: 500,
          message: 'Fe1 初始磁矩 +5.0，最终磁矩 -4.8',
        },
      ],
      possible_causes: ['可能进入不同磁态'],
      recommendations: [
        {
          recommendation_id: 'rec_02',
          action: 'review_magnetic',
          target: 'INCAR',
          parameter: 'MAGMOM',
          rationale: '核实预期磁态，可能需要调整初始磁矩',
          requires_user_confirmation: true,
          priority: 2,
        },
      ],
      auto_fixable: false,
      confidence: 0.75,
      blocking: false,
      tags: ['magnetic', 'sign_flip'],
    },
  ],
  plots: {
    scf: {
      x_label: '电子步',
      y_label: '能量 (eV)',
      series: (() => {
        const data = [];
        for (let ionic = 0; ionic < 3; ionic++) {
          let energy = -150.0;
          for (let elec = 0; elec < 200; elec++) {
            energy -= Math.random() * 0.01 + (elec > 150 ? 0.0005 : 0.01);
            data.push({ ionic_step: ionic + 1, electronic_step: elec + 1, energy });
          }
        }
        return data;
      })(),
    },
    magnetization: {
      x_label: '原子索引',
      y_label: '磁矩 (μB)',
      series: [
        { atom_index: 1, element: 'Fe', initial_moment: 5.0, final_moment: 4.8 },
        { atom_index: 2, element: 'Fe', initial_moment: 5.0, final_moment: -4.6 },
        { atom_index: 3, element: 'O', initial_moment: 0.0, final_moment: 0.1 },
        { atom_index: 4, element: 'O', initial_moment: 0.0, final_moment: 0.0 },
        { atom_index: 5, element: 'O', initial_moment: 0.0, final_moment: 0.0 },
      ],
    },
  },
  recommended_fixes: [
    {
      fix_id: 'fix_01',
      issue_ids: ['SCF_NOT_CONVERGED_001'],
      target_file: 'INCAR',
      strategy: 'parameter_patch',
      fix_status: 'proposed',
      safe_to_generate: true,
      requires_user_confirmation: true,
      changes: [
        {
          parameter: 'ALGO',
          operation: 'replace',
          old_value: 'Fast',
          new_value: 'Normal',
          reason: '提高稳健性',
        },
      ],
      diff: '- ALGO = Fast\n+ ALGO = Normal\n',
      generated_file_id: 'file_fix_01',
      warnings: ['这是排错建议，不保证对所有体系收敛。'],
    },
  ],
  missing_evidence: [],
  next_step: {
    allowed: false,
    suggested_task: null,
    reason: '先处理 high 问题',
  },
  report: {
    report_id: 'report_01',
    format: 'markdown',
    ready: true,
    download_url: '/api/v1/diagnosis/diag_01/report',
  },
  provenance: {
    parser_version: '0.1.0',
    rule_set_version: '0.1.0',
    recipe_pack_version: '1.0.0',
    composition_sha256: 'composition-c1...',
    vasp_version: '6.4.3',
    vasp_binary_hint: 'vasp_std',
    calculation_mode: {
      is_spin_polarized: true,
      is_dftu: true,
      is_soc: false,
      is_noncollinear: false,
      magnetization_analysis_mode: 'collinear',
    },
    llm_used: false,
    mode: 'rule_based',
  },
};

// ---- 诊断上传结果 ----
export const diagnosisUploadFixture = {
  request_id: 'req_03',
  diagnosis_id: 'diag_01',
  detected_run: diagnosisResultFixture.detected_run,
};

// ---- 错误响应 ----
export const errorFixture = {
  request_id: 'req_error_01',
  error: {
    code: 'FILE_TOO_LARGE',
    message: '文件超过 100MB 上限',
    retryable: false,
    field_errors: [],
    help: '请减小文件大小或分批上传',
  },
};

// ---- HPC 集群 ----
export const clustersFixture: ClusterProfile[] = [
  {
    cluster_profile_id: 'cluster_demo_slurm',
    display_name: '教学 Slurm 集群',
    scheduler_profile_id: 'scheduler_demo_slurm',
    scheduler_type: 'slurm',
    connector_status: 'available',
    capabilities: ['deploy', 'submit', 'status', 'collect'],
    limits: {
      max_nodes: 1,
      max_tasks: 64,
      max_walltime: '24:00:00',
      max_upload_bytes: 10485760,
    },
    allowed_partitions: ['cpu'],
    pseudopotential_mode: 'remote_authorized_library',
    parallel_policy: {
      defaults: { KPAR: 2, NCORE: 32 },
      editable: true,
      scope: 'cluster_demo_slurm',
      provenance: '当前教学集群经验设置',
      disclaimer: '不代表其他集群或体系的通用最优值',
    },
    security: {
      host_key_verification: true,
      credential_location: 'connector_only',
      arbitrary_shell: false,
    },
  },
];

// ---- HPC 部署计划 ----
export const deploymentPlanFixture = {
  schema_version: '1.0',
  deployment_id: 'deploy_01',
  deployment_status: 'ready_for_confirmation' as const,
  workflow_id: 'wf_01',
  workflow_revision: 2,
  cluster_profile_id: 'cluster_demo_slurm',
  bundle_sha256: 'a81f...',
  target_relative_path: 'vasp-copilot/wf_01/rev-2-a81f29c4',
  overwrite: false,
  file_count: 14,
  total_bytes: 25120,
  operations: [
    { operation_id: 'op_01', type: 'create_directory' as const, relative_path: '01_relax' },
    { operation_id: 'op_02', type: 'upload_file' as const, relative_path: '01_relax/INCAR', source_file_id: 'file_11', size_bytes: 421, sha256: '...' },
    { operation_id: 'op_03', type: 'upload_file' as const, relative_path: '01_relax/KPOINTS', source_file_id: 'file_12', size_bytes: 120, sha256: '...' },
    { operation_id: 'op_04', type: 'upload_file' as const, relative_path: '01_relax/POSCAR', source_file_id: 'file_13', size_bytes: 842, sha256: '...' },
  ] as DeploymentOperation[],
  preflight: { passed: true, checks: [], warnings: [], expires_at: '2026-08-02T03:10:00Z' },
  required_capability: 'HPC_DEPLOY' as const,
};

// ---- HPC Job (模拟) ----
export const remoteJobFixture = {
  remote_job_id: 'rjob_01',
  scheduler_type: 'slurm' as const,
  scheduler_profile_id: 'scheduler_demo_slurm',
  scheduler_job_id: '842193',
  cluster_profile_id: 'cluster_demo_slurm',
  manifest_id: 'rmanifest_01',
  step_id: '01_relax',
  hpc_job_status: 'running' as const,
  state: {
    normalized: 'running' as const,
    scheduler_state: 'RUNNING',
    reason: null,
    exit_code: null,
  },
  submitted_at: '2026-08-02T03:04:00Z',
  last_synced_at: '2026-08-02T03:14:20Z',
  collectable: false,
};
