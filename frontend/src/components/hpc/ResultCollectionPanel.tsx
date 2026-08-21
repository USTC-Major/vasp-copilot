// ============================================================
// ResultCollectionPanel — 结果回收入口
// ============================================================

import React from 'react';
import { Card, List, Tag, Button, Space, Typography, Alert } from 'antd';
import {
  FolderOpenOutlined, DownloadOutlined, StopOutlined,
  WarningOutlined,
} from '@ant-design/icons';

const { Text } = Typography;

interface CollectedFile {
  relative_path: string;
  size_bytes: number;
  sha256: string;
}

interface ExcludedFile {
  relative_path: string;
  reason: string;
}

interface ResultCollectionPanelProps {
  files: CollectedFile[];
  excluded: ExcludedFile[];
  partial: boolean;
  collectable: boolean;
  onCollect: () => void;
  onAuthorize: () => void;
  loading?: boolean;
  authorized?: boolean;
}

const ResultCollectionPanel: React.FC<ResultCollectionPanelProps> = ({
  files,
  excluded,
  partial,
  collectable,
  onCollect,
  onAuthorize,
  loading,
  authorized = false,
}) => {
  return (
    <Card title={<span><FolderOpenOutlined /> 结果回收</span>} bordered={false}>
      {!collectable && (
        <Alert
          type="info"
          showIcon
          message="作业尚未结束"
          description="只有作业完成后才能回收结果文件。"
          style={{ marginBottom: 12 }}
        />
      )}

      {partial && (
        <Alert
          type="warning"
          showIcon
          icon={<WarningOutlined />}
          message="部分回收"
          description="由于策略限制，部分文件未能回收（见下方排除列表）。"
          style={{ marginBottom: 12 }}
        />
      )}

      {files.length > 0 && (
        <Card title="可回收文件" size="small" style={{ marginBottom: 12 }}>
          <List
            size="small"
            dataSource={files}
            renderItem={(f) => (
              <List.Item>
                <Text>{f.relative_path}</Text>
                <Text type="secondary">{(f.size_bytes / 1024).toFixed(1)} KB</Text>
                <Tag>{f.sha256.substring(0, 8)}</Tag>
              </List.Item>
            )}
          />
        </Card>
      )}

      {excluded.length > 0 && (
        <Card title="排除文件" size="small" style={{ marginBottom: 12 }}>
          <List
            size="small"
            dataSource={excluded}
            renderItem={(f) => (
              <List.Item>
                <Space>
                  <StopOutlined style={{ color: '#ff4d4f' }} />
                  <Text delete>{f.relative_path}</Text>
                  <Tag color="red">{f.reason}</Tag>
                </Space>
              </List.Item>
            )}
          />
        </Card>
      )}

      <Space>
        {!authorized && (
          <Button
            type="primary"
            onClick={onAuthorize}
            disabled={!collectable || loading}
            loading={loading}
            icon={<DownloadOutlined />}
          >
            授权回收
          </Button>
        )}
        {authorized && (
          <Button
            type="primary"
            danger
            onClick={onCollect}
            disabled={!collectable || loading}
            loading={loading}
            icon={<DownloadOutlined />}
          >
            确认回收
          </Button>
        )}
      </Space>
    </Card>
  );
};

export default ResultCollectionPanel;