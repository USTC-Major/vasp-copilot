// ============================================================
// LlmExplainPanel — LLM 通俗解释 / 追问
// ============================================================

import React, { useRef, useState } from 'react';
import { Card, Input, Button, Space, Tag, Typography, Spin, Alert } from 'antd';
import { RobotOutlined, SendOutlined } from '@ant-design/icons';
import { Link } from 'react-router-dom';
import { ApiError } from '../../api/client';
import { useDiagnosisCapabilities, useDiagnosisExplain } from '../../hooks/useApi';

const { Text, Paragraph } = Typography;
const { TextArea } = Input;

const DEFAULT_QUESTION = '请用通俗的语言解释这份诊断报告的主要问题是什么、为什么发生，以及接下来建议怎么做。';

interface LlmExplainPanelProps {
  diagnosisId: string;
}

const LlmExplainPanel: React.FC<LlmExplainPanelProps> = ({ diagnosisId }) => {
  const [question, setQuestion] = useState('');
  const [localError, setLocalError] = useState<string | null>(null);
  const [answers, setAnswers] = useState<{ q: string; a: string; truncated?: boolean }[]>([]);
  const lastSubmittedQuestion = useRef<string | null>(null);
  const explainMutation = useDiagnosisExplain();
  const capabilitiesQuery = useDiagnosisCapabilities();
  const capabilities = capabilitiesQuery.data;
  const enabled = !capabilitiesQuery.isError && Boolean(capabilities?.available);
  const reasonCode = capabilities?.reason_code;
  const explainFailed = Boolean(localError || explainMutation.isError);
  const explainErrorCode = explainMutation.error instanceof ApiError
    ? explainMutation.error.code
    : undefined;
  const diagnosisNotReady = explainErrorCode === 'AI_MODE_DIAGNOSIS_NOT_READY';
  const diagnosisNotFound = explainErrorCode === 'AI_MODE_DIAGNOSIS_NOT_FOUND';

  const status = capabilitiesQuery.isLoading
    ? { color: 'processing', label: '检查中' }
    : capabilitiesQuery.isError
      ? { color: 'red', label: '状态读取失败' }
      : explainFailed
        ? diagnosisNotReady
          ? { color: 'orange', label: '诊断未完成' }
          : diagnosisNotFound
            ? { color: 'red', label: '诊断不存在' }
            : { color: 'red', label: '模型调用失败' }
      : capabilities?.available
        ? { color: 'green', label: '可用' }
        : { color: 'orange', label: reasonCode === 'AI_MODE_DIAGNOSIS_DISABLED' ? '已关闭' : reasonCode === 'AI_MODE_DIAGNOSIS_FAKE_PROVIDER' ? '离线假模型' : reasonCode === 'AI_MODE_DIAGNOSIS_PROVIDER_INVALID' ? '配置无效' : '未配置' };

  const unavailableMessage = capabilitiesQuery.isError
    ? '无法读取 AI 能力状态；原有诊断报告仍可查看，请重试读取状态。'
    : reasonCode === 'AI_MODE_DIAGNOSIS_DISABLED'
      ? '智能模式已关闭；原有诊断报告仍可查看。'
      : reasonCode === 'AI_MODE_DIAGNOSIS_FAKE_PROVIDER'
        ? '当前为离线假模型，不能用于诊断解释；请在统一智能模式设置中配置真实模型。'
        : reasonCode === 'AI_MODE_DIAGNOSIS_PROVIDER_INVALID'
          ? 'LLM provider 配置无效，请到模型设置修正后重试。'
          : '尚未配置可用的统一智能模式模型，请先到模型设置完成配置。';

  const handleAsk = async (text?: string) => {
    const q = (text ?? question).trim();
    if (!q || explainMutation.isPending) return;
    lastSubmittedQuestion.current = q;
    setLocalError(null);
    explainMutation.reset();
    try {
      const answer = await explainMutation.mutateAsync({ diagnosisId, question: q });
      if (answer.ok !== true || !answer.answer?.trim()) {
        throw new Error('解释接口未返回可展示的回答');
      }
      setAnswers((prev) => [...prev, { q, a: answer.answer, truncated: answer.context_truncated }]);
      setQuestion('');
    } catch (error) {
      setLocalError(error instanceof Error ? error.message : '调用解释接口失败');
    }
  };

  return (
    <Card
      title={
        <Space wrap>
          <RobotOutlined />
          LLM 通俗解释 / 追问
          <Tag color={status.color}>{status.label}</Tag>
        </Space>
      }
      style={{ marginTop: 16 }} className="diagnosis-explain-panel"
    >
      {!enabled && !capabilitiesQuery.isLoading && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message={unavailableMessage}
          action={capabilitiesQuery.isError
            ? <Button size="small" onClick={() => void capabilitiesQuery.refetch()}>重试</Button>
            : reasonCode === 'AI_MODE_DIAGNOSIS_DISABLED' || reasonCode === 'AI_MODE_DIAGNOSIS_NOT_CONFIGURED' || reasonCode === 'AI_MODE_DIAGNOSIS_FAKE_PROVIDER' || reasonCode === 'AI_MODE_DIAGNOSIS_PROVIDER_INVALID'
              ? <Link to="/ai/settings">打开模型设置</Link>
              : undefined}
        />
      )}

      <Space direction="vertical" style={{ width: '100%' }} size="middle">
        {answers.map((item, i) => (
          <div key={i} style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
            <Paragraph style={{ marginBottom: 4 }}>
              <Text strong>问：</Text>
              <Text>{item.q}</Text>
            </Paragraph>
            <div className="diagnosis-explain-answer" style={{ background: 'var(--diag-field, #f6f8fa)', padding: 12, borderRadius: 6 }}>
              <Text>{item.a}</Text>
            </div>
            {item.truncated && <Text type="warning" style={{ fontSize: 12 }}>解释上下文已裁剪，回答可能未包含全部诊断证据。</Text>}
          </div>
        ))}

        {explainMutation.isPending && (
          <div>
            <Spin size="small" /> <Text type="secondary">正在调用大模型解释……</Text>
          </div>
        )}

        {explainFailed && (
          <Alert
            type="error"
            showIcon
            message={diagnosisNotReady
              ? '请先运行诊断后再解释。'
              : diagnosisNotFound
                ? '诊断记录不存在，请返回诊断列表重新选择。'
                : localError || explainMutation.error?.message || '调用解释接口失败'}
            action={!diagnosisNotReady && !diagnosisNotFound
              ? <Button size="small" onClick={() => void handleAsk(lastSubmittedQuestion.current ?? question)}>重试</Button>
              : undefined}
          />
        )}

        <Space.Compact style={{ width: '100%' }}>
          <TextArea
            aria-label="诊断解释问题"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder={DEFAULT_QUESTION}
            autoSize={{ minRows: 2, maxRows: 5 }}
            onPressEnter={(e) => {
              if (!e.shiftKey) {
                e.preventDefault();
                handleAsk();
              }
            }}
          />
          <Button
            type="primary"
            icon={<SendOutlined />}
            loading={explainMutation.isPending}
            disabled={!enabled}
            onClick={() => handleAsk()}
          >
            发送
          </Button>
        </Space.Compact>

        <Button onClick={() => handleAsk(question || DEFAULT_QUESTION)} disabled={explainMutation.isPending || !enabled}>
          一键通俗解释
        </Button>
      </Space>
    </Card>
  );
};

export default LlmExplainPanel;
