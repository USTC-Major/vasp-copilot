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


// ============================================================
// AI 模式 API（智能模式独立后端，端口与 /ai/v1 前缀经 Vite 代理；
// 项目/任务/消息/上下文/等待队列已对接真实后端（Vite 代理到 :8500），MSW 仅用于前端演示层测试）
// ============================================================
const AI_BASE = "/ai/v1";

interface AiError { code?: string; message?: string; retryable?: boolean; field_errors?: { field: string; code: string; message: string }[] }

async function aiRequest<T>(endpoint: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, headers = {}, signal } = options;
  const fetchHeaders: Record<string, string> = { ...headers, ...(body ? { "Content-Type": "application/json" } : {}) };
  const response = await fetch(`${AI_BASE}${endpoint}`, {
    method,
    headers: fetchHeaders,
    body: body ? JSON.stringify(body) : undefined,
    signal,
  });
  let data: { error?: AiError } = {};
  try {
    data = (await response.json()) as { error?: AiError };
  } catch {
    data = {};
  }
  if (!response.ok || data.error) {
    const err = data.error || {};
    throw new ApiError(
      err.code || "UNKNOWN",
      err.message || "智能模式请求失败",
      !!err.retryable,
      response.status,
      err.field_errors || []
    );
  }
  return data as unknown as T;
}

