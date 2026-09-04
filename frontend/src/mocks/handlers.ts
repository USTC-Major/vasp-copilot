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


// ============================================================
// AI 模式（/ai/v1）— 项目/任务/消息/上下文/等待队列（演示数据后端）
// 设置类接口暂不在此拦截（走真实后端；如需离线演示可加 handler）。
// 注意：handler 顺序无关（MSW 按路径匹配），放在最后即可。
// ============================================================
import { aiDemo } from './aiStore';

// M41：模拟进行中的流式生成停止标记（键=projectId:taskId）；与真实后端 _ACTIVE_STOPS 语义一致。
const aiStreamStops = new Set<string>();
const aiStreamKey = (projectId: string, taskId: string) => `${projectId}:${taskId}`;

const AI_BASE = '/ai/v1';

export const aiHandlers = [
  http.get(`${AI_BASE}/projects`, async () => {
    await delay(250);
    return HttpResponse.json({ projects: aiDemo.listProjects() });
  }),

  http.post(`${AI_BASE}/projects`, async ({ request }) => {
    const body = (await request.json()) as { name?: string; description?: string };
    await delay(300);
    return HttpResponse.json({ project: aiDemo.createProject(body?.name || '', body?.description || '') });
  }),

  http.delete(`${AI_BASE}/projects/:projectId`, async ({ params }) => {
    await delay(250);
    return HttpResponse.json({ deleted: aiDemo.deleteProject(String(params.projectId)) });
  }),

  http.get(`${AI_BASE}/projects/:projectId/tasks`, async ({ params }) => {
    await delay(250);
    return HttpResponse.json({ tasks: aiDemo.listTasks(String(params.projectId)) });
  }),

  http.post(`${AI_BASE}/projects/:projectId/tasks`, async ({ params, request }) => {
    const body = (await request.json()) as { title?: string; goal?: string; local_workspace?: string; hpc_workspace?: string };
    await delay(300);
    const task = aiDemo.createTask(String(params.projectId), body?.title || '', body?.goal || '', { local_workspace: body?.local_workspace, hpc_workspace: body?.hpc_workspace });
    return HttpResponse.json({ task });
  }),

  http.patch(`${AI_BASE}/projects/:projectId/tasks/:taskId`, async ({ params, request }) => {
    const body = (await request.json()) as { title?: string; goal?: string; local_workspace?: string; hpc_workspace?: string };
    await delay(200);
    const task = aiDemo.updateTask(String(params.projectId), String(params.taskId), body);
    if (!task) {
      return HttpResponse.json({ error: { code: "AI_MODE_PROJECT_NOT_FOUND", message: "计算任务不存在或被删除", retryable: false } }, { status: 404 });
    }
    return HttpResponse.json({ task });
  }),

  http.delete(`${AI_BASE}/projects/:projectId/tasks/:taskId`, async ({ params }) => {
    await delay(200);
    const deleted = aiDemo.deleteTask(String(params.projectId), String(params.taskId));
    if (!deleted) {
      return HttpResponse.json({ error: { code: "AI_MODE_PROJECT_NOT_FOUND", message: "计算任务不存在或被删除", retryable: false } }, { status: 404 });
    }
    return HttpResponse.json({ deleted: true, task_id: String(params.taskId) });
  }),

  http.get(`${AI_BASE}/projects/:projectId/tasks/:taskId/messages`, async ({ params }) => {
    await delay(200);
    return HttpResponse.json({ messages: aiDemo.getMessages(String(params.projectId), String(params.taskId)) });
  }),

  http.post(`${AI_BASE}/projects/:projectId/tasks/:taskId/messages`, async ({ params, request }) => {
    const body = (await request.json()) as { content?: string };
    await delay(700);
    const answer = aiDemo.sendMessage(String(params.projectId), String(params.taskId), body?.content || '');
    return HttpResponse.json({ answer });
  }),

  http.post(`${AI_BASE}/projects/:projectId/tasks/:taskId/messages/stream`, async ({ params, request }) => {
    const body = (await request.json()) as { content?: string };
    const projectId = String(params.projectId);
    const taskId = String(params.taskId);
    const key = aiStreamKey(projectId, taskId);
    aiStreamStops.add(key);
    const answer = aiDemo.sendMessage(projectId, taskId, body?.content || '');
    const wantsCard = /(修改\s*INCAR|INCAR\s*草稿|弹卡)/i.test(body?.content || '');
    await delay(120);
    const emit = (ev: unknown) => `data: ${JSON.stringify(ev)}\n\n`;
    const sse = async function* () {
      if (wantsCard) {
        yield emit({ type: "card", card: {
          card_id: "card_m47_demo",
          tool: "propose_incar",
          args: { job_key: "relax", entries: [{ tag: "ENCUT", value: 520 }] },
          risk: "medium",
          reason: "INCAR 变更已生成确定性预览，写入前需你确认本次精确内容。",
          options: ["同意本次", "拒绝"],
          batch_key: "incar|relax|demo",
          kind: "workspace",
          summary: "relax/INCAR 的结构化参数草稿（ENCUT = 520），确认后原子写入。",
        } });
        yield emit({ type: "done", answer: "已生成授权请求，等待你确认。" });
        aiStreamStops.delete(key);
        return;
      }

      yield emit({ type: "thinking", text: "正在读取任务与工作区…" });
      const third = Math.max(1, Math.ceil(answer.length / 3));
      let sent = "";
      for (let i = 0; i < 3; i++) {
        await new Promise((resolve) => setTimeout(resolve, 120));
        if (aiStreamStops.has(key)) {
          aiStreamStops.delete(key);
          yield emit({ type: "stopped", answer: sent });
          return;
        }
        const chunk = answer.slice(i * third, (i + 1) * third);
        sent += chunk;
        yield emit({ type: "answer", text: chunk });
      }
      if (aiStreamStops.has(key)) {
        aiStreamStops.delete(key);
        yield emit({ type: "stopped", answer: sent });
        return;
      }
      aiStreamStops.delete(key);
      yield emit({ type: "done", answer });
    };
    const encoder = new TextEncoder();
    const stream = new ReadableStream({
      async start(controller) {
        for await (const chunk of sse()) {
          controller.enqueue(encoder.encode(chunk));
        }
        controller.close();
      },
    });
    return new HttpResponse(stream, {
      headers: { "Content-Type": "text/event-stream; charset=utf-8", "Cache-Control": "no-cache" },
    });
  }),
  http.post(`${AI_BASE}/projects/:projectId/tasks/:taskId/messages/stop`, async ({ params }) => {
    await delay(120);
    const projectId = String(params.projectId);
    const taskId = String(params.taskId);
    if (!aiDemo.getTask(projectId, taskId)) {
      return HttpResponse.json(
        { error: { code: "AI_MODE_PROJECT_NOT_FOUND", message: "计算任务不存在或被删除", retryable: false } },
        { status: 404 }
      );
    }
    return HttpResponse.json({ mode: "ai", stopped: aiStreamStops.has(aiStreamKey(projectId, taskId)) });
  }),
  http.post(`${AI_BASE}/projects/:projectId/tasks/:taskId/messages/consent`, async ({ request }) => {
    const body = (await request.json()) as { card_id?: string; approved?: boolean; note?: string };
    if (!body || !body.card_id) {
      return HttpResponse.json({ mode: "ai", ok: false, reason: "card_missing", message: "该授权卡片不存在或已处理，请重新发起。" }, { status: 400 });
    }
    const approved = !!body.approved;
    return HttpResponse.json({
      mode: "ai", ok: true, kind: "workspace", approved,
      result: approved ? "已授权本批操作，后续同类操作将直接执行" : "已拒绝本批操作，后续同类操作不再弹卡",
    });
  }),
  http.get(`${AI_BASE}/projects/:projectId/tasks/:taskId/detail`, async ({ params }) => {
    await delay(200);
    const detail = aiDemo.flowDetail(String(params.projectId), String(params.taskId));
    if (!detail) {
      return HttpResponse.json(
        { error: { code: "AI_MODE_TASK_NOT_FOUND", message: "计算任务不存在或被删除", retryable: false } },
        { status: 404 }
      );
    }
    return HttpResponse.json(detail);
  }),
  http.get(`${AI_BASE}/projects/:projectId/tasks/:taskId/context`, async ({ params }) => {
    await delay(150);
    return HttpResponse.json(aiDemo.taskContext(String(params.projectId), String(params.taskId)));
  }),

  http.get(`${AI_BASE}/context`, async () => {
    await delay(200);
    return HttpResponse.json(aiDemo.context);
  }),

  http.get(`${AI_BASE}/jobs/waiting`, async () => {
    await delay(200);
    return HttpResponse.json(aiDemo.getWaitQueue());
  }),

  http.get(`${AI_BASE}/browse/local`, async ({ request }) => {
    const url = new URL(request.url);
    const p = url.searchParams.get("path") || "";
    return HttpResponse.json({
      mode: "ai",
      kind: "local",
      path: p,
      parent: p ? p.split(/[\\/]/).slice(0, -1).join("\\") : null,
      exists: true,
      is_dir: true,
      roots: p ? undefined : [
        { name: "C:\\", is_dir: true },
        { name: "D:\\", is_dir: true },
      ],
      entries: p ? [
        { name: "calc", is_dir: true },
        { name: "data", is_dir: true },
        { name: "POSCAR", is_dir: false },
      ] : [],
    });
  }),

  http.get(`${AI_BASE}/browse/hpc`, async ({ request }) => {
    const url = new URL(request.url);
    const p = url.searchParams.get("path") || "";
    return HttpResponse.json({
      mode: "ai",
      kind: "hpc",
      path: p,
      parent: p ? p.split("/").slice(0, -1).join("/") : null,
      exists: true,
      is_dir: true,
      roots: p ? undefined : [{ name: "/", is_dir: true }],
      entries: p ? [
        { name: "hpc_home", is_dir: true },
        { name: "INCAR", is_dir: false },
      ] : [],
    });
  }),

  http.post(`${AI_BASE}/browse/local/mkdir`, async ({ request }) => {
    const body = (await request.json()) as { path?: string; name?: string };
    const name = (body.name || "").trim();
    if (!name) {
      return HttpResponse.json({ mode: "ai", ok: false, notice: "文件夹名不能为空" });
    }
    const p = ((body.path || "D:/").replace(/[/]+$/, "") + "/" + name);
    return HttpResponse.json({ mode: "ai", kind: "local", ok: true, path: p, notice: "" });
  }),

  http.post(`${AI_BASE}/browse/local/pick`, () =>
    HttpResponse.json({ mode: "ai", kind: "local", ok: true, path: "D:\\mock\\workspace\\picked" }),
  ),

  http.post(`${AI_BASE}/browse/hpc/mkdir`, async ({ request }) => {
    const body = (await request.json()) as { path?: string; name?: string };
    const name = (body.name || "").trim();
    if (!name) {
      return HttpResponse.json({ mode: "ai", ok: false, notice: "文件夹名不能为空" });
    }
    const p = ((body.path || "/").replace(/[/]+$/, "") + "/" + name);
    return HttpResponse.json({ mode: "ai", kind: "hpc", ok: true, path: p, notice: "" });
  }),
];

