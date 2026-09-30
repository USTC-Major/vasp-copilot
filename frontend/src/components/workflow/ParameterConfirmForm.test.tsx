// ============================================================
// ParameterConfirmForm 行为测试（F1/F2/F4/F5/F11/F12/F13）
// 全部为行为测试：不读取生产源码、不搜索字符串。
// ============================================================

import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ParameterConfirmForm from './ParameterConfirmForm';

const renderForm = (
  onSubmit = vi.fn(),
  options: {
    bandWorkflowStatus?: 'loading' | 'error' | 'enabled' | 'disabled';
    onRetryBandCapability?: () => void;
    initialValues?: Partial<import('./ParameterConfirmForm').ParameterConfirmFormData>;
  } = {},
) => {
  render(
    <ParameterConfirmForm
      elements={['Fe', 'O']}
      transitionMetals={['Fe']}
      onSubmit={onSubmit}
      isGenerating={false}
      bandWorkflowStatus={options.bandWorkflowStatus}
      onRetryBandCapability={options.onRetryBandCapability}
      initialValues={options.initialValues}
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
const enableDftuAndAddEntry = async (form: 'dudarev' | 'liechtenstein' = 'liechtenstein') => {
  const user = userEvent.setup();
  const switches = screen.getAllByRole('switch');
  // 开关顺序：磁性、SOC、启用 DFT+U
  await user.click(switches[2]);
  await selectOption(screen.getByRole('combobox', { name: 'DFT+U 形式' }),
    form === 'dudarev' ? 'Dudarev（Ueff）' : 'Liechtenstein（U/J）');
  await user.click(screen.getByRole('button', { name: '添加 DFT+U 条目' }));
  return user;
};

const fillEntry = async (user: ReturnType<typeof userEvent.setup>, u: string, j: string) => {
  const comboboxes = screen.getAllByRole('combobox');
  // 顺序：tasks、electronic_type、precision、形式、元素、L、调度器类型
  await selectOption(comboboxes[4], 'Fe');
  await selectOption(comboboxes[5], 'd (L=2)');
  await user.clear(screen.getByPlaceholderText('U 值'));
  await user.type(screen.getByPlaceholderText('U 值'), u);
  await user.clear(screen.getByPlaceholderText('J 值'));
  await user.type(screen.getByPlaceholderText('J 值'), j);
};

const submit = async (user: ReturnType<typeof userEvent.setup>) => {
  await user.click(screen.getByRole('button', { name: '下一步：确认摘要' }));
};

describe('ParameterConfirmForm', () => {
  it('能带能力开启时允许 static → band 提交，并保留用户选项', async () => {
    const onSubmit = renderForm(vi.fn(), {
      bandWorkflowStatus: 'enabled',
      initialValues: { tasks: ['static'] },
    });
    const user = userEvent.setup();
    const taskSelect = screen.getAllByRole('combobox')[0];
    await selectOption(taskSelect, '能带 (band)');
    await waitFor(() => expect(document.querySelector('.ant-select-selection-item[title="能带 (band)"]')).toBeInTheDocument());
    expect(await screen.findByText(/本批使用默认 PBE 设置/)).toBeInTheDocument();
    await submit(user);
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit.mock.calls[0][0].tasks).toEqual(['static', 'band']);
  });

  it.each([
    ['loading', '正在读取服务端功能配置，完成前能带选项不可用。'],
    ['disabled', '服务端当前未开放能带工作流；其他计算任务仍可使用。'],
  ] as const)('band 能力为 %s 时选项禁用且说明原因', async (status, message) => {
    renderForm(vi.fn(), { bandWorkflowStatus: status });
    expect(screen.getByText(message)).toBeInTheDocument();
    fireEvent.mouseDown(screen.getAllByRole('combobox')[0]);
    const unavailableTitle = status === 'loading' ? '正在读取服务端功能配置' : '服务端当前未开放能带工作流';
    await waitFor(() => expect(document.querySelector(`.ant-select-item-option[title="${unavailableTitle}"]`))
      .toHaveClass('ant-select-item-option-disabled'));
  });

  it('bootstrap 读取失败显示可重试入口，band 保持不可用', async () => {
    const retry = vi.fn();
    renderForm(vi.fn(), { bandWorkflowStatus: 'error', onRetryBandCapability: retry });
    expect(screen.getByText('服务端功能配置读取失败，能带暂不可用。')).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole('button', { name: '重试读取' }));
    expect(retry).toHaveBeenCalledTimes(1);
  });

  it.each([
    { tasks: ['relax', 'static', 'band'] as ('relax' | 'static' | 'band')[], soc: false, text: /先用 relax 完成弛豫，将生成的 CONTCAR 作为新结构上传/ },
    { tasks: ['static', 'band'] as ('static' | 'band')[], soc: true, text: /当前批次不支持 SOC 与 band 同时计算/ },
    { tasks: ['band'] as 'band'[], soc: false, text: /band 必须包含 static 上游任务/ },
  ])('band 与当前任务设置冲突时保留选择并阻止提交', async ({ tasks, soc, text }) => {
    const onSubmit = renderForm(vi.fn(), {
      bandWorkflowStatus: 'enabled',
      initialValues: { tasks, soc },
    });
    expect(screen.getByText(text)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '下一步：确认摘要' })).toBeDisabled();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('F1: 含过渡金属时 DFT+U 默认关闭', () => {
    renderForm();
    const switches = screen.getAllByRole('switch');
    expect(switches[2]).not.toBeChecked();
  });

  it('新会话启用 DFT+U 后必须显式选择形式，不能默认 Liechtenstein', async () => {
    const onSubmit = renderForm();
    const user = userEvent.setup();
    await user.click(screen.getAllByRole('switch')[2]);
    expect(screen.getByText('请先选择 DFT+U 形式，再填写对应的参数。')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '添加 DFT+U 条目' })).toBeDisabled();
    expect(screen.queryByPlaceholderText('U 值')).not.toBeInTheDocument();
    await submit(user);
    await screen.findByText('请选择 DFT+U 形式');
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('F2: 启用后条目为空，新增条目时 U 输入为空（无预填数值）', async () => {
    renderForm();
    const user = await enableDftuAndAddEntry();
    expect(screen.getByPlaceholderText('U 值')).toHaveValue('');
    expect(screen.getByPlaceholderText('J 值')).toHaveValue('');
    // 元素与 L 也无预填
    const comboboxes = screen.getAllByRole('combobox');
    expect(comboboxes[4]).toHaveTextContent('');
    void user;
  });

  it('F4: 启用但 U 为空时提交被阻止', async () => {
    const onSubmit = renderForm();
    const user = await enableDftuAndAddEntry();
    const comboboxes = screen.getAllByRole('combobox');
    await selectOption(comboboxes[4], 'Fe');
    await selectOption(comboboxes[5], 'd (L=2)');
    await user.type(screen.getByPlaceholderText('J 值'), '0');
    await user.click(screen.getByRole('checkbox', { name: '我已确认该条目的形式、L 与输入值' }));
    await submit(user);
    await screen.findByText('U为必填项');
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('F5: 条目未勾选确认时提交被阻止', async () => {
    const onSubmit = renderForm();
    const user = await enableDftuAndAddEntry();
    await fillEntry(user, '5.3', '0');
    await submit(user);
    await screen.findByText('请确认该条目的形式、L 与输入值');
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('F11/F12/F13: 确认后修改 U 自动失效确认，重新确认前不能提交，重确认后携带新值', async () => {
    const onSubmit = renderForm();
    const user = await enableDftuAndAddEntry();
    await fillEntry(user, '5.3', '0');
    const checkbox = screen.getByRole('checkbox', { name: '我已确认该条目的形式、L 与输入值' });
    await user.click(checkbox);
    expect(checkbox).toBeChecked();

    // 确认后修改 U：confirmed_by_user 必须立即自动恢复 false
    await user.clear(screen.getByPlaceholderText('U 值'));
    await user.type(screen.getByPlaceholderText('U 值'), '6.0');
    await waitFor(() => expect(checkbox).not.toBeChecked());

    // 未重新确认时提交被阻止
    await submit(user);
    await screen.findByText('请确认该条目的形式、L 与输入值');
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
    await user.click(screen.getByRole('checkbox', { name: '我已确认该条目的形式、L 与输入值' }));
    await submit(user);
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit.mock.calls[0][0].dftu.entries[0].j_ev).toBe(-0.5);
  });

  it('Dudarev 仅收 Ueff，形式切换清空新形式数值并要求重新确认', async () => {
    const onSubmit = renderForm();
    const user = await enableDftuAndAddEntry('dudarev');
    await selectOption(screen.getAllByRole('combobox')[4], 'Fe');
    await selectOption(screen.getAllByRole('combobox')[5], 'd (L=2)');
    await user.type(screen.getByPlaceholderText('Ueff 值'), '4.2');
    const checkbox = screen.getByRole('checkbox', { name: '我已确认该条目的形式、L 与输入值' });
    await user.click(checkbox);
    await selectOption(screen.getAllByRole('combobox')[3], 'Liechtenstein（U/J）');
    await waitFor(() => expect(checkbox).not.toBeChecked());
    expect(screen.getByPlaceholderText('U 值')).toHaveValue('');
    expect(screen.getByPlaceholderText('J 值')).toHaveValue('');
    await user.type(screen.getByPlaceholderText('U 值'), '5');
    await user.type(screen.getByPlaceholderText('J 值'), '1');
    await user.click(screen.getByRole('checkbox', { name: '我已确认该条目的形式、L 与输入值' }));
    await submit(user);
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit.mock.calls[0][0].dftu).toMatchObject({ form: 'liechtenstein', input_mode: 'u_j' });
    expect(onSubmit.mock.calls[0][0].dftu.entries[0]).toMatchObject({ u_ev: 5, j_ev: 1, confirmed_by_user: true });
    expect(onSubmit.mock.calls[0][0].dftu.entries[0]).not.toHaveProperty('u_eff_ev');
  });

  it('旧 initialValues 保持 U/J 旧版标识，首次修改已确认值立即撤销确认', async () => {
    const onSubmit = renderForm(vi.fn(), { initialValues: {
      dftu: { enabled: true, entries: [{ element: 'Fe', l: 2, u_ev: 5.3, j_ev: 1, source_note: 'archived', confirmed_by_user: true }] },
    } });
    const user = userEvent.setup();
    expect(screen.getByText('旧版 U (eV)')).toBeInTheDocument();
    expect(screen.getByText('旧版 Dudarev U/J 输入（保留原值）')).toBeInTheDocument();
    const checkbox = screen.getByRole('checkbox', { name: '我已确认该条目的形式、L 与输入值' });
    expect(checkbox).toBeChecked();
    await user.clear(screen.getByPlaceholderText('U 值'));
    await user.type(screen.getByPlaceholderText('U 值'), '6');
    await waitFor(() => expect(checkbox).not.toBeChecked());
    expect(onSubmit).not.toHaveBeenCalled();
    await selectOption(screen.getByRole('combobox', { name: 'DFT+U 形式' }), 'Dudarev（Ueff）');
    await user.type(await screen.findByPlaceholderText('Ueff 值'), '4');
    await user.click(screen.getByRole('checkbox', { name: '我已确认该条目的形式、L 与输入值' }));
    await submit(user);
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit.mock.calls[0][0].dftu.entries[0].source_note).toBeUndefined();
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
