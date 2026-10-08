// ============================================================
// WorkflowBuilderPage 集成测试（F3/F6/F7/F8/F9/F14 + 快速双击回归）
// 通过 MSW setupServer 捕获真实请求体，验证展示与发送同源。
// ============================================================

import { act, render, screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ConfigProvider } from 'antd';
import { http, HttpResponse, delay } from 'msw';
import WorkflowBuilderPage, { buildSnapshot, canBuildConfirmSnapshot } from './WorkflowBuilderPage';
import type { DftuEntryFormData } from '../components/workflow/ParameterConfirmForm';
import { server } from '../mocks/server';
import { uploadSuccessFixture, structureAnalysisFixture, workflowPlanFixture, fileTreeFixture } from '../mocks/fixtures';
import type { WorkflowPlanRequestBody } from '../types/workflow-contract';
// 静态源码回归：通过 ?raw 导入生产文件源码（vite/client 提供类型声明），
// 断言不得出现无条件的 confirmed_by_user: true。
import pageSource from './WorkflowBuilderPage.tsx?raw';
import formSource from '../components/workflow/ParameterConfirmForm.tsx?raw';
import modalSource from '../components/workflow/WorkflowConfirmSummaryModal.tsx?raw';
import contractSource from '../types/workflow-contract.ts?raw';
import generatedApiSource from '../types/generated-api.ts?raw';
import { potcarApi } from '../api/potcar';
import type { PotcarArtifact, PotcarLibrary, PotcarPreview, WorkflowPotcarState } from '../types/potcar';
import useApiSource from '../hooks/useApi.ts?raw';
import clientSource from '../api/client.ts?raw';

const API = '/api/v1';

let planBodies: WorkflowPlanRequestBody[];

const useFastMocks = (planDelayMs = 0) => {
  server.use(
    http.post(`${API}/files/upload`, () =>
      HttpResponse.json({ request_id: 'req_t_upload', file: uploadSuccessFixture })
    ),
    http.post(`${API}/structure/analyze`, () => HttpResponse.json(structureAnalysisFixture)),
    http.post(`${API}/workflows/plan`, async ({ request }) => {
      planBodies.push((await request.json()) as WorkflowPlanRequestBody);
      if (planDelayMs > 0) await delay(planDelayMs);
      return HttpResponse.json({ request_id: 'req_t_plan', ...workflowPlanFixture });
    })
  );
};

const renderPage = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      {/* 测试环境关闭 antd 动画（motion:false）：jsdom 不会派发 transitionend，
          离场动画永不完成会导致 Modal portal DOM 泄漏到后续用例。 */}
      <ConfigProvider theme={{ token: { motion: false } }}>
        <WorkflowBuilderPage />
      </ConfigProvider>
    </QueryClientProvider>
  );
  return queryClient;
};

/** 上传文件并进入“确认参数”步骤。 */
const uploadAndEnterConfirm = async (user: ReturnType<typeof userEvent.setup>) => {
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  // 文件名必须匹配 Dragger 的 accept（.poscar 等），否则 userEvent.upload 不触发 change。
  await user.upload(input, new File(['Fe2O3 poscar'], 'Fe2O3.poscar', { type: 'text/plain' }));
  await screen.findByText('结构解析完成', {}, { timeout: 8000 });
  await user.click(screen.getByRole('button', { name: /下一步：确认参数/ }));
  await screen.findByText('确认计算参数');
};

const selectOption = async (combobox: HTMLElement, title: string) => {
  fireEvent.mouseDown(combobox);
  const option = await waitFor(() => {
    const el = document.querySelector(`.ant-select-item-option[title="${title}"]`);
    if (!el) throw new Error(`option ${title} not rendered`);
    return el as HTMLElement;
  });
  fireEvent.click(option);
};

const removeTask = (task: 'relax' | 'dos') => {
  const item = Array.from(document.querySelectorAll('.ant-select-selection-item'))
    .find((el) => el.textContent?.toLowerCase().includes(`(${task})`));
  const remove = item?.querySelector('.ant-select-selection-item-remove');
  if (!remove) throw new Error(`selected task ${task} not found`);
  fireEvent.click(remove);
};

