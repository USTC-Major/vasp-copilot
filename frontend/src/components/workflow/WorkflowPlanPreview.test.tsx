import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import WorkflowPlanPreview from "./WorkflowPlanPreview";
import type { FileInheritanceDependency, WorkflowStep } from "../../types/generated-api";

vi.mock("@xyflow/react", () => ({
  ReactFlow: ({
    nodes,
    children,
    fitView,
    fitViewOptions,
  }: {
    nodes: Array<{ data: { label: React.ReactNode }; style?: { width?: number } }>;
    children: React.ReactNode;
    fitView?: boolean;
    fitViewOptions?: { padding?: number };
  }) => (
    <div
      data-testid="react-flow"
      data-fit-view={String(fitView)}
      data-fit-padding={String(fitViewOptions?.padding)}
      data-node-width={String(nodes[0]?.style?.width)}
    >
      {nodes[0]?.data.label}
      {children}
    </div>
  ),
  Background: () => <div data-testid="background" />,
  Controls: () => <div data-testid="controls" />,
  MiniMap: () => <div data-testid="minimap" />,
  MarkerType: { ArrowClosed: "arrowclosed" },
  applyNodeChanges: (_changes: unknown[], nodes: unknown[]) => nodes,
  applyEdgeChanges: (_changes: unknown[], edges: unknown[]) => edges,
}));

const steps: WorkflowStep[] = [{
  step_id: "relax-1",
  task: "relax" as WorkflowStep["task"],
  label: "这是一个需要在窄窗口中换行的超长工作流节点名称",
  directory: "01-relax",
  depends_on: [],
  runnable: true,
  blocked_by: [],
  requires_runtime_outputs: [],
  produces: ["CONTCAR", "vasprun.xml"],
  parameters: {},
}];

const dependencies: FileInheritanceDependency[] = [];

describe("WorkflowPlanPreview", () => {
  it("uses one measured node width, fit view, and no minimap", () => {
    render(<WorkflowPlanPreview steps={steps} dependencies={dependencies} />);

    expect(screen.getByTestId("react-flow")).toHaveAttribute("data-node-width", "220");
    expect(screen.getByTestId("react-flow")).toHaveAttribute("data-fit-view", "true");
    expect(screen.getByTestId("react-flow")).toHaveAttribute("data-fit-padding", "0.2");
    expect(screen.queryByTestId("minimap")).not.toBeInTheDocument();
    expect(screen.getByText("这是一个需要在窄窗口中换行的超长工作流节点名称")).toBeInTheDocument();
  });
});
