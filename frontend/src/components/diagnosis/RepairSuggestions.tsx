import React from 'react';
import { Alert, Card, Space, Table, Tag, Typography } from 'antd';
import type { DiagnosisIssue, RecommendedFix } from '../../types/generated-api';

const { Text, Paragraph } = Typography;

interface RepairSuggestionsProps {
  fixes: RecommendedFix[];
  issues: DiagnosisIssue[];
  fixAvailable?: boolean;
  reasonCode?: string;
  reason?: string;
  manualSteps?: string[];
}

const missingInfoCodes = new Set(['missing_incar', 'missing_value']);
const manualCodes = new Set([
  'safety_rejected', 'manual_review', 'candidate_missing', 'candidate_invalid', 'generation_failed',
]);
const noChangeCodes = new Set(['no_issues', 'no_rule', 'no_changes']);

function valueLabel(value: unknown, operation: string, isNewValue: boolean): React.ReactNode {
  if (value === null || value === undefined) {
    const label = isNewValue && operation === 'remove' ? '移除' : '未设置';
    return <Text type="secondary">{label}</Text>;
  }
  if (typeof value === 'string') return value;
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

const RepairSuggestions: React.FC<RepairSuggestionsProps> = ({
  fixes,
  issues,
  fixAvailable,
  reasonCode,
  reason,
  manualSteps = [],
}) => {
  const issueTitles = new Map(issues.map((issue) => [issue.issue_id, issue.title]));
  const knownCode = typeof reasonCode === 'string' && (
    missingInfoCodes.has(reasonCode) || manualCodes.has(reasonCode) || noChangeCodes.has(reasonCode) || reasonCode === 'candidate_ready'
  );
  let title = '修复状态待核验';
  let color: 'blue' | 'orange' | 'default' = 'orange';
  let defaultDescription = '服务端修复状态缺失或无法识别。请刷新修复状态后再判断是否有候选可下载。';

  if (fixAvailable === true) {
    title = '候选修复已生成，应用前请审阅';
    color = 'blue';
    defaultDescription = '下载只会获取候选修复包，不会自动应用参数，也不代表问题已修复。请核对差异和风险后再手工处理。';
  } else if (missingInfoCodes.has(reasonCode || '')) {
    title = '需补充信息';
    defaultDescription = reasonCode === 'missing_incar'
      ? '请上传原始且非空的 INCAR 文件后重新诊断。'
      : '请核实建议参数的具体新值，再人工处理或重新诊断。';
  } else if (manualCodes.has(reasonCode || '')) {
    title = '建议人工处理';
    defaultDescription = '当前无法提供可下载候选，请按诊断建议人工核验和处理。';
  } else if (noChangeCodes.has(reasonCode || '')) {
    title = '暂无可生成的参数修改';
    color = 'default';
    defaultDescription = reasonCode === 'no_issues'
      ? '当前没有诊断问题。'
      : reasonCode === 'no_rule'
        ? '当前问题暂无可用的参数修复规则。'
        : '当前没有检测到有效的参数变化。';
  } else if (fixAvailable === false && knownCode) {
    defaultDescription = '当前没有可下载的候选修复包。';
  }

  const description = reason?.trim() || defaultDescription;
  const detailFixes = fixes.filter((fix) => fix.changes.length > 0 || Boolean(fix.diff?.trim()));
  const generalNotes = [...new Set(fixes
    .filter((fix) => fix.changes.length === 0 && !fix.diff?.trim())
    .flatMap((fix) => [fix.reason, ...(fix.warnings || [])])
    .filter((note): note is string => typeof note === 'string' && Boolean(note.trim()) && note.trim() !== description))];
  const allSteps = [...new Set([
    ...manualSteps,
    ...fixes.flatMap((fix) => fix.manual_steps || []),
  ].filter((step) => typeof step === 'string' && step.trim()))];

  return (
    <Card title="修复建议" style={{ marginBottom: 16 }} className="diagnosis-repair-panel">
      <Space orientation="vertical" size="middle" style={{ width: '100%', minWidth: 0 }}>
        <Alert
          type={color === 'blue' ? 'info' : color === 'default' ? 'info' : 'warning'}
          showIcon
          title={<strong>{title}</strong>}
          description={(
            <div>
              <div>{description}</div>
              {fixAvailable === true && !description.includes('下载只会获取候选修复包，不会自动应用参数') && (
                <div style={{ marginTop: 4 }}>
                  下载只会获取候选修复包，不会自动应用参数，也不代表问题已修复。请核对差异和风险后再手工处理。
                </div>
              )}
            </div>
          )}
        />

        {generalNotes.length > 0 && (
          <div>
            {generalNotes.map((note, index) => (
              <Alert key={`repair-note-${index}`} type="warning" showIcon title={`说明：${note}`} />
            ))}
          </div>
        )}

        {detailFixes.map((fix) => {
          const changeColumns = [
            { title: '参数', dataIndex: 'parameter', key: 'parameter', width: 120, render: (value: string) => <Text code>{value}</Text> },
            { title: '操作', dataIndex: 'operation', key: 'operation', width: 90, render: (value: string) => ({ add: '新增', replace: '修改', remove: '移除' }[value] || '待核验') },
            { title: '修改前', dataIndex: 'old_value', key: 'old_value', width: 180, render: (value: unknown, row: RecommendedFix['changes'][number]) => valueLabel(value, row.operation, false) },
            { title: '修改后', dataIndex: 'new_value', key: 'new_value', width: 180, render: (value: unknown, row: RecommendedFix['changes'][number]) => valueLabel(value, row.operation, true) },
          ];
          const issueNames = fix.issue_ids.map((issueId) => issueTitles.get(issueId) || issueId);
          const fixReason = fix.reason?.trim() || reason?.trim() || ({
            candidate_missing: '候选文件缺失或为空，请重新运行诊断生成候选。',
            candidate_invalid: '候选内容与有效参数差异不一致，请重新运行诊断生成候选。',
            safety_rejected: '安全检查未通过，请人工核验参数建议。',
            manual_review: '该建议需要人工核验，不能通过确认操作自动生成。',
            generation_failed: '候选生成失败，请重试诊断或按建议手工处理。',
          }[fix.reason_code || '']);
          const canClaimCandidate = fixAvailable === true && fix.reason_code === 'candidate_ready';

          return (
            <Card key={fix.fix_id} size="small" title={fix.fix_id}>
              <Space orientation="vertical" size="small" style={{ width: '100%', minWidth: 0 }}>
                <Space wrap>
                  <Text strong>目标文件：{fix.target_file}</Text>
                  {!canClaimCandidate && <Tag color="orange">建议修改，当前无可用候选</Tag>}
                </Space>
                <Text>关联问题：{issueNames.length ? issueNames.join('、') : '未关联具体问题'}</Text>
                {fixReason && <Paragraph style={{ marginBottom: 0 }}>{fixReason}</Paragraph>}
                {fix.changes.length > 0 && (
                  <div style={{ maxWidth: '100%', overflowX: 'auto' }}>
                    <Table
                      size="small"
                      pagination={false}
                      rowKey={(change) => `${change.parameter}-${change.operation}`}
                      columns={changeColumns}
                      dataSource={fix.changes}
                      scroll={{ x: 570 }}
                    />
                  </div>
                )}
                {fix.diff?.trim() && (
                  <details>
                    <summary>{canClaimCandidate ? '查看候选差异' : '查看建议差异'}</summary>
                    <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxHeight: 320, overflow: 'auto', background: 'var(--diag-field, #f6f8fa)', padding: 12, borderRadius: 4, fontSize: 12 }}>
                      {fix.diff}
                    </pre>
                  </details>
                )}
                {fix.warnings.length > 0 && (
                  <div aria-label="风险提示">
                    {fix.warnings.map((warning, index) => (
                      <Paragraph key={`${fix.fix_id}-warning-${index}`} type="warning" style={{ marginBottom: 4 }}>
                        风险提示：{warning}
                      </Paragraph>
                    ))}
                  </div>
                )}
              </Space>
            </Card>
          );
        })}

        {allSteps.length > 0 && (
          <div>
            <Text strong>人工处理步骤</Text>
            <ol style={{ paddingInlineStart: 22, marginBottom: 0 }}>
              {allSteps.map((step, index) => <li key={`${step}-${index}`}>{step}</li>)}
            </ol>
          </div>
        )}
      </Space>
    </Card>
  );
};

export default RepairSuggestions;
