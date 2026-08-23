// ============================================================
// WorkflowBuilderPage 集成测试（F3/F6/F7/F8/F9/F14 + 快速双击回归）
// 通过 MSW setupServer 捕获真实请求体，验证展示与发送同源。
// ============================================================

import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ConfigProvider } from 'antd';
import { http, HttpResponse, delay } from 'msw';
import WorkflowBuilderPage, { buildSnapshot, canBuildConfirmSnapshot } from './WorkflowBuilderPage';
import type { DftuEntryFormData } from '../components/workflow/ParameterConfirmForm';
import { server } from '../mocks/server';
import { uploadSuccessFixture, structureAnalysisFixture, workflowPlanFixture } from '../mocks/fixtures';
import type { WorkflowPlanRequestBody } from '../types/workflow-contract';
// 静态源码回归：通过 ?raw 导入生产文件源码（vite/client 提供类型声明），
// 断言不得出现无条件的 confirmed_by_user: true。
import pageSource from './WorkflowBuilderPage.tsx?raw';
import formSource from '../components/workflow/ParameterConfirmForm.tsx?raw';
import modalSource from '../components/workflow/WorkflowConfirmSummaryModal.tsx?raw';
import contractSource from '../types/workflow-contract.ts?raw';
import generatedApiSource from '../types/generated-api.ts?raw';
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
    await user.click(screen.getByRole('button', { name: '添加 DFT+U 条目' }));
    const comboboxes = screen.getAllByRole('combobox');
    await selectOption(comboboxes[3], 'Fe');
    await selectOption(comboboxes[4], 'd (L=2)');
    await user.type(screen.getByPlaceholderText('U 值'), '5.3');
    await user.type(screen.getByPlaceholderText('J 值'), '1');
    await user.click(screen.getByRole('checkbox', { name: '我已确认该条目的 L/U/J' }));

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
