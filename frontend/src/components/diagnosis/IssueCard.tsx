// ============================================================
// IssueCard — 诊断问题卡片，证据展开，修复建议
// ============================================================

import React, { useState } from 'react';
import { Card, Tag, Collapse, List, Button, Space, Typography, Progress } from 'antd';
import {
  ExclamationCircleOutlined, WarningOutlined, InfoCircleOutlined,
  FileTextOutlined, BugOutlined, CheckCircleOutlined,
} from '@ant-design/icons';
import type { DiagnosisIssue } from '../../types/generated-api';
import { getSeverityColor } from '../../utils/formatters';

const { Text, Paragraph } = Typography;

interface IssueCardProps {
  issue: DiagnosisIssue;
  selected: boolean;
  onSelect: (issueId: string, selected: boolean) => void;
}

const SEVERITY_ICON: Record<string, React.ReactNode> = {
  critical: <BugOutlined style={{ color: '#ff4d4f', fontSize: 18 }} />,
  high: <ExclamationCircleOutlined style={{ color: '#ff4d4f', fontSize: 18 }} />,
  medium: <WarningOutlined style={{ color: '#faad14', fontSize: 18 }} />,
  low: <InfoCircleOutlined style={{ color: '#1677ff', fontSize: 18 }} />,
  info: <InfoCircleOutlined style={{ color: '#999', fontSize: 18 }} />,
};

const IssueCard: React.FC<IssueCardProps> = ({ issue, selected, onSelect }) => {
  const [expanded, setExpanded] = useState(
    issue.severity === 'critical' || issue.severity === 'high'
  );

  const severityColor = getSeverityColor(issue.severity);
  const confidencePercent = Math.round((issue.confidence || 0) * 100);

  return (
    <Card
      size="small"
      style={{
        marginBottom: 12,
        borderLeft: `4px solid ${
          issue.severity === 'critical' ? '#ff4d4f' :
          issue.severity === 'high' ? '#ff7a45' :
          issue.severity === 'medium' ? '#faad14' :
          '#1677ff'
        }`,
      }}
      title={
        <Space>
          {SEVERITY_ICON[issue.severity] || SEVERITY_ICON.info}
          <span>{issue.title}</span>
          <Tag color={severityColor}>{issue.severity.toUpperCase()}</Tag>
          <Tag>{issue.category}</Tag>
          {issue.blocking && <Tag color="error">阻塞</Tag>}
          {issue.auto_fixable && <Tag color="green">可自动修复</Tag>}
        </Space>
      }
      extra={
        <Button
          type={selected ? 'primary' : 'default'}
          size="small"
          onClick={() => onSelect(issue.issue_id, !selected)}
          icon={selected ? <CheckCircleOutlined /> : undefined}
        >
          {selected ? '已选中修复' : '选中修复'}
        </Button>
      }
    >
      <Paragraph>{issue.summary}</Paragraph>

      {/* 置信度 */}
      <div style={{ marginBottom: 8 }}>
        <Text type="secondary" style={{ fontSize: 12 }}>
          置信度: {confidencePercent}%
        </Text>
        <Progress
          percent={confidencePercent}
          size="small"
          strokeColor={confidencePercent > 80 ? '#52c41a' : confidencePercent > 50 ? '#faad14' : '#ff4d4f'}
          showInfo={false}
          style={{ width: 120, marginLeft: 8, display: 'inline-block' }}
        />
      </div>

      <Button
        type="link"
        size="small"
        onClick={() => setExpanded(!expanded)}
      >
        {expanded ? '收起详情' : '展开详情'}
      </Button>

      {expanded && (
        <div style={{ marginTop: 12 }}>
          {/* 证据 */}
          <Collapse
            size="small"
            items={[{
              key: 'evidence',
              label: <span><FileTextOutlined /> 证据 ({issue.evidence.length} 条)</span>,
              children: (
                <List
                  size="small"
                  dataSource={issue.evidence}
                  renderItem={(ev) => (
                    <List.Item>
                      <div style={{ width: '100%' }}>
                        <Space>
                          <Tag>{ev.file}</Tag>
                          {ev.line != null && <Tag>行 {ev.line}</Tag>}
                        </Space>
                        <div>{ev.message}</div>
                        {ev.excerpt && (
                          <pre style={{
                            background: '#f6f8fa',
                            padding: 4,
                            fontSize: 11,
                            marginTop: 4,
                            borderRadius: 3,
                            border: '1px solid #e8e8e8',
                            whiteSpace: 'pre-wrap',
                            wordBreak: 'break-all',
                          }}>
                            {ev.excerpt}
                          </pre>
                        )}
                      </div>
                    </List.Item>
                  )}
                  locale={{ emptyText: '无证据' }}
                />
              ),
            }]}
            style={{ marginBottom: 8 }}
          />

          {/* 可能原因 */}
          {issue.possible_causes.length > 0 && (
            <div style={{ marginBottom: 8 }}>
              <Text strong>可能原因: </Text>
              {issue.possible_causes.map((c, i) => (
                <Tag key={i}>{c}</Tag>
              ))}
            </div>
          )}

          {/* 修复建议 */}
          {issue.recommendations.length > 0 && (
            <Collapse
              size="small"
              items={[{
                key: 'recommendations',
                label: <span>修复建议 ({issue.recommendations.length} 条)</span>,
                children: (
                  <List
                    size="small"
                    dataSource={issue.recommendations}
                    renderItem={(rec) => (
                      <List.Item>
                        <div style={{ width: '100%' }}>
                          <Space>
                            <Text strong>{rec.action}</Text>
                            {rec.target && <Tag>{rec.target}</Tag>}
                            {rec.parameter && <Tag color="blue">{rec.parameter}</Tag>}
                          </Space>
                          {rec.old_value != null && rec.new_value != null && (
                            <div style={{ marginTop: 4 }}>
                              <Text code delete>{String(rec.old_value)}</Text>
                              <span style={{ margin: '0 8px' }}>→</span>
                              <Text code style={{ color: '#52c41a' }}>{String(rec.new_value)}</Text>
                            </div>
                          )}
                          <div style={{ fontSize: 12, color: '#666', marginTop: 4 }}>
                            {rec.rationale}
                          </div>
                          {rec.requires_user_confirmation && (
                            <Tag color="warning" style={{ marginTop: 4 }}>需要用户确认</Tag>
                          )}
                        </div>
                      </List.Item>
                    )}
                    locale={{ emptyText: '暂无修复建议' }}
                  />
                ),
              }]}
            />
          )}

          {/* Tags */}
          <div style={{ marginTop: 8 }}>
            {issue.tags.map((tag) => (
              <Tag key={tag} color="default">{tag}</Tag>
            ))}
            <Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>
              Rule: {issue.rule_id}
            </Text>
          </div>
        </div>
      )}
    </Card>
  );
};

export default IssueCard;