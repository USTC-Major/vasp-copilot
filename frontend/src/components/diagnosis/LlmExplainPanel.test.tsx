import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api/client";
import LlmExplainPanel from "./LlmExplainPanel";

const mocks = vi.hoisted(() => ({
  explain: vi.fn(),
  explainError: null as ApiError | null,
  capabilitiesError: false,
  capabilities: { mode: "ai", enabled: true, configured: true, available: true, reason_code: "AI_MODE_DIAGNOSIS_READY" },
}));

vi.mock("../../hooks/useApi", () => ({
  useDiagnosisCapabilities: () => ({ data: mocks.capabilities, isLoading: false, isError: mocks.capabilitiesError, refetch: vi.fn() }),
  useDiagnosisExplain: () => ({
    mutateAsync: mocks.explain,
    isPending: false,
    isError: Boolean(mocks.explainError),
    error: mocks.explainError,
    reset: vi.fn(),
  }),
}));

describe("LlmExplainPanel", () => {
  beforeEach(() => {
    mocks.explain.mockReset();
    mocks.explainError = null;
    mocks.capabilitiesError = false;
  });

  it("does not display an answer when the explain response is not ok", async () => {
    mocks.explain.mockResolvedValue({
      mode: "ai", ok: false, diagnosis_id: "diag_1", answer: "不应显示", evidence_source: "toolbox_diagnosis", context_truncated: false,
    });
    const user = userEvent.setup();
    render(<MemoryRouter><LlmExplainPanel diagnosisId="diag_1" /></MemoryRouter>);

    await user.click(screen.getByRole("button", { name: "一键通俗解释" }));

    expect(screen.queryByText("不应显示")).not.toBeInTheDocument();
    expect(await screen.findByText("解释接口未返回可展示的回答")).toBeInTheDocument();
  });

  it("shows a distinct fake-provider state and links to model settings", () => {
    mocks.capabilities = { mode: "ai", enabled: true, configured: false, available: false, reason_code: "AI_MODE_DIAGNOSIS_FAKE_PROVIDER" };
    render(<MemoryRouter><LlmExplainPanel diagnosisId="diag_1" /></MemoryRouter>);

    expect(screen.getByText("离线假模型")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "打开模型设置" })).toHaveAttribute("href", "/ai/settings");
    mocks.capabilities = { mode: "ai", enabled: true, configured: true, available: true, reason_code: "AI_MODE_DIAGNOSIS_READY" };
  });

  it("distinguishes an incomplete diagnosis from a model failure", () => {
    mocks.explainError = new ApiError("AI_MODE_DIAGNOSIS_NOT_READY", "请先运行诊断后再解释", false, 409);
    render(<MemoryRouter><LlmExplainPanel diagnosisId="diag_1" /></MemoryRouter>);

    expect(screen.getByText("诊断未完成")).toBeInTheDocument();
    expect(screen.getByText("请先运行诊断后再解释。")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "重试" })).not.toBeInTheDocument();
  });

  it("retries the default one-click question after failure", async () => {
    mocks.explain.mockRejectedValue(new Error("synthetic failure"));
    const user = userEvent.setup();
    render(<MemoryRouter><LlmExplainPanel diagnosisId="diag_1" /></MemoryRouter>);

    await user.click(screen.getByRole("button", { name: "一键通俗解释" }));
    await screen.findByText("synthetic failure");
    expect(mocks.explain).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: /重\s*试/ }));
    await waitFor(() => expect(mocks.explain).toHaveBeenCalledTimes(2));
    expect(mocks.explain.mock.calls[1][0].question).toContain("通俗的语言解释");
  });

  it("retries the last submitted manual question, not an edited draft", async () => {
    mocks.explain.mockRejectedValue(new Error("synthetic failure"));
    const user = userEvent.setup();
    render(<MemoryRouter><LlmExplainPanel diagnosisId="diag_1" /></MemoryRouter>);

    const input = screen.getByRole("textbox");
    await user.type(input, "为什么没有收敛？");
    await user.click(screen.getByRole("button", { name: /发送/ }));
    await screen.findByText("synthetic failure");
    await user.clear(input);
    await user.type(input, "这是新的草稿");
    await user.click(screen.getByRole("button", { name: /重\s*试/ }));
    await waitFor(() => expect(mocks.explain).toHaveBeenCalledTimes(2));
    expect(mocks.explain.mock.calls[1][0].question).toBe("为什么没有收敛？");
  });

  it("distinguishes a capabilities read failure from model unavailability", () => {
    mocks.capabilitiesError = true;
    mocks.capabilities = { mode: "ai", enabled: false, configured: false, available: false, reason_code: "AI_MODE_DIAGNOSIS_STATUS_READ_FAILED" };
    render(<MemoryRouter><LlmExplainPanel diagnosisId="diag_1" /></MemoryRouter>);

    expect(screen.getByText("状态读取失败")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("无法读取 AI 能力状态");
  });

});
