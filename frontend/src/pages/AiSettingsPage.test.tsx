import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { Modal } from "antd";
import AiSettingsPage from "./AiSettingsPage";

const mocks = vi.hoisted(() => ({
  save: vi.fn().mockResolvedValue({}),
  secret: vi.fn().mockResolvedValue({}),
  refetch: vi.fn(),
  secretError: null as Error | null,
  data: { settings: {
    max_jobs: 1, poll_interval_seconds: 60,
    llm: { base_url: "https://example.invalid/v1", model: "demo", provider: "openai" },
    ssh: { name: "demo", host: "example.invalid", port: 2222, username: "user",
      known_hosts_path: "/trusted/hosts", identity_file: "/trusted/original_key" },
  } },
}));

vi.mock("../hooks/useApi", () => ({
  useAiSettings: () => ({ data: mocks.data, refetch: mocks.refetch }),
  useAiSettingsSave: () => ({ mutateAsync: mocks.save }),
  useAiSettingsTest: () => ({ mutateAsync: vi.fn() }),
  useAiSecretStatus: () => ({ data: mocks.secretError ? undefined : { secrets: { llm: false, mp: false, ssh: false } }, error: mocks.secretError, refetch: mocks.refetch }),
  useAiSecretUpdate: () => ({ mutateAsync: mocks.secret }),
}));

// antd 的静态 Modal 不在 Testing Library 的自动清理范围内：不显式销毁会残留到
// 下一条用例，导致确认框重复、点到旧对话框的按钮。
afterEach(() => {
  Modal.destroyAll();
  // 静态 Modal 的容器不在 umount 范围内，显式清掉避免残留到下一條用例。
  document.querySelectorAll(".ant-modal-root, .ant-modal-wrap, .ant-modal-mask")
    .forEach((node) => node.remove());
});

it('does not show an editable form when credential status fails, and guides to Toolbox', async () => {
  mocks.secretError = new Error('智能服务不可达');
  try {
    render(<MemoryRouter><AiSettingsPage /></MemoryRouter>);
    expect(screen.getByText('凭据状态读取失败')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Toolbox 执行设置' })).toHaveAttribute('href', '/toolbox/settings');
    expect(screen.queryByRole('button', { name: '保存设置' })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /重\s*试/ }));
    expect(mocks.refetch).toHaveBeenCalled();
  } finally {
    mocks.secretError = null;
  }
});

it("回显并保存本机密钥路径，不调用密钥内容替换接口；可清空回到密码认证", async () => {
  const user = userEvent.setup();
  render(<MemoryRouter><AiSettingsPage /></MemoryRouter>);
  const input = await screen.findByRole("textbox", { name: "SSH 密钥文件路径" });
  expect(input).toHaveValue("/trusted/original_key");
  await user.clear(input);
  await user.type(input, "/trusted/demo_key");
  await user.click(screen.getByRole("button", { name: "保存设置" }));
  await waitFor(() => expect(mocks.save).toHaveBeenCalledWith(expect.objectContaining({
    ssh_identity_file: "/trusted/demo_key", ssh_known_hosts_path: "/trusted/hosts",
  })));
  expect(mocks.secret).not.toHaveBeenCalled();
  await user.clear(input);
  await user.click(screen.getByRole("button", { name: "保存设置" }));
  await waitFor(() => expect(mocks.save).toHaveBeenLastCalledWith(expect.objectContaining({
    ssh_identity_file: "",
  })));
});

it("默认标准Slurm，用户明确选择ParaCloud后保存调度协议", async () => {
  mocks.save.mockClear();
  const user = userEvent.setup();
  render(<MemoryRouter><AiSettingsPage /></MemoryRouter>);
  expect(await screen.findByText("标准 Slurm（sbatch / squeue）")).toBeInTheDocument();
  await user.click(screen.getByRole("combobox", { name: "调度平台" }));
  await user.click(await screen.findByText("ParaCloud 云超算（cbatch / cqueue）"));
  await user.click(screen.getByRole("button", { name: "保存设置" }));
  await waitFor(() => expect(mocks.save).toHaveBeenCalledWith(expect.objectContaining({
    scheduler_backend: "paracloud",
  })));
});

// 保存前对齐服务端：他处改过的配置不能被页面里的旧值悄悄覆盖。
it("后台配置已被改动时先提示冲突，选择刷新则不覆盖", async () => {
  mocks.save.mockClear();
  mocks.refetch.mockReset();
  mocks.refetch.mockResolvedValue({
    data: { settings: {
      ...mocks.data.settings,
      ssh: { ...mocks.data.settings.ssh, username: "demo-user@CLUSTER" },
    } },
  });
  const user = userEvent.setup();
  render(<MemoryRouter><AiSettingsPage /></MemoryRouter>);
  await user.click(await screen.findByRole("button", { name: "保存设置" }));

  expect(mocks.refetch).toHaveBeenCalledTimes(1);
  // antd 的静态 Modal 在 jsdom 下会渲染出多份节点，这里取最后一份（最新创建）即可。
  expect((await screen.findAllByText("后台配置已被修改")).length).toBeGreaterThan(0);
  const refreshButtons = await screen.findAllByRole("button", { name: "用后台值刷新" });
  refreshButtons.forEach((button) => fireEvent.click(button));

  await waitFor(() => expect(mocks.save).not.toHaveBeenCalled());
  // 页面改用后台最新值，旧用户名不会写回去
  expect(await screen.findByDisplayValue("demo-user@CLUSTER")).toBeInTheDocument();
  mocks.refetch.mockReset();
});

