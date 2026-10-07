import { useState } from 'react';
import { describe, expect, it, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { ConfigProvider, theme } from 'antd';
import App from '../../App';
import { readWorkflowTheme } from './workflowTheme';
import { hasScientificContent, workspaceLocation } from './scientificNavigation';

const flags = vi.hoisted(() => ({ data: {} as { ENABLE_FAKE_HPC?: boolean } }));
vi.mock('../../hooks/useApi', () => ({ useFeatureFlags: () => flags }));
vi.mock('../settings/LlmSettingsModal', () => ({ default: ({ open }: { open: boolean }) => open ? <div role="dialog" aria-label="模型设置" /> : null }));
vi.mock('../chat/ChatPanel', () => ({
  default: function ChatStub({ onOpenSettings }: { onOpenSettings: () => void }) {
    const [draft, setDraft] = useState('');
    return <><input aria-label="助手草稿" value={draft} onChange={event => setDraft(event.target.value)} /><button onClick={onOpenSettings}>助手设置</button></>;
  },
}));
function ThemeProbe() { const { token } = theme.useToken(); return <div data-testid="theme-token">{token.colorBgContainer}</div>; }
const setup = (path = '/workflow') => render(
  <ConfigProvider theme={{ token: { colorBgContainer: '#fafafa' } }}>
    <MemoryRouter initialEntries={[path]}><Routes><Route element={<App />}>
      <Route path="*" element={<ThemeProbe />} />
    </Route></Routes></MemoryRouter>
  </ConfigProvider>,
);
beforeEach(() => { cleanup(); localStorage.clear(); vi.restoreAllMocks(); flags.data = {}; });

describe('shared scientific navigation and isolated content', () => {
  it('keeps the same preference on home/workflow and the original theme inside other tools', () => {
    setup();
    expect(document.querySelector('.scientific-shell')).toHaveAttribute('data-workflow-theme', 'dark');
    expect(screen.getByTestId('theme-token')).toHaveTextContent('#1B2028');
    fireEvent.click(screen.getByRole('button', { name: '切换浅色主题' }));
    expect(localStorage.getItem('vasp-copilot.workflow-theme')).toBe('light');
    fireEvent.click(screen.getByRole('link', { name: '首页' }));
    expect(screen.getByTestId('theme-token')).toHaveTextContent('#FFFFFF');
    fireEvent.click(screen.getByRole('link', { name: '计算任务' }));
    expect(document.querySelector('.scientific-shell')).toHaveAttribute('data-workflow-theme', 'light');
    expect(screen.getByTestId('theme-token')).toHaveTextContent('#FFFFFF');
    expect(screen.getByTestId('theme-token').closest('.scientific-workflow')).not.toBeNull();
    fireEvent.click(screen.getByRole('link', { name: '执行设置' }));
    expect(screen.getByTestId('theme-token')).toHaveTextContent('#fafafa');
    expect(screen.getByTestId('theme-token').closest('.scientific-workflow')).toBeNull();
    expect(screen.getByTestId('theme-token').closest('.wf-tool-panel')).not.toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '切换深色主题' }));
    expect(screen.getByTestId('theme-token')).toHaveTextContent('#fafafa');
    fireEvent.click(screen.getByRole('link', { name: '生成工作流' }));
    expect(screen.getByTestId('theme-token')).toHaveTextContent('#1B2028');
  });

  it('restores the existing preference and works with blocked browser storage', () => {
    localStorage.setItem('vasp-copilot.workflow-theme', 'light');
    expect(readWorkflowTheme()).toBe('light');
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked'); });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked'); });
    expect(readWorkflowTheme()).toBe('dark');
    setup();
    fireEvent.click(screen.getByRole('button', { name: '切换浅色主题' }));
    expect(document.querySelector('.scientific-shell')).toHaveAttribute('data-workflow-theme', 'light');
  });

  it.each([
    ['/', '首页', '首页'],
    ['/workflow', '生成工作流', '生成工作流'],
    ['/workflow/history/long%2Fid', '生成工作流', '工作流详情'],
    ['/diagnosis/upload', '诊断计算', '诊断计算'],
    ['/diagnosis/result-id', '诊断计算', '诊断结果'],
    ['/toolbox/projects', '计算任务', '计算任务'],
    ['/toolbox/projects/p/tasks/t', '计算任务', '任务详情'],
    ['/toolbox/settings', '执行设置', '执行设置'],
    ['/ai', '智能模式', '智能模式'],
    ['/ai/projects/p', '智能模式', '项目详情'],
    ['/ai/settings', '智能设置', '智能设置'],
    ['/hpc/deploy', '远程部署（离线演示）', '远程部署（离线演示）'],
    ['/hpc/jobs/job', '远程部署（离线演示）', '远程作业'],
    ['/ai/', '智能模式', '智能模式'],
    ['/toolbox/settings/', '执行设置', '执行设置'],
    ['/workflow/history/CaseSensitiveId/', '生成工作流', '工作流详情'],
    ['/AI/Settings/', '智能设置', '智能设置'],
  ])('marks exactly one current entry for %s', (path, name, title) => {
    flags.data = { ENABLE_FAKE_HPC: true };
    setup(path);
    expect(screen.getByRole('link', { name })).toHaveAttribute('aria-current', 'page');
    expect(document.querySelectorAll('a[aria-current="page"]')).toHaveLength(1);
    expect(screen.getByLabelText('当前位置')).toHaveTextContent(title);
    if (!hasScientificContent(path)) {
      expect(screen.getByTestId('theme-token').closest('.scientific-workflow')).toBeNull();
      expect(screen.getByTestId('theme-token')).toHaveTextContent('#fafafa');
    }
  });

  it('does not let an unknown parent prefix or root claim the current page', () => {
    expect(workspaceLocation('/workflow-extra').current).toBe('');
    expect(workspaceLocation('/toolbox/settings-extra').current).toBe('');
    expect(workspaceLocation('/ai/settings-extra').current).toBe('');
    setup('/workflow-extra');
    expect(document.querySelectorAll('a[aria-current="page"]')).toHaveLength(0);
  });

  it.each(['/workflow/', '/Workflow/', '/diagnosis/upload/', '/Diagnosis/CaseSensitiveId/', '/toolbox/projects', '/Toolbox/Projects/', '/toolbox/projects/ProjectCase/tasks/TaskCase/'])('applies the migrated theme to %s', path => {
    setup(path);
    expect(screen.getByTestId('theme-token')).toHaveTextContent('#1B2028');
    expect(document.querySelector('a[aria-current="page"]')).toHaveAttribute('href', workspaceLocation(path).current);
  });

  it.each(['/toolbox', '/toolbox/settings', '/toolbox/projects/project-only', '/toolbox/projects/p/tasks/t/extra', '/ai/projects/p'])('does not extend the toolbox content theme to %s', path => {
    expect(hasScientificContent(path)).toBe(false);
    setup(path);
    expect(screen.getByTestId('theme-token')).toHaveTextContent('#fafafa');
    expect(screen.getByTestId('theme-token').closest('.scientific-workflow')).toBeNull();
  });

  it('retains the diagnosis content theme on route/theme changes without migrating the embedded AI tools', () => {
    setup('/diagnosis/upload');
    expect(screen.getByTestId('theme-token')).toHaveTextContent('#1B2028');
    fireEvent.click(screen.getByRole('button', { name: '切换浅色主题' }));
    expect(screen.getByTestId('theme-token')).toHaveTextContent('#FFFFFF');
    fireEvent.click(screen.getByRole('link', { name: '智能模式' }));
    expect(screen.getByTestId('theme-token')).toHaveTextContent('#fafafa');
    expect(screen.getByTestId('theme-token').closest('.scientific-workflow')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '切换深色主题' }));
    expect(screen.getByTestId('theme-token')).toHaveTextContent('#fafafa');
    fireEvent.click(screen.getByRole('link', { name: '诊断计算' }));
    expect(screen.getByTestId('theme-token')).toHaveTextContent('#1B2028');
  });

  it.each([{}, { ENABLE_FAKE_HPC: false }])('requires an explicit true flag for the remote entry', data => {
    flags.data = data;
    setup();
    expect(screen.queryByRole('link', { name: '远程部署（离线演示）' })).not.toBeInTheDocument();
  });

  it('keeps assistant drafts and the open settings modal across route/theme boundaries', () => {
    setup();
    fireEvent.change(screen.getByLabelText('助手草稿'), { target: { value: '保留草稿' } });
    fireEvent.click(screen.getByRole('button', { name: '助手设置' }));
    fireEvent.click(screen.getByRole('link', { name: '诊断计算' }));
    expect(screen.getByLabelText('助手草稿')).toHaveValue('保留草稿');
    expect(screen.getAllByRole('dialog', { name: '模型设置' })).toHaveLength(1);
    fireEvent.click(screen.getByRole('link', { name: '首页' }));
    fireEvent.click(screen.getByRole('button', { name: '切换浅色主题' }));
    expect(screen.getByLabelText('助手草稿')).toHaveValue('保留草稿');
    expect(screen.getAllByRole('dialog', { name: '模型设置' })).toHaveLength(1);
    expect(screen.getByRole('link', { name: '跳到主内容' })).toHaveAttribute('href', '#workspace-content');
    expect(document.getElementById('workspace-content')).toHaveAttribute('tabindex', '-1');
  });
});
