// ============================================================
// ReportDownloadPanel — 下载诊断报告和候选修复包
// ============================================================

import React from 'react';
import { Alert, Card, Button, Space, Typography } from 'antd';
import { FileMarkdownOutlined, FileZipOutlined } from '@ant-design/icons';
import { useDiagnosisReport, useDiagnosisFixDownload } from '../../hooks/useApi';

const { Text } = Typography;

interface ReportDownloadPanelProps {
  diagnosisId: string;
  reportReady: boolean;
  reportUrl: string;
  fixAvailable: boolean;
  fixReason?: string;
  refreshing?: boolean;
  onRefresh?: () => void | Promise<unknown>;
}

const ReportDownloadPanel: React.FC<ReportDownloadPanelProps> = ({
  diagnosisId,
  reportReady,
  fixAvailable,
  fixReason,
  refreshing = false,
  onRefresh,
}) => {
  const reportMutation = useDiagnosisReport();
  const fixMutation = useDiagnosisFixDownload();
  const [fixDownloadStarted, setFixDownloadStarted] = React.useState(false);

  React.useEffect(() => {
    setFixDownloadStarted(false);
  }, [diagnosisId]);

  const saveBlob = (blob: Blob, filename: string) => {
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = filename;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  const handleDownloadReport = async () => {
    try {
      saveBlob(await reportMutation.mutateAsync(diagnosisId), `diagnosis_report_${diagnosisId}.md`);
    } catch {
      // Error is shown from mutation state.
    }
  };

  const handleDownloadFix = async () => {
    setFixDownloadStarted(false);
    try {
      saveBlob(await fixMutation.mutateAsync(diagnosisId), `fix_${diagnosisId}.zip`);
      setFixDownloadStarted(true);
    } catch {
      // Error is shown from mutation state.
    }
  };

  return (
    <Card title="下载" variant="borderless">
      <Space orientation="vertical" style={{ width: '100%' }}>
        <Button
          icon={<FileMarkdownOutlined />}
          onClick={handleDownloadReport}
          loading={reportMutation.isPending}
          disabled={!reportReady}
          block
        >
          下载诊断报告 (Markdown)
        </Button>

        <Button
          icon={<FileZipOutlined />}
          onClick={handleDownloadFix}
          loading={fixMutation.isPending}
          disabled={!fixAvailable || refreshing}
          block
        >
          下载候选修复包 (ZIP)
        </Button>

        {!fixAvailable && (
          <Text type="secondary">
            {fixReason?.trim() || '当前没有已核验的候选修复包。刷新修复状态后再检查。'}
          </Text>
        )}
        {onRefresh && (
          <Button onClick={() => { void onRefresh(); }} loading={refreshing} disabled={refreshing} block>
            刷新修复状态
          </Button>
        )}
        <Text type="secondary">
          候选包仅供下载和审阅，不会自动应用；下载成功也不表示问题已修复。
        </Text>
        {fixDownloadStarted && <Alert type="success" showIcon title="已发起候选文件下载，请审阅后再手工处理。" />}
        {reportMutation.error && (
          <Alert type="error" showIcon title={`诊断报告下载失败：${reportMutation.error.message || '请稍后重试。'}`} />
        )}
        {fixMutation.error && (
          <Alert
            type="error"
            showIcon
            title="候选修复包下载失败，服务端拒绝或无法提供当前候选。"
            description={fixMutation.error.message || '请刷新修复状态后重试。'}
          />
        )}
      </Space>
    </Card>
  );
};

export default ReportDownloadPanel;