// 并入主 handlers：MSW server（测试）与 browser（?mock=1 演示）均依赖它。
aiDemo.enqueue('本机等待队列未满时自动提交；此处演示队列界面。', 'Fe2O3 能带计算');
if (aiDemo.waiting.length === 1) {
  aiDemo.enqueue('超算作业数已达上限，排队等待空位。', 'Fe2O3 DOS 计算');
  aiDemo.enqueue('排队顺序按确认先后回填。', 'Fe2O3 band 结构精修');
}
handlers.push(...aiHandlers);


// ---- AI 设置类（MSW 离线演示/测试用；正常开发直连真实后端）----
let mockAiSettings = {
  enabled: true,
  max_jobs: 20,
  llm: { base_url: "https://api.openai.com/v1", model: "gpt-4o", provider: "auto", api_key: "" },
  materials_project: { api_key: "" },
  ssh: { name: "", host: "", port: 22, username: "" },
};
let mockSshPassword = "";
let mockProjectSettings: Record<string, { project_id: string; accuracy: string[] }> = {};

const mockPublicAiSettings = () => ({
  ...mockAiSettings,
  llm: { ...mockAiSettings.llm, api_key: "" },
  materials_project: { api_key: "" },
});

const AI_SETTINGS_BASE = "/ai/v1";

