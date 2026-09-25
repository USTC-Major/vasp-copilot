import type { ReactNode } from 'react';
import { act, renderHook } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { HttpResponse, http } from 'msw';
import { server } from '../mocks/server';
import { createQueryClient } from '../queryClient';
import {
  useToolboxProjectCreate, useToolboxTaskCreate, useToolboxRunTool, useToolboxResolveConsent,
  useAiSettingsSave, useAiSecretUpdate,
} from './useApi';

const BASE = '/api/v1/toolbox';

function renderWithProductionDefaults<T>(hook: () => T) {
  const queryClient = createQueryClient();
  const wrapper = ({ children }: { children: ReactNode }) =>
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  const rendered = renderHook(hook, { wrapper });
  expect(queryClient.getDefaultOptions().mutations?.retry).toBe(false);
  return { ...rendered, queryClient };
}

describe('Toolbox writes with production QueryClient defaults', () => {
  it('does not replay project creation after a server error', async () => {
    let posts = 0;
    server.use(http.post(`${BASE}/projects`, () => {
      posts += 1;
      return HttpResponse.json({ error: { code: 'FAILED', message: '创建失败', retryable: true } }, { status: 503 });
    }));
    const { result, queryClient } = renderWithProductionDefaults(useToolboxProjectCreate);

    await act(async () => {
      await expect(result.current.mutateAsync({ name: 'once' })).rejects.toThrow('创建失败');
    });
    expect(posts).toBe(1);
    expect(queryClient.getMutationCache().getAll()[0].options.retry).toBe(false);
  });

  it('does not replay task creation after the connection drops', async () => {
    let posts = 0;
    server.use(http.post(`${BASE}/projects/:projectId/tasks`, () => {
      posts += 1;
      return HttpResponse.error();
    }));
    const { result } = renderWithProductionDefaults(useToolboxTaskCreate);

    await act(async () => {
      await expect(result.current.mutateAsync({ projectId: 'project', body: { title: 'once' } })).rejects.toThrow();
    });
    expect(posts).toBe(1);
  });

  it('does not replay a tool request when the response is lost after receipt', async () => {
    let posts = 0;
    server.use(http.post(`${BASE}/projects/:projectId/tasks/:taskId/tools`, () => {
      posts += 1;
      return HttpResponse.error();
    }));
    const { result } = renderWithProductionDefaults(useToolboxRunTool);

    await act(async () => {
      await expect(result.current.mutateAsync({ projectId: 'project', taskId: 'task', name: 'submit' })).rejects.toThrow();
    });
    expect(posts).toBe(1);
  });

  it('does not replay consent on a business error response', async () => {
    let posts = 0;
    server.use(http.post(`${BASE}/projects/:projectId/tasks/:taskId/consents/:cardId`, () => {
      posts += 1;
      return HttpResponse.json({ mode: 'toolbox', ok: false, error: { code: 'FAILED', message: '确认失败', retryable: true } });
    }));
    const { result } = renderWithProductionDefaults(useToolboxResolveConsent);

    await act(async () => {
      await expect(result.current.mutateAsync({ projectId: 'project', taskId: 'task', cardId: 'card', approved: true })).rejects.toThrow('确认失败');
    });
    expect(posts).toBe(1);
  });
});

describe('AI writes with production QueryClient defaults', () => {
  it('does not replay settings or secret writes on unavailable responses', async () => {
    let settingsPuts = 0;
    let secretPuts = 0;
    server.use(
      http.put('/ai/v1/settings', () => {
        settingsPuts += 1;
        return HttpResponse.text('Bad Gateway', { status: 502 });
      }),
      http.put('/ai/v1/settings/secrets/llm', () => {
        secretPuts += 1;
        return HttpResponse.error();
      }),
    );
    const { result: settings } = renderWithProductionDefaults(useAiSettingsSave);
    const { result: secrets } = renderWithProductionDefaults(useAiSecretUpdate);
    await act(async () => {
      await expect(settings.current.mutateAsync({ max_jobs: 2 })).rejects.toThrow();
      await expect(secrets.current.mutateAsync({ kind: 'llm', action: 'clear' })).rejects.toThrow();
    });
    expect(settingsPuts).toBe(1);
    expect(secretPuts).toBe(1);
  });
});
