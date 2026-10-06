import { fireEvent, render, screen } from '@testing-library/react';
import { ConfigProvider, theme } from 'antd';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import App from '../../App';
import DiagnosisUploadPanel from './DiagnosisUploadPanel';
import LlmExplainPanel from './LlmExplainPanel';
import './scientific-diagnosis.css';

const explain = vi.hoisted(() => vi.fn(async () => ({ ok: true, answer: '离线验证回答' })));
vi.mock('../../hooks/useApi', () => ({
  useFeatureFlags: () => ({ data: {} }),
  useDiagnosisUpload: () => ({ isPending: true, error: null, reset: vi.fn(), mutateAsync: vi.fn() }),
  useDiagnosisCapabilities: () => ({ data: { available: true }, isLoading: false, isError: false }),
  useDiagnosisExplain: () => ({ mutateAsync: explain, isPending: false, isError: false, reset: vi.fn() }),
}));
vi.mock('../settings/LlmSettingsModal', () => ({ default: () => null }));
vi.mock('../chat/ChatPanel', () => ({ default: () => null }));

function EmbeddedTools() {
  const { token } = theme.useToken();
  return <><span data-testid="content-color">{token.colorBgContainer}</span><DiagnosisUploadPanel onDetected={vi.fn()} /><LlmExplainPanel diagnosisId="synthetic" /></>;
}

describe('shared diagnosis component theme boundaries', () => {
  it('keeps AI embedded content on its original provider and fallback colors while navigation themes change', async () => {
    localStorage.clear();
    render(<ConfigProvider theme={{ token: { colorBgContainer: '#fafafa' } }}><MemoryRouter initialEntries={['/ai/projects/p']}><Routes><Route element={<App />}><Route path="*" element={<EmbeddedTools />} /></Route></Routes></MemoryRouter></ConfigProvider>);
    expect(screen.getByTestId('content-color')).toHaveTextContent('#fafafa');
    const scan = screen.getByText('正在扫描文件...');
    expect(scan.closest('.diagnosis-page')).toBeNull();
    expect(scan.closest('.scientific-workflow')).toBeNull();
    expect(scan.style.color).toBe('var(--diag-muted, #999)');
    fireEvent.click(screen.getByRole('button', { name: '一键通俗解释' }));
    const answer = await screen.findByText('离线验证回答');
    expect(answer.closest('.diagnosis-explain-answer')).toHaveAttribute('style', expect.stringContaining('var(--diag-field, #f6f8fa)'));
    expect(answer.closest('.diagnosis-page')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '切换浅色主题' }));
    expect(screen.getByTestId('content-color')).toHaveTextContent('#fafafa');
    expect(screen.getByText('离线验证回答')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '切换深色主题' }));
    expect(screen.getByTestId('content-color')).toHaveTextContent('#fafafa');
    expect(explain).toHaveBeenCalledOnce();
  });
});
