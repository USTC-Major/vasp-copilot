import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { useState } from "react";
import AiSettingsPage from "./AiSettingsPage";

vi.mock("../api/plotPreferences", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/plotPreferences")>();
  return { ...actual, plotPreferencesApi: { get: vi.fn().mockResolvedValue({ preferences: actual.defaultPlotPreferences(), presets: actual.PLOT_PALETTES }), save: vi.fn() } };
});

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
  mocks.secret.mockClear();
});

// Context Modal is owned by React and uses Testing Library's normal unmount cleanup.

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
  // The conflict dialog retains the original refresh/cancel decision.
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
  expect(maxJobs).toHaveAttribute("aria-invalid", "true");
  expect(maxJobs).toHaveAccessibleDescription(/至少为 1。 最大作业数必须是至少为 1 的整数/);
  expect(interval).toHaveAttribute("aria-invalid", "true");
  expect(interval).toHaveAccessibleDescription(/默认 60 秒。 轮询间隔必须是 10–3600 秒的整数/);
  expect(mocks.save).not.toHaveBeenCalled();
});

it("字段说明关联各自控件，默认值说明不覆盖已保存的轮询值", async () => {
  const previousInterval = mocks.data.settings.poll_interval_seconds;
  mocks.data.settings.poll_interval_seconds = 120;
  try {
    render(<MemoryRouter><AiSettingsPage /></MemoryRouter>);
    const interval = await screen.findByLabelText("监控轮询间隔（秒）");
    expect(interval).toHaveValue(120);
    expect(interval).toHaveAccessibleDescription("影响已提交作业的状态查询频率；范围 10–3600 秒，默认 60 秒。");
    expect(screen.getByLabelText("最大作业数")).toHaveAccessibleDescription("本软件提交时参考该超算账号排队和运行中的作业数量，并按此上限限制新提交。至少为 1。");
    const mp = screen.getByRole("group", { name: "MP API Key" });
    expect(mp).toHaveAccessibleDescription("用于 Materials Project 材料搜索与结构导入；密钥可替换或清除。");
    expect(within(mp).getByLabelText("输入新的密钥以整体替换")).toHaveValue("");
    expect(screen.getByLabelText("known_hosts 路径")).toHaveAccessibleDescription(/SSH 仅信任系统或指定 known_hosts/);
    expect(screen.getByLabelText("SSH 密钥文件路径")).toHaveAccessibleDescription(/填写密钥路径时仅使用该密钥，不回退密码/);
    expect(screen.getByLabelText("调度平台")).toHaveAccessibleDescription(/按实际平台选择/);
    expect(screen.getByRole("switch", { name: "深度思考" })).toHaveAccessibleDescription(/是否支持以接入模型\/网关为准/);
  } finally {
    mocks.data.settings.poll_interval_seconds = previousInterval;
  }
});

it('saves secret replacements through the dedicated endpoint and leaves blank credentials unchanged', async () => {
  render(<MemoryRouter><AiSettingsPage /></MemoryRouter>);
  const llm = within(await screen.findByRole('group', { name: 'API Key' })).getByLabelText('输入新的密钥以整体替换');
  const ssh = within(screen.getByRole('group', { name: '密码' })).getByLabelText('输入新的密钥以整体替换');
  const mp = within(screen.getByRole('group', { name: 'MP API Key' })).getByLabelText('输入新的密钥以整体替换');
  expect(llm).toHaveValue('');
  expect(ssh).toHaveValue('');
  expect(mp).toHaveValue('');
  fireEvent.change(llm, { target: { value: 'synthetic-llm-key' } });
  fireEvent.change(ssh, { target: { value: 'synthetic-ssh-password' } });
  await userEvent.click(screen.getByRole('button', { name: '保存设置' }));
  await waitFor(() => expect(mocks.secret.mock.calls).toEqual([
    [{ kind: 'llm', action: 'replace', value: 'synthetic-llm-key' }],
    [{ kind: 'ssh', action: 'replace', value: 'synthetic-ssh-password' }],
  ]));
  const patch = mocks.save.mock.calls[0][0];
  expect(patch).not.toHaveProperty('llm_api_key');
  expect(patch).not.toHaveProperty('mp_api_key');
  expect(patch).not.toHaveProperty('ssh_password');
  await waitFor(() => expect(llm).toHaveValue(''));
  expect(ssh).toHaveValue('');
  expect(mp).toHaveValue('');
});
