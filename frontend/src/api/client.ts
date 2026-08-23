// ============================================================
// API Client — 类型安全的 API 请求封装
// ============================================================

const API_BASE = '/api/v1';

interface RequestOptions {
  method?: string;
  body?: unknown;
  headers?: Record<string, string>;
  params?: Record<string, string | number>;
  responseType?: 'json' | 'blob';
  signal?: AbortSignal;
}

export class ApiError extends Error {
  code: string;
  retryable: boolean;
  status: number;
  fieldErrors: { field: string; code: string; message: string }[];

  constructor(
    code: string,
    message: string,
    retryable: boolean = false,
    status: number = 500,
    fieldErrors: { field: string; code: string; message: string }[] = []
  ) {
    super(message);
    this.name = 'ApiError';
    this.code = code;
    this.retryable = retryable;
    this.status = status;
    this.fieldErrors = fieldErrors;
  }
}

async function request<T>(endpoint: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, headers = {}, params, responseType = 'json', signal } = options;

  let url = `${API_BASE}${endpoint}`;
  if (params) {
    const searchParams = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => searchParams.append(k, String(v)));
    url += `?${searchParams.toString()}`;
  }

  const fetchHeaders: Record<string, string> = { ...headers };
  if (body && !(body instanceof FormData)) {
    fetchHeaders['Content-Type'] = 'application/json';
  }

  const response = await fetch(url, {
    method,
    headers: fetchHeaders,
    body: body instanceof FormData ? body : body ? JSON.stringify(body) : undefined,
    signal,
  });

  if (responseType === 'blob') {
    if (!response.ok) throw new ApiError('DOWNLOAD_FAILED', '下载失败', false, response.status);
    return response.blob() as unknown as T;
  }

  const data = await response.json();

  if (!response.ok || data.error) {
    const err = data.error || {};
    throw new ApiError(
      err.code || 'UNKNOWN',
      err.message || '未知错误',
      err.retryable || false,
      response.status,
      err.field_errors || []
    );
  }

  // 后端统一返回 {request_id, data:{...}} 封装（IR-01）；MSW 直接返回扁平对象。
  // 这里解包 data，使调用方始终拿到扁平字段。
  if (data && typeof data === 'object' && 'data' in data) {
    const payload = (data as { data?: unknown }).data;
    if (payload && typeof payload === 'object') {
      const merged = { ...data, ...(payload as object) };
      delete (merged as { data?: unknown }).data;
      return merged as T;
    }
  }
  return data as T;
}

// ---- Files API ----
export const filesApi = {
  upload: (file: File, purpose: 'structure' | 'pseudopotential' = 'structure') => {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('purpose', purpose);
    if (purpose === 'pseudopotential') {
      formData.append('license_confirmed', 'true');
    }
    return request<{ request_id: string; file: import('../types/generated-api').UploadedFile }>('/files/upload', {
      method: 'POST',
      body: formData,
    });
  },

  preview: (fileId: string, options?: { mode?: string; start_line?: number; max_lines?: number }) => {
    const params: Record<string, string | number> = {};
    if (options?.mode) params.mode = options.mode;
    if (options?.start_line) params.start_line = options.start_line;
    if (options?.max_lines) params.max_lines = options.max_lines;
    return request<import('../types/generated-api').FilePreviewResponse>(`/files/${fileId}/preview`, { params });
  },
};

// ---- Structure API ----
export const structureApi = {
  analyze: (fileId: string, options?: { symmetry_tolerance?: number; standardize?: boolean }) =>
    request<import('../types/generated-api').StructureAnalysisResponse>('/structure/analyze', {
      method: 'POST',
      body: { file_id: fileId, ...options },
    }),
};

