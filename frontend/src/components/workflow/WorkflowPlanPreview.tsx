// ============================================================
// WorkflowPlanPreview — React Flow DAG 步骤图
// ============================================================

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Card, Tag, Space, Typography, Tooltip } from 'antd';
import { ApartmentOutlined, MinusCircleOutlined } from '@ant-design/icons';
import {
  ReactFlow,
  Background,
  Controls,
  MarkerType,
  applyNodeChanges,
  applyEdgeChanges,
  BaseEdge,
  EdgeLabelRenderer,
  Position,
  type EdgeProps,
  type Node,
  type Edge,
  type NodeChange,
  type EdgeChange,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import type { WorkflowStep, FileInheritanceDependency } from '../../types/generated-api';
import { layoutDagTracks, type DagTrack } from './dagLayout';

const { Text } = Typography;

interface WorkflowPlanPreviewProps {
  steps: WorkflowStep[];
  dependencies: FileInheritanceDependency[];
}

const TASK_COLORS: Record<string, string> = {
  relax: '#75A7EE',
  static: '#7FAFAD',
  dos: '#A6A6CD',
  band: '#D9B983',
};

const NODE_WIDTH = 220;
type FileEdge = Edge<{ track: DagTrack; label: string }>;
function FileInheritanceEdge({ sourceX, sourceY, targetX, targetY, markerEnd, style, data }: EdgeProps<FileEdge>) {
  if (!data?.track) return null;
  const { track, label } = data;
  const railY = track.y + track.height / 2;
  const path = `M ${sourceX} ${sourceY} L ${track.sourceEscape} ${sourceY} L ${track.sourceEscape} ${railY} L ${track.targetEscape} ${railY} L ${track.targetEscape} ${targetY} L ${targetX} ${targetY}`;
  return <><BaseEdge path={path} markerEnd={markerEnd} style={style} />
    <EdgeLabelRenderer><div className="wf-dag-label" style={{ width: track.width, height: track.height, transform: `translate(${track.x}px, ${track.y}px)` }}>{label}</div></EdgeLabelRenderer>
  </>;
}
const edgeTypes = { inheritance: FileInheritanceEdge };

const WorkflowPlanPreview: React.FC<WorkflowPlanPreviewProps> = ({ steps, dependencies }) => {
  const derivedNodes: Node[] = useMemo(() => {
    return steps.map((step, idx) => ({
      id: step.step_id,
      type: 'default',
      position: { x: 50 + idx * 260, y: 50 },
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
      data: {
        label: (
          <div className="wf-dag-node" style={{ borderTopColor: TASK_COLORS[step.task] }}>
            <div className="wf-dag-node-title">
              {step.label}
              {!step.runnable && (
                <MinusCircleOutlined style={{ color: '#999', marginLeft: 6 }} />
              )}
            </div>
            <Tag color={TASK_COLORS[step.task]}>{step.task}</Tag>
            <div className="wf-dag-node-meta">
              {step.directory}
            </div>
            {step.blocked_by.length > 0 && (
              <div style={{ marginTop: 4 }}>
                {step.blocked_by.map((b) => (
                  <Tooltip key={b} title="阻塞原因">
                    <Tag color="error">{b}</Tag>
                  </Tooltip>
                ))}
              </div>
            )}
            {step.runnable && (
              <div style={{ marginTop: 4 }}>
                <Tag color="success">可提交</Tag>
              </div>
            )}
            {step.produces.length > 0 && (
              <div className="wf-dag-node-meta">
                产出: {step.produces.join(', ')}
              </div>
            )}
          </div>
        ),
      },
      style: {
        background: 'transparent',
        border: 'none',
        padding: 0,
        width: NODE_WIDTH,
      },
    }));
  }, [steps]);

  const derivedEdges: Edge[] = useMemo(() => {
    return dependencies.map((dep) => ({
      id: dep.dependency_id,
      source: dep.from_step_id,
      target: dep.to_step_id,
      label: `${dep.source_file} → ${dep.target_file}`,
      type: 'inheritance',
      animated: !dep.satisfied,
      style: {
        stroke: dep.satisfied ? '#7FAFAD' : dep.required ? '#D9B983' : '#7F8C9F',
        strokeDasharray: dep.satisfied ? undefined : '5,5',
      },
      markerEnd: { type: MarkerType.ArrowClosed },
    }));
  }, [dependencies]);

  // 受控节点/边状态：plan 变化时重置为派生结果，plan 之外把
  // React Flow 产生的选中/拖动等 change 回写，交互才能落地。
  const [nodes, setNodes] = useState<Node[]>(derivedNodes);
  const [edges, setEdges] = useState<Edge[]>(derivedEdges);

  useEffect(() => {
    setNodes(derivedNodes);
  }, [derivedNodes]);

  useEffect(() => {
    setEdges(derivedEdges);
  }, [derivedEdges]);

  const onNodesChange = useCallback(
    (changes: NodeChange[]) => setNodes((nds) => applyNodeChanges(changes, nds)),
    [],
  );
  const onEdgesChange = useCallback(
    (changes: EdgeChange[]) => setEdges((eds) => applyEdgeChanges(changes, eds)),
    [],
  );

  const tracks = useMemo(() => layoutDagTracks(
    nodes.map(node => ({ id: node.id, x: node.position.x, y: node.position.y, width: NODE_WIDTH, height: node.measured?.height ?? 160 })),
    edges.map(edge => ({ id: edge.id, source: edge.source, target: edge.target, label: String(edge.label) })),
  ), [nodes, edges]);
  const graphEdges = edges.flatMap(edge => {
    const track = tracks.find(item => item.id === edge.id);
    return track ? [{ ...edge, data: { track, label: String(edge.label) } }] : [];
  });
  // A non-interactive, transparent bounds node makes ReactFlow's native fit control
  // include the file-label rail. It does not replace the real interactive cards.
  const graphNodes = [...nodes];
  if (tracks.length) {
    const left = Math.min(...tracks.map(track => track.x));
    const top = Math.min(...tracks.map(track => track.y));
    graphNodes.push({ id: '__file-label-bounds', position: { x: left, y: top }, data: { label: null },
      selectable: false, draggable: false, connectable: false, focusable: false,
      domAttributes: { 'aria-hidden': true },
      style: { opacity: 0, pointerEvents: 'none', width: Math.max(...tracks.map(track => track.x + track.width)) - left, height: Math.max(...tracks.map(track => track.y + track.height)) - top, border: 0, padding: 0 },
    });
  }

  return (
    <Card
      title={<span><ApartmentOutlined /> 工作流步骤计划 (DAG)</span>}
      variant="borderless"
    >
      <Space wrap size={4} style={{ marginBottom: 8 }}>
        <Tag color="success">可运行</Tag>
        <Tag color="default">等待上游</Tag>
        <Tag color="error">阻塞</Tag>
      </Space>
      <div className="wf-dag-canvas">
        <ReactFlow
          nodes={graphNodes}
          edges={graphEdges}
          edgeTypes={edgeTypes}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          nodesConnectable={false}
          fitView
          fitViewOptions={{ padding: 0.2, includeHiddenNodes: false }}
          attributionPosition="bottom-left"
        >
          <Background />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>

      <div style={{ marginTop: 16 }}>
        <Text strong>文件继承计划: </Text>
        <div style={{ marginTop: 8 }}>
          {dependencies.map((dep) => (
            <div key={dep.dependency_id} style={{ marginBottom: 4, fontSize: 13 }}>
              <Space wrap>
                <Text code>{dep.from_step_id}/{dep.source_file}</Text>
                <span>→</span>
                <Text code>{dep.to_step_id}/{dep.target_file}</Text>
                {dep.required && <Tag color="orange">必需</Tag>}
                <Tag color={dep.satisfied ? 'success' : 'default'}>
                  {dep.satisfied ? '已满足' : '未满足'}
                </Tag>
                {dep.requires_upstream_diagnosis_pass && (
                  <Tag color="blue">需上游诊断通过</Tag>
                )}
              </Space>
            </div>
          ))}
        </div>
      </div>
    </Card>
  );
};

export default WorkflowPlanPreview;
