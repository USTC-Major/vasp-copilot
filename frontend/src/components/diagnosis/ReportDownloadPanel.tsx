// ============================================================
// ReportDownloadPanel — 下载诊断报告和修复包
// ============================================================

import React from 'react';
import { Card, Button, Space, Typography } from 'antd';
import {
  FileMarkdownOutlined, FileZipOutlined,
} from '@ant-design/icons';
import { useDiagnosisReport, useDiagnosisFixDownload } from '../../hooks/useApi';

const { Text } = Typography;

interface ReportDownloadPanelProps {
  diagnosisId: string;
  reportReady: boolean;
  reportUrl: string;
  fixAvailable: boolean;
}

const ReportDownloadPanel: React.FC<ReportDownloadPanelProps> = ({
  diagnosisId,
  reportReady,
  fixAvailable,
}) => {
  const reportMutation = useDiagnosisReport();
  const fixMutation = useDiagnosisFixDownload();

  const handleDownloadReport = async () => {
    try {
      const blob = await reportMutation.mutateAsync(diagnosisId);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `diagnosis_report_${diagnosisId}.md`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      // handled by mutation state
    }
  };

  const handleDownloadFix = async () => {
    try {
      const blob = await fixMutation.mutateAsync(diagnosisId);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `fix_${diagnosisId}.zip`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      // handled by mutation state
    }
  };

  return (
    <Card title="下载" bordered={false}>
      <Space direction="vertical" style={{ width: '100%' }}>
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
          disabled={!fixAvailable}
          block
        >
          下载修复包 (ZIP)
        </Button>

        {(reportMutation.error || fixMutation.error) && (
          <Text type="danger">
            {reportMutation.error?.message || fixMutation.error?.message || '下载失败'}
          </Text>
        )}
      </Space>
    </Card>
  );
};

export default ReportDownloadPanel;