import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { server } from '../mocks/server';
import { filePreviewFixture } from '../mocks/fixtures';
import { useFilePreview } from './useApi';

function Preview({ fileId }: { fileId: string }) {
  const { data } = useFilePreview(fileId);
  return <div>{data?.preview.content ?? '加载预览'}</div>;
}

describe('content-bound preview IDs with the existing query cache', () => {
  it('uses one QueryClient for A, B, changed A, then cached original A without cross-reading', async () => {
    const a = 'file_g1_' + 'a'.repeat(64);
    const b = 'file_g1_' + 'b'.repeat(64);
    const changedA = 'file_g1_' + 'c'.repeat(64);
    const contents: Record<string, string> = { [a]: 'A: ENCUT = 520', [b]: 'B: ENCUT = 520', [changedA]: 'A: ENCUT = 650' };
    const calls: Record<string, number> = {};
    server.use(http.get('/api/v1/files/:fileId/preview', ({ params }) => {
      const id = String(params.fileId);
      calls[id] = (calls[id] ?? 0) + 1;
      return HttpResponse.json({ request_id: 'preview-id-regression', data: {
        ...filePreviewFixture, file_id: id,
        preview: { ...filePreviewFixture.preview, content: contents[id] },
      } });
    }));
    // Infinite freshness intentionally exercises the existing cache; no clearing,
    // invalidation, or query-key strategy change is used to hide an ID collision.
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
    const view = (id: string) => <QueryClientProvider client={client}><Preview fileId={id} /></QueryClientProvider>;
    const rendered = render(view(a));
    await screen.findByText(contents[a]);
    rendered.rerender(view(b));
    await screen.findByText(contents[b]);
    expect(screen.queryByText(contents[a])).not.toBeInTheDocument();
    rendered.rerender(view(changedA));
    await screen.findByText(contents[changedA]);
    rendered.rerender(view(a));
    await screen.findByText(contents[a]);
    await waitFor(() => expect(calls).toEqual({ [a]: 1, [b]: 1, [changedA]: 1 }));
    for (const id of [a, b, changedA]) {
      expect(client.getQueryData<{ preview: { content: string } }>(['filePreview', id])?.preview.content).toBe(contents[id]);
    }
    client.clear();
  });
});
