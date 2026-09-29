import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ConfigProvider } from 'antd';
import { http, HttpResponse } from 'msw';
import { vi } from 'vitest';
import MaterialsProjectPanel from './MaterialsProjectPanel';
import { server } from '../../mocks/server';

const candidate = { material_id: 'mp-123', formula: 'Fe2O3', elements: ['Fe', 'O'], band_gap: 2.1 };
const interpreted = {
  query: '稳定且带隙1-3eV的Fe-O材料',
  criteria: { chemsys: 'Fe-O', band_gap: { min: 1, max: 3 }, is_stable: true, is_metal: false },
  interpreted_conditions: ['模型认为要求稳定、非金属'],
  unresolved_conditions: [],
  warnings: ['请核对稳定性含义'],
  status: 'ready_for_confirmation',
};

function renderPanel(onStructureImported = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><ConfigProvider theme={{ token: { motion: false } }}>
    <MaterialsProjectPanel onStructureImported={onStructureImported} />
  </ConfigProvider></QueryClientProvider>);
  return onStructureImported;
}

function naturalMode() {
  fireEvent.click(screen.getByText('自然语言条件（AI）'));
  fireEvent.change(screen.getByRole('textbox', { name: '自然语言材料条件' }), {
    target: { value: interpreted.query },
  });
}