const aiSettingsHandlers = [
  http.get(`${AI_SETTINGS_BASE}/settings`, async () => {
    await delay(250);
    return HttpResponse.json({ mode: "ai", enabled: true, settings: mockPublicAiSettings(), writable: ["max_jobs", "llm_provider", "llm_base_url", "llm_model", "ssh_name", "ssh_host", "ssh_port", "ssh_username"] });
  }),

  http.put(`${AI_SETTINGS_BASE}/settings`, async ({ request }) => {
    const patch = (await request.json()) as Record<string, unknown>;
    await delay(250);
    const pick = <T>(k: string, fb: T): T => (k in patch ? (patch[k] as T) : fb);
    mockAiSettings = {
      enabled: mockAiSettings.enabled,
      max_jobs: pick("max_jobs", mockAiSettings.max_jobs),
      llm: {
        ...mockAiSettings.llm,
        base_url: pick("llm_base_url", mockAiSettings.llm.base_url),
        model: pick("llm_model", mockAiSettings.llm.model),
        provider: pick("llm_provider", mockAiSettings.llm.provider),
        api_key: mockAiSettings.llm.api_key,
      },
      materials_project: {
        api_key: mockAiSettings.materials_project.api_key,
      },
      ssh: {
        ...mockAiSettings.ssh,
        name: pick("ssh_name", mockAiSettings.ssh.name),
        host: pick("ssh_host", mockAiSettings.ssh.host),
        username: pick("ssh_username", mockAiSettings.ssh.username),
        port: pick("ssh_port", mockAiSettings.ssh.port),
      },
    };
    return HttpResponse.json({ mode: "ai", ok: true, settings: mockPublicAiSettings() });
  }),

  http.post(`${AI_SETTINGS_BASE}/settings/test/:provider`, async ({ params }) => {
    await delay(400);
    const provider = String(params.provider);
    if (!["llm", "mp", "ssh"].includes(provider)) {
      return HttpResponse.json({ mode: "ai", ok: false, message: "未知 provider（Mock）" }, { status: 400 });
    }
    return HttpResponse.json({ mode: "ai", provider, ok: true, message: `${provider} 连通成功（Mock）` });
  }),

  http.get(`${AI_SETTINGS_BASE}/settings/secret-status`, async () => {
    await delay(200);
    return HttpResponse.json({
      mode: "ai",
      enabled: true,
      secrets: {
        llm: { configured: mockAiSettings.llm.api_key !== "", source: mockAiSettings.llm.api_key ? "local_config" : "none", manageable: true },
        mp: { configured: mockAiSettings.materials_project.api_key !== "", source: mockAiSettings.materials_project.api_key ? "local_config" : "none", manageable: true },
        ssh: { configured: mockSshPassword !== "", source: mockSshPassword ? "credential_store" : "none", manageable: true },
      },
    });
  }),

  http.put(`${AI_SETTINGS_BASE}/settings/secrets/:kind`, async ({ params, request }) => {
    const kind = String(params.kind || "").trim().toLowerCase();
    const body = (await request.json().catch(() => ({}))) as { action?: string; value?: string };
    await delay(200);
    if (!["llm", "mp", "ssh"].includes(kind)) {
      return HttpResponse.json({ mode: "ai", error: { code: "AI_MODE_UNKNOWN_SECRET", message: "未知密钥类型", retryable: false } }, { status: 400 });
    }
    const action = String(body.action || "");
    if (!["replace", "clear"].includes(action) || (action === "replace" && !body.value)) {
      return HttpResponse.json({ mode: "ai", error: { code: "AI_MODE_BAD_SECRET_ACTION", message: "只能整体替换或清除密钥", retryable: false } }, { status: 400 });
    }
    const value = action === "clear" ? "" : String(body.value);
    if (kind === "llm") mockAiSettings.llm.api_key = value;
    if (kind === "mp") mockAiSettings.materials_project.api_key = value;
    if (kind === "ssh") mockSshPassword = value;
    const source = value ? (kind === "ssh" ? "credential_store" : "local_config") : "none";
    return HttpResponse.json({ mode: "ai", ok: true, kind, configured: value !== "", source, manageable: true, secret: { configured: value !== "", source, manageable: true } });
  }),

  http.post(`${AI_SETTINGS_BASE}/settings/reveal`, async () => {
    await delay(200);
    return HttpResponse.json({ mode: "ai", error: { code: "AI_SECRET_REVEAL_DISABLED", message: "已保存密钥不可查看、复制或取回；只能整体替换或清除", retryable: false } }, { status: 403 });
  }),

  http.get(`${AI_SETTINGS_BASE}/projects/:projectId/settings`, async ({ params }) => {
    const pid = String(params.projectId);
    await delay(200);
    return HttpResponse.json({ mode: "ai", project_id: pid, settings: mockProjectSettings[pid] ?? { project_id: pid, accuracy: [] } });
  }),

  http.put(`${AI_SETTINGS_BASE}/projects/:projectId/settings`, async ({ params, request }) => {
    const pid = String(params.projectId);
    const body = (await request.json()) as { accuracy?: string[] };
    const accuracy = Array.isArray(body?.accuracy)
      ? body.accuracy.filter((x) => typeof x === 'string' && x.trim() !== '')
      : [];
    for (const entry of accuracy) {
      if (/(password|passwd|secret|credential|api_key|private key|access token|bearer)/i.test(entry)) {
        return HttpResponse.json({ mode: "ai", error: { code: "AI_MODE_BAD_PROJECT_SETTINGS", message: "条目内容疑似含敏感信息（密钥/口令等不得写入项目设置）", retryable: false } }, { status: 400 });
      }
    }
    await delay(250);
    mockProjectSettings[pid] = { project_id: pid, accuracy };
    return HttpResponse.json({ mode: "ai", ok: true, settings: mockProjectSettings[pid] });
  }),

  http.delete(`${AI_SETTINGS_BASE}/projects/:projectId/settings`, async ({ params }) => {
    const pid = String(params.projectId);
    await delay(200);
    const deleted = pid in mockProjectSettings;
    delete mockProjectSettings[pid];
    return HttpResponse.json({ mode: "ai", ok: true, deleted, project_id: pid });
  }),
];

handlers.push(...aiSettingsHandlers);
