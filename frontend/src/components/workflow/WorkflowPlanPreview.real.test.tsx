import { fireEvent, render, screen } from "@testing-library/react";
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
  it("renders a real selectable and draggable node without a minimap", () => {
    render(<WorkflowPlanPreview steps={[step("真实 ReactFlow 节点")] } dependencies={dependencies} />);

    const node = document.querySelector(".react-flow__node");
    expect(node).toBeTruthy();
    expect(screen.queryByTestId("minimap")).not.toBeInTheDocument();
    expect(node).toHaveClass("selectable", "draggable");
    fireEvent.pointerDown(node!, { button: 0, clientX: 100, clientY: 100 });
    fireEvent.pointerUp(node!, { button: 0, clientX: 100, clientY: 100 });
  });

  it("renders long labels in a narrow viewport without adding a minimap", () => {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 390 });
    render(<WorkflowPlanPreview steps={[step("这是一个需要在窄屏中换行的超长真实节点名称")] } dependencies={dependencies} />);

    expect(screen.getByText("这是一个需要在窄屏中换行的超长真实节点名称")).toBeInTheDocument();
    expect(screen.queryByTestId("minimap")).not.toBeInTheDocument();
  });
});
