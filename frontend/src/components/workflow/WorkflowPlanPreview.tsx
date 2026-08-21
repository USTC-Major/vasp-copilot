// ============================================================
// WorkflowPlanPreview — React Flow DAG 步骤图
// ============================================================

import React, { useMemo } from 'react';
import { Card, Tag, Space, Typography, Tooltip } from 'antd';
import { ApartmentOutlined, MinusCircleOutlined } from '@ant-design/icons';
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  MarkerType,
  type Node,
  type Edge,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import type { WorkflowStep, FileInheritanceDependency } from '../../types/generated-api';

const { Text } = Typography;

interface WorkflowPlanPreviewProps {
  steps: WorkflowStep[];
  dependencies: FileInheritanceDependency[];
}

const TASK_COLORS: Record<string, string> = {
  relax: '#1677ff',
  static: '#52c41a',
  dos: '#722ed1',
  band: '#fa8c16',
};

const WorkflowPlanPreview: React.FC<WorkflowPlanPreviewProps> = ({ steps, dependencies }) => {
  const nodes: Node[] = useMemo(() => {
    return steps.map((step, idx) => ({
      id: step.step_id,
      type: 'default',
      position: { x: 50 + idx * 260, y: 50 },
      data: {
        label: (
          <div style={{
            padding: '8px 12px',
            border: `2px solid ${step.runnable ? TASK_COLORS[step.task] || '#999' : '#d9d9d9'}`,
            borderRadius: 8,
            background: step.runnable ? '#fff' : '#f5f5f5',
            minWidth: 180,
            opacity: step.runnable ? 1 : 0.6,
          }}>
            <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 4 }}>
              {step.label}
              {!step.runnable && (
                <MinusCircleOutlined style={{ color: '#999', marginLeft: 6 }} />
              )}
            </div>
            <Tag color={TASK_COLORS[step.task]}>{step.task}</Tag>
            <div style={{ fontSize: 11, color: '#999', marginTop: 4 }}>
              {step.directory}
            </div>
            {step.blocked_by.length > 0 && (
              <div style={{ marginTop: 4 }}>
                {step.blocked_by.map((b) => (
                  <Tooltip key={b} title="阻塞原因">
                    <Tag color="error" style={{ fontSize: 10 }}>{b}</Tag>
                  </Tooltip>
                ))}
              </div>
            )}
            {step.runnable && (
              <div style={{ marginTop: 4 }}>
                <Tag color="success" style={{ fontSize: 10 }}>可提交</Tag>
              </div>
            )}
            {step.produces.length > 0 && (
              <div style={{ marginTop: 4, fontSize: 10, color: '#666' }}>
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
      },
    }));
  }, [steps]);

  const edges: Edge[] = useMemo(() => {
    return dependencies.map((dep) => ({
      id: dep.dependency_id,
      source: dep.from_step_id,
      target: dep.to_step_id,
      label: `${dep.source_file} → ${dep.target_file}`,
      type: 'smoothstep',
      animated: !dep.satisfied,
      style: {
        stroke: dep.satisfied ? '#52c41a' : dep.required ? '#fa8c16' : '#999',
        strokeDasharray: dep.satisfied ? undefined : '5,5',
      },
      markerEnd: { type: MarkerType.ArrowClosed },
      labelStyle: { fontSize: 10, fontWeight: 500 },
      labelBgStyle: { fill: '#fff', fillOpacity: 0.9 },
      labelBgPadding: [4, 2] as [number, number],
    }));
  }, [dependencies]);

  return (
    <Card
      title={<span><ApartmentOutlined /> 工作流步骤计划 (DAG)</span>}
      bordered={false}
      extra={
        <Space>
          <Tag color="success">可运行</Tag>
          <Tag color="default">等待上游</Tag>
          <Tag color="error">阻塞</Tag>
        </Space>
      }
    >
      <div style={{ width: '100%', height: 300, border: '1px solid #e8e8e8', borderRadius: 8 }}>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          fitView
          attributionPosition="bottom-left"
        >
          <Background />
          <Controls showInteractive={false} />
          <MiniMap />
        </ReactFlow>
      </div>

      <div style={{ marginTop: 16 }}>
        <Text strong>文件继承计划: </Text>
        <div style={{ marginTop: 8 }}>
          {dependencies.map((dep) => (
            <div key={dep.dependency_id} style={{ marginBottom: 4, fontSize: 13 }}>
              <Space>
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