// ---- Materials Project API ----
export const materialsApi = {
  search: (query: string, limit = 20) =>
    request<{ request_id: string; query: string; criteria: Record<string, unknown>; llm_used: boolean; count: number; materials: import('../types/generated-api').MaterialCandidate[] }>('/materials/search', {
      method: 'POST',
      body: { query, limit },
    }),

  importMaterial: (materialId: string) =>
    request<{ request_id: string; structure_id: string; normalized_poscar_file_id: string; file_id: string; material_id: string; summary: import('../types/generated-api').StructureSummary }>('/materials/import', {
      method: 'POST',
      body: { material_id: materialId },
    }),
};
// ---- Workflows API ----
export const workflowsApi = {
  plan: (body: import('../types/workflow-contract').WorkflowPlanRequestBody) =>
    request<{ request_id: string } & import('../types/generated-api').WorkflowPlan>('/workflows/plan', {
      method: 'POST',
      body,
    }),

  planFromNl: (body: { structure_id: string; goals: string[]; assumptions?: Record<string, unknown> }) =>
    request<{ request_id: string } & import('../types/generated-api').WorkflowPlan & { ai?: { enabled: boolean; degraded: boolean; user_needs: string; requested_tasks: string[]; explanations: { step: string; label: string; explanation: string }[] } }>('/workflows/plan_from_nl', {
      method: 'POST',
      body,
    }),
  generate: (workflowId: string, patches: import('../types/generated-api').ParameterPatch[] = []) =>
    request<{ request_id: string; workflow_id: string; workflow_status: string; file_tree: import('../types/generated-api').FileTreeNode }>('/workflows/generate', {
      method: 'POST',
      body: { workflow_id: workflowId, patches },
    }),

  get: (workflowId: string) =>
    request<{ request_id: string; workflow_id: string; workflow_status: string; plan: import('../types/generated-api').WorkflowPlan; file_tree: import('../types/generated-api').FileTreeNode }>(`/workflows/${workflowId}`),

  download: (workflowId: string) =>
    request<Blob>(`/workflows/${workflowId}/download`, { responseType: 'blob' }),
};

// ---- Diagnosis API ----
export const diagnosisApi = {
  upload: (file: File) => {
    const formData = new FormData();
    formData.append('file', file);
    return request<{ request_id: string; diagnosis_id: string; detected_run: import('../types/generated-api').DetectedRun }>('/diagnosis/upload', {
      method: 'POST',
      body: formData,
    });
  },

  run: (diagnosisId: string) =>
    request<{ request_id: string } & import('../types/generated-api').DiagnosisResult>('/diagnosis/run', {
      method: 'POST',
      body: { diagnosis_id: diagnosisId },
    }),

  get: (diagnosisId: string) =>
    request<{ request_id: string } & import('../types/generated-api').DiagnosisResult>(`/diagnosis/${diagnosisId}`),

  report: (diagnosisId: string) =>
    request<Blob>(`/diagnosis/${diagnosisId}/report`, { responseType: 'blob' }),

  downloadFix: (diagnosisId: string) =>
    request<Blob>(`/diagnosis/${diagnosisId}/download-fix`, { responseType: 'blob' }),

  explain: (diagnosisId: string, question: string) =>
    request<{ request_id: string; diagnosis_id: string; diagnosis_status: string; answer: string; degraded?: boolean }>(`/diagnosis/${diagnosisId}/explain`, {
      method: 'POST',
      body: { question },
    }),
};

// ---- Recipes API ----
export const recipesApi = {
  list: () =>
    request<{ request_id: string; recipes: import('../types/generated-api').RecipeManifest[] }>('/recipes'),

  get: (recipeId: string) =>
    request<{ request_id: string } & import('../types/generated-api').RecipeManifest>(`/recipes/${recipeId}`),
};

