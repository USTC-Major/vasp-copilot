import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import AiProjectExtraSettings from './AiProjectExtraSettings';

function renderSettings() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <AiProjectExtraSettings projectId="template_test" open onClose={() => undefined} />
    </QueryClientProvider>,
  );
}

describe('AiProjectExtraSettings 内置模板', () => {
  beforeEach(() => localStorage.clear());

  it('只有明确点击才追加，并保留用户已有条目', async () => {
    const user = userEvent.setup();
    renderSettings();

    expect(await screen.findByText('内置常用模板')).toBeInTheDocument();
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
    renderSettings();

    await screen.findByText('内置常用模板');
    await user.click(screen.getByRole('button', { name: '追加「relax → static → band」' }));

    expect(screen.getByDisplayValue(/band 阶段不得使用 ISMEAR=-5/)).toBeInTheDocument();
    expect(screen.getByDisplayValue(/不得根据材料名称猜测或默认设置 Hubbard U/)).toBeInTheDocument();
  });
});