export const aiApi = {
  ping: () => aiRequest<{ mode: string; enabled: boolean; version: string }>("/ping"),

  getSettings: () =>
    aiRequest<{ mode: string; enabled: boolean; settings: import("../types/ai").AiSettingsOut; writable: string[] }>("/settings"),
  saveSettings: (patch: Record<string, unknown>) =>
    aiRequest<{ mode: string; ok: boolean; settings: import("../types/ai").AiSettingsOut }>("/settings", { method: "PUT", body: patch }),
  testProvider: (provider: string) =>
    aiRequest<{ mode: string; provider: string; ok: boolean; message: string }>(`/settings/test/${encodeURIComponent(provider)}`, { method: "POST" }),
  getSecretStatus: () =>
    aiRequest<{ mode: string; enabled: boolean; secrets: import("../types/ai").AiSecretStatus }>("/settings/secret-status"),
  updateSecret: (kind: "llm" | "mp" | "ssh", action: "replace" | "clear", value?: string) =>
    aiRequest<{ mode: string; ok: boolean; kind: string; configured: boolean; source: string; manageable: boolean; secret: import("../types/ai").AiSecretState }>(`/settings/secrets/${kind}`, { method: "PUT", body: { action, ...(value ? { value } : {}) } }),

  getProjectSettings: (projectId: string) =>
    aiRequest<{ mode: string; project_id: string; settings: import("../types/ai").AiProjectSettings }>(`/projects/${encodeURIComponent(projectId)}/settings`),
  saveProjectSettings: (projectId: string, accuracy: string[]) =>
    aiRequest<{ mode: string; ok: boolean; settings: import("../types/ai").AiProjectSettings }>(`/projects/${encodeURIComponent(projectId)}/settings`, { method: "PUT", body: { accuracy } }),
  deleteProjectSettings: (projectId: string) =>
    aiRequest<{ mode: string; ok: boolean; deleted: boolean; project_id: string }>(`/projects/${encodeURIComponent(projectId)}/settings`, { method: "DELETE" }),

  // 演示数据后端（M13 前）：项目/任务/上下文/等待队列
  listProjects: () =>
    aiRequest<{ projects: import("../types/ai").AiProject[] }>("/projects"),
  createProject: (body: { name: string; description?: string }) =>
    aiRequest<{ project: import("../types/ai").AiProject }>("/projects", { method: "POST", body }),
  deleteProject: (projectId: string) =>
    aiRequest<{ deleted: boolean }>(`/projects/${encodeURIComponent(projectId)}`, { method: "DELETE" }),
  listTasks: (projectId: string) =>
    aiRequest<{ tasks: import("../types/ai").AiTask[] }>(`/projects/${encodeURIComponent(projectId)}/tasks`),
  createTask: (projectId: string, body: { title: string; goal?: string; local_workspace?: string; hpc_workspace?: string }) =>
    aiRequest<{ task: import("../types/ai").AiTask }>(`/projects/${encodeURIComponent(projectId)}/tasks`, { method: "POST", body }),
  updateTask: (projectId: string, taskId: string, body: import("../types/ai").AiTaskPatch) =>
    aiRequest<{ task: import("../types/ai").AiTask }>(`/projects/${encodeURIComponent(projectId)}/tasks/${encodeURIComponent(taskId)}`, { method: "PATCH", body }),
  deleteTask: (projectId: string, taskId: string) =>
    aiRequest<{ deleted: boolean }>(`/projects/${encodeURIComponent(projectId)}/tasks/${encodeURIComponent(taskId)}`, { method: "DELETE" }),
  browseLocal: (path?: string) =>
    aiRequest<import("../types/ai").AiBrowseResponse>(`/browse/local${path ? `?path=${encodeURIComponent(path)}` : ""}`),
  browseHpc: (path?: string) =>
    aiRequest<import("../types/ai").AiBrowseResponse>(`/browse/hpc${path ? `?path=${encodeURIComponent(path)}` : ""}`),

  mkdirLocal: (path: string, name: string) =>
    aiRequest<import("../types/ai").AiMkdirResponse>("/browse/local/mkdir", { method: "POST", body: { path, name } }),
  mkdirHpc: (path: string, name: string) =>
    aiRequest<import("../types/ai").AiMkdirResponse>("/browse/hpc/mkdir", { method: "POST", body: { path, name } }),

  pickLocal: (initialDir?: string) =>
    aiRequest<import("../types/ai").AiPickResponse>("/browse/local/pick", {
      method: "POST",
      body: initialDir && initialDir.trim() ? { initial_dir: initialDir } : {},
    }),
  getMessages: (projectId: string, taskId: string) =>
    aiRequest<{ messages: import("../types/ai").AiMessage[] }>(`/projects/${encodeURIComponent(projectId)}/tasks/${encodeURIComponent(taskId)}/messages`),
  sendMessage: (projectId: string, taskId: string, content: string) =>
    aiRequest<{ answer: string }>(`/projects/${encodeURIComponent(projectId)}/tasks/${encodeURIComponent(taskId)}/messages`, { method: "POST", body: { content } }),
  async *sendMessageStream(projectId: string, taskId: string, content: string)
    : AsyncGenerator<import("../types/ai").AiStreamEvent> {
    const resp = await fetch(`${AI_BASE}/projects/${encodeURIComponent(projectId)}/tasks/${encodeURIComponent(taskId)}/messages/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify({ content }),
    });
    if (!resp.ok) {
      let msg = `HTTP ${resp.status}`;
      try {
        const j = await resp.json().catch(() => null);
        msg = j?.error?.message || msg;
      } catch { /* 忽略 */ }
      throw new Error(msg);
    }
    if (!resp.body) throw new Error("响应无流式内容");
    const reader = resp.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const blocks = buffer.split(/\r?\n\r?\n/);
      buffer = blocks.pop() ?? "";
      for (const block of blocks) {
        const line = block.split("\n").find((l) => l.startsWith("data:"));
        if (!line) continue;
        const raw = line.slice(5).trim();
        if (!raw) continue;
        try {
          yield JSON.parse(raw) as import("../types/ai").AiStreamEvent;
        } catch { /* 忽略非 JSON */ }
      }
    }
  },
  stopMessage: (projectId: string, taskId: string) =>
    aiRequest<{ mode: string; stopped: boolean }>(`/projects/${encodeURIComponent(projectId)}/tasks/${encodeURIComponent(taskId)}/messages/stop`, { method: "POST" }),
  resolveConsent: (projectId: string, taskId: string, cardId: string, approved: boolean, note?: string) =>
    aiRequest<{ mode: string; ok: boolean; kind: string; approved: boolean; result?: string }>(
      `/projects/${encodeURIComponent(projectId)}/tasks/${encodeURIComponent(taskId)}/messages/consent`,
      { method: "POST", body: { card_id: cardId, approved, note: note ?? "" } }
    ),
  getContext: () =>
    aiRequest<import("../types/ai").AiContextSummary>("/context"),
  getTaskContext: (projectId: string, taskId: string) =>
    aiRequest<import("../types/ai").AiContextSummary>(`/projects/${encodeURIComponent(projectId)}/tasks/${encodeURIComponent(taskId)}/context`),
  getTaskDetail: (projectId: string, taskId: string) =>
    aiRequest<{ mode: string; task_id: string; flow: import("../types/ai").AiFlowDetail }>(
      `/projects/${encodeURIComponent(projectId)}/tasks/${encodeURIComponent(taskId)}/detail`),
  getWaitQueue: () =>
    aiRequest<{ waiting: import("../types/ai").AiWaitQueueEntry[]; count: number }>("/jobs/waiting"),
};
