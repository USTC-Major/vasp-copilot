import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import AiProjectExtraSettings from './AiProjectExtraSettings';
import { server } from '../../mocks/server';

function renderSettings(projectId: string) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <AiProjectExtraSettings projectId={projectId} open onClose={() => undefined} />
    </QueryClientProvider>,
  );
}

describe('AiProjectExtraSettings 内置模板', () => {
  beforeEach(() => localStorage.clear());

  it('只有明确点击才追加，并保留用户已有条目', async () => {
    const user = userEvent.setup();
    renderSettings('template_custom');

    expect(await screen.findByText('内置常用模板')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('button', { name: '追加「快速检查」' })).toBeEnabled());
    expect(screen.queryByDisplayValue(/快速检查建议/)).not.toBeInTheDocument();

    const newEntry = screen.getByPlaceholderText(/新增一条要求/);
    await user.type(newEntry, '保留这条用户自定义要求');
    await user.click(screen.getByRole('button', { name: /新增条目/ }));
    await user.click(screen.getByRole('button', { name: '追加「快速检查」' }));

    expect(screen.getByDisplayValue('保留这条用户自定义要求')).toBeInTheDocument();
    expect(screen.getByDisplayValue(/快速检查建议/)).toBeInTheDocument();
    expect(screen.getByDisplayValue(/不构成对文件写入、命令执行、作业提交/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText('已自动保存')).toBeInTheDocument());
  });

  it('band 模板禁止四面体方法，且不为材料默认 Hubbard U', async () => {
    const user = userEvent.setup();
    renderSettings('template_band');

    await screen.findByText('内置常用模板');
    const bandButton = screen.getByRole('button', { name: '追加「relax → static → band」' });
    await waitFor(() => expect(bandButton).toBeEnabled());
    await user.click(bandButton);

    expect(screen.getByDisplayValue(/band 阶段不得使用 ISMEAR=-5/)).toBeInTheDocument();
    expect(screen.getByDisplayValue(/不得根据材料名称猜测或默认设置 Hubbard U/)).toBeInTheDocument();
  });

  it('等待首次设置加载后才允许编辑，并在追加时保留及保存已有条目', async () => {
    let releaseGet!: () => void;
    const getGate = new Promise<void>((resolve) => { releaseGet = resolve; });
    const saved: string[][] = [];
    server.use(
      http.get('/ai/v1/projects/template_delayed/settings', async () => {
        await getGate;
        return HttpResponse.json({ mode: 'ai', project_id: 'template_delayed', settings: { project_id: 'template_delayed', accuracy: ['服务端已有条目'] } });
      }),
      http.put('/ai/v1/projects/template_delayed/settings', async ({ request }) => {
        const body = (await request.json()) as { accuracy: string[] };
        saved.push(body.accuracy);
        return HttpResponse.json({ mode: 'ai', ok: true, settings: { project_id: 'template_delayed', accuracy: body.accuracy } });
      }),
    );
    const user = userEvent.setup();
    renderSettings('template_delayed');
    const bandButton = await screen.findByRole('button', { name: '追加「relax → static → band」' });
    expect(screen.getByText('正在加载项目设置，加载完成后可编辑')).toBeInTheDocument();
    expect(bandButton).toBeDisabled();
    expect(screen.getByPlaceholderText(/新增一条要求/)).toBeDisabled();
    expect(screen.getByRole('button', { name: /新增条目/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: '清空' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '存为模板' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '追加本机模板' })).toBeDisabled();

    releaseGet();
    await waitFor(() => expect(bandButton).toBeEnabled());
    expect(screen.getByDisplayValue('服务端已有条目')).toBeInTheDocument();
    await user.click(bandButton);
    expect(screen.getByDisplayValue('服务端已有条目')).toBeInTheDocument();
    expect(screen.getByDisplayValue(/band 阶段不得使用 ISMEAR=-5/)).toBeInTheDocument();
    await waitFor(() => expect(saved).toHaveLength(1));
    expect(saved[0][0]).toBe('服务端已有条目');
    expect(saved[0][1]).toContain('band 阶段不得使用 ISMEAR=-5');
  });

  it('加载失败时保持不可编辑，重试成功后开放编辑', async () => {
    let gets = 0;
    server.use(http.get('/ai/v1/projects/template_retry/settings', () => {
      gets += 1;
      if (gets === 1) return HttpResponse.json({ error: { message: '暂时失败', retryable: true } }, { status: 503 });
      return HttpResponse.json({ mode: 'ai', project_id: 'template_retry', settings: { project_id: 'template_retry', accuracy: ['重试后条目'] } });
    }));
    const user = userEvent.setup();
    renderSettings('template_retry');
    const bandButton = await screen.findByRole('button', { name: '追加「relax → static → band」' });
    expect(await screen.findByText('项目设置加载失败，暂不可编辑；请重试加载')).toBeInTheDocument();
    expect(bandButton).toBeDisabled();
    expect(screen.getByRole('button', { name: '存为模板' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: /重试/ }));
    await waitFor(() => expect(bandButton).toBeEnabled());
    expect(screen.getByDisplayValue('重试后条目')).toBeInTheDocument();
    expect(gets).toBe(2);
  });
});
