import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ComponentProps } from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { describe, expect, it, vi } from 'vitest';
import { server } from '../../mocks/server';
import ReportDownloadPanel from './ReportDownloadPanel';

function renderPanel(props: Partial<ComponentProps<typeof ReportDownloadPanel>> = {}) {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false }, queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ReportDownloadPanel diagnosisId="diag-test" reportReady reportUrl="/report" fixAvailable fixReason="候选已核验。" onRefresh={props.onRefresh} refreshing={props.refreshing} />
    </QueryClientProvider>,
  );
}

describe('ReportDownloadPanel', () => {
  it('labels the archive as a candidate and allows a report plus refresh and retry after download rejection', async () => {
    let attempts = 0;
    server.use(http.get('/api/v1/diagnosis/diag-test/download-fix', () => {
      attempts += 1;
      if (attempts === 1) {
        return HttpResponse.json({ error: { code: 'FIX_PACKAGE_FAILED', message: '候选包暂时不可用，请刷新后重试。' } }, { status: 409 });
      }
      return new HttpResponse(new Uint8Array([0x50, 0x4b]).buffer, { status: 200, headers: { 'Content-Type': 'application/zip' } });
    }));

    const refresh = vi.fn();
    const createObjectURL = vi.fn(() => 'blob:test');
    const oldCreate = Object.getOwnPropertyDescriptor(URL, 'createObjectURL');
    const oldRevoke = Object.getOwnPropertyDescriptor(URL, 'revokeObjectURL');
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createObjectURL });
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() });
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    renderPanel({ onRefresh: refresh });

    expect(screen.getByRole('button', { name: /下载候选修复包/ })).toBeEnabled();
    await fireEvent.click(screen.getByRole('button', { name: /下载候选修复包/ }));
    expect(await screen.findByText(/候选修复包下载失败/)).toBeInTheDocument();
    expect(screen.getByText('候选包暂时不可用，请刷新后重试。')).toBeInTheDocument();
    await fireEvent.click(screen.getByRole('button', { name: '刷新修复状态' }));
    expect(refresh).toHaveBeenCalledOnce();

    await fireEvent.click(screen.getByRole('button', { name: /下载候选修复包/ }));
    await waitFor(() => expect(attempts).toBe(2));
    expect(await screen.findByText('已发起候选文件下载，请审阅后再手工处理。')).toBeInTheDocument();
    expect(screen.getByText(/不表示问题已修复/)).toBeInTheDocument();

    await fireEvent.click(screen.getByRole('button', { name: /下载诊断报告/ }));
    await waitFor(() => expect(createObjectURL).toHaveBeenCalled());
    expect(click).toHaveBeenCalled();
    if (oldCreate) Object.defineProperty(URL, 'createObjectURL', oldCreate); else delete (URL as unknown as { createObjectURL?: unknown }).createObjectURL;
    if (oldRevoke) Object.defineProperty(URL, 'revokeObjectURL', oldRevoke); else delete (URL as unknown as { revokeObjectURL?: unknown }).revokeObjectURL;
    click.mockRestore();
  });

  it('keeps report download available while candidate downloads are disabled for stale status', async () => {
    const createObjectURL = vi.fn(() => 'blob:test-report');
    const oldCreate = Object.getOwnPropertyDescriptor(URL, 'createObjectURL');
    const oldRevoke = Object.getOwnPropertyDescriptor(URL, 'revokeObjectURL');
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createObjectURL });
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() });
    const refresh = vi.fn();
    render(
      <QueryClientProvider client={new QueryClient()}>
        <ReportDownloadPanel diagnosisId="diag-test" reportReady reportUrl="/report" fixAvailable={false} fixReason="请重新运行诊断。" onRefresh={refresh} />
      </QueryClientProvider>,
    );

    expect(screen.getByRole('button', { name: /下载候选修复包/ })).toBeDisabled();
    expect(screen.getByText('请重新运行诊断。')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /下载诊断报告/ })).toBeEnabled();
    await fireEvent.click(screen.getByRole('button', { name: '刷新修复状态' }));
    expect(refresh).toHaveBeenCalledOnce();
    await fireEvent.click(screen.getByRole('button', { name: /下载诊断报告/ }));
    await waitFor(() => expect(createObjectURL).toHaveBeenCalled());
    if (oldCreate) Object.defineProperty(URL, 'createObjectURL', oldCreate); else delete (URL as unknown as { createObjectURL?: unknown }).createObjectURL;
    if (oldRevoke) Object.defineProperty(URL, 'revokeObjectURL', oldRevoke); else delete (URL as unknown as { revokeObjectURL?: unknown }).revokeObjectURL;
  });
});
