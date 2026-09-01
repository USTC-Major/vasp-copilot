// ============================================================
// AiInspectPanel — 第 3 步：上传结果目录 -> AI 质检 -> 报告
// ============================================================

import React from 'react';
import { Card, Button, Space, Tag, Typography, Empty } from 'antd';
import { BugOutlined, ExperimentOutlined } from '@ant-design/icons';
import DiagnosisUploadPanel from '../diagnosis/DiagnosisUploadPanel';
import LlmExplainPanel from '../diagnosis/LlmExplainPanel';
import type { DetectedRun } from '../../types/generated-api';
import type { AiJobRecord } from '../../types/ai';
import { AI_JOB_STATUS_MAP } from '../../types/ai';

const { Text } = Typography;

interface AiInspectPanelProps {
  job: AiJobRecord;
  inspecting: boolean;
  onInspect: (diagnosisId: string) => void;
}

const AiInspectPanel: React.FC<AiInspectPanelProps> = ({ job, inspecting, onInspect }) => {
  const [diagnosisId, setDiagnosisId] = React.useState<string | null>(null);
  const [detected, setDetected] = React.useState<DetectedRun | null>(null);

  return (
    <div>
      <Card
        title={<Space><ExperimentOutlined style={{ color: '#34c759' }} /> 第 3 步：结果质检</Space>}
        size="small"
        style={{ borderRadius: 16, borderColor: '#34c759', marginBottom: 16 }}
      >
        <Text type="secondary">把超算跑完的结果目录打包成 zip 上传，AI 会自动识别并检查。 </Text>
        <div style={{ marginTop: 12 }}>
          <DiagnosisUploadPanel onDetected={(id, det) => { setDiagnosisId(id); setDetected(det); }} />
        </div>
      </Card>

      {diagnosisId && detected && !inspecting && !job.diagnosis_id && (
        <Card size="small" style={{ borderRadius: 16, marginTop: 16, background: '#f5f9ff', borderColor: '#34c759' }}>
          <Space direction="vertical" style={{ width: '100%' }}>
            <Space>
              <Tag color="green">诊断 {diagnosisId}</Tag>
              <Text type="secondary">已检测 {detected.files.length} 个文件 · 根目录 {detected.root}</Text>
            </Space>
            <Button type="primary" size="large" icon={<BugOutlined />} onClick={() => onInspect(diagnosisId)}>
              交给 AI 质检并出报告
            </Button>
          </Space>
        </Card>
      )}

      {job.diagnosis_id && (
        <Card size="small" style={{ borderRadius: 16, marginTop: 16 }}>
          <Space direction="vertical" size="small" style={{ width: '100%' }}>
            <Space>
              <Tag color={AI_JOB_STATUS_MAP[job.status].color}>{AI_JOB_STATUS_MAP[job.status].label}</Tag>
              <Tag color="green">已绑定结果：{job.diagnosis_id}</Tag>
            </Space>
            {job.report_summary?.report_ready && <Text>报告已就绪，可在下方查看/下载。</Text>}
            {!job.report_summary?.report_ready && <Text type="secondary">质检报告暂未生成。</Text>}
          </Space>
        </Card>
      )}

      {!diagnosisId && !job.diagnosis_id && (
        <Empty description="等结果目录上传完成后即可开始质检" />
      )}

      {diagnosisId && (
        <div style={{ marginTop: 16 }}>
          <LlmExplainPanel diagnosisId={diagnosisId} />
        </div>
      )}
    </div>
  );
};

export default AiInspectPanel;
