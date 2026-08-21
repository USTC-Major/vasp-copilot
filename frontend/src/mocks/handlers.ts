// ============================================================
// MSW Handlers — 拦截所有 P0 端点
// ============================================================

import { http, HttpResponse, delay } from 'msw';
import {
  featureFlagsFixture,
  uploadSuccessFixture,
  structureAnalysisFixture,
  workflowPlanFixture,
  fileTreeFixture,
  filePreviewFixture,
  diagnosisUploadFixture,
  diagnosisResultFixture,
  errorFixture,
  clustersFixture,
  deploymentPlanFixture,
  remoteJobFixture,
} from './fixtures';

const API_BASE = '/api/v1';


// ---- LLM 配置（前端模型设置 mock，有状态）----
const initialLlmConfig = {
  enabled: true,
  base_url: 'http://127.0.0.1:8001/v1',
  model: 'demo-model',
  api_key_set: true,
  api_key_masked: 'sk-d****emo',
  source: 'runtime',
  usable: true,
  timeout_seconds: 30,
  max_retries: 1,
  max_tokens: 1024,
  temperature: 0.2,
};
let mockLlmConfig = { ...initialLlmConfig };
let mockChatHistory: { role: string; content: string }[] = [];


export const handlers = [
  // ============================================================
  // Feature Flags / Bootstrap
  // ============================================================
  http.get(`${API_BASE}/bootstrap`, async () => {
    await delay(200);
    return HttpResponse.json(featureFlagsFixture);
  }),

  // ============================================================
  // Files
  // ============================================================
  http.post(`${API_BASE}/files/upload`, async () => {
    await delay(800);
    return HttpResponse.json({ request_id: 'req_01', file: uploadSuccessFixture });
  }),

  http.get(`${API_BASE}/files/:fileId/preview`, async ({ params }) => {
    await delay(300);
    const { fileId } = params;
    // POTCAR 拒绝
    if (String(fileId).startsWith('potcar')) {
      return HttpResponse.json(
        { request_id: 'req_preview_err', error: { code: 'FILE_PREVIEW_POLICY_DENIED', message: '策略限制不可预览', retryable: false, field_errors: [] } },
        { status: 403 }
      );
    }
    // 二进制拒绝
    if (String(fileId).startsWith('binary')) {
      return HttpResponse.json(
        { request_id: 'req_preview_err', error: { code: 'FILE_PREVIEW_UNSUPPORTED_BINARY', message: '不支持预览', retryable: false, field_errors: [] } },
        { status: 415 }
      );
    }
    return HttpResponse.json(filePreviewFixture);
  }),

  // ============================================================
  // Structure
  // ============================================================
  http.post(`${API_BASE}/structure/analyze`, async () => {
    await delay(500);
    return HttpResponse.json(structureAnalysisFixture);
  }),

  // ============================================================
  // Workflows
  // ============================================================
  http.post(`${API_BASE}/workflows/plan`, async () => {
    await delay(600);
    return HttpResponse.json({ request_id: 'req_plan', ...workflowPlanFixture });
  }),

  http.post(`${API_BASE}/workflows/generate`, async () => {
    await delay(1000);
    return HttpResponse.json({
      request_id: 'req_gen',
      workflow_id: 'wf_01',
      workflow_status: 'generated',
      file_tree: fileTreeFixture,
    });
  }),

  http.get(`${API_BASE}/workflows/:workflowId`, async ({ params }) => {
    await delay(300);
    return HttpResponse.json({
      request_id: 'req_get_wf',
      workflow_id: params.workflowId,
      workflow_status: 'generated',
      plan: workflowPlanFixture,
      file_tree: fileTreeFixture,
    });
  }),

  http.get(`${API_BASE}/workflows/:workflowId/download`, async () => {
    await delay(500);
    return new HttpResponse(
      new Uint8Array([0x50, 0x4B, 0x03, 0x04]).buffer,
      {
        status: 200,
        headers: {
          'Content-Type': 'application/zip',
          'Content-Disposition': 'attachment; filename="vasp_workflow_wf_01.zip"',
        },
      }
    );
  }),

  // ============================================================
  // Diagnosis
  // ============================================================
  http.post(`${API_BASE}/diagnosis/upload`, async () => {
    await delay(800);
    return HttpResponse.json(diagnosisUploadFixture);
  }),

  http.post(`${API_BASE}/diagnosis/run`, async () => {
    await delay(1500);
    return HttpResponse.json({
      request_id: 'req_run',
      ...diagnosisResultFixture,
    });
  }),


  http.post(`${API_BASE}/diagnosis/:diagnosisId/explain`, async ({ request }) => {
    await delay(900);
    const body = (await request.json()) as { question?: string };
    return HttpResponse.json({
      request_id: 'req_explain',
      diagnosis_id: 'diag_demo_01',
      diagnosis_status: 'succeeded',
      answer: `【模拟回答】${body?.question || '请解释这份诊断报告。'}\n\nSCF 计算在达到最大电子步数后仍未收敛，这是常见的收敛失败。建议：检查 KPOINTS 与 SIGMA 取值，放宽 NELM 或调整混合参数后重跑。`,
    });
  }),
  http.get(`${API_BASE}/diagnosis/:diagnosisId`, async () => {
    await delay(300);
    return HttpResponse.json({
      request_id: 'req_get_diag',
      ...diagnosisResultFixture,
    });
  }),

  http.get(`${API_BASE}/diagnosis/:diagnosisId/report`, async () => {
    await delay(300);
    return new HttpResponse(
      '# VASP-Doctor+ 诊断报告\n\n## 摘要\n...',
      {
        status: 200,
        headers: { 'Content-Type': 'text/markdown; charset=utf-8' },
      }
    );
  }),

  http.get(`${API_BASE}/diagnosis/:diagnosisId/download-fix`, async () => {
    await delay(500);
    return new HttpResponse(
      new Uint8Array([0x50, 0x4B, 0x03, 0x04]).buffer,
      {
        status: 200,
        headers: {
          'Content-Type': 'application/zip',
          'Content-Disposition': 'attachment; filename="fix_diag_01.zip"',
        },
      }
    );
  }),

  // ============================================================
  // Recipes (P1)
  // ============================================================
  http.get(`${API_BASE}/recipes`, async () => {
    await delay(300);
    return HttpResponse.json({
      request_id: 'req_recipes',
      recipes: [
        { recipe_id: 'base.vasp', version: '1.0.0', kind: 'base', recipe_status: 'published', display_name: 'VASP 基础参数', sha256: '...' },
        { recipe_id: 'task.relax.standard', version: '1.0.0', kind: 'task', recipe_status: 'published', display_name: '常规结构优化', sha256: '...' },
        { recipe_id: 'task.static.standard', version: '1.0.0', kind: 'task', recipe_status: 'published', display_name: '静态计算', sha256: '...' },
        { recipe_id: 'task.dos.standard', version: '1.0.0', kind: 'task', recipe_status: 'published', display_name: '态密度计算', sha256: '...' },
      ],
    });
  }),

  // ============================================================
  // HPC (P1 — Fake)
  // ============================================================
  http.get(`${API_BASE}/hpc/clusters`, async () => {
    await delay(300);
    return HttpResponse.json({ request_id: 'req_clusters', clusters: clustersFixture });
  }),

  http.post(`${API_BASE}/hpc/deployments/plan`, async () => {
    await delay(500);
    return HttpResponse.json({ request_id: 'req_deploy_plan', ...deploymentPlanFixture });
  }),

  http.post(`${API_BASE}/hpc/deployments/:deploymentId/preflight`, async () => {
    await delay(400);
    return HttpResponse.json({
      request_id: 'req_preflight',
      deployment_id: 'deploy_01',
      preflight: { passed: true, checks: [], warnings: [] },
    });
  }),

  http.post(`${API_BASE}/hpc/deployments/:deploymentId/authorize`, async () => {
    await delay(300);
    return HttpResponse.json({
      request_id: 'req_auth',
      grant_id: 'grant_deploy_01',
      capability: 'HPC_DEPLOY',
      single_use: true,
      expires_at: '2026-08-02T03:10:00Z',
    });
  }),

  http.post(`${API_BASE}/hpc/deployments/:deploymentId/execute`, async () => {
    await delay(1000);
    return HttpResponse.json({
      request_id: 'req_exec',
      deployment_id: 'deploy_01',
      deployment_status: 'deployed',
    });
  }),

  http.get(`${API_BASE}/hpc/deployments/:deploymentId`, async () => {
    await delay(300);
    return HttpResponse.json({
      request_id: 'req_get_deploy',
      ...deploymentPlanFixture,
      deployment_status: 'deployed',
    });
  }),

  http.post(`${API_BASE}/hpc/jobs/plan`, async () => {
    await delay(400);
    return HttpResponse.json({
      request_id: 'req_job_plan',
      submission_draft_id: 'subdraft_01',
      hpc_job_status: 'ready_for_confirmation',
      resources: { partition: 'cpu', nodes: 1, tasks: 32, memory_gb: 64, walltime: '12:00:00' },
    });
  }),

  http.post(`${API_BASE}/hpc/jobs/:jobId/authorize`, async () => {
    await delay(300);
    return HttpResponse.json({
      request_id: 'req_job_auth',
      grant_id: 'grant_submit_01',
      capability: 'HPC_SUBMIT',
      single_use: true,
      expires_at: '2026-08-02T03:10:00Z',
    });
  }),

  http.post(`${API_BASE}/hpc/jobs/:jobId/submit`, async () => {
    await delay(800);
    return HttpResponse.json({ request_id: 'req_submit', ...remoteJobFixture });
  }),

  http.get(`${API_BASE}/hpc/jobs/:jobId`, async () => {
    await delay(300);
    return HttpResponse.json({ request_id: 'req_get_job', ...remoteJobFixture });
  }),

  // --- HPC collection (P1 Fake) ---
  http.post(`${API_BASE}/hpc/jobs/:jobId/authorize-collection`, async () => {
    await delay(300);
    return HttpResponse.json({
      request_id: 'req_collect_auth',
      grant_id: 'grant_collect_01',
      capability: 'HPC_COLLECT',
      single_use: true,
      expires_at: '2026-08-02T03:10:00Z',
    });
  }),

  http.post(`${API_BASE}/hpc/jobs/:jobId/collect`, async () => {
    await delay(600);
    return HttpResponse.json({
      request_id: 'req_collect',
      job_id: 'rjob_01',
      results: [
        { path: 'OUTCAR', bytes: 187, sha256: 'mock-sha-0' },
        { path: 'OSZICAR', bytes: 92, sha256: 'mock-sha-1' },
      ],
      rejected: ['POTCAR', 'WAVECAR', 'CHGCAR'],
    });
  }),

  // ---- Error simulation — 通过 header 触发 ----
  http.get(`${API_BASE}/llm/config`, async () => {
    await delay(200);
    return HttpResponse.json({ request_id: 'req_llm_cfg', ...mockLlmConfig });
  }),

  http.post(`${API_BASE}/llm/config`, async ({ request }) => {
    await delay(300);
    const body = (await request.json()) as Record<string, unknown>;
    mockLlmConfig = {
      ...mockLlmConfig,
      ...body,
      api_key_set: true,
      usable: true,
      source: 'runtime',
    };
    return HttpResponse.json({ request_id: 'req_llm_cfg', ...mockLlmConfig });
  }),

  http.delete(`${API_BASE}/llm/config`, async () => {
    await delay(200);
    mockLlmConfig = { ...initialLlmConfig };
    return HttpResponse.json({ request_id: 'req_llm_cfg', ...mockLlmConfig });
  }),

  http.post(`${API_BASE}/llm/config/test`, async () => {
    await delay(600);
    return HttpResponse.json({ request_id: 'req_llm_test', ok: true, message: '连接成功（Mock）', reply: 'pong' });
  }),
  http.post(`${API_BASE}/chat`, async () => {
    await delay(700);
    return HttpResponse.json({
      request_id: 'req_chat',
      answer: '【模拟回答】这是 MSW 演示回复（?mock=1）。接入真实大模型后，此面板会返回模型答案。',
      usable: true,
    });
  }),

  // ---- AI 对话历史（有状态 mock）----
  http.get(`${API_BASE}/chat/history`, async () => {
    await delay(200);
    return HttpResponse.json({ request_id: 'req_chat_hist', messages: mockChatHistory, persisted: true });
  }),

  http.post(`${API_BASE}/chat/history`, async ({ request }) => {
    await delay(200);
    const body = (await request.json()) as { messages?: { role?: string; content?: string }[] };
    if (Array.isArray(body?.messages)) {
      mockChatHistory = body.messages.filter(
        (m): m is { role: string; content: string } =>
          !!m &&
          (m.role === 'user' || m.role === 'assistant') &&
          typeof m.content === 'string' &&
          m.content.trim() !== ''
      ).slice(-200);
    }
    return HttpResponse.json({ request_id: 'req_chat_hist', messages: mockChatHistory, persisted: true });
  }),

  http.delete(`${API_BASE}/chat/history`, async () => {
    await delay(200);
    mockChatHistory = [];
    return HttpResponse.json({ request_id: 'req_chat_hist', messages: [], persisted: true });
  }),
  http.all(`${API_BASE}/*`, async ({ request }) => {
    const simulateError = request.headers.get('X-Simulate-Error');
    if (simulateError) {
      await delay(200);
      const errors: Record<string, { status: number; body: unknown }> = {
        '400': { status: 400, body: { ...errorFixture, error: { ...errorFixture.error, code: 'BAD_REQUEST', message: '参数错误' } } },
        '413': { status: 413, body: { ...errorFixture, error: { ...errorFixture.error, code: 'FILE_TOO_LARGE', message: '文件过大' } } },
        '422': { status: 422, body: { ...errorFixture, error: { ...errorFixture.error, code: 'VALIDATION_ERROR', message: '业务校验失败' } } },
        '500': { status: 500, body: { ...errorFixture, error: { ...errorFixture.error, code: 'INTERNAL_ERROR', message: '服务器内部错误' } } },
      };
      const err = errors[simulateError] || errors['500'];
      return HttpResponse.json(err.body as never, { status: err.status });
    }
    return;
  }),
];