it("冲突时明确选择仍然覆盖，才按本页值保存", async () => {
  mocks.save.mockClear();
  mocks.refetch.mockReset();
  mocks.refetch.mockResolvedValue({
    data: { settings: {
      ...mocks.data.settings,
      llm: { ...mocks.data.settings.llm, model: "deepseek-flash" },
    } },
  });
  const user = userEvent.setup();
  render(<MemoryRouter><AiSettingsPage /></MemoryRouter>);
  // 等表单真正装载完（含 SSH 用户名）再保存，确保走的是冲突分支
  expect(await screen.findByDisplayValue("user")).toBeInTheDocument();
  await user.click(await screen.findByRole("button", { name: "保存设置" }));

  const confirmButtons = await screen.findAllByRole("button", { name: "仍然覆盖" });
  confirmButtons.forEach((button) => fireEvent.click(button));
  await waitFor(() => expect(mocks.save).toHaveBeenCalled());
  mocks.refetch.mockReset();
});

it("免批范围开关默认关闭，开启后随设置一起提交", async () => {
  mocks.save.mockClear();
  mocks.refetch.mockReset();
  const user = userEvent.setup();
  render(<MemoryRouter><AiSettingsPage /></MemoryRouter>);

  const copySwitch = await screen.findByRole("switch", { name: /复制已登记输入到作业目录/ });
  const kpointsSwitch = screen.getByRole("switch", { name: /确定性生成 KPOINTS 网格/ });
  const uploadSwitch = screen.getByRole("switch", { name: /上传已登记文件到超算工作区/ });
  expect(copySwitch).not.toBeChecked();
  expect(kpointsSwitch).not.toBeChecked();
  expect(uploadSwitch).not.toBeChecked();

  await user.click(kpointsSwitch);
  await user.click(uploadSwitch);
  await user.click(screen.getByRole("button", { name: "保存设置" }));

  await waitFor(() => expect(mocks.save).toHaveBeenCalledWith(expect.objectContaining({
    auto_approve_kinds: ["generate_kpoints", "hpc_upload"],
  })));
});

const clearModals = () => {
  Modal.destroyAll();
  document.querySelectorAll(".ant-modal-root, .ant-modal-wrap, .ant-modal-mask")
    .forEach((node) => node.remove());
};

it("POTCAR 开关默认关闭：取消免责声明不生效，确认后才生效并随设置提交", async () => {
  mocks.save.mockClear();
  mocks.refetch.mockReset();
  const user = userEvent.setup();
  render(<MemoryRouter><AiSettingsPage /></MemoryRouter>);

  const potcar = await screen.findByRole("switch", { name: /允许用 vaspkit 在超算作业目录生成 POTCAR/ });
  expect(potcar).not.toBeChecked();

  // 取消免责声明 → 开关保持关闭
  await user.click(potcar);
  expect((await screen.findAllByText(/开启 POTCAR 自动生成/)).length).toBeGreaterThan(0);
  expect((await screen.findAllByText(/使用许可与适用性由使用者负责/)).length).toBeGreaterThan(0);
  await user.click(screen.getByRole("button", { name: /取\s*消/ }));
  await waitFor(() => expect(potcar).not.toBeChecked());
  clearModals();

  // 确认后才生效
  await user.click(potcar);
  await user.click(await screen.findByRole("button", { name: "我已知悉，开启" }));
  await waitFor(() => expect(potcar).toBeChecked());

  await user.click(screen.getByRole("button", { name: "保存设置" }));
  await waitFor(() => expect(mocks.save).toHaveBeenCalledWith(expect.objectContaining({
    allow_potcar_assembly: true,
  })));
});

it("提交脚本模板路径可填写，脚本复制开关确认后才生效", async () => {
  mocks.save.mockClear();
  mocks.refetch.mockReset();
  const user = userEvent.setup();
  render(<MemoryRouter><AiSettingsPage /></MemoryRouter>);

  const script = await screen.findByRole("switch", { name: /允许 AI 把模板脚本复制到作业目录/ });
  expect(script).not.toBeChecked();
  await user.type(screen.getByPlaceholderText(/templates\/run\.sh/),
                  "/publicfs03/templates/run.sh");

  await user.click(script);
  expect((await screen.findAllByText(/允许 AI 复制提交脚本模板/)).length).toBeGreaterThan(0);
  expect((await screen.findAllByText(/逐字节复制/)).length).toBeGreaterThan(0);
  await user.click(screen.getByRole("button", { name: /开\s*启/ }));
  await waitFor(() => expect(script).toBeChecked());

  await user.click(screen.getByRole("button", { name: "保存设置" }));
  await waitFor(() => expect(mocks.save).toHaveBeenCalledWith(expect.objectContaining({
    allow_script_deploy: true,
    submit_script_template: "/publicfs03/templates/run.sh",
  })));
});
