import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { Modal } from "antd";
import { useState } from "react";
import AiSettingsPage from "./AiSettingsPage";

const mocks = vi.hoisted(() => ({
  save: vi.fn().mockResolvedValue({}),
  test: vi.fn().mockResolvedValue({ ok: true, message: "测试成功" }),
  secret: vi.fn().mockResolvedValue({}),
  refetch: vi.fn().mockImplementation(() => Promise.resolve({ data: mocks.data })),
  secretError: null as Error | null,
  data: { settings: {
    max_jobs: 1, poll_interval_seconds: 60,
    auto_approve_kinds: ["copy_inputs", "generate_kpoints", "hpc_upload"],
    allow_potcar_assembly: true, allow_script_deploy: true,
    submit_script_template: "/legacy/run.sh",
    llm: { base_url: "https://example.invalid/v1", model: "demo", provider: "openai" },
    ssh: { name: "demo", host: "example.invalid", port: 2222, username: "user",
      known_hosts_path: "/trusted/hosts", identity_file: "/trusted/original_key" },
  } },
}));

vi.mock("../hooks/useApi", () => ({
  useAiSettings: () => {
    const [data, setData] = useState(mocks.data);
    return {
      data,
      refetch: async () => {
        const result = await mocks.refetch();
        if (result?.data) setData(result.data);
        return result;
      },
    };
  },
  useAiSettingsSave: () => ({ mutateAsync: mocks.save }),
  useAiSettingsTest: () => ({ mutateAsync: mocks.test, isPending: false, variables: undefined }),
  useAiSecretStatus: () => ({ data: mocks.secretError ? undefined : { secrets: { llm: false, mp: false, ssh: false } }, error: mocks.secretError, refetch: mocks.refetch }),
  useAiSecretUpdate: () => ({ mutateAsync: mocks.secret }),
}));

beforeEach(() => {
  mocks.refetch.mockImplementation(() => Promise.resolve({ data: mocks.data }));
  mocks.save.mockClear();
  mocks.test.mockClear();
});

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
  // 模拟第一次保存已写入服务端；第二次清空时应继续走正常保存而不是冲突分支。
  mocks.refetch.mockResolvedValue({ data: { settings: {
    ...mocks.data.settings,
    ssh: { ...mocks.data.settings.ssh, identity_file: "/trusted/demo_key" },
  } } });
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

it("旧服务即使返回扩展设置字段也不展示开关，保存时不回传这些字段", async () => {
  mocks.save.mockClear();
  mocks.refetch.mockReset().mockResolvedValue({ data: mocks.data });
  const user = userEvent.setup();
  render(<MemoryRouter><AiSettingsPage /></MemoryRouter>);

  expect(await screen.findByRole("textbox", { name: "SSH 密钥文件路径" })).toBeInTheDocument();
  expect(screen.queryByText("免批范围（可选）")).not.toBeInTheDocument();
  expect(screen.queryByRole("switch", { name: /复制已登记输入|KPOINTS 网格|上传已登记文件/ })).not.toBeInTheDocument();
  expect(screen.queryByRole("switch", { name: /POTCAR|模板脚本/ })).not.toBeInTheDocument();
  expect(screen.queryByPlaceholderText(/templates\/run\.sh/)).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "保存设置" }));

  await waitFor(() => expect(mocks.save).toHaveBeenCalled());
  const payload = mocks.save.mock.calls[0][0];
  for (const field of ["auto_copy_inputs", "auto_generate_kpoints", "auto_hpc_upload", "auto_approve_kinds", "allow_potcar_assembly", "allow_script_deploy", "submit_script_template"]) {
    expect(payload).not.toHaveProperty(field);
  }
});

it("保存前无法读取最新设置时不写入", async () => {
  mocks.save.mockClear();
  mocks.refetch.mockReset().mockResolvedValue({ isError: true, data: undefined });
  const user = userEvent.setup();
  render(<MemoryRouter><AiSettingsPage /></MemoryRouter>);
  await user.click(await screen.findByRole("button", { name: "保存设置" }));
  await waitFor(() => expect(mocks.save).not.toHaveBeenCalled());
});

it("连接测试只针对已保存配置，存在未保存修改时先提示保存", async () => {
  mocks.test.mockClear();
  const user = userEvent.setup();
  render(<MemoryRouter><AiSettingsPage /></MemoryRouter>);
  await user.click(await screen.findByRole("button", { name: "测试 LLM（已保存配置）" }));
  expect(await screen.findByText("测试成功")).toBeInTheDocument();
  mocks.test.mockClear();
  const model = await screen.findByPlaceholderText("gpt-4o");
  await user.clear(model);
  await user.type(model, "changed-model");
  expect(screen.queryByText("测试成功")).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "测试 LLM（已保存配置）" }));
  expect(await screen.findByText("当前表单有未保存修改。请先点击“保存设置”，再测试已保存配置。")).toBeInTheDocument();
  expect(mocks.test).not.toHaveBeenCalled();
});

it("最大作业数和轮询间隔执行范围校验", async () => {
  mocks.save.mockClear();
  const user = userEvent.setup();
  render(<MemoryRouter><AiSettingsPage /></MemoryRouter>);
  const maxJobs = await screen.findByRole("spinbutton", { name: "最大作业数" });
  const interval = screen.getByRole("spinbutton", { name: "监控轮询间隔（秒）" });
  fireEvent.change(maxJobs, { target: { value: "0" } });
  fireEvent.change(interval, { target: { value: "9" } });
  await user.click(screen.getByRole("button", { name: "保存设置" }));
  expect(await screen.findByText("最大作业数必须是至少为 1 的整数")).toBeInTheDocument();
  expect(await screen.findByText("轮询间隔必须是 10–3600 秒的整数")).toBeInTheDocument();
  expect(mocks.save).not.toHaveBeenCalled();
});
