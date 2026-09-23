import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, vi } from 'vitest';
import { toolboxApi } from '../../api/client';
import type { ToolboxJob } from '../../types/toolbox';
import ToolboxResultsPanel from './ToolboxResultsPanel';

const job = (key: string, status = 'completed'): ToolboxJob => ({
  key, label: key, kind: 'static', requires: [], status,
  attempt_id: `attempt-${key}`, submission_state: 'submitted', slurm_id: 42,
});

beforeEach(() => {
  vi.stubGlobal('URL', { ...URL, createObjectURL: vi.fn(() => 'blob:result'), revokeObjectURL: vi.fn() });
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it('downloads exact current job and attempt only on explicit click', async () => {
  const blob = new Blob([new Uint8Array([0, 1, 255])]);
  const call = vi.spyOn(toolboxApi, 'downloadResult').mockResolvedValue(blob);
  const refresh = vi.fn();
  render(<ToolboxResultsPanel projectId="project" taskId="task" jobs={[job('static')]} report="done" onRefresh={refresh} />);
  expect(call).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole('button', { name: '下载 OUTCAR' }));
  await waitFor(() => expect(call).toHaveBeenCalledWith('project', 'task', 'static', 'attempt-static', 'OUTCAR', expect.any(AbortSignal)));
  await waitFor(() => expect(refresh).toHaveBeenCalled());
  expect(URL.createObjectURL).toHaveBeenCalledWith(blob);
  await userEvent.click(screen.getByRole('button', { name: '保存当前任务报告（Markdown）' }));
  expect(URL.createObjectURL).toHaveBeenCalledTimes(2);
});

it('does not enable result fetch before terminal submitted identity', () => {
  const call = vi.spyOn(toolboxApi, 'downloadResult').mockResolvedValue(new Blob());
  render(<ToolboxResultsPanel projectId="p" taskId="t" jobs={[job('static', 'running')]} report="" />);
  expect(screen.getByRole('button', { name: '下载 OUTCAR' })).toBeDisabled();
  expect(call).not.toHaveBeenCalled();
});

it('locks multi-job selection during an in-flight download', async () => {
  let release!: (blob: Blob) => void;
  vi.spyOn(toolboxApi, 'downloadResult').mockImplementation(() => new Promise<Blob>((resolve) => { release = resolve; }));
  render(<ToolboxResultsPanel projectId="p" taskId="t" jobs={[job('a'), job('b')]} report="" />);
  const user = userEvent.setup();
  await user.click(screen.getByLabelText('选择结果作业'));
  await user.click(await screen.findByText(/a · attempt-a/));
  await user.click(screen.getByRole('button', { name: '下载 OUTCAR' }));
  expect(screen.getByLabelText('选择结果作业')).toBeDisabled();
  release(new Blob(["ok"]));
  await waitFor(() => expect(screen.getByLabelText('选择结果作业')).not.toBeDisabled());
});