// ---- HPC API ----
export const hpcApi = {
  getClusters: () =>
    request<{ request_id: string; clusters: import('../types/generated-api').ClusterProfile[] }>('/hpc/clusters'),

  planDeployment: (body: { workflow_id: string; cluster_profile_id: string }) =>
    request<{ request_id: string } & import('../types/generated-api').RemoteDeploymentPlan>('/hpc/deployments/plan', {
      method: 'POST',
      body,
    }),

  getDeployment: (deploymentId: string) =>
    request<{ request_id: string } & import('../types/generated-api').RemoteDeploymentPlan>(`/hpc/deployments/${deploymentId}`),

  authorizeDeployment: (deploymentId: string) =>
    request<{ request_id: string; grant_id: string; capability: string; single_use: boolean; expires_at: string }>(`/hpc/deployments/${deploymentId}/authorize`, {
      method: 'POST',
    }),

  executeDeployment: (deploymentId: string) =>
    request<{ request_id: string; deployment_id: string; deployment_status: string }>(`/hpc/deployments/${deploymentId}/execute`, {
      method: 'POST',
    }),

  planJob: (body: { manifest_id: string; step_id: string; resources?: Record<string, unknown> }) =>
    request<{ request_id: string; submission_draft_id: string; hpc_job_status: string; resources: Record<string, unknown> }>('/hpc/jobs/plan', {
      method: 'POST',
      body,
    }),

  authorizeJob: (jobId: string) =>
    request<{ request_id: string; grant_id: string }>(`/hpc/jobs/${jobId}/authorize`, { method: 'POST' }),

  submitJob: (jobId: string) =>
    request<{ request_id: string } & import('../types/generated-api').RemoteJob>(`/hpc/jobs/${jobId}/submit`, {
      method: 'POST',
    }),

  getJob: (jobId: string) =>
    request<{ request_id: string } & import('../types/generated-api').RemoteJob>(`/hpc/jobs/${jobId}`),

  authorizeCollection: (jobId: string) =>
    request<{ request_id: string; grant_id: string }>(`/hpc/jobs/${jobId}/authorize-collection`, { method: 'POST' }),

  collect: (jobId: string) =>
    request<{ request_id: string } & import('../types/generated-api').ResultCollection>(`/hpc/jobs/${jobId}/collect`, {
      method: 'POST',
    }),
};

// ---- LLM 配置 API（前端可切换/测试模型，运行期覆盖后端 .env）----
export interface LlmConfigSummary {
  enabled: boolean;
  base_url: string;
  model: string;
  api_key_set: boolean;
  api_key_masked: string;
  source: string;
  usable: boolean;
  timeout_seconds: number;
  max_retries: number;
  max_tokens: number;
  temperature: number;
}

export interface LlmConfigUpdate {
  enabled?: boolean;
  base_url: string;
  api_key: string;
  model: string;
  timeout_seconds?: number;
  max_retries?: number;
  max_tokens?: number;
  temperature?: number;
}

export interface LlmTestResult {
  ok: boolean;
  message: string;
  reply?: string;
}

export const llmApi = {
  getConfig: () =>
    request<{ request_id: string } & LlmConfigSummary>('/llm/config'),

  saveConfig: (body: LlmConfigUpdate) =>
    request<{ request_id: string } & LlmConfigSummary>('/llm/config', {
      method: 'POST',
      body,
    }),

  resetConfig: () =>
    request<{ request_id: string } & LlmConfigSummary>('/llm/config', { method: 'DELETE' }),

  testConfig: (body: Partial<LlmConfigUpdate>) =>
    request<{ request_id: string } & LlmTestResult>('/llm/config/test', {
      method: 'POST',
      body,
    }),
};


// ---- AI 对话 ----
export interface ChatMessageItem {
  role: 'user' | 'assistant';
  content: string;
}

export interface ChatSendResult {
  request_id: string;
  answer: string;
  usable?: boolean;
  degraded?: boolean;
}

export interface ChatHistoryResult {
  messages: ChatMessageItem[];
  persisted: boolean;
}

export const chatApi = {
  send: (message: string, history: ChatMessageItem[] = []) =>
    request<ChatSendResult>('/chat', {
      method: 'POST',
      body: { message, history },
    }),

  getHistory: () =>
    request<{ request_id: string } & ChatHistoryResult>('/chat/history'),

  saveHistory: (messages: ChatMessageItem[]) =>
    request<{ request_id: string } & ChatHistoryResult>('/chat/history', {
      method: 'POST',
      body: { messages },
    }),

  clearHistory: () =>
    request<{ request_id: string } & ChatHistoryResult>('/chat/history', {
      method: 'DELETE',
    }),
};
