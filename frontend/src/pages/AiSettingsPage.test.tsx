import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import AiSettingsPage from "./AiSettingsPage";

const mocks = vi.hoisted(() => ({
  save: vi.fn().mockResolvedValue({}),
  secret: vi.fn().mockResolvedValue({}),
  refetch: vi.fn(),
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
  useAiSecretStatus: () => ({ refetch: mocks.refetch }),
  useAiSecretUpdate: () => ({ mutateAsync: mocks.secret }),
}));

it("回显并保存本机密钥路径，不调用密钥内容替换接口；可清空回到密码认证", async () => {
  const user = userEvent.setup();
  render(<AiSettingsPage />);
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
  render(<AiSettingsPage />);
  expect(await screen.findByText("标准 Slurm（sbatch / squeue）")).toBeInTheDocument();
  await user.click(screen.getByRole("combobox", { name: "调度平台" }));
  await user.click(await screen.findByText("ParaCloud 云超算（cbatch / cqueue）"));
  await user.click(screen.getByRole("button", { name: "保存设置" }));
  await waitFor(() => expect(mocks.save).toHaveBeenCalledWith(expect.objectContaining({
    scheduler_backend: "paracloud",
  })));
});
