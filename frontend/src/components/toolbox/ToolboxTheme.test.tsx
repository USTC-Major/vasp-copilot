import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { ConfigProvider, theme } from 'antd';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from '../../App';
import { toolboxApi } from '../../api/client';
import type { ToolboxTaskDetail } from '../../types/toolbox';
import AiDirectoryPicker from '../ai/AiDirectoryPicker';
import { hasScientificContent } from '../workflow/scientificNavigation';
import ToolboxTaskStatus from './ToolboxTaskStatus';
import './scientific-toolbox.css';

const resolve = vi.hoisted(() => vi.fn());
const detail: ToolboxTaskDetail = {
  mode: 'toolbox', task_id: 'synthetic-task', backend_mode: 'None',
  task: { id: 'synthetic-task', project_id: 'synthetic-project', title: '合成任务', goal: '界面验收', local_workspace: '/synthetic', hpc_workspace: '/synthetic/remote', status: 'unknown', updated_at: '2026-10-07T00:00:00Z' },
  flow: {
    execution_mode: 'Fake', phase: 'monitoring', goal: '界面验收', strategy: '按依赖顺序', local_dir: '/synthetic', hpc_dir: '/synthetic/remote', waiting: [], precheck: { ok: false, issues: [], per_job: true }, draft: [], artifacts: {}, report: '',
    jobs: [
      { key: 'failed', label: '失败的计算', kind: 'static', requires: [], status: 'failed', attempt_id: 'failed-1', submission_state: 'failed', diagnosis: { verdict: 'not_converged' } },
      { key: 'unknown', label: '待核实的计算', kind: 'static', requires: [], status: 'unknown', attempt_id: 'unknown-1', submission_state: 'unknown' },
    ],
  },
  consents: [{ card_id: 'pending-card', action_id: 'pending-card', kind: 'submit', summary: '合成人工确认：只确认本次计算', state: 'pending', args: { job_key: 'failed' }, binding: { attempt_id: 'failed-1', scope_id: 'synthetic-scope' } }],
  events: [], monitor: { state: 'recovering', interval_seconds: 60, remote_cancelled: false },
};
vi.mock('../../hooks/useApi', () => ({
  useFeatureFlags: () => ({ data: {} }),
  useToolboxTaskDetail: () => ({ data: detail, isLoading: false, isError: false }),
  useToolboxResolveConsent: () => ({ mutateAsync: resolve }),
}));
vi.mock('../settings/LlmSettingsModal', () => ({ default: () => null }));
vi.mock('../chat/ChatPanel', () => ({ default: () => null }));

