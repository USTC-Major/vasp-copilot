// ============================================================
// ParameterConfirmForm 行为测试（F1/F2/F4/F5/F11/F12/F13）
// 全部为行为测试：不读取生产源码、不搜索字符串。
// ============================================================

import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ParameterConfirmForm from './ParameterConfirmForm';

const renderForm = (onSubmit = vi.fn()) => {
  render(
    <ParameterConfirmForm
      elements={['Fe', 'O']}
      transitionMetals={['Fe']}
      onSubmit={onSubmit}
      isGenerating={false}
    />
  );
  return onSubmit;
};

/** antd Select：mouseDown 打开后按 title 点击选项。 */
const selectOption = async (combobox: HTMLElement, title: string) => {
  fireEvent.mouseDown(combobox);
  const option = await waitFor(() => {
    const el = document.querySelector(`.ant-select-item-option[title="${title}"]`);
    if (!el) throw new Error(`option ${title} not rendered`);
    return el as HTMLElement;
  });
  fireEvent.click(option);
};

/** 启用 DFT+U 并添加一条条目，返回用户事件实例。 */
const enableDftuAndAddEntry = async () => {
  const user = userEvent.setup();
  const switches = screen.getAllByRole('switch');
  // 开关顺序：磁性、SOC、启用 DFT+U
  await user.click(switches[2]);
  await user.click(screen.getByRole('button', { name: '添加 DFT+U 条目' }));
  return user;
};

const fillEntry = async (user: ReturnType<typeof userEvent.setup>, u: string, j: string) => {
  const comboboxes = screen.getAllByRole('combobox');
  // 顺序：tasks、electronic_type、precision、元素、L、调度器类型
  await selectOption(comboboxes[3], 'Fe');
  await selectOption(comboboxes[4], 'd (L=2)');
  await user.clear(screen.getByPlaceholderText('U 值'));
  await user.type(screen.getByPlaceholderText('U 值'), u);
  await user.clear(screen.getByPlaceholderText('J 值'));
  await user.type(screen.getByPlaceholderText('J 值'), j);
};

const submit = async (user: ReturnType<typeof userEvent.setup>) => {
  await user.click(screen.getByRole('button', { name: '下一步：确认摘要' }));
};

describe('ParameterConfirmForm', () => {
  it('F1: 含过渡金属时 DFT+U 默认关闭', () => {
    renderForm();
    const switches = screen.getAllByRole('switch');
    expect(switches[2]).not.toBeChecked();
  });

  it('F2: 启用后条目为空，新增条目时 U 输入为空（无预填数值）', async () => {
    renderForm();
    const user = await enableDftuAndAddEntry();
    expect(screen.getByPlaceholderText('U 值')).toHaveValue('');
    expect(screen.getByPlaceholderText('J 值')).toHaveValue('');
    // 元素与 L 也无预填
    const comboboxes = screen.getAllByRole('combobox');
    expect(comboboxes[3]).toHaveTextContent('');
    void user;
  });

  it('F4: 启用但 U 为空时提交被阻止', async () => {
    const onSubmit = renderForm();
    const user = await enableDftuAndAddEntry();
    const comboboxes = screen.getAllByRole('combobox');
    await selectOption(comboboxes[3], 'Fe');
    await selectOption(comboboxes[4], 'd (L=2)');
    await user.type(screen.getByPlaceholderText('J 值'), '0');
    await user.click(screen.getByRole('checkbox', { name: '我已确认该条目的 L/U/J' }));
    await submit(user);
    await screen.findByText('U为必填项');
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('F5: 条目未勾选确认时提交被阻止', async () => {
    const onSubmit = renderForm();
    const user = await enableDftuAndAddEntry();
    await fillEntry(user, '5.3', '0');
    await submit(user);
    await screen.findByText('请确认该条目最终的 L/U/J 取值');
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('F11/F12/F13: 确认后修改 U 自动失效确认，重新确认前不能提交，重确认后携带新值', async () => {
    const onSubmit = renderForm();
    const user = await enableDftuAndAddEntry();
    await fillEntry(user, '5.3', '0');
    const checkbox = screen.getByRole('checkbox', { name: '我已确认该条目的 L/U/J' });
    await user.click(checkbox);
    expect(checkbox).toBeChecked();

    // 确认后修改 U：confirmed_by_user 必须立即自动恢复 false
    await user.clear(screen.getByPlaceholderText('U 值'));
    await user.type(screen.getByPlaceholderText('U 值'), '6.0');
    await waitFor(() => expect(checkbox).not.toBeChecked());

    // 未重新确认时提交被阻止
    await submit(user);
    await screen.findByText('请确认该条目最终的 L/U/J 取值');
    expect(onSubmit).not.toHaveBeenCalled();

    // 重新确认后可提交，且携带修改后的最终值
    await user.click(checkbox);
    await submit(user);
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    const data = onSubmit.mock.calls[0][0];
    expect(data.dftu.enabled).toBe(true);
    expect(data.dftu.entries[0]).toMatchObject({
      element: 'Fe',
      l: 2,
      u_ev: 6.0,
      j_ev: 0,
      confirmed_by_user: true,
    });
  });

  it('J 为负时仅显示 warning：不阻止、不改写，确认后可提交原值', async () => {
    const onSubmit = renderForm();
    const user = await enableDftuAndAddEntry();
    await fillEntry(user, '5.3', '-0.5');
    // J<0 的异常提示与 U 的提示分开说明；无 U≤0 提示。
    await screen.findByText(/J 值为负（<0）/);
    expect(screen.queryByText(/U 值不常见/)).not.toBeInTheDocument();
    // 不阻止、不改写：确认后仍可提交，且 j_ev 保持用户输入的 -0.5。
    await user.click(screen.getByRole('checkbox', { name: '我已确认该条目的 L/U/J' }));
    await submit(user);
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit.mock.calls[0][0].dftu.entries[0].j_ev).toBe(-0.5);
  });

  it.each(['vasp_std', 'vasp_gam', '/opt/vasp/bin/vasp_std'])(
    'vasp_binary_hint 合法值 %s 可通过并进入提交数据',
    async (value) => {
      const onSubmit = renderForm();
      const user = userEvent.setup();
      fireEvent.change(screen.getByPlaceholderText('vasp_std'), { target: { value } });
      await submit(user);
      await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
      expect(onSubmit.mock.calls[0][0].scheduler.vasp_binary_hint).toBe(value);
    }
  );

  it.each([
    'vasp_std;echo',
    'vasp_std && echo',
    'vasp_std\necho hi',
    'vasp_std | tee',
    'vasp`id`',
    "vasp'x'",
    'vasp$(id)',
  ])('vasp_binary_hint 含 shell 运算符的 %j 不能提交', async (value) => {
    const onSubmit = renderForm();
    const user = userEvent.setup();
    fireEvent.change(screen.getByPlaceholderText('vasp_std'), { target: { value } });
    await submit(user);
    await screen.findByText('仅允许安全的可执行文件名或 POSIX 路径，不允许 shell 运算符');
    expect(onSubmit).not.toHaveBeenCalled();
  });
});
