// ============================================================
// 自定义 Hooks — TanStack Query 封装
// ============================================================

import { useQuery, useMutation } from '@tanstack/react-query';
import {
  filesApi, structureApi, workflowsApi,
  diagnosisApi, recipesApi, hpcApi, llmApi, chatApi, materialsApi, aiApi,
} from '../api/client';
import type { LlmConfigUpdate, ChatMessageItem } from '../api/client';
import { getFeatureFlags } from '../config/featureFlags';

// ---- Feature Flags ----
export function useFeatureFlags() {
  return useQuery({
    queryKey: ['featureFlags'],
    queryFn: async () => {
      try {
        const resp = await fetch('/api/v1/bootstrap');
        const flags = await resp.json();
        return flags;
      } catch {
        return getFeatureFlags();
      }
    },
    staleTime: 60 * 1000,
  });
}

// ---- 文件上传 ----
export function useFileUpload() {
  return useMutation({
    mutationFn: ({ file, purpose }: { file: File; purpose: 'structure' | 'pseudopotential' }) =>
      filesApi.upload(file, purpose),
  });
}

export function useFilePreview(fileId: string | null) {
  return useQuery({
    queryKey: ['filePreview', fileId],
    queryFn: () => filesApi.preview(fileId!),
    enabled: !!fileId,
  });
}

// ---- 结构分析 ----
export function useStructureAnalysis() {
  return useMutation({
    mutationFn: ({ fileId, options }: { fileId: string; options?: { symmetry_tolerance?: number; standardize?: boolean } }) =>
      structureApi.analyze(fileId, options),
  });
}

// ---- 工作流 ----
export function useWorkflowPlan() {
  return useMutation({
    mutationFn: (params: import('../types/workflow-contract').WorkflowPlanRequestBody) =>
      workflowsApi.plan(params),
  });
}

export function useWorkflowGenerate() {
  return useMutation({
    mutationFn: ({ workflowId, patches }: { workflowId: string; patches?: import('../types/generated-api').ParameterPatch[] }) =>
      workflowsApi.generate(workflowId, patches),
  });
}

export function useWorkflow(workflowId: string | null) {
  return useQuery({
    queryKey: ['workflow', workflowId],
    queryFn: () => workflowsApi.get(workflowId!),
    enabled: !!workflowId,
  });
}

export function useWorkflowDownload() {
  return useMutation({
    mutationFn: (workflowId: string) => workflowsApi.download(workflowId),
  });
}

// ---- 诊断 ----
export function useDiagnosisUpload() {
  return useMutation({
    mutationFn: (file: File) => diagnosisApi.upload(file),
  });
}

export function useDiagnosisRun() {
  return useMutation({
    mutationFn: (diagnosisId: string) => diagnosisApi.run(diagnosisId),
  });
}

export function useDiagnosis(diagnosisId: string | null) {
  return useQuery({
    queryKey: ['diagnosis', diagnosisId],
    queryFn: () => diagnosisApi.get(diagnosisId!),
    enabled: !!diagnosisId,
    refetchInterval: (query) => {
      const data = query.state.data;
      if (data?.diagnosis_status && !['succeeded', 'failed'].includes(data.diagnosis_status)) {
        return 3000;
      }
      return false;
    },
  });
}

export function useDiagnosisReport() {
  return useMutation({
    mutationFn: (diagnosisId: string) => diagnosisApi.report(diagnosisId),
  });
}

export function useDiagnosisFixDownload() {
  return useMutation({
    mutationFn: (diagnosisId: string) => diagnosisApi.downloadFix(diagnosisId),
  });
}

export function useDiagnosisExplain() {
  return useMutation({
    mutationFn: ({ diagnosisId, question }: { diagnosisId: string; question: string }) =>
      diagnosisApi.explain(diagnosisId, question),
  });
}

// ---- Recipes ----
export function useRecipes() {
  return useQuery({
    queryKey: ['recipes'],
    queryFn: recipesApi.list,
  });
}

export function useRecipe(recipeId: string | null) {
  return useQuery({
    queryKey: ['recipe', recipeId],
    queryFn: () => recipesApi.get(recipeId!),
    enabled: !!recipeId,
  });
}

// ---- HPC ----
export function useClusters() {
  return useQuery({
    queryKey: ['clusters'],
    queryFn: hpcApi.getClusters,
  });
}

