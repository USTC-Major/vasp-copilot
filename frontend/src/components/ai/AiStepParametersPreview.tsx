// ============================================================
// AiStepParametersPreview — 步骤参数摘要（INCAR 关键参数）
// ============================================================

import React from 'react';
import { Card, Tag, Space, Typography, Descriptions, Empty, Tooltip } from 'antd';
import type { WorkflowStep } from '../../types/generated-api';

const { Text } = Typography;

interface AiStepParametersPreviewProps {
  steps: WorkflowStep[];
}

function formatValue(v: unknown): string {
  if (Array.isArray(v)) return v.map((x) => (typeof x === 'number' ? Number(x.toFixed(6)) : x)).join(', ');
  if (typeof v === 'object' && v !== null) return JSON.stringify(v);
  return String(v);
}

const AiStepParametersPreview: React.FC<AiStepParametersPreviewProps> = ({ steps }) => {
  const withParams = steps.filter((s) => s.parameters && Object.keys(s.parameters).length > 0);
  if (withParams.length === 0) {
    return <Empty description="暂无可预览的步骤参数" />;
  }

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      {withParams.map((step) => {
        const entries = Object.entries(step.parameters) as [string, unknown][];
        return (
          <Card key={step.step_id} size="small" style={{ borderRadius: 14, marginBottom: 0 }}>
            <Space wrap>
              <Tag color="purple" style={{ fontWeight: 600 }}>{step.step_id}</Tag>
              <Text strong>{step.label}</Text>
              <Tag color="blue">{step.task}</Tag>
              <Text type="secondary" style={{ fontSize: 12 }}>{step.directory}</Text>
            </Space>
            <Descriptions size="small" column={{ xs: 1, sm: 2, md: 3 }} style={{ marginTop: 10 }}>
              {entries.map(([k, v]) => (
                <Descriptions.Item key={k} label={<Tooltip title={k}><span>{k}</span></Tooltip>}>
                  {formatValue(v)}
                </Descriptions.Item>
              ))}
            </Descriptions>
          </Card>
        );
      })}
      <Text type="secondary" style={{ fontSize: 12 }}>
        仅展示自动生成的 INCAR 关键参数摘要；KPOINTS 网格由系统按任务自动生成，确认后可编辑。
      </Text>
    </Space>
  );
};

export default AiStepParametersPreview;
