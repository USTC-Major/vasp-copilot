// ============================================================
// CodePreview — 文本文件预览，处理截断、二进制拒绝、OUTCAR 限制
// ============================================================

import React, { useState } from 'react';
import { Card, Button, Spin, Alert, Space, Typography } from 'antd';
import { ExpandOutlined, FileTextOutlined } from '@ant-design/icons';
import { useFilePreview } from '../../hooks/useApi';

const { Text } = Typography;

interface CodePreviewProps {
  fileId: string | null;
  fileName?: string;
  title?: string;
  maxHeight?: number;
}

const CodePreview: React.FC<CodePreviewProps> = ({
  fileId,
  fileName,
  title,
  maxHeight = 400,
}) => {
  const [expanded, setExpanded] = useState(false);
  const { data, isLoading, error } = useFilePreview(fileId);

  if (!fileId) {
    return (
      <Card size="small">
        <Text type="secondary">未选择文件</Text>
      </Card>
    );
  }

  if (isLoading) {
    return (
      <Card size="small">
        <div style={{ textAlign: 'center', padding: 16 }}>
          <Spin tip="加载中..." />
        </div>
      </Card>
    );
  }

  if (error) {
    const errMsg = error instanceof Error ? error.message : '加载失败';
    const isPolicyDenied = errMsg.includes('POLICY_DENIED') || errMsg.includes('策略限制');
    const isBinary = errMsg.includes('UNSUPPORTED_BINARY') || errMsg.includes('不支持预览');

    return (
      <Card size="small" title={title || fileName || '文件预览'}>
        <Alert
          type={isBinary ? 'warning' : 'info'}
          showIcon
          message={
            isPolicyDenied
              ? '策略限制不可预览'
              : isBinary
              ? '不支持预览二进制文件'
              : '预览加载失败'
          }
          description={
            isPolicyDenied
              ? 'POTCAR 文件因策略限制不可通过浏览器预览'
              : isBinary
              ? 'WAVECAR / CHGCAR 等二进制文件不支持文本预览'
              : errMsg
          }
        />
      </Card>
    );
  }

  if (!data) return null;

  const { preview, policy } = data;

  return (
    <Card
      size="small"
      title={
        <Space>
          <FileTextOutlined />
          <span>{title || fileName || data.name}</span>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {preview.start_line}-{preview.end_line} / {preview.total_lines} 行
          </Text>
        </Space>
      }
      extra={
        <Button
          size="small"
          icon={<ExpandOutlined />}
          onClick={() => setExpanded(!expanded)}
        >
          {expanded ? '收起' : '展开'}
        </Button>
      }
    >
      {preview.truncated && (
        <Alert
          type="warning"
          showIcon
          message="内容已截断"
          description={`预览限制：最多 ${policy.max_preview_lines} 行 / ${(policy.max_preview_bytes / 1024).toFixed(0)} KB。请勿将截断内容作为完整文件。`}
          style={{ marginBottom: 8 }}
        />
      )}
      <pre
        style={{
          background: '#f6f8fa',
          padding: 12,
          borderRadius: 4,
          fontSize: 13,
          lineHeight: 1.6,
          overflow: 'auto',
          maxHeight: expanded ? undefined : maxHeight,
          margin: 0,
          border: '1px solid #e8e8e8',
          fontFamily: 'Consolas, Monaco, "Courier New", monospace',
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-all',
        }}
      >
        {preview.content}
      </pre>
    </Card>
  );
};

export default CodePreview;