export function useDeploymentPlan() {
  return useMutation({
    mutationFn: (params: { workflow_id: string; cluster_profile_id: string }) =>
      hpcApi.planDeployment(params),
  });
}

export function useDeployment(deploymentId: string | null) {
  return useQuery({
    queryKey: ['deployment', deploymentId],
    queryFn: () => hpcApi.getDeployment(deploymentId!),
    enabled: !!deploymentId,
    refetchInterval: (query) => {
      const data = query.state.data;
      if (data?.deployment_status && !['deployed', 'failed'].includes(data.deployment_status)) {
        return 3000;
      }
      return false;
    },
  });
}

export function useDeploymentAuthorize() {
  return useMutation({
    mutationFn: (deploymentId: string) => hpcApi.authorizeDeployment(deploymentId),
  });
}

export function useDeploymentExecute() {
  return useMutation({
    mutationFn: (deploymentId: string) => hpcApi.executeDeployment(deploymentId),
  });
}

export function useJobPlan() {
  return useMutation({
    mutationFn: (params: { manifest_id: string; step_id: string; resources?: Record<string, unknown> }) =>
      hpcApi.planJob(params),
  });
}

export function useJobAuthorize() {
  return useMutation({
    mutationFn: (jobId: string) => hpcApi.authorizeJob(jobId),
  });
}

export function useJobSubmit() {
  return useMutation({
    mutationFn: (jobId: string) => hpcApi.submitJob(jobId),
  });
}

export function useRemoteJob(jobId: string | null) {
  return useQuery({
    queryKey: ['remoteJob', jobId],
    queryFn: () => hpcApi.getJob(jobId!),
    enabled: !!jobId,
    refetchInterval: (query) => {
      const data = query.state.data;
      const terminal = ['completed', 'failed', 'cancelled', 'timeout', 'out_of_memory'];
      if (data?.hpc_job_status && !terminal.includes(data.hpc_job_status)) {
        return 5000;
      }
      return false;
    },
  });
}

// ---- LLM 配置 ----
export function useLlmConfig(enabled: boolean) {
  return useQuery({
    queryKey: ['llmConfig'],
    queryFn: () => llmApi.getConfig(),
    enabled,
    staleTime: 30 * 1000,
  });
}

export function useLlmConfigSave() {
  return useMutation({
    mutationFn: (body: LlmConfigUpdate) => llmApi.saveConfig(body),
  });
}

export function useLlmConfigReset() {
  return useMutation({
    mutationFn: () => llmApi.resetConfig(),
  });
}

export function useLlmConfigTest() {
  return useMutation({
    mutationFn: (body: Partial<LlmConfigUpdate>) => llmApi.testConfig(body),
  });
}


// ---- AI 对话 ----
export function useChatSend() {
  return useMutation({
    mutationFn: ({ message, history }: { message: string; history: ChatMessageItem[] }) =>
      chatApi.send(message, history),
  });
}


// ---- AI 对话历史 ----
export function useChatHistory(enabled: boolean) {
  return useQuery({
    queryKey: ['chatHistory'],
    queryFn: () => chatApi.getHistory(),
    enabled,
    staleTime: 30 * 1000,
  });
}

export function useChatHistorySave() {
  return useMutation({
    mutationFn: (messages: ChatMessageItem[]) => chatApi.saveHistory(messages),
  });
}

export function useChatHistoryClear() {
  return useMutation({
    mutationFn: () => chatApi.clearHistory(),
  });
}
// ---- Materials Project ----
export function useMaterialsSearch() {
  return useMutation({
    mutationFn: ({ query, limit }: { query: string; limit?: number }) =>
      materialsApi.search(query, limit),
  });
}

export function useMaterialsImport() {
  return useMutation({
    mutationFn: (materialId: string) => materialsApi.importMaterial(materialId),
  });
}


// ---- AI 模式（M12：设置/项目/任务/消息/上下文/队列）----
export function useAiSettings(enabled: boolean) {
  return useQuery({
    queryKey: ['aiSettings'],
    queryFn: () => aiApi.getSettings(),
    enabled,
    staleTime: 30 * 1000,
  });
}

export function useAiSettingsSave() {
  return useMutation({
    mutationFn: (patch: Record<string, unknown>) => aiApi.saveSettings(patch),
  });
}

export function useAiSettingsTest() {
  return useMutation({
    mutationFn: (provider: string) => aiApi.testProvider(provider),
  });
}


