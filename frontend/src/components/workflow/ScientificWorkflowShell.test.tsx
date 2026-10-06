import { describe, expect, it, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { ConfigProvider, theme } from 'antd';
import App from '../../App';
import { readWorkflowTheme } from './workflowTheme';

vi.mock('../../hooks/useApi', () => ({ useFeatureFlags: () => ({ data: {} }) }));
vi.mock('../settings/LlmSettingsModal', () => ({ default: () => null }));
vi.mock('../chat/ChatPanel', () => ({ default: () => null }));
function ThemeProbe() { const { token } = theme.useToken(); return <div data-testid="theme-token">{token.colorBgContainer}</div>; }
const setup = () => render(<ConfigProvider theme={{ token: { colorBgContainer: '#fafafa' } }}><MemoryRouter initialEntries={['/workflow']}><Routes><Route element={<App />}><Route path="workflow" element={<ThemeProbe />} /><Route path="/" element={<ThemeProbe />} /></Route></Routes></MemoryRouter></ConfigProvider>);
beforeEach(() => { cleanup(); localStorage.clear(); vi.restoreAllMocks(); });
describe('workflow-only visual theme', () => {
  it('defaults to graphite, switches to light, and leaves the home theme intact', () => {
    setup();
    expect(document.querySelector('.scientific-workflow')).toHaveAttribute('data-workflow-theme', 'dark');
    expect(screen.getByTestId('theme-token')).toHaveTextContent('#1B2028');
    fireEvent.click(screen.getByRole('button', { name: '切换浅色主题' }));
    expect(document.querySelector('.scientific-workflow')).toHaveAttribute('data-workflow-theme', 'light');
    expect(localStorage.getItem('vasp-copilot.workflow-theme')).toBe('light');
    fireEvent.click(screen.getByRole('link', { name: '首页' }));
    expect(document.querySelector('.scientific-workflow')).toBeNull();
    expect(screen.getByTestId('theme-token')).toHaveTextContent('#fafafa');
  });
  it('restores a preference, and remains usable if browser storage is blocked', () => {
    localStorage.setItem('vasp-copilot.workflow-theme', 'light');
    expect(readWorkflowTheme()).toBe('light');
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked'); });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked'); });
    expect(readWorkflowTheme()).toBe('dark');
    setup();
    fireEvent.click(screen.getByRole('button', { name: '切换浅色主题' }));
    expect(document.querySelector('.scientific-workflow')).toHaveAttribute('data-workflow-theme', 'light');
  });
});
