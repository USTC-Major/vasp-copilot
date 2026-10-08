import { useState } from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ConfigProvider } from 'antd';
import { vi } from 'vitest';
import { potcarApi } from '../../api/potcar';
import { workflowPlanFixture } from '../../mocks/fixtures';
import { ApiError } from '../../api/client';
import type { PotcarArtifact, PotcarLibrary, PotcarPreview, WorkflowPotcarChoice, WorkflowPotcarState } from '../../types/potcar';
import WorkflowPotcarPanel from './WorkflowPotcarPanel';

const future = (minutes: number) => new Date(Date.now() + minutes * 60_000).toISOString();
const library: PotcarLibrary = { library_id: 'synthetic-library', display_name: '合成库', root_path: 'synthetic-only', version_note: null, revision: 1, index_revision: 1, source_ack: { confirmed: true, confirmed_at: future(0) }, source_fingerprint: 'synthetic', created_at: future(0), updated_at: future(0), is_default: true, reachable: true, scan: null, summary: null };
const binding = { workflow_id: workflowPlanFixture.workflow_id, revision: 2, request_sha256: 'synthetic-request', step_ids: ['static', 'band'] };
function preview(): PotcarPreview {
  return { mode: 'toolbox', preview_id: 'synthetic-preview', selection_digest: 'synthetic-digest', structure_sha256: 'synthetic-poscar', library: { library_id: library.library_id, display_name: library.display_name, version_note: null, index_revision: 1 }, rows: [{ position: 1, element: 'In', atom_count: 1, dataset_id: 'in-d', candidates: [{ dataset_id: 'in-d', library_id: library.library_id, relative_path: 'In_d/POTCAR', compression: 'raw', element: 'In', variant: 'In_d', family: 'PAW_PBE', lexch: 'PE', zval: 13, enmax_ev: 300, dataset_date: null, title: 'Synthetic In_d', decoded_sha256: 'synthetic-dataset', source_sha256: 'synthetic-source', status: 'ready', issues: [], duplicate_of: null }], reason: { code: 'RULE_RECOMMENDED', message: '合成推荐理由' }, advice: [{ code: 'ELEMENT_GUIDANCE', message: '合成独立建议保留', rule_ids: ['G-PBLOCK-D'], source_ids: ['S1'], target_variants: ['In_d'] }] }], blockers: [], expires_at: future(30), rule_version: 'synthetic-r1', context: { purpose: 'regular', functional: 'PBE+U' }, context_source: 'workflow', workflow_binding: binding };
}
function artifact(): PotcarArtifact {
  return { artifact_id: 'synthetic-artifact', preview_id: 'synthetic-preview', selection_digest: 'synthetic-digest', structure_sha256: 'synthetic-poscar', library_id: library.library_id, index_revision: 1, status: 'ready', size_bytes: 4, sha256: 'synthetic-sha', created_at: future(0), expires_at: future(1440), rows: [], workflow_binding: binding };
}
const prepare = vi.fn();
let choices: WorkflowPotcarChoice[];
function Harness({ draft = '', revision = 1, redirectPreparedId, generation }: { draft?: string; revision?: number; redirectPreparedId?: string; generation?: WorkflowPotcarState }) {
  const [plan, setPlan] = useState({ ...workflowPlanFixture, revision });
  const [choice, setChoice] = useState<WorkflowPotcarChoice>({ mode: 'include' });
  return <ConfigProvider theme={{ token: { motion: false } }}><WorkflowPotcarPanel plan={{ ...plan, revision: revision === 1 ? plan.revision : revision }} draftKey={draft} choice={choice} onChoice={value => { choices.push(value); setChoice(value); }} preparePlan={prepare} onPlanPrepared={value => setPlan(redirectPreparedId ? { ...value, workflow_id: redirectPreparedId } : value)} generation={generation} disabled={false} /><output data-testid="choice">{choice.mode}:{choice.artifact_id ?? 'none'}</output></ConfigProvider>;
}
async function read() {
  await waitFor(() => expect(screen.getByRole('button', { name: '读取 / 刷新工作流 POTCAR 预览' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: '读取 / 刷新工作流 POTCAR 预览' }));
  await waitFor(() => expect(screen.getByRole('checkbox')).toBeEnabled());
}
async function confirm() {
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(screen.getByRole('button', { name: '确认并准备工作流 POTCAR' }));
  await screen.findByText('POTCAR 已核验，等待加入工作流文件');
}
beforeEach(() => {
  choices = []; prepare.mockReset(); prepare.mockResolvedValue({ ...workflowPlanFixture, revision: 2 });
  vi.spyOn(potcarApi, 'libraries').mockResolvedValue({ mode: 'toolbox', libraries: [library], default_library_id: library.library_id, revision: 1 });
  vi.spyOn(potcarApi, 'workflowPreview').mockResolvedValue(preview());
  vi.spyOn(potcarApi, 'assemble').mockResolvedValue({ mode: 'toolbox', artifact: artifact() });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it('先保存计划再仅传服务端revision和库选择，static/band单次确认共用产物', async () => {
  render(<Harness />); await read();
  expect(prepare).toHaveBeenCalledTimes(1);
  expect(potcarApi.workflowPreview).toHaveBeenCalledWith(workflowPlanFixture.workflow_id, { revision: 2, library_id: library.library_id, index_revision: 1 }, expect.any(AbortSignal));
  expect(screen.getByText('一次确认关联步骤：static → band')).toBeInTheDocument();
  expect(screen.getByText(/来自服务端工作流参数/)).toBeInTheDocument();
  expect(screen.getByText(/库发布版本未知/)).toBeInTheDocument();
  expect(screen.getByRole('link', { name: /S1 选择赝势/ })).toHaveAttribute('href', expect.stringContaining('oldid=38073'));
  expect(screen.getByRole('button', { name: '确认并准备工作流 POTCAR' })).toBeDisabled();
  await confirm();
  expect(potcarApi.assemble).toHaveBeenCalledTimes(1);
  expect(screen.getByTestId('choice')).toHaveTextContent('include:synthetic-artifact');
  expect(screen.queryByLabelText('POSCAR 文本')).not.toBeInTheDocument();
});

it('建议来源优先使用服务端规则包的标题和固定链接', async () => {
  const documented = preview(); documented.rule_sources = [{ source_id: 'S1', title: '服务端已审固定来源', url: 'https://vasp.at/wiki/index.php?title=Choosing_pseudopotentials&oldid=38073#p-elements' }];
  vi.mocked(potcarApi.workflowPreview).mockResolvedValue(documented);
  render(<Harness />); await read();
  expect(screen.getByRole('link', { name: /S1 服务端已审固定来源/ })).toHaveAttribute('href', documented.rule_sources[0].url);
  expect(screen.queryByRole('link', { name: /S1 选择赝势/ })).not.toBeInTheDocument();
});

it('参数草稿改变撤销旧确认和产物，不恢复异步旧预览', async () => {
  let resolve!: (value: PotcarPreview) => void;
  vi.mocked(potcarApi.workflowPreview).mockImplementation(() => new Promise(done => { resolve = done; }));
  const view = render(<Harness />);
  await waitFor(() => expect(screen.getByRole('button', { name: '读取 / 刷新工作流 POTCAR 预览' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: '读取 / 刷新工作流 POTCAR 预览' }));
  await waitFor(() => expect(potcarApi.workflowPreview).toHaveBeenCalledTimes(1));
  view.rerender(<Harness draft="changed-ENCUT" />);
  await act(async () => resolve(preview()));
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  expect(screen.getByTestId('choice')).toHaveTextContent('include:none');
});

it('自身replan尚未消费时切换另一同revision工作流，晚到旧响应不得恢复选择', async () => {
  let resolve!: (value: PotcarPreview) => void;
  let signal: AbortSignal | undefined;
  vi.mocked(potcarApi.workflowPreview).mockImplementation((_workflowId, _body, requestSignal) => { signal = requestSignal; return new Promise(done => { resolve = done; }); });
  render(<Harness redirectPreparedId="synthetic-another-workflow" />);
  await waitFor(() => expect(screen.getByRole('button', { name: '读取 / 刷新工作流 POTCAR 预览' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: '读取 / 刷新工作流 POTCAR 预览' }));
  await waitFor(() => expect(potcarApi.workflowPreview).toHaveBeenCalledTimes(1));
  expect(signal?.aborted).toBe(true);
  await act(async () => resolve(preview()));
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  expect(screen.queryByText('一次确认关联步骤：static → band')).not.toBeInTheDocument();
  expect(screen.getByTestId('choice')).toHaveTextContent('include:none');
  expect(potcarApi.assemble).not.toHaveBeenCalled();
});

it('产物提示随工作流生成成功或失败更新，不继续显示等待加入', async () => {
  const view = render(<Harness />); await read(); await confirm();
  view.rerender(<Harness generation={{ mode: 'include', artifact_id: artifact().artifact_id, status: 'generated', steps: [] }} />);
  expect(screen.getByText('POTCAR 已加入工作流文件')).toBeInTheDocument();
  expect(screen.queryByText('POTCAR 已核验，等待加入工作流文件')).not.toBeInTheDocument();
  view.rerender(<Harness generation={{ mode: 'include', artifact_id: artifact().artifact_id, status: 'failed', steps: [] }} />);
  expect(screen.getByText('POTCAR 已核验，本次加入工作流失败，请恢复后重新生成')).toBeInTheDocument();
  expect(screen.queryByText('POTCAR 已核验，等待加入工作流文件')).not.toBeInTheDocument();
});

it('确认产物后计划revision改变必须重新预览确认', async () => {
  const view = render(<Harness />); await read(); await confirm();
  view.rerender(<Harness revision={7} />);
  expect(screen.getByTestId('choice')).toHaveTextContent('include:none');
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  expect(screen.queryByText('POTCAR 已核验，等待加入工作流文件')).not.toBeInTheDocument();
});

it('手选优先、独立建议保留；刷新只发送明确选择而非旧推荐', async () => {
  const multiple = preview(); multiple.rows[0].candidates.push({ ...multiple.rows[0].candidates[0], dataset_id: 'in-plain', variant: 'In' });
  vi.mocked(potcarApi.workflowPreview).mockResolvedValue(multiple);
  const user = userEvent.setup(); render(<Harness />); await read();
  await user.click(screen.getByRole('combobox', { name: '第 1 项 In 变体' }));
  await user.click(screen.getByText(/In · ZVAL 13 · ENMAX 300/));
  expect(screen.getByRole('checkbox')).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: '读取 / 刷新工作流 POTCAR 预览' }));
  await waitFor(() => expect(potcarApi.workflowPreview).toHaveBeenCalledTimes(2));
  expect(vi.mocked(potcarApi.workflowPreview).mock.calls[1][1].dataset_ids).toEqual(['in-plain']);
  expect(screen.getByText('合成独立建议保留')).toBeInTheDocument();
});

it('库缺失可以明确省略；不重复要求库许可', async () => {
  vi.mocked(potcarApi.libraries).mockResolvedValue({ mode: 'toolbox', libraries: [], default_library_id: null, revision: 1 });
  render(<Harness />); await screen.findByText(/尚未登记赝势库/);
  fireEvent.click(screen.getByRole('radio', { name: '不包含，后续自行补齐' }));
  expect(screen.getByTestId('choice')).toHaveTextContent('omit:none');
  expect(screen.getByText('未包含 / 待补齐')).toBeInTheDocument();
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  expect(potcarApi.assemble).not.toHaveBeenCalled();
});

it('过期或源变化导致确认失效，失败不提供伪成功产物', async () => {
  vi.mocked(potcarApi.assemble).mockRejectedValue(new ApiError('POTCAR_SOURCE_CHANGED', '合成源已变化', true, 409));
  render(<Harness />); await read();
  fireEvent.click(screen.getByRole('checkbox')); fireEvent.click(screen.getByRole('button', { name: '确认并准备工作流 POTCAR' }));
  await screen.findByText('合成源已变化');
  expect(screen.getByRole('checkbox')).not.toBeChecked();
  expect(screen.getByRole('checkbox')).toBeDisabled();
  expect(screen.getByTestId('choice')).toHaveTextContent('include:none');
  expect(screen.queryByText('POTCAR 已核验，等待加入工作流文件')).not.toBeInTheDocument();
});

it('不接受缺失或不匹配workflow绑定的预览', async () => {
  vi.mocked(potcarApi.workflowPreview).mockResolvedValue({ ...preview(), workflow_binding: { ...binding, revision: 99 } });
  render(<Harness />);
  await waitFor(() => expect(screen.getByRole('button', { name: '读取 / 刷新工作流 POTCAR 预览' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: '读取 / 刷新工作流 POTCAR 预览' }));
  await screen.findByText('预览未绑定当前工作流计划，请重新读取。');
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  expect(potcarApi.assemble).not.toHaveBeenCalled();
});
