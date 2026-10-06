import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import WorkflowPlanPreview from "./WorkflowPlanPreview";
import type { FileInheritanceDependency, WorkflowStep } from "../../types/generated-api";

const step = (label: string): WorkflowStep => ({
  step_id: "relax-1",
  task: "relax" as WorkflowStep["task"],
  label,
  directory: "01-relax",
  depends_on: [],
  runnable: true,
  blocked_by: [],
  requires_runtime_outputs: [],
  produces: ["CONTCAR", "vasprun.xml"],
  parameters: {},
});

const dependencies: FileInheritanceDependency[] = [];

describe("WorkflowPlanPreview with the real ReactFlow implementation", () => {
  it("renders a real selectable and draggable node without a minimap", async () => {
    render(<WorkflowPlanPreview steps={[step("真实 ReactFlow 节点")] } dependencies={dependencies} />);

    const node = document.querySelector(".react-flow__node");
    expect(node).toBeTruthy();
    expect(screen.queryByTestId("minimap")).not.toBeInTheDocument();
    expect(node).toHaveClass("selectable", "draggable");
    const pointerInit = { bubbles: true, cancelable: true, button: 0, buttons: 1, pointerType: "mouse", clientX: 100, clientY: 100 };
    node!.dispatchEvent(new PointerEvent("pointerdown", { ...pointerInit }));
    node!.dispatchEvent(new PointerEvent("pointerup", { ...pointerInit, buttons: 0 }));
    fireEvent.click(node!);
    // 接上 onNodesChange 后，点击产生的 select change 必须被回写落地。
    await waitFor(() => expect(node).toHaveClass("selected"), { timeout: 3000 });
  });

  it("renders long labels in a narrow viewport without adding a minimap", () => {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 390 });
    render(<WorkflowPlanPreview steps={[step("这是一个需要在窄屏中换行的超长真实节点名称")] } dependencies={dependencies} />);

    expect(screen.getByText("这是一个需要在窄屏中换行的超长真实节点名称")).toBeInTheDocument();
    expect(screen.queryByTestId("minimap")).not.toBeInTheDocument();
  });

  it("keeps an unresolved dependency in the full inheritance list without rendering an invalid edge", () => {
    render(<WorkflowPlanPreview steps={[step("上游步骤")]} dependencies={[{
      dependency_id: 'missing-endpoint', from_step_id: 'relax-1', to_step_id: 'unknown-step',
      source_file: 'CONTCAR', target_file: 'POSCAR', required: true, satisfied: false,
      requires_upstream_diagnosis_pass: false,
    }]} />);
    expect(screen.getByText('unknown-step/POSCAR')).toBeInTheDocument();
    expect(document.querySelector('.react-flow__edge')).toBeNull();
  });
});
