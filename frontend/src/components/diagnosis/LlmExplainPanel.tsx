// ============================================================
// LlmExplainPanel — LLM 通俗解释 / 追问
// ============================================================

import React, { useState } from 'react';
import { Card, Input, Button, Space, Tag, Typography, Spin, Alert } from 'antd';
import { RobotOutlined, SendOutlined } from '@ant-design/icons';
import { useDiagnosisExplain, useLlmConfig } from '../../hooks/useApi';

const { Text, Paragraph } = Typography;
const { TextArea } = Input;

const DEFAULT_QUESTION = '请用通俗的语言解释这份诊断报告的主要问题是什么、为什么发生，以及接下来建议怎么做。';

interface LlmExplainPanelProps {
  diagnosisId: string;
}

const LlmExplainPanel: React.FC<LlmExplainPanelProps> = ({ diagnosisId }) => {
  const [question, setQuestion] = useState('');
  const [answers, setAnswers] = useState<{ q: string; a: string; degraded?: boolean }[]>([]);
  const explainMutation = useDiagnosisExplain();
  const { data: llmConfig } = useLlmConfig(true);
  const enabled = Boolean(llmConfig?.usable);

  const handleAsk = async (text?: string) => {
    const q = (text ?? question).trim();
    if (!q || explainMutation.isPending) return;
    const answer = await explainMutation.mutateAsync({ diagnosisId, question: q });
    setAnswers((prev) => [...prev, { q, a: answer.answer, degraded: answer.degraded }]);
    setQuestion('');
  };

  return (
    <Card
      title={
        <Space>
          <RobotOutlined />
          LLM 通俗解释 / 追问
          {enabled ? (
            <Tag color="green">已启用</Tag>
          ) : (
            <Tag color="orange">未启用</Tag>
          )}
        </Space>
      }
      style={{ marginTop: 16 }}
    >
      {!enabled && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message="后端尚未启用 LLM：点击右上角「模型设置」填写接口地址 / Key / 模型名（本地服务 Key 填任意非空值即可），或在 backend/.env 配置后重启后端。"
        />
      )}

      <Space direction="vertical" style={{ width: '100%' }} size="middle">
        {answers.map((item, i) => (
          <div key={i} style={{ whiteSpace: 'pre-wrap' }}>
            <Paragraph style={{ marginBottom: 4 }}>
              <Text strong>问：</Text>
              <Text>{item.q}</Text>
            </Paragraph>
            <div style={{ background: '#f6f8fa', padding: 12, borderRadius: 6 }}>
              {item.degraded ? (
                <Text type="secondary">{item.a}</Text>
              ) : (
                <Text>{item.a}</Text>
              )}
            </div>
          </div>
        ))}

        {explainMutation.isPending && (
          <div>
            <Spin size="small" /> <Text type="secondary">正在调用大模型解释……</Text>
          </div>
        )}

        {explainMutation.isError && (
          <Alert type="error" showIcon message={explainMutation.error?.message || '调用解释接口失败'} />
        )}

        <Space.Compact style={{ width: '100%' }}>
          <TextArea
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
            onClick={() => handleAsk()}
          >
            发送
          </Button>
        </Space.Compact>

        <Button onClick={() => handleAsk(question || DEFAULT_QUESTION)} disabled={explainMutation.isPending}>
          一键通俗解释
        </Button>
      </Space>
    </Card>
  );
};

export default LlmExplainPanel;