function Content({ scientific }: { scientific: boolean }) {
  const { token } = theme.useToken();
  return <div className={scientific ? 'toolbox-page' : undefined}>
    <span data-testid="content-color">{token.colorBgContainer}</span>
    <ToolboxTaskStatus projectId="synthetic-project" taskId="synthetic-task" />
    <AiDirectoryPicker scientific={scientific} open kind="local" initialPath="/synthetic" onCancel={() => undefined} onSelect={() => undefined} />
  </div>;
}
function setup(path: string, scientific: boolean) {
  render(<ConfigProvider theme={{ token: { colorBgContainer: '#fafafa' } }}><MemoryRouter initialEntries={[path]}><Routes><Route element={<App />}><Route path="*" element={<Content scientific={scientific} />} /></Route></Routes></MemoryRouter></ConfigProvider>);
}
const panel = (dialog: HTMLElement) => dialog.querySelector('.ant-modal-body')?.firstElementChild as HTMLElement;
beforeEach(() => {
  localStorage.clear();
  resolve.mockClear();
  vi.spyOn(toolboxApi, 'browse').mockResolvedValue({ mode: 'toolbox', kind: 'local', path: '/synthetic', parent: null, exists: true, is_dir: true, entries: [{ name: 'synthetic-child', is_dir: true }] });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('shared toolbox theme boundaries', () => {
  it('keeps shared toolbox content on its fallback provider at the legacy AI progress route', async () => {
    const fallbackPath = '/ai/projects/p/progress/t';
    expect(hasScientificContent(fallbackPath)).toBe(false);
    setup(fallbackPath, false);
    const dialog = await screen.findByRole('dialog', { name: '选择本地工作区目录' });
    expect(dialog).not.toHaveClass('toolbox-directory-picker');
    expect(panel(dialog).style.background).toBe('rgb(250, 251, 252)');
    const pending = document.querySelector('.toolbox-pending-card') as HTMLElement;
    expect(pending.style.background).toBe('var(--toolbox-pending-bg, #fffbe6)');
    expect(pending.closest('.toolbox-page')).toBeNull();
    expect(screen.getByTestId('content-color')).toHaveTextContent('#fafafa');
    fireEvent.click(await within(dialog).findByText('synthetic-child'));
    const selected = within(dialog).getByText('synthetic-child').closest('.ant-list-item') as HTMLElement;
    expect(selected.style.background).toBe('rgb(230, 244, 255)');
    fireEvent.click(screen.getByRole('button', { name: '切换浅色主题' }));
    expect(panel(dialog).style.background).toBe('rgb(250, 251, 252)');
    expect(selected.style.background).toBe('rgb(230, 244, 255)');
    expect(screen.getByTestId('content-color')).toHaveTextContent('#fafafa');
    expect(toolboxApi.browse).toHaveBeenCalledTimes(1);
    expect(resolve).not.toHaveBeenCalled();
  });

  it('changes the task and portal colors without clearing selection, evidence or pending authorization', async () => {
    setup('/toolbox/projects/p/tasks/t', true);
    const dialog = await screen.findByRole('dialog', { name: '选择本地工作区目录' });
    expect(dialog).toHaveClass('toolbox-directory-picker');
    expect(screen.getByTestId('content-color')).toHaveTextContent('#1B2028');
    const darkPanel = panel(dialog).style.background;
    fireEvent.click(await within(dialog).findByText('synthetic-child'));
    const selected = within(dialog).getByText('synthetic-child').closest('.ant-list-item') as HTMLElement;
    const darkSelection = selected.style.background;
    fireEvent.click(screen.getByRole('button', { name: '切换浅色主题' }));
    expect(screen.getByTestId('content-color')).toHaveTextContent('#FFFFFF');
    expect(panel(dialog).style.background).not.toBe(darkPanel);
    expect(selected.style.background).not.toBe(darkSelection);
    expect(within(dialog).getByRole('button', { name: /选择此文件夹/ })).toBeEnabled();
    expect(screen.getByText('合成人工确认：只确认本次计算')).toBeInTheDocument();
    expect(screen.getByText('范围：synthetic-scope')).toBeInTheDocument();
    expect(screen.getByText('服务恢复后等待重新采集')).toBeInTheDocument();
    expect(screen.getByText('提交结果待核实')).toBeInTheDocument();
    expect(screen.getByText('历史执行：模拟')).toBeInTheDocument();
    expect(screen.getByText('当前后端：未配置')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '切换深色主题' }));
    await waitFor(() => expect(panel(dialog).style.background).toBe(darkPanel));
    expect(selected.style.background).toBe(darkSelection);
    expect(toolboxApi.browse).toHaveBeenCalledTimes(1);
    expect(resolve).not.toHaveBeenCalled();
  });

  it('follows the scientific provider in migrated AI chat while keeping the picker fallback and task content', async () => {
    setup('/ai/projects/p', false);
    expect(screen.getByTestId('content-color')).toHaveTextContent('#1B2028');
    const dialog = await screen.findByRole('dialog', { name: '选择本地工作区目录' });
    expect(dialog).not.toHaveClass('toolbox-directory-picker');
    expect(panel(dialog).style.background).toBe('rgb(250, 251, 252)');
    const pending = document.querySelector('.toolbox-pending-card') as HTMLElement;
    expect(pending.style.background).toBe('var(--toolbox-pending-bg, #fffbe6)');
    expect(pending.closest('.toolbox-page')).toBeNull();
    expect(screen.getByText('合成人工确认：只确认本次计算')).toBeInTheDocument();
    expect(screen.getByText('范围：synthetic-scope')).toBeInTheDocument();
    expect(screen.getByText('服务恢复后等待重新采集')).toBeInTheDocument();
    expect(screen.getByText('提交结果待核实')).toBeInTheDocument();
    expect(screen.getByText('历史执行：模拟')).toBeInTheDocument();
    expect(screen.getByText('当前后端：未配置')).toBeInTheDocument();
    fireEvent.click(await within(dialog).findByText('synthetic-child'));
    const selected = within(dialog).getByText('synthetic-child').closest('.ant-list-item') as HTMLElement;
    expect(selected.style.background).toBe('rgb(230, 244, 255)');
    fireEvent.click(screen.getByRole('button', { name: '切换浅色主题' }));
    expect(screen.getByTestId('content-color')).toHaveTextContent('#FFFFFF');
    expect(panel(dialog).style.background).toBe('rgb(250, 251, 252)');
    expect(selected.style.background).toBe('rgb(230, 244, 255)');
    expect(screen.getByText('提交结果待核实')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '切换深色主题' }));
    expect(screen.getByTestId('content-color')).toHaveTextContent('#1B2028');
    expect(panel(dialog).style.background).toBe('rgb(250, 251, 252)');
    expect(selected.style.background).toBe('rgb(230, 244, 255)');
    expect(screen.getByText('合成人工确认：只确认本次计算')).toBeInTheDocument();
    expect(toolboxApi.browse).toHaveBeenCalledTimes(1);
    expect(resolve).not.toHaveBeenCalled();
  });
});
