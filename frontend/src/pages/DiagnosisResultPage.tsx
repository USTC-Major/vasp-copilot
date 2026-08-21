// ============================================================
// DiagnosisResultPage — 诊断结果展示
// ============================================================

import React, { useState } from 'react';
import { useParams } from 'react-router-dom';
import { Typography, Row, Col, Spin, Card, Statistic, Space, Tag } from 'antd';
import {
  BugOutlined,
} from '@ant-design/icons';
import { useDiagnosis } from '../hooks/useApi';
import LlmExplainPanel from '../components/diagnosis/LlmExplainPanel';
import IssueCard from '../components/diagnosis/IssueCard';
import ScfPlot from '../components/diagnosis/ScfPlot';
import MagnetizationPlot from '../components/diagnosis/MagnetizationPlot';
import ReportDownloadPanel from '../components/diagnosis/ReportDownloadPanel';
import ErrorAlert from '../components/common/ErrorAlert';
import EmptyState from '../components/common/EmptyState';
import StatusBadge from '../components/common/StatusBadge';

const { Title, Text } = Typography;

const DiagnosisResultPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const { data, isLoading, error, refetch } = useDiagnosis(id || null);
  const [selectedFixes, setSelectedFixes] = useState<Set<string>>(new Set());

  const handleSelectIssue = (issueId: string, selected: boolean) => {
    setSelectedFixes((prev) => {
      const next = new Set(prev);
      if (selected) next.add(issueId);
      else next.delete(issueId);
      return next;
    });
  };

  if (isLoading) {
    return (
      <div style={{ textAlign: 'center', padding: 100 }}>
        <Spin size="large" tip="加载诊断结果..." />
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ maxWidth: 600, margin: '0 auto', padding: '24px 16px' }}>
        <ErrorAlert error={error} onRetry={() => refetch()} />
      </div>
    );
  }

  if (!data) {
    return (
      <EmptyState
        title="未找到诊断结果"
        description="该诊断 ID 可能不存在或已过期"
      />
    );
  }

  const { summary, issues, plots, provenance, recommended_fixes } = data;

  return (
    <div style={{ maxWidth: 1200, margin: '0 auto', padding: '24px 16px' }}>
      {/* 标题 */}
      <Title level={3}>
        <BugOutlined style={{ marginRight: 8 }} />
        诊断结果
        <StatusBadge status={data.diagnosis_status} type="diagnosis" />
      </Title>

      {/* 摘要统计 */}
      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
        <Col xs={12} sm={6}>
          <Card>
            <Statistic title="诊断状态" value={summary.headline} valueStyle={{ fontSize: 16 }} />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card>
            <Statistic
              title="严重问题"
              value={summary.issue_count.critical + summary.issue_count.high}
              valueStyle={{ color: '#ff4d4f' }}
              suffix={`/ ${Object.values(summary.issue_count).reduce((a, b) => a + b, 0)}`}
            />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card>
            <Statistic title="最高严重度" value={summary.highest_severity?.toUpperCase()} valueStyle={{ fontSize: 16, color: '#ff4d4f' }} />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card>
            <Statistic
              title="VASP 版本"
              value={provenance.vasp_version || '未知'}
              valueStyle={{ fontSize: 14 }}
            />
          </Card>
        </Col>
      </Row>

      {/* Provenance 信息 */}
      <Card size="small" style={{ marginBottom: 16 }}>
        <Space wrap>
          <Text type="secondary">规则版本: {provenance.rule_set_version}</Text>
          <Text type="secondary">Recipe Pack: {provenance.recipe_pack_version}</Text>
          <Text type="secondary">解析器: {provenance.parser_version}</Text>
          <Text type="secondary">模式: {provenance.mode}</Text>
          {provenance.calculation_mode.is_spin_polarized && <Tag color="blue">自旋极化</Tag>}
          {provenance.calculation_mode.is_dftu && <Tag color="orange">DFT+U</Tag>}
          {provenance.calculation_mode.is_soc && <Tag color="volcano">SOC</Tag>}
        </Space>
      </Card>

      {/* 问题列表 */}
      <Card title={`诊断问题 (${issues.length})`} style={{ marginBottom: 16 }}>
        {issues.length === 0 ? (
          <EmptyState title="未发现问题" description="当前计算目录未检测到已知问题" />
        ) : (
          issues.map((issue) => (
            <IssueCard
              key={issue.issue_id}
              issue={issue}
              selected={selectedFixes.has(issue.issue_id)}
              onSelect={handleSelectIssue}
            />
          ))
        )}
      </Card>

      {/* 图表 */}
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col xs={24} lg={14}>
          <ScfPlot data={plots.scf} />
        </Col>
        <Col xs={24} lg={10}>
          <MagnetizationPlot
            data={plots.magnetization}
            calculationMode={provenance.calculation_mode}
          />
        </Col>
      </Row>

      {/* 修复建议 */}
      {recommended_fixes.length > 0 && (
        <Card title="修复建议" style={{ marginBottom: 16 }}>
          {recommended_fixes.map((fix) => (
            <Card key={fix.fix_id} size="small" style={{ marginBottom: 8 }}>
              <Space>
                <Tag color={fix.safe_to_generate ? 'green' : 'orange'}>
                  {fix.safe_to_generate ? '可生成' : '需确认'}
                </Tag>
                <Text>{fix.target_file}</Text>
                <Tag>{fix.strategy}</Tag>
                {fix.requires_user_confirmation && <Tag color="warning">需确认</Tag>}
              </Space>
              <pre style={{
                background: '#f6f8fa',
                padding: 8,
                marginTop: 8,
                fontSize: 12,
                borderRadius: 4,
              }}>
                {fix.diff}
              </pre>
              {fix.warnings.map((w, i) => (
                <Text key={i} type="warning" style={{ fontSize: 12 }}>{w}</Text>
              ))}
            </Card>
          ))}
        </Card>
      )}

      {/* LLM 通俗解释 / 追问 */}
      <LlmExplainPanel diagnosisId={data.diagnosis_id} />

      {/* 下载 */}
      <ReportDownloadPanel
        diagnosisId={data.diagnosis_id}
        reportReady={data.report.ready}
        reportUrl={data.report.download_url}
        fixAvailable={recommended_fixes.length > 0}
      />

      {/* Missing Evidence */}
      {data.missing_evidence.length > 0 && (
        <Card title="证据不足项目" size="small" style={{ marginTop: 16 }}>
          <Text type="secondary">
            有 {data.missing_evidence.length} 个项目因证据不足无法判断
          </Text>
        </Card>
      )}

      {/* Next Step */}
      {!data.next_step.allowed && (
        <Card size="small" style={{ marginTop: 16, background: '#fff7e6' }}>
          <Text type="warning">
            ⚠ {data.next_step.reason}
          </Text>
        </Card>
      )}
    </div>
  );
};

export default DiagnosisResultPage;