const openSummaryModal = async (user: ReturnType<typeof userEvent.setup>) => {
  await user.click(screen.getByRole('button', { name: '下一步：确认摘要' }));
  await screen.findByText('最终确认：工作流参数摘要');
};

/**
 * 等待 Modal 真正卸载（动画已在测试环境全局关闭，无需手动派发 transitionend）。
 * 若不清场，残留的 portal DOM 会泄漏到后续用例（各 Modal 标题在测试环境均为 id="test-id"）。
 */
const finishModalLeave = async () => {
  await waitFor(
    () => expect(document.querySelector('.ant-modal-title')).not.toBeInTheDocument(),
    { timeout: 3000 }
  );
};

/** 确认并等待 plan 成功进入下一步，同时清场 Modal 离场动画（避免 portal 泄漏到后续用例）。 */
const confirmAndWaitPlan = async (user: ReturnType<typeof userEvent.setup>) => {
  await user.click(screen.getByRole('button', { name: /确认并生成工作流计划/ }));
  await waitFor(() => expect(planBodies.length).toBeGreaterThanOrEqual(1));
  await finishModalLeave();
};

beforeEach(() => {
  planBodies = [];
});

describe('WorkflowBuilderPage', () => {
  it('本地库include一次确认绑定新计划，真实生成请求带artifact；omit重放清除旧引用', async () => {
    useFastMocks();
    const generationBodies: Record<string, unknown>[] = [];
    const replayBodies: Record<string, unknown>[] = [];
    let releaseGeneration!: () => void;
    const generationPending = new Promise<void>(resolve => { releaseGeneration = resolve; });
    const future = new Date(Date.now() + 60 * 60_000).toISOString();
    const library: PotcarLibrary = { library_id: 'synthetic-lib', display_name: '合成库', root_path: 'synthetic-only', version_note: null, revision: 1, index_revision: 1, source_ack: { confirmed: true, confirmed_at: future }, source_fingerprint: null, created_at: future, updated_at: future, is_default: true, reachable: true, scan: null, summary: null };
    const binding = { workflow_id: workflowPlanFixture.workflow_id, revision: 2, request_sha256: 'synthetic-request', step_ids: workflowPlanFixture.steps.map(step => step.step_id) };
    const preview: PotcarPreview = { mode: 'toolbox', preview_id: 'synthetic-preview', selection_digest: 'synthetic-digest', structure_sha256: 'synthetic-structure', library: { library_id: 'synthetic-lib', display_name: '合成库', version_note: null, index_revision: 1 }, rows: [], blockers: [], expires_at: future, workflow_binding: binding };
    const artifact: PotcarArtifact = { artifact_id: 'synthetic-artifact', preview_id: preview.preview_id, selection_digest: preview.selection_digest, structure_sha256: preview.structure_sha256, library_id: 'synthetic-lib', index_revision: 1, status: 'ready', size_bytes: 4, sha256: 'synthetic-sha', created_at: future, expires_at: future, rows: [], workflow_binding: binding };
    vi.spyOn(potcarApi, 'libraries').mockResolvedValue({ mode: 'toolbox', libraries: [library], default_library_id: library.library_id, revision: 1 });
    vi.spyOn(potcarApi, 'workflowPreview').mockResolvedValue(preview);
    vi.spyOn(potcarApi, 'assemble').mockResolvedValue({ mode: 'toolbox', artifact });
    const generated: WorkflowPotcarState = { mode: 'include', artifact_id: artifact.artifact_id, status: 'generated', steps: binding.step_ids.map(step_id => ({ step_id, status: 'generated', artifact_id: artifact.artifact_id, sha256: artifact.sha256, size_bytes: 4 })) };
    server.use(
      http.post(`${API}/workflows/plan`, async ({ request }) => {
        const body = await request.json() as Record<string, unknown>;
        if (body.workflow_id) { replayBodies.push(body); return HttpResponse.json({ ...workflowPlanFixture, revision: 2, potcar: { mode: 'include', status: 'pending_confirmation', steps: [] } }); }
        planBodies.push(body as unknown as WorkflowPlanRequestBody); return HttpResponse.json(workflowPlanFixture);
      }),
      http.post(`${API}/workflows/generate`, async ({ request }) => { const body = await request.json() as Record<string, unknown>; generationBodies.push(body); if (generationBodies.length === 1) await generationPending; return HttpResponse.json({ workflow_id: workflowPlanFixture.workflow_id, workflow_status: 'generated', revision: 2, potcar: body.potcar && (body.potcar as { mode: string }).mode === 'include' ? generated : { mode: 'omit', status: 'omitted', steps: [] }, file_tree: fileTreeFixture }); }),
    );
    try {
      const user = userEvent.setup(); renderPage(); await uploadAndEnterConfirm(user); await openSummaryModal(user); await confirmAndWaitPlan(user);
      await user.click(screen.getByRole('radio', { name: '从本地库包含 POTCAR' }));
      expect(screen.getByRole('button', { name: /下一步：生成文件/ })).toBeDisabled();
      await waitFor(() => expect(screen.getByRole('button', { name: '读取 / 刷新工作流 POTCAR 预览' })).toBeEnabled());
      await user.click(screen.getByRole('button', { name: '读取 / 刷新工作流 POTCAR 预览' }));
      await waitFor(() => expect(screen.getByRole('checkbox')).toBeEnabled());
      expect(replayBodies[0]).toEqual({ workflow_id: workflowPlanFixture.workflow_id, patches: [], potcar: { mode: 'include' } });
      await user.click(screen.getByRole('checkbox')); await user.click(screen.getByRole('button', { name: '确认并准备工作流 POTCAR' }));
      await waitFor(() => expect(screen.getByRole('button', { name: /下一步：生成文件/ })).toBeEnabled());
      await user.click(screen.getByRole('button', { name: /下一步：生成文件/ }));
      await waitFor(() => expect(generationBodies).toHaveLength(1));
      expect(screen.getByRole('button', { name: '编辑参数 (可选)' })).toBeDisabled();
      expect(screen.getByRole('button', { name: /上一步/ })).toBeDisabled();
      expect(screen.getByText('POTCAR 已核验，正在加入工作流文件')).toBeInTheDocument();
      await act(async () => releaseGeneration());
      await screen.findByRole('button', { name: /下载工作流/ });
      expect(screen.getByText('POTCAR 已加入工作流文件')).toBeInTheDocument();
      expect(screen.queryByText('POTCAR 已核验，等待加入工作流文件')).not.toBeInTheDocument();
      expect(generationBodies[0]).toMatchObject({ potcar: { mode: 'include', artifact_id: artifact.artifact_id } });
      expect(screen.getByText(/文件准备不会启动计算/)).toBeInTheDocument();
      await user.click(screen.getByRole('button', { name: /修改参数后重新生成/ }));
      expect(screen.getByRole('button', { name: /下一步：生成文件/ })).toBeDisabled();
      await user.click(screen.getByRole('radio', { name: '不包含，后续自行补齐' }));
      await user.click(screen.getByRole('button', { name: /下一步：生成文件/ }));
      await screen.findByRole('button', { name: /下载工作流/ });
      expect(generationBodies[1]).toMatchObject({ potcar: { mode: 'omit' } });
      expect((generationBodies[1].potcar as Record<string, unknown>).artifact_id).toBeUndefined();
    } finally { vi.restoreAllMocks(); }
  });

  it('保留计划、可选编辑、生成、重新生成、下载与新工作流重置的真实请求路径', async () => {
    useFastMocks();
    const generationBodies: unknown[] = [];
    let downloads = 0;
    server.use(
      http.post(`${API}/workflows/generate`, async ({ request }) => {
        generationBodies.push(await request.json());
        return HttpResponse.json({ request_id: 'req_gen', workflow_id: 'wf_01', workflow_status: 'generated', file_tree: fileTreeFixture });
      }),
      http.get(`${API}/workflows/:workflowId/download`, () => {
        downloads++;
        return new HttpResponse(new Uint8Array([0x50, 0x4b]).buffer, { headers: { 'Content-Type': 'application/zip' } });
      }),
    );
    const BrowserURL = URL;
    const createObjectURL = vi.fn(() => 'blob:workflow-test');
    const revokeObjectURL = vi.fn();
    vi.stubGlobal('URL', class extends BrowserURL { static createObjectURL = createObjectURL; static revokeObjectURL = revokeObjectURL; });
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    try {
      const user = userEvent.setup();
      renderPage();
      await uploadAndEnterConfirm(user);
      await openSummaryModal(user);
      await confirmAndWaitPlan(user);
      await user.click(screen.getByRole('button', { name: '编辑参数 (可选)' }));
      expect(screen.getByText('参数白名单编辑')).toBeInTheDocument();
      await user.click(screen.getByRole('button', { name: /下一步：生成文件/ }));
      await screen.findByRole('button', { name: /下载工作流/ });
      await user.click(screen.getByRole('button', { name: /修改参数后重新生成/ }));
      expect(screen.queryByRole('button', { name: /下载工作流/ })).not.toBeInTheDocument();
      await user.click(screen.getByRole('button', { name: /下一步：生成文件/ }));
      await user.click(await screen.findByRole('button', { name: /下载工作流/ }));
      await screen.findByText('工作流已准备就绪');
      expect(generationBodies).toHaveLength(2);
      expect(generationBodies[0]).toMatchObject({ workflow_id: 'wf_01' });
      expect(downloads).toBe(1);
      expect(createObjectURL).toHaveBeenCalledTimes(1);
      expect(revokeObjectURL).toHaveBeenCalledWith('blob:workflow-test');
      expect(click).toHaveBeenCalledTimes(1);
      await user.click(screen.getByRole('button', { name: '开始新的工作流' }));
      expect(screen.getByText('上传结构文件')).toBeInTheDocument();
      expect(screen.getByText(/尚未载入结构/)).toBeInTheDocument();
      expect(screen.queryByText('工作流已准备就绪')).not.toBeInTheDocument();
    } finally { click.mockRestore(); vi.unstubAllGlobals(); }
  });

  it('样品名编辑、空白回退与实际 POSCAR 首行进入同一确认快照和请求', async () => {
    useFastMocks();
    const user = userEvent.setup();
    renderPage();
    await uploadAndEnterConfirm(user);
    const name = screen.getByRole('textbox', { name: '样品名称' });
    await user.clear(name);
    expect(screen.getByText(/POSCAR 首行预览：Fe2O3/)).toBeInTheDocument();
    await user.type(name, '氧化物样品');
    expect(screen.getByText(/POSCAR 首行预览：氧化物样品/)).toBeInTheDocument();
    await openSummaryModal(user);
    expect(screen.getAllByText('氧化物样品').length).toBeGreaterThanOrEqual(2);
    await confirmAndWaitPlan(user);
    expect(planBodies[0].workflow.sample_name).toBe('氧化物样品');
  });

  it('名称含控制字符时阻止确认，修正后可以继续', async () => {
    useFastMocks();
    const user = userEvent.setup();
    renderPage();
    await uploadAndEnterConfirm(user);
    const name = screen.getByRole('textbox', { name: '样品名称' });
    fireEvent.change(name, { target: { value: 'name\u200b' } });
    expect(screen.getByText(/不允许换行或控制字符/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '下一步：确认摘要' }));
    expect(screen.queryByText('最终确认：工作流参数摘要')).not.toBeInTheDocument();
    expect(planBodies).toHaveLength(0);
    fireEvent.change(name, { target: { value: '  ' } });
    await openSummaryModal(user);
    await confirmAndWaitPlan(user);
    expect(planBodies[0].workflow.sample_name).toBe('Fe2O3');
  });

  it('bootstrap 开启能带时允许 static → band，正常请求不包含客户端强开字段', async () => {
    useFastMocks();
    server.use(http.get(`${API}/bootstrap`, () => HttpResponse.json({ ENABLE_BAND_WORKFLOW: true })));
    const user = userEvent.setup();
    renderPage();
    await uploadAndEnterConfirm(user);

    removeTask('relax');
    removeTask('dos');
    await selectOption(screen.getAllByRole('combobox')[0], '能带 (band)');
    await waitFor(() => expect(document.querySelector('.ant-select-selection-item[title="能带 (band)"]')).toBeInTheDocument());
    expect(await screen.findByText(/本批使用默认 PBE 设置/)).toBeInTheDocument();
    await openSummaryModal(user);
    expect(screen.getByText('static → band')).toBeInTheDocument();
    await confirmAndWaitPlan(user);
    const body = planBodies[0];
    expect(body.workflow.requested_tasks).toEqual(['static', 'band']);
    expect('enable_band_workflow' in body.workflow).toBe(false);
  });

  it('bootstrap 关闭时只禁用 band，其他任务仍可确认提交', async () => {
    useFastMocks();
    server.use(http.get(`${API}/bootstrap`, () => HttpResponse.json({ ENABLE_BAND_WORKFLOW: false })));
    const user = userEvent.setup();
    renderPage();
    await uploadAndEnterConfirm(user);
    expect(await screen.findByText('服务端当前未开放能带工作流；其他计算任务仍可使用。')).toBeInTheDocument();

    const taskSelect = screen.getAllByRole('combobox')[0];
    fireEvent.mouseDown(taskSelect);
    await waitFor(() => expect(document.querySelector('.ant-select-item-option[title="服务端当前未开放能带工作流"]'))
      .toHaveClass('ant-select-item-option-disabled'));
    expect(document.querySelector('.ant-select-item-option[title="结构优化 (relax)"]'))
      .not.toHaveClass('ant-select-item-option-disabled');
    fireEvent.keyDown(taskSelect, { key: 'Escape' });

    await openSummaryModal(user);
    await confirmAndWaitPlan(user);
    expect(planBodies[0].workflow.requested_tasks).toEqual(['relax', 'static', 'dos']);
    expect(planBodies[0].workflow.confirm).toBe(true);
  });

  it('bootstrap 读取失败时可重试，成功后启用 band 选项', async () => {
    useFastMocks();
    let bootstrapCalls = 0;
    server.use(http.get(`${API}/bootstrap`, () => {
      bootstrapCalls += 1;
      return bootstrapCalls === 1
        ? HttpResponse.json({ message: 'unavailable' }, { status: 503 })
        : HttpResponse.json({ ENABLE_BAND_WORKFLOW: true });
    }));
    const user = userEvent.setup();
    renderPage();
    await uploadAndEnterConfirm(user);
    expect(await screen.findByText('服务端功能配置读取失败，能带暂不可用。')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '重试读取' }));
    await waitFor(() => expect(screen.queryByText('服务端功能配置读取失败，能带暂不可用。')).not.toBeInTheDocument());

    removeTask('relax');
    await selectOption(screen.getAllByRole('combobox')[0], '能带 (band)');
    await waitFor(() => expect(document.querySelector('.ant-select-selection-item[title="能带 (band)"]')).toBeInTheDocument());
    expect(await screen.findByText(/本批使用默认 PBE 设置/)).toBeInTheDocument();
  });

  it('band 快照打开后能力变为 false 时拒绝旧确认并返回保留 band 的表单', async () => {
    useFastMocks();
    server.use(http.get(`${API}/bootstrap`, () => HttpResponse.json({ ENABLE_BAND_WORKFLOW: true })));
    const user = userEvent.setup();
    const queryClient = renderPage();
    await uploadAndEnterConfirm(user);
    removeTask('relax');
    removeTask('dos');
    await selectOption(screen.getAllByRole('combobox')[0], '能带 (band)');
    await waitFor(() => expect(document.querySelector('.ant-select-selection-item[title="能带 (band)"]')).toBeInTheDocument());
    await openSummaryModal(user);

    queryClient.setQueryData(['featureFlags'], { ENABLE_BAND_WORKFLOW: false });
    await user.click(screen.getByRole('button', { name: /确认并生成工作流计划/ }));
    await finishModalLeave();
    expect(planBodies).toHaveLength(0);
    expect(Array.from(document.querySelectorAll('.ant-select-selection-item'))
      .some((item) => item.textContent?.includes('能带 (band)'))).toBe(true);
    expect(screen.getByText('服务端当前未开放能带工作流；其他计算任务仍可使用。')).toBeInTheDocument();
  });

  it('F3: DFT+U 关闭时 payload 携带 enabled:false 与空 entries', async () => {
    useFastMocks();
    const user = userEvent.setup();
    renderPage();
    await uploadAndEnterConfirm(user);
    await openSummaryModal(user);
    // DFT+U 关闭时不会生成 LDAU 数组：文案不得声称“派生默认”。
    expect(screen.getByText('未启用（INCAR 不生成 LDAU/LDAUL/LDAUU/LDAUJ 参数）')).toBeInTheDocument();
    expect(screen.queryByText(/全部元素使用派生默认/)).not.toBeInTheDocument();
    await confirmAndWaitPlan(user);
    expect(planBodies).toHaveLength(1);
    expect(planBodies[0].workflow.dftu).toEqual({ enabled: false, entries: [] });
    expect(planBodies[0].workflow.confirm).toBe(true);
  });

  it('F6/F7/F14: 确认的 DFT+U 与 scheduler 完整进入 payload，且与 Modal 展示一致', async () => {
    useFastMocks();
    const user = userEvent.setup();
    renderPage();
    await uploadAndEnterConfirm(user);

    // 启用 DFT+U 并填写一条确认条目
    const switches = screen.getAllByRole('switch');
    await user.click(switches[2]);
    await selectOption(screen.getByRole('combobox', { name: 'DFT+U 形式' }), 'Liechtenstein（U/J）');
    await user.click(screen.getByRole('button', { name: '添加 DFT+U 条目' }));
    const comboboxes = screen.getAllByRole('combobox');
    await selectOption(comboboxes[4], 'Fe');
    await selectOption(comboboxes[5], 'd (L=2)');
    await user.type(screen.getByPlaceholderText('U 值'), '5.3');
    await user.type(screen.getByPlaceholderText('J 值'), '1');
    await user.click(screen.getByRole('checkbox', { name: '我已确认该条目的形式、L 与输入值' }));

    // 调整 scheduler
    fireEvent.change(screen.getByPlaceholderText('HH:MM:SS'), { target: { value: '08:00:00' } });
    fireEvent.change(screen.getByPlaceholderText('vasp_std'), { target: { value: 'vasp_gam' } });

    await openSummaryModal(user);
    // Modal 展示内容（与快照同源）；取最近渲染的标题并向上定位当前 Modal 实例，
    // 避免命中前序用例可能残留的 portal DOM。
    const titleEl = screen.getAllByText('最终确认：工作流参数摘要').at(-1) as HTMLElement;
    const modalScope = titleEl.closest('.ant-modal') as HTMLElement;
    expect(modalScope).toBeInTheDocument();
    expect(screen.getByText(/Fe：L=2，U=5.3 eV，J=1 eV/)).toBeInTheDocument();
    expect(screen.getByText('已由用户确认')).toBeInTheDocument();
    expect(screen.getByText('08:00:00')).toBeInTheDocument();
    expect(screen.getByText('vasp_gam')).toBeInTheDocument();

    await confirmAndWaitPlan(user);
    const body = planBodies.at(-1) as WorkflowPlanRequestBody;

    // F6: DFT+U 完整进入请求体
    expect(body.workflow.dftu).toEqual({
      enabled: true,
      form: 'liechtenstein',
      input_mode: 'u_j',
      entries: [{
        element: 'Fe', l: 2, u_ev: 5.3, j_ev: 1,
        source_note: 'user_input', confirmed_by_user: true,
      }],
    });
    // F7: scheduler 完整进入请求体（请求侧字段为 type）
    expect(body.workflow.scheduler).toMatchObject({
      type: 'slurm', nodes: 1, tasks_per_node: 32,
      walltime: '08:00:00', vasp_binary_hint: 'vasp_gam',
    });
    // 结构与假设字段
    expect(body.structure_id).toBe('str_01');
    expect(body.workflow.material_assumptions.precision).toBe(body.workflow.precision);
    expect(body.workflow.requested_tasks).toEqual(expect.arrayContaining(['relax']));

    // F14: Modal 展示的关键值与捕获 payload 一致（同源快照）
    const entry = body.workflow.dftu.entries[0];
    expect(modalScope.textContent).toContain(`${entry.element}：L=${entry.l}，U=${entry.u_ev} eV`);
    expect(modalScope.textContent).toContain(body.workflow.scheduler.walltime);
  });

  it('Dudarev 确认摘要与请求使用相同 Ueff 和实际 LDAU 值', async () => {
    useFastMocks();
    const user = userEvent.setup();
    renderPage();
    await uploadAndEnterConfirm(user);
    await user.click(screen.getAllByRole('switch')[2]);
    await selectOption(screen.getByRole('combobox', { name: 'DFT+U 形式' }), 'Dudarev（Ueff）');
    await user.click(screen.getByRole('button', { name: '添加 DFT+U 条目' }));
    await selectOption(screen.getAllByRole('combobox')[4], 'Fe');
    await selectOption(screen.getAllByRole('combobox')[5], 'd (L=2)');
    await user.type(screen.getByPlaceholderText('Ueff 值'), '4.6');
    await user.click(screen.getByRole('checkbox', { name: '我已确认该条目的形式、L 与输入值' }));
    await openSummaryModal(user);
    expect(screen.getByText(/Dudarev（Ueff 输入；LDAUTYPE=2）/)).toBeInTheDocument();
    expect(screen.getByText(/Ueff=4.6 eV → LDAUU=4.6, LDAUJ=0/)).toBeInTheDocument();
    await confirmAndWaitPlan(user);
    expect(planBodies[0].workflow.dftu).toEqual({
      enabled: true,
      form: 'dudarev',
      input_mode: 'u_eff',
      entries: [{ element: 'Fe', l: 2, u_eff_ev: 4.6, source_note: 'user_input', confirmed_by_user: true }],
    });
  });

  it('F8: 取消最终确认不发送请求并回到表单', async () => {
    useFastMocks();
    const user = userEvent.setup();
    renderPage();
    await uploadAndEnterConfirm(user);
    await openSummaryModal(user);
    await user.click(screen.getByRole('button', { name: '返回修改' }));
    // 取消后：Modal 卸载，回到参数表单，且无 API 调用。
    await finishModalLeave();
    expect(planBodies).toHaveLength(0);
    expect(screen.getByText('确认计算参数')).toBeInTheDocument();
  });

  it('F9: 快速双击确认按钮只产生一次 plan 请求', async () => {
    useFastMocks(300);
    const user = userEvent.setup();
    renderPage();
    await uploadAndEnterConfirm(user);
    await openSummaryModal(user);
    const okButton = screen.getByRole('button', { name: /确认并生成工作流计划/ });
    // 同步连续双击：第二次必须被同步锁阻止
    fireEvent.click(okButton);
    fireEvent.click(okButton);
    await waitFor(() => expect(planBodies).toHaveLength(1), { timeout: 5000 });
    await new Promise((resolve) => setTimeout(resolve, 400));
    expect(planBodies).toHaveLength(1);
    await finishModalLeave();
    void user;
  });
});

