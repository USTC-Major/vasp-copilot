import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { catalysisApi } from '../../api/catalysis';
import { ApiError } from '../../api/client';
import { catalysisWorkflowDraftFixture, catalysisWorkflowResponseFixture } from '../../mocks/catalysisWorkflowFixture';
import { getWorkflowDraft, resetWorkflowDraft, setWorkflowDraftField } from '../../stores/workflowDraft';
import type { CatalysisWorkflowBindingResponse } from '../../types/catalysis';
import CatalysisWorkflowHandoff from './CatalysisWorkflowHandoff';

const doc = catalysisWorkflowDraftFixture;
function page(disabled = false, identity = 'revision-7', onConflict?: () => void) {
  return <ConfigProvider theme={{ token: { motion: false } }}><MemoryRouter>
    <Routes><Route path="/" element={<CatalysisWorkflowHandoff doc={doc} target={{ surface_id: 'surface-1' }} snapshot={doc.input_snapshot} identity={identity} disabled={disabled} label="传入清洁表面" onConflict={onConflict} />} />
      <Route path="/workflow" element={<h1>Workflow 参数</h1>} /></Routes>
  </MemoryRouter></ConfigProvider>;
}
beforeEach(() => { resetWorkflowDraft(); vi.spyOn(catalysisApi, 'workflowBinding').mockResolvedValue(catalysisWorkflowResponseFixture()); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it('requires explicit single-model confirmation and offers replace/cancel for an existing Workflow draft', async () => {
  setWorkflowDraftField('sampleName', '已有科研输入');
  const before = getWorkflowDraft(); render(page());
  fireEvent.click(screen.getByRole('button', { name: '传入清洁表面' }));
  expect(await screen.findByText('已有 Workflow 草稿将被替换')).toBeInTheDocument();
  expect(catalysisApi.workflowBinding).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '取消，保留当前草稿' }));
  expect(getWorkflowDraft()).toBe(before);
  await waitFor(() => expect(screen.queryByText('确认单个模型传入 Workflow')).not.toBeInTheDocument());
  fireEvent.click(screen.getByRole('button', { name: '传入清洁表面' }));
  fireEvent.click(await screen.findByRole('button', { name: '确认替换草稿并进入 Workflow' }));
  await screen.findByRole('heading', { name: 'Workflow 参数' });
  expect(catalysisApi.workflowBinding).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ revision: 7 }), { surface_id: 'surface-1' });
  expect(getWorkflowDraft().catalysisBinding?.parent_clean_snapshot_id).toBe('snapshot-1');
});

it('invalidates an open confirmation when unapplied inputs change', async () => {
  const view = render(page()); fireEvent.click(screen.getByRole('button', { name: '传入清洁表面' }));
  await screen.findByText('确认单个模型传入 Workflow');
  view.rerender(page(true, 'edited-input'));
  await waitFor(() => expect(screen.queryByText('确认单个模型传入 Workflow')).not.toBeInTheDocument());
  expect(screen.getByRole('button', { name: '传入清洁表面' })).toBeDisabled();
  expect(catalysisApi.workflowBinding).not.toHaveBeenCalled();
});

it('preserves a newer Workflow draft while the binding request is pending', async () => {
  let resolve!: (response: CatalysisWorkflowBindingResponse) => void;
  vi.mocked(catalysisApi.workflowBinding).mockImplementationOnce(() => new Promise(value => { resolve = value; }));
  render(page()); fireEvent.click(screen.getByRole('button', { name: '传入清洁表面' }));
  fireEvent.click(await screen.findByRole('button', { name: '确认并进入 Workflow' }));
  act(() => setWorkflowDraftField('sampleName', '新草稿输入'));
  await act(async () => resolve(catalysisWorkflowResponseFixture()));
  expect(getWorkflowDraft().sampleName).toBe('新草稿输入'); expect(getWorkflowDraft().catalysisBinding).toBeNull();
  expect(screen.getByText(/已保留新输入/)).toBeInTheDocument();
});

it('does not adopt a late binding after the chosen input changes or reuse a failed confirmation', async () => {
  let resolve!: (response: CatalysisWorkflowBindingResponse) => void;
  vi.mocked(catalysisApi.workflowBinding).mockImplementationOnce(() => new Promise(value => { resolve = value; }));
  const view = render(page()); fireEvent.click(screen.getByRole('button', { name: '传入清洁表面' }));
  fireEvent.click(await screen.findByRole('button', { name: '确认并进入 Workflow' }));
  view.rerender(page(true, 'edited-input'));
  await act(async () => resolve(catalysisWorkflowResponseFixture()));
  expect(getWorkflowDraft().structureId).toBeNull();
  expect(screen.getByText(/旧快照未替换/)).toBeInTheDocument();
  const onConflict = vi.fn();
  view.rerender(page(false, 'edited-input', onConflict));
  vi.mocked(catalysisApi.workflowBinding).mockRejectedValueOnce(new ApiError('CAT_REVISION_CONFLICT', '草稿 revision 已变化', false, 409));
  fireEvent.click(screen.getByRole('button', { name: '传入清洁表面' })); fireEvent.click(await screen.findByRole('button', { name: '确认并进入 Workflow' }));
  await screen.findByText('草稿 revision 已变化');
  expect(screen.queryByText('确认单个模型传入 Workflow')).not.toBeInTheDocument();
  expect(onConflict).toHaveBeenCalledOnce();
  expect(getWorkflowDraft().structureId).toBeNull();
});
