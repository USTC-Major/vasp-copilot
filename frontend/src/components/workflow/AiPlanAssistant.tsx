// ============================================================
// AiPlanAssistant — LLM 驱动的工作流规划（可选入口）
// 用户用自然语言描述需求 -> LLM 解析 -> 展示计划/解释 -> 强制确认
// ============================================================

import React, { useState } from 'react';
import { Card, Input, Button, Space, Typography, Tag, Alert, Checkbox, Spin, Divider, Empty } from 'antd';
import {
  RobotOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import { workflowsApi } from '../../api/client';
import WorkflowPlanPreview from './WorkflowPlanPreview';
import type { WorkflowStep, FileInheritancePlan, RecipeComposition, WorkflowConfirmation } from '../../types/generated-api';

const { Text, Paragraph } = Typography;

export interface AiPlanExplanations {
  enabled: boolean;
  degraded: boolean;
  user_needs: string;
  requested_tasks: string[];
  explanations: { step: string; label: string; explanation: string }[];
}

export interface AiPlanAssistantResult {
  request_id: string;
  workflow_id: string;
  workflow_status: string;
  steps: WorkflowStep[];
  file_inheritance_plan: FileInheritancePlan;
  recipe_compositions: RecipeComposition[];
  confirmations: WorkflowConfirmation[];
  conflicts: unknown[];
  warnings: unknown[];
  needs_confirmation: boolean;
  ai: AiPlanExplanations;
}

interface AiPlanAssistantProps {
  structureId: string;
  formula?: string;
  elements?: string[];
  onAccepted: (result: AiPlanAssistantResult) => void;
}

const TASK_LABELS: Record<string, string> = {
  relax: '结构优化 (relax)',
  static: '静态计算 (static)',
  dos: '态密度 (DOS)',
  band: '能带 (band)',
};

const AiPlanAssistant: React.FC<AiPlanAssistantProps> = ({
  structureId,
  formula,
  elements,
  onAccepted,
}) => {
  const [inputText, setInputText] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<AiPlanAssistantResult | null>(null);
  const [confirmed, setConfirmed] = useState(false);

  const handlePlan = async () => {
    const text = inputText.trim();
    if (!text) return;
    setLoading(true);
    setError(null);
    setResult(null);
    setConfirmed(false);
    try {
      const resp = await workflowsApi.planFromNl({ structure_id: structureId, goals: [text] });
      setResult(resp as unknown as AiPlanAssistantResult);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'AI 规划失败');
    } finally {
      setLoading(false);
    }
  };

  return (
    <Card
      title={<span><RobotOutlined style={{ color: '#0071e3' }} /> AI 规划工作流（可选）</span>}
      bordered={false}
    >
      <Space align="start" direction="vertical" style={{ width: '100%' }}>
        {formula && (
          <Text type="secondary">
            结构：<Text strong>{formula}</Text>
            {elements && elements.length > 0 && ` · ${elements.join(', ')}`}
          </Text>
        )}
        <Input.TextArea
          value={inputText}
          onChange={(e) => setInputText(e.target.value)}
          placeholder="用自然语言描述你的计算需求，例如：对氧化铁做结构优化，再算态密度，磁性开启，ENCUT 提到 620"
          autoSize={{ minRows: 3, maxRows: 5 }}
          style={{ fontSize: 14 }}
        />
        <Space>
          <Button
            type="primary"
            icon={!result ? <ThunderboltOutlined /> : <ReloadOutlined />}
            loading={loading}
            disabled={!inputText.trim()}
            onClick={handlePlan}
          >
            {!result ? 'AI 帮我规划' : '按新描述重新规划'}
          </Button>
          {result && (
            <Text type="secondary" style={{ fontSize: 12 }}>
              可直接修改上方描述后点“重新规划”微调计划
            </Text>
          )}
        </Space>
        {error && <Alert type="error" message="调用 AI 规划失败" description={error} showIcon />}
      </Space>

      {loading && (
        <div style={{ textAlign: 'center', padding: '24px 0', marginTop: 8 }}>
          <Spin tip="AI 正在解析你的需求并生成计划…" />
        </div>
      )}

      {result && !loading && (
        <div style={{ marginTop: 16 }}>
          <Alert
            type={result.ai.degraded ? 'warning' : 'success'}
            showIcon
            message={result.ai.degraded ? 'AI 尚未启用，已按规则生成默认计划（可手动微调）' : 'AI 已解析你的需求并生成以下计划'}
            description={result.ai.degraded ? '未检测到可用的大模型：请点击右上角「模型设置」启用 LLM 并填写接口地址/密钥/模型名后保存，再点「重新规划」即可真正由 AI 生成计划。当前展示的是确定性默认计划。' : ('需求：' + (result.ai.user_needs || ''))}
            style={{ marginBottom: 16 }}
          />

          <Divider titlePlacement="left"><Text strong>识别到的任务</Text></Divider>
          <Space wrap>
            {(result.ai.requested_tasks || []).map((task) => (
              <Tag key={task} color="blue" style={{ borderRadius: 999, padding: '2px 12px' }}>
                {TASK_LABELS[task] || task}
              </Tag>
            ))}
            {!result.ai.requested_tasks?.length && <Text type="secondary">无明确任务，使用默认</Text>}
          </Space>

          <Divider titlePlacement="left"><Text strong>AI 逐步中文解释</Text></Divider>
          {result.ai.explanations && result.ai.explanations.length > 0 ? (
            <div>
              {result.ai.explanations.map((ex, idx) => (
                <Card key={`${ex.step}-${idx}`} size="small" style={{ borderRadius: 14, marginBottom: 12 }}>
                  <Space wrap>
                    <Tag color="purple" style={{ fontWeight: 600 }}>{ex.step}</Tag>
                    {ex.label && <Text strong>{ex.label}</Text>}
                  </Space>
                  <Paragraph style={{ margin: '8px 0 0', fontSize: 13, color: '#3a3a3c' }}>
                    {ex.explanation}
                  </Paragraph>
                </Card>
              ))}
            </div>
          ) : (
            <Empty description="AI 未返回分步说明，可点击上方按钮重新规划" />
          )}

          <Divider titlePlacement="left"><Text strong>工作流 DAG</Text></Divider>
          <WorkflowPlanPreview
            steps={result.steps}
            dependencies={result.file_inheritance_plan.dependencies}
          />

          <Divider titlePlacement="left"><Text strong>确认计划</Text></Divider>
          <Card size="small" style={{ borderRadius: 14, borderColor: '#0071e3' }}>
            <Space direction="vertical" size="middle" style={{ width: '100%' }}>
              <Checkbox checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)}>
                <Text strong>我已知悉并确认此计划</Text>
              </Checkbox>
              <Text type="secondary" style={{ fontSize: 12 }}>
                AI 生成的计划仅作建议，最终参数与文件由你确认后生成。未勾选确认将无法进入下一步。
              </Text>
              <Button
                type="primary"
                size="large"
                icon={<SafetyCertificateOutlined />}
                disabled={!confirmed}
                onClick={() => onAccepted(result)}
                style={{ alignSelf: 'flex-end' }}
              >
                确认此计划并继续
              </Button>
            </Space>
          </Card>
        </div>
      )}
    </Card>
  );
};

export default AiPlanAssistant;