// ============================================================
// DiagnosisResultPage — 诊断结果展示
// ============================================================

import React, { useState } from 'react';
import { useParams } from 'react-router-dom';
import { Typography, Row, Col, Spin, Card, Space, Tag } from 'antd';
import { Link } from 'react-router-dom';
import { useDiagnosis } from '../hooks/useApi';
import { ApiError } from '../api/client';
import LlmExplainPanel from '../components/diagnosis/LlmExplainPanel';
import IssueCard from '../components/diagnosis/IssueCard';
import ScfPlot from '../components/diagnosis/ScfPlot';
import MagnetizationPlot from '../components/diagnosis/MagnetizationPlot';
import ReportDownloadPanel from '../components/diagnosis/ReportDownloadPanel';
import RepairSuggestions from '../components/diagnosis/RepairSuggestions';
import ErrorAlert from '../components/common/ErrorAlert';
import EmptyState from '../components/common/EmptyState';
import StatusBadge from '../components/common/StatusBadge';
import '../components/diagnosis/scientific-diagnosis.css';

const { Text } = Typography;

const DiagnosisResultPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const { data, isLoading, isFetching, error, refetch } = useDiagnosis(id || null);
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
      <div className="diagnosis-page diagnosis-state" role="status">
        <Spin size="large" /><p>加载诊断结果...</p>
      </div>
    );
  }

  if (error) {
    const missingRecord = error instanceof ApiError && error.code === 'DIAGNOSIS_NOT_FOUND';
    return (
      <div className="diagnosis-page diagnosis-error-page">
        <header className="diagnosis-heading"><h1>诊断结果</h1></header>
        <ErrorAlert error={error} title={missingRecord ? '诊断记录不可用或已过期' : undefined} onRetry={() => refetch()} />
        {missingRecord && <p className="diagnosis-history-note">诊断记录仅在当前进程的有效期内保留，请重新上传计算目录；此页面不会恢复已过期的记录。</p>}
        <Link className="diagnosis-return-link" to="/diagnosis/upload">返回诊断上传</Link>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="diagnosis-page diagnosis-state"><EmptyState
        title="未找到诊断结果"
        description="该诊断 ID 可能不存在或已过期"
      /><Link className="diagnosis-return-link" to="/diagnosis/upload">返回诊断上传</Link></div>
    );
  }

  const { summary, issues, plots, provenance, recommended_fixes } = data;

  return (
    <div className="diagnosis-page diagnosis-result-page">
      {/* 标题 */}
      <header className="diagnosis-heading diagnosis-report-heading">
        <div><h1>诊断结果</h1><p>查看诊断问题、计算证据与修复建议。</p></div>
        <StatusBadge status={data.diagnosis_status} type="diagnosis" />
      </header>

      {/* 摘要统计 */}
      <dl className="diagnosis-summary">
        <div className="diagnosis-summary-headline"><dt>诊断状态</dt><dd>{summary.headline}</dd></div>
        <div><dt>严重问题</dt><dd className="diagnosis-severity-count">{summary.issue_count.critical + summary.issue_count.high}<span> / {Object.values(summary.issue_count).reduce((a, b) => a + b, 0)}</span></dd></div>
        <div><dt>最高严重度</dt><dd>{summary.highest_severity?.toUpperCase()}</dd></div>
        <div><dt>VASP 版本</dt><dd>{provenance.vasp_version || '未知'}</dd></div>
      </dl>

      {/* Provenance 信息 */}
      <Card size="small" className="diagnosis-provenance" style={{ marginBottom: 16 }}>
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
        <Col xs={24}>
          <ScfPlot data={plots.scf} />
        </Col>
      </Row>
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col xs={24}>
          <MagnetizationPlot
            data={plots.magnetization}
            calculationMode={provenance.calculation_mode}
          />
        </Col>
      </Row>

      {/* 修复建议始终显示权威状态，包括候选不可用或旧响应缺少状态字段时。 */}
      <RepairSuggestions
        fixes={recommended_fixes || []}
        issues={issues}
        fixAvailable={data.fix_available}
        reasonCode={data.fix_reason_code}
        reason={data.fix_reason}
        manualSteps={data.fix_manual_steps}
      />

      {/* LLM 通俗解释 / 追问 */}
      <LlmExplainPanel diagnosisId={data.diagnosis_id} />

      {/* 下载 */}
      <ReportDownloadPanel
        key={data.diagnosis_id}
        diagnosisId={data.diagnosis_id}
        reportReady={data.report.ready}
        reportUrl={data.report.download_url}
        fixAvailable={data.fix_available === true && !isFetching}
        fixReason={data.fix_reason}
        onRefresh={() => refetch()}
        refreshing={isFetching}
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
        <Card size="small" className="diagnosis-next-step" style={{ marginTop: 16 }}>
          <Text type="warning">
            ⚠ {data.next_step.reason}
          </Text>
        </Card>
      )}
    </div>
  );
};

export default DiagnosisResultPage;
