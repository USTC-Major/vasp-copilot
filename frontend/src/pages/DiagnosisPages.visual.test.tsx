import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { HttpResponse, http, delay } from 'msw';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import { server } from '../mocks/server';
import { diagnosisResultFixture, diagnosisUploadFixture } from '../mocks/fixtures';
import DiagnosisUploadPage from './DiagnosisUploadPage';
import DiagnosisResultPage from './DiagnosisResultPage';

vi.mock('../components/diagnosis/ScfPlot', () => ({ default: () => <div>SCF 图表区域</div> }));
vi.mock('../components/diagnosis/MagnetizationPlot', () => ({ default: () => <div>磁性图表区域</div> }));

const Location = () => <div data-testid="location">{useLocation().pathname}</div>;
const setup = (path: string) => render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}><MemoryRouter initialEntries={[path]}><Routes>
  <Route path="/diagnosis/upload" element={<DiagnosisUploadPage />} />
  <Route path="/diagnosis/:id" element={<><DiagnosisResultPage /><Location /></>} />
</Routes></MemoryRouter></QueryClientProvider>);

describe('diagnosis page visual states and preserved actions', () => {
  it('shows scanning, detected files and the same explicit diagnosis run action', async () => {
    let runBody: unknown;
    server.use(
      http.post('/api/v1/diagnosis/upload', async () => { await delay(100); return HttpResponse.json(diagnosisUploadFixture); }),
      http.post('/api/v1/diagnosis/run', async ({ request }) => { runBody = await request.json(); return HttpResponse.json(diagnosisResultFixture); }),
    );
    setup('/diagnosis/upload');
    expect(screen.getByRole('heading', { name: '诊断计算' })).toBeInTheDocument();
    expect(screen.queryByText(/VASP-Doctor\+/)).not.toBeInTheDocument();
    const input = document.querySelector<HTMLInputElement>('input[type="file"]')!;
    await userEvent.upload(input, new File(['synthetic zip'], 'ui.zip', { type: 'application/zip' }));
    expect(await screen.findByText('正在扫描文件...')).toBeInTheDocument();
    expect(await screen.findByText('文件检测完成，点击开始诊断')).toBeInTheDocument();
    expect(screen.getByText('OUTCAR')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /开始诊断/ }));
    expect(await screen.findByTestId('location')).toHaveTextContent('/diagnosis/diag_01');
    expect(runBody).toEqual({ diagnosis_id: 'diag_01' });
  });

  it('preserves a detected upload after a run failure and requires another explicit start after resetting the error', async () => {
    let runs = 0;
    server.use(
      http.post('/api/v1/diagnosis/upload', () => HttpResponse.json(diagnosisUploadFixture)),
      http.post('/api/v1/diagnosis/run', () => { runs++; return runs === 1 ? HttpResponse.json({ error: { code: 'RUN_DOWN', message: '诊断执行暂不可用', retryable: true } }, { status: 503 }) : HttpResponse.json(diagnosisResultFixture); }),
    );
    setup('/diagnosis/upload');
    await userEvent.upload(document.querySelector<HTMLInputElement>('input[type="file"]')!, new File(['zip'], 'ui.zip', { type: 'application/zip' }));
    fireEvent.click(await screen.findByRole('button', { name: /开始诊断/ }));
    expect(await screen.findByText('诊断执行暂不可用')).toBeInTheDocument();
    expect(screen.getByText('OUTCAR')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /重\s*试/ }));
    expect(runs).toBe(1);
    fireEvent.click(screen.getByRole('button', { name: /开始诊断/ }));
    expect(await screen.findByTestId('location')).toHaveTextContent('/diagnosis/diag_01');
    expect(runs).toBe(2);
  });

  it('identifies an unavailable TTL record without showing report, candidate or chart actions', async () => {
    server.use(http.get('/api/v1/diagnosis/missing', () => HttpResponse.json({ error: { code: 'DIAGNOSIS_NOT_FOUND', message: 'diagnosis expired', retryable: false } }, { status: 404 })));
    setup('/diagnosis/missing');
    expect(await screen.findByText('诊断记录不可用或已过期')).toBeInTheDocument();
    expect(screen.getByText(/此页面不会恢复已过期的记录/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '返回诊断上传' })).toHaveAttribute('href', '/diagnosis/upload');
    expect(screen.queryByRole('button', { name: /下载/ })).not.toBeInTheDocument();
    expect(screen.queryByText('SCF 图表区域')).not.toBeInTheDocument();
  });

  it('keeps transient report errors retryable and renders the original summary when retry succeeds', async () => {
    let reads = 0;
    const headline = '实际诊断摘要与长标题'.repeat(12);
    server.use(http.get('/api/v1/diagnosis/retry', () => { reads++; return reads === 1 ? HttpResponse.json({ error: { code: 'SERVICE_DOWN', message: '报告读取暂不可用', retryable: true } }, { status: 503 }) : HttpResponse.json({ ...diagnosisResultFixture, summary: { ...diagnosisResultFixture.summary, headline } }); }));
    setup('/diagnosis/retry');
    expect(await screen.findByText('报告读取暂不可用')).toBeInTheDocument();
    expect(screen.queryByText('诊断记录不可用或已过期')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /重\s*试/ }));
    expect(await screen.findByText(headline)).toBeInTheDocument();
    expect(screen.getByText('SCF 图表区域')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('button', { name: /下载诊断报告/ })).toBeEnabled());
    expect(reads).toBe(2);
  });
});
