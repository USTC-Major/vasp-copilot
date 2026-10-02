import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import { server } from '../mocks/server';
import { diagnosisResultFixture } from '../mocks/fixtures';
import DiagnosisResultPage from './DiagnosisResultPage';

vi.mock('../components/diagnosis/ScfPlot', () => ({ default: () => null }));
vi.mock('../components/diagnosis/MagnetizationPlot', () => ({ default: () => null }));

describe('DiagnosisResultPage repair delivery', () => {
  it('keeps repair information and report available when AI capabilities cannot be reached', async () => {
    server.use(http.get('/ai/v1/diagnosis/capabilities', () => HttpResponse.json(
      { error: { code: 'AI_UNAVAILABLE', message: 'AI service unavailable' } },
      { status: 503 },
    )));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });

    render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={['/diagnosis/diag_01']}>
          <Routes><Route path="/diagnosis/:id" element={<DiagnosisResultPage />} /></Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByText('候选修复已生成，应用前请审阅')).toBeInTheDocument();
    expect(await screen.findByText('无法读取 AI 能力状态；原有诊断报告仍可查看，请重试读取状态。')).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /下载候选修复包/ })).toBeEnabled();
      expect(screen.getByRole('button', { name: /下载诊断报告/ })).toBeEnabled();
    });
  });

  it('offers refresh for a legacy response without fix status fields and refetches it', async () => {
    let reads = 0;
    server.use(http.get('/api/v1/diagnosis/diag_01', () => {
      reads += 1;
      const { fix_available: _available, fix_reason_code: _code, fix_reason: _reason, fix_manual_steps: _steps, ...legacy } = diagnosisResultFixture;
      return HttpResponse.json({ ...legacy, diagnosis_id: 'diag_01' });
    }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });

    render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={['/diagnosis/diag_01']}>
          <Routes><Route path="/diagnosis/:id" element={<DiagnosisResultPage />} /></Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByText('修复状态待核验')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /下载候选修复包/ })).toBeDisabled();
    await fireEvent.click(screen.getByRole('button', { name: '刷新修复状态' }));
    await waitFor(() => expect(reads).toBeGreaterThanOrEqual(2));
  });

  it('isolates an old pending download completion when the diagnosis id changes', async () => {
    let releaseFirstDownload: (() => void) | undefined;
    let markDownloadStarted: (() => void) | undefined;
    const downloadStarted = new Promise<void>((resolve) => { markDownloadStarted = resolve; });
    const downloadGate = new Promise<void>((resolve) => { releaseFirstDownload = resolve; });
    server.use(
      http.get('/api/v1/diagnosis/:diagnosisId', ({ params }) => HttpResponse.json({
        ...diagnosisResultFixture,
        diagnosis_id: String(params.diagnosisId),
      })),
      http.get('/api/v1/diagnosis/diag-a/download-fix', async () => {
        markDownloadStarted?.();
        await downloadGate;
        return new HttpResponse(new Uint8Array([0x50, 0x4b]).buffer, { status: 200, headers: { 'Content-Type': 'application/zip' } });
      }),
    );
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    client.setQueryData(['diagnosis', 'diag-b'], { ...diagnosisResultFixture, diagnosis_id: 'diag-b' });
    const PageWithSwitcher = () => {
      const navigate = useNavigate();
      return (
        <>
          <button onClick={() => navigate('/diagnosis/diag-b')}>切换诊断</button>
          <Routes><Route path="/diagnosis/:id" element={<DiagnosisResultPage />} /></Routes>
        </>
      );
    };
    const createObjectURL = vi.fn(() => 'blob:diag-a');
    const revokeObjectURL = vi.fn();
    const oldCreate = Object.getOwnPropertyDescriptor(URL, 'createObjectURL');
    const oldRevoke = Object.getOwnPropertyDescriptor(URL, 'revokeObjectURL');
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createObjectURL });
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: revokeObjectURL });
    const anchorClick = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});

    try {
      render(
        <QueryClientProvider client={client}>
          <MemoryRouter initialEntries={['/diagnosis/diag-a']}><PageWithSwitcher /></MemoryRouter>
        </QueryClientProvider>,
      );

      await screen.findByText('候选修复已生成，应用前请审阅');
      await fireEvent.click(screen.getByRole('button', { name: /下载候选修复包/ }));
      await downloadStarted;
      await fireEvent.click(screen.getByRole('button', { name: '切换诊断' }));
      await waitFor(() => expect(screen.getByRole('button', { name: /下载候选修复包/ })).toBeEnabled());
      expect(screen.getByText('诊断结果')).toBeInTheDocument();

      releaseFirstDownload?.();
      await waitFor(() => expect(createObjectURL).toHaveBeenCalledOnce());
      expect(anchorClick).toHaveBeenCalledOnce();
      expect(revokeObjectURL).toHaveBeenCalledOnce();
      expect(screen.queryByText('已发起候选文件下载，请审阅后再手工处理。')).not.toBeInTheDocument();
      expect(screen.queryByText(/候选修复包下载失败/)).not.toBeInTheDocument();
    } finally {
      if (oldCreate) Object.defineProperty(URL, 'createObjectURL', oldCreate);
      else delete (URL as unknown as { createObjectURL?: unknown }).createObjectURL;
      if (oldRevoke) Object.defineProperty(URL, 'revokeObjectURL', oldRevoke);
      else delete (URL as unknown as { revokeObjectURL?: unknown }).revokeObjectURL;
      anchorClick.mockRestore();
    }
  });
});