describe('MaterialsProjectPanel', () => {
  it('shows a numeric AlphaID alias but imports the original API ID', async () => {
    const importedIds: string[] = [];
    server.use(
      http.post('/api/v1/materials/search', () => HttpResponse.json({ request_id: 'req-alpha', data: {
        query: 'SiO', criteria: { formula: 'SiO' }, llm_used: false, count: 1,
        materials: [{ ...candidate, material_id: 'mp-aaaabwmb', formula: 'SiO' }],
      } })),
      http.post('/api/v1/materials/import', async ({ request }) => {
        const body = await request.json() as { material_id: string };
        importedIds.push(body.material_id);
        return HttpResponse.json({ request_id: 'req-import-alpha', data: {
          structure_id: 'str-alpha', normalized_poscar_file_id: 'file-alpha', file_id: 'file-alpha',
          material_id: body.material_id, summary: { formula: 'SiO' },
        } });
      }),
    );
    const onImported = renderPanel();
    fireEvent.change(screen.getByRole('textbox', { name: '化学式' }), { target: { value: 'SiO' } });
    await userEvent.click(screen.getByRole('button', { name: /搜索化学式/ }));

    expect(await screen.findByText('mp-32761')).toBeInTheDocument();
    expect(screen.getByText('API ID:')).toBeInTheDocument();
    expect(screen.getByText('mp-aaaabwmb')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('radio', { name: '' }));
    await userEvent.click(screen.getByRole('button', { name: /导入所选结构/ }));

    await waitFor(() => expect(importedIds).toEqual(['mp-aaaabwmb']));
    expect(onImported).toHaveBeenCalledWith('str-alpha', { formula: 'SiO' });
  });

  it('shows criteria from structured fields and sends only confirmed criteria after a user click', async () => {
    const searches: unknown[] = [];
    server.use(
      http.post('/ai/v1/materials/interpret', async ({ request }) => {
        expect(await request.json()).toEqual({ query: interpreted.query });
        return HttpResponse.json(interpreted);
      }),
      http.post('/api/v1/materials/search', async ({ request }) => {
        searches.push(await request.json());
        return HttpResponse.json({ request_id: 'req-1', data: {
          query: interpreted.query, criteria: interpreted.criteria, llm_used: false, count: 1, materials: [candidate],
        } });
      }),
      http.post('/api/v1/materials/import', async ({ request }) => {
        expect(await request.json()).toEqual({ material_id: 'mp-123' });
        return HttpResponse.json({ request_id: 'req-2', data: {
          structure_id: 'str-1', normalized_poscar_file_id: 'file-1', file_id: 'file-1',
          material_id: 'mp-123', summary: { formula: 'Fe2O3' },
        } });
      }),
    );
    const onImported = renderPanel();
    naturalMode();
    expect(screen.getByRole('button', { name: /确认条件并搜索/ })).toBeDisabled();
    await userEvent.click(screen.getByRole('button', { name: /解析条件/ }));
    await screen.findByText(`原始需求：${interpreted.query}`);
    expect(searches).toHaveLength(0);
    expect(screen.getByText('限定化学体系：Fe-O')).toBeInTheDocument();
    expect(screen.getByText('带隙：1–3 eV')).toBeInTheDocument();
    expect(screen.getByText('MP 计算稳定性：稳定')).toBeInTheDocument();
    expect(screen.getByText('金属性：非金属')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /确认条件并搜索/ }));
    await waitFor(() => expect(searches).toEqual([{
      query: interpreted.query, criteria: interpreted.criteria, confirmed: true, unresolved_conditions: [], limit: 20,
    }]));
    expect(await screen.findByText('搜索结果：1 条候选')).toBeInTheDocument();
    expect(screen.getByText(/不能证明实验可合成/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('radio', { name: '' }));
    await userEvent.click(screen.getByRole('button', { name: /导入所选结构/ }));
    await waitFor(() => expect(onImported).toHaveBeenCalledWith('str-1', { formula: 'Fe2O3' }));
  });

  it('blocks unresolved and null-criteria interpretations', async () => {
    let searches = 0;
    server.use(
      http.post('/ai/v1/materials/interpret', () => HttpResponse.json({
        ...interpreted, criteria: null, interpreted_conditions: [],
        unresolved_conditions: ['实验可合成性不受支持'], status: 'needs_clarification',
      })),
      http.post('/api/v1/materials/search', () => { searches += 1; return HttpResponse.json({}); }),
    );
    renderPanel();
    naturalMode();
    await userEvent.click(screen.getByRole('button', { name: /解析条件/ }));
    expect(await screen.findByText('实验可合成性不受支持')).toBeInTheDocument();
    expect(screen.getByText('无可执行条件')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /确认条件并搜索/ })).toBeDisabled();
    expect(searches).toBe(0);
  });

  it('invalidates a late interpretation and old candidates when the input or mode changes', async () => {
    let release: ((value: Response) => void) | undefined;
    server.use(
      http.post('/ai/v1/materials/interpret', () => new Promise<Response>((resolve) => { release = resolve; })),
    );
    renderPanel();
    naturalMode();
    await userEvent.click(screen.getByRole('button', { name: /解析条件/ }));
    await waitFor(() => expect(release).toBeDefined());
    fireEvent.change(screen.getByRole('textbox', { name: '自然语言材料条件' }), { target: { value: '新需求' } });
    release!(HttpResponse.json(interpreted));
    await waitFor(() => expect(screen.queryByText(`原始需求：${interpreted.query}`)).not.toBeInTheDocument());
    expect(screen.getByRole('button', { name: /确认条件并搜索/ })).toBeDisabled();
    fireEvent.click(screen.getByText('明确化学式'));
    expect(screen.getByRole('textbox', { name: '化学式' })).toHaveValue('');
  });

  it('clears late search results and selection, and reports zero results', async () => {
    let release: ((value: Response) => void) | undefined;
    server.use(http.post('/api/v1/materials/search', () => new Promise<Response>((resolve) => { release = resolve; })));
    renderPanel();
    fireEvent.change(screen.getByRole('textbox', { name: '化学式' }), { target: { value: 'NaCl' } });
    await userEvent.click(screen.getByRole('button', { name: /搜索化学式/ }));
    await waitFor(() => expect(release).toBeDefined());
    fireEvent.change(screen.getByRole('textbox', { name: '化学式' }), { target: { value: 'Si' } });
    release!(HttpResponse.json({ request_id: 'req-old', data: {
      query: 'NaCl', criteria: { formula: 'NaCl' }, llm_used: false, count: 1, materials: [candidate],
    } }));
    await waitFor(() => expect(screen.queryByText('mp-123')).not.toBeInTheDocument());
    server.use(http.post('/api/v1/materials/search', () => HttpResponse.json({ request_id: 'req-zero', data: {
      query: 'Si', criteria: { formula: 'Si' }, llm_used: false, count: 0, materials: [],
    } })));
    await userEvent.click(screen.getByRole('button', { name: /搜索化学式/ }));
    expect(await screen.findByText('未找到匹配材料，请调整条件后重试')).toBeInTheDocument();
    expect(screen.getByText('化学式：Si')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /导入所选结构/ })).toBeDisabled();
  });

  it('shows AI failure and keeps the deterministic formula path available', async () => {
    const searches: unknown[] = [];
    server.use(
      http.post('/ai/v1/materials/interpret', () => HttpResponse.json({ error: {
        code: 'AI_DISABLED', message: 'AI 已关闭', retryable: false,
      } }, { status: 503 })),
      http.post('/api/v1/materials/search', async ({ request }) => {
        searches.push(await request.json());
        return HttpResponse.json({ request_id: 'req-formula', data: {
          query: 'NaCl', criteria: { formula: 'NaCl' }, llm_used: false, count: 0, materials: [],
        } });
      }),
    );
    renderPanel();
    naturalMode();
    await userEvent.click(screen.getByRole('button', { name: /解析条件/ }));
    expect(await screen.findByText('AI 已关闭')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /确认条件并搜索/ })).toBeDisabled();
    expect(searches).toHaveLength(0);
    fireEvent.click(screen.getByText('明确化学式'));
    fireEvent.change(screen.getByRole('textbox', { name: '化学式' }), { target: { value: 'NaCl' } });
    await userEvent.click(screen.getByRole('button', { name: /搜索化学式/ }));
    await waitFor(() => expect(searches).toEqual([{ query: 'NaCl', limit: 20 }]));
  });
});
