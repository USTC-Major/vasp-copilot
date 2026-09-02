// ============================================================
// AiPlanReview — 计划门：AI 展示规划（含 DAG 预览），用户批准后进入生成
// ============================================================

import React from 'react';
import { Card, Alert, Tag, Space, Button, Typography, Divider, Empty, Badge, List } from 'antd';
import { SafetyCertificateOutlined, CloseOutlined, RobotOutlined } from '@ant-design/icons';
import WorkflowPlanPreview from '../workflow/WorkflowPlanPreview';
import AiStepParametersPreview from './AiStepParametersPreview';
import type { AiJobDetail } from '../../types/ai';
import { AI_JOB_STATUS_MAP } from '../../types/ai';

const { Text, Paragraph } = Typography;

const TASK_LABELS: Record<string, string> = {
  relax: '结构优化 (Relax)',
  static: '静态计算 (Static)',
  dos: '态密度 (DOS)',
  band: '能带 (Band)',
};

interface AiPlanReviewProps {
  job: AiJobDetail;
  pending: boolean;
  onConfirm: () => void;
  onReject: () => void;
}

const AiPlanReview: React.FC<AiPlanReviewProps> = ({ job, pending, onConfirm, onReject }) => {
  const plan = job.plan;
  const preview = job.workflow_preview;
  const requestedTasks = plan?.requested_tasks ?? [];
  const explanations = plan?.step_explanations ?? [];
  const hasDag = Boolean(preview?.steps?.length && preview.steps[0]?.step_id);

  return (
    <div>
      <Alert
        type={job.degraded ? 'warning' : 'success'}
        showIcon
        icon={<RobotOutlined />}
        message={job.degraded ? 'AI 未启用（降级为确定性默认计划）' : 'AI 已解析你的需求并生成计划'}
        description={
          job.degraded
            ? '未配置可用的大模型，当前展示的是规则生成的默认计划；你也可以在右上角「模型设置」启用 LLM 后重新创建作业获取 AI 专属规划。'
            : (plan?.user_needs ? `你的需求：${plan.user_needs}` : '请核对下面的自动规划是否符合你的计算目标。')
        }
      />

      <Divider titlePlacement="left"><Text strong>识别到的计算任务</Text></Divider>
      {requestedTasks.length > 0 ? (
        <Space wrap>
          {requestedTasks.map((task) => (
            <Tag key={task} color="blue" style={{ borderRadius: 999, padding: '2px 14px', fontSize: 13 }}>
              {TASK_LABELS[task] || task}
            </Tag>
          ))}
        </Space>
      ) : (
        <Empty description="未识别到明确任务，将使用默认流程" />
      )}

      <Divider titlePlacement="left"><Text strong>AI 逐步中文解释</Text></Divider>
      {explanations.length > 0 ? (
        explanations.map((ex, idx) => (
          <Card key={`${ex.step}-${idx}`} size="small" style={{ borderRadius: 14, marginBottom: 10 }}>
            <Space wrap>
              <Tag color="purple" style={{ fontWeight: 600 }}>{ex.step}</Tag>
              {ex.label && <Text strong>{ex.label}</Text>}
            </Space>
            <Paragraph style={{ margin: '8px 0 0', fontSize: 13, color: '#3a3a3c' }}>
              {ex.explanation}
            </Paragraph>
          </Card>
        ))
      ) : (
        <Empty description="AI 未返回分步说明" />
      )}

      <Divider titlePlacement="left"><Text strong>工作流 DAG 预览</Text></Divider>
      {hasDag ? (
        <WorkflowPlanPreview
          steps={preview!.steps as never}
          dependencies={(preview!.file_inheritance_plan?.dependencies ?? []) as never}
        />
      ) : (
        <Empty description="暂无可预览的步骤 DAG" />
      )}

      {preview?.warnings && preview.warnings.length > 0 && (
        <List
          size="small"
          header={<Text strong>预检警告</Text>}
          style={{ marginTop: 12 }}
          dataSource={preview.warnings}
          renderItem={(w) => (
            <List.Item><Text type="warning">{w.message}</Text></List.Item>
          )}
        />
      )}

      <Divider titlePlacement="left"><Text strong>步骤参数预览（INCAR 关键参数）</Text></Divider>
      {hasDag ? (
        <AiStepParametersPreview steps={preview!.steps as never} />
      ) : (
        <Empty description="暂无可预览的步骤参数" />
      )}

      <Divider titlePlacement="left"><Text strong>确认门</Text></Divider>
      <Card size="small" style={{ borderRadius: 14, borderColor: '#0071e3', background: '#f5f9ff' }}>
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          <Space>
            <Badge status="processing" />
            <Text strong>AI 已就绪，等待你的批准</Text>
            <Tag color={AI_JOB_STATUS_MAP.planned.color}>{AI_JOB_STATUS_MAP.planned.label}</Tag>
          </Space>
          <Text type="secondary" style={{ fontSize: 13 }}>
            批准后 AI 将自动生成全部输入文件并打包为待提交 zip；你也可以否决并调整描述后重新创建。
          </Text>
          <Space>
            <Button type="primary" size="large" icon={<SafetyCertificateOutlined />} loading={pending} onClick={onConfirm}>
              批准计划并开始生成
            </Button>
            <Button size="large" icon={<CloseOutlined />} disabled={pending} onClick={onReject}>
              否决此计划
            </Button>
          </Space>
        </Space>
      </Card>
    </div>
  );
};

export default AiPlanReview;