// ---------------------------------------------------------------------------
// 确认状态防伪回归（代码审查定向修复）
// ---------------------------------------------------------------------------

describe('确认状态防伪（fail-closed）', () => {
  const summary = {
    structure_id: 'str_01',
    formula: 'Fe2O3',
    elements: ['Fe', 'O'],
    counts: [2, 3],
    atom_count: 5,
    lattice: {
      a: 5.03, b: 5.03, c: 13.75,
      alpha: 90, beta: 90, gamma: 120,
      volume: 300,
    },
    coordinate_mode: 'direct' as const,
    selective_dynamics: false,
    transition_metals: ['Fe'],
    magnetism_hint: 'possible' as const,
    source_format: 'poscar',
    source_sha256: 'test-sha',
    warnings: [],
  };
  const baseForm = {
    electronic_type: 'unknown' as const,
    magnetic: true,
    soc: false,
    precision: 'standard' as const,
    tasks: ['relax' as const],
    scheduler: {
      type: 'slurm' as const,
      nodes: 1,
      tasks_per_node: 32,
      walltime: '12:00:00',
      vasp_binary_hint: 'vasp_std',
    },
  };
  const entry = (confirmed: boolean): DftuEntryFormData => ({
    element: 'Fe', l: 2, u_ev: 5.3, j_ev: 0, confirmed_by_user: confirmed,
  });

  it('未确认条目：守卫阻止构造确认快照（最终确认 Modal 不得打开）', () => {
    const data = { ...baseForm, dftu: { enabled: true, entries: [entry(false)] } };
    expect(canBuildConfirmSnapshot(data)).toBe(false);
  });

  it('全部条目已确认或 DFT+U 关闭时守卫放行', () => {
    expect(canBuildConfirmSnapshot({ ...baseForm, dftu: { enabled: true, entries: [entry(true)] } })).toBe(true);
    expect(canBuildConfirmSnapshot({ ...baseForm, dftu: { enabled: false, entries: [] } })).toBe(true);
  });

  it('无明确形式且无旧 U/J 记录时快照守卫拒绝提交', () => {
    expect(canBuildConfirmSnapshot({ ...baseForm, dftu: {
      enabled: true, entries: [{ element: 'Fe', l: 2, u_eff_ev: 4, confirmed_by_user: true }],
    } })).toBe(false);
    expect(canBuildConfirmSnapshot({ ...baseForm, dftu: {
      enabled: true, form: 'liechtenstein', input_mode: 'u_eff', entries: [entry(true)],
    } })).toBe(false);
  });

  it('快照中的 confirmed_by_user 必须来自表单实际值（不得伪造）', () => {
    const unconfirmed = buildSnapshot(
      { ...baseForm, dftu: { enabled: true, entries: [entry(false)] } }, summary
    );
    expect(unconfirmed.dftu.entries[0].confirmed_by_user).toBe(false);
    const confirmed = buildSnapshot(
      { ...baseForm, dftu: { enabled: true, entries: [entry(true)] } }, summary
    );
    expect(confirmed.dftu.entries[0].confirmed_by_user).toBe(true);
  });

  it('旧 initialValues 缺少形式时构造旧请求，原 U/J、source_note 与确认值原样保留', () => {
    const legacy = buildSnapshot({
      ...baseForm,
      dftu: { enabled: true, entries: [{
        element: 'Fe', l: 2, u_ev: 5.3, j_ev: 1, source_note: 'archived-source', confirmed_by_user: true,
      }] },
    }, summary);
    expect(legacy.dftu).toEqual({ enabled: true, entries: [{
      element: 'Fe', l: 2, u_ev: 5.3, j_ev: 1, source_note: 'archived-source', confirmed_by_user: true,
    }] });
    expect(legacy.dftu.entries[0]).not.toHaveProperty('u_eff_ev');
  });

  it('生产代码不得出现无条件的 confirmed_by_user: true', () => {
    // 扫描范围：工作流参数链路相关生产文件（页面/表单/摘要 Modal/类型层）。
    // Recipe 补丁链路的 ParameterPatchEditor 不在本回归范围内。
    const targets: [string, string][] = [
      ['pages/WorkflowBuilderPage.tsx', pageSource],
      ['components/workflow/ParameterConfirmForm.tsx', formSource],
      ['components/workflow/WorkflowConfirmSummaryModal.tsx', modalSource],
      ['types/workflow-contract.ts', contractSource],
      ['types/generated-api.ts', generatedApiSource],
      ['hooks/useApi.ts', useApiSource],
      ['api/client.ts', clientSource],
    ];
    const offenders = targets
      .filter(([, source]) => /confirmed_by_user:\s*true/.test(source))
      .map(([name]) => name);
    expect(offenders).toEqual([]);
  });
});