export function useAiSecretStatus(enabled: boolean) {
  return useQuery({
    queryKey: ["aiSecretStatus"],
    queryFn: () => aiApi.getSecretStatus(),
    enabled,
    staleTime: 30 * 1000,
  });
}

export function useAiSecretUpdate() {
  return useMutation({
    mutationFn: ({ kind, action, value }: { kind: "llm" | "mp" | "ssh"; action: "replace" | "clear"; value?: string }) =>
      aiApi.updateSecret(kind, action, value),
  });
}
export function useAiProjectSettings(projectId: string, enabled: boolean) {
  return useQuery({
    queryKey: ['aiProjectSettings', projectId],
    queryFn: () => aiApi.getProjectSettings(projectId),
    enabled: !!projectId && enabled,
  });
}

export function useAiProjectSettingsSave() {
  return useMutation({
    mutationFn: ({ projectId, accuracy }: { projectId: string; accuracy: string[] }) =>
      aiApi.saveProjectSettings(projectId, accuracy),
  });
}

export function useAiProjectSettingsDelete() {
  return useMutation({
    mutationFn: (projectId: string) => aiApi.deleteProjectSettings(projectId),
  });
}

export function useAiProjects() {
  return useQuery({
    queryKey: ['aiProjects'],
    queryFn: () => aiApi.listProjects(),
    staleTime: 15 * 1000,
  });
}

export function useAiProjectCreate() {
  return useMutation({
    mutationFn: (body: { name: string; description?: string }) => aiApi.createProject(body),
  });
}

export function useAiProjectDelete() {
  return useMutation({
    mutationFn: (projectId: string) => aiApi.deleteProject(projectId),
  });
}

export function useAiTasks(projectId: string | null) {
  return useQuery({
    queryKey: ['aiTasks', projectId],
    queryFn: () => aiApi.listTasks(projectId!),
    enabled: !!projectId,
  });
}

export function useAiTaskCreate() {
  return useMutation({
    mutationFn: ({ projectId, title, goal, local_workspace, hpc_workspace }: { projectId: string; title: string; goal?: string; local_workspace?: string; hpc_workspace?: string }) =>
      aiApi.createTask(projectId, { title, goal, local_workspace, hpc_workspace }),
  });
}


export function useAiTaskUpdate() {
  return useMutation({
    mutationFn: ({ projectId, taskId, patch }: { projectId: string; taskId: string; patch: import('../types/ai').AiTaskPatch }) =>
      aiApi.updateTask(projectId, taskId, patch),
  });
}

export function useAiTaskDelete() {
  return useMutation({
    mutationFn: ({ projectId, taskId }: { projectId: string; taskId: string }) =>
      aiApi.deleteTask(projectId, taskId),
  });
}
export function useAiMessages(projectId: string | null, taskId: string | null) {
  return useQuery({
    queryKey: ['aiMessages', projectId, taskId],
    queryFn: () => aiApi.getMessages(projectId!, taskId!),
    enabled: !!projectId && !!taskId,
  });
}

export function useAiSendMessage() {
  return useMutation({
    mutationFn: ({ projectId, taskId, content }: { projectId: string; taskId: string; content: string }) =>
      aiApi.sendMessage(projectId, taskId, content),
  });
}

export function useAiContext() {
  return useQuery({
    queryKey: ['aiContext'],
    queryFn: () => aiApi.getContext(),
    staleTime: 15 * 1000,
  });
}

export function useAiTaskContext(projectId: string | null, taskId: string | null) {
  return useQuery({
    queryKey: ['aiTaskContext', projectId, taskId],
    queryFn: () => aiApi.getTaskContext(projectId!, taskId!),
    enabled: !!projectId && !!taskId,
  });
}

export function useAiTaskDetail(projectId: string | null, taskId: string | null) {
  return useQuery({
    queryKey: ['aiTaskDetail', projectId, taskId],
    queryFn: () => aiApi.getTaskDetail(projectId!, taskId!),
    enabled: !!projectId && !!taskId,
    // 监控中状态变化频繁，进度页轮询 15s 自动刷新
    refetchInterval: 15 * 1000,
  });
}

export function useAiWaitQueue() {
  return useQuery({
    queryKey: ['aiWaitQueue'],
    queryFn: () => aiApi.getWaitQueue(),
    staleTime: 15 * 1000,
  });
}
