// ============================================================
// DiagnosisUploadPanel — 上传 zip，展示文件检测结果
// ============================================================

import React, { useState, useCallback } from 'react';
import { Upload, Card, Descriptions, Tag, List, Alert, Spin, Button, Typography } from 'antd';
import { InboxOutlined, FileSearchOutlined, CheckCircleOutlined, WarningOutlined } from '@ant-design/icons';
import type { UploadProps } from 'antd';
import { useDiagnosisUpload } from '../../hooks/useApi';
import { formatFileSize } from '../../utils/formatters';
import ErrorAlert from '../common/ErrorAlert';
import type { DetectedFile, DetectedRun } from '../../types/generated-api';
import { getFeatureFlags } from '../../config/featureFlags';

const { Dragger } = Upload;
const { Text } = Typography;

interface DiagnosisUploadPanelProps {
  onDetected: (diagnosisId: string, detected: DetectedRun) => void;
}

const DiagnosisUploadPanel: React.FC<DiagnosisUploadPanelProps> = ({ onDetected }) => {
  const [detected, setDetected] = useState<DetectedRun | null>(null);
  const [, setDiagnosisId] = useState<string | null>(null);

  const uploadMutation = useDiagnosisUpload();

  const handleUpload = useCallback(async (file: File) => {
    const flags = getFeatureFlags();
    if (file.size > flags.MAX_UPLOAD_SIZE_MB * 1024 * 1024) {
      return false;
    }
    try {
      const result = await uploadMutation.mutateAsync(file);
      setDetected(result.detected_run);
      onDetected(result.diagnosis_id, result.detected_run);
    } catch {
      // handled by error display
    }
    return false;
  }, [uploadMutation, onDetected]);

  const uploadProps: UploadProps = {
    name: 'file',
    multiple: false,
    accept: '.zip',
    maxCount: 1,
    showUploadList: false,
    beforeUpload: (file) => {
      handleUpload(file);
      return false;
    },
  };

  return (
    <Card title="上传计算目录" bordered={false}>
      {!detected && (
        <Dragger {...uploadProps} disabled={uploadMutation.isPending}>
          {uploadMutation.isPending ? (
            <div style={{ padding: 24 }}>
              <Spin size="large" />
              <p style={{ marginTop: 12, color: '#999' }}>正在扫描文件...</p>
            </div>
          ) : (
            <>
              <p className="ant-upload-drag-icon">
                <InboxOutlined />
              </p>
              <p className="ant-upload-text">点击或拖拽计算目录 zip 文件</p>
              <p className="ant-upload-hint">
                包含 INCAR、POSCAR、OSZICAR、OUTCAR 等输出文件的压缩包，最大 {getFeatureFlags().MAX_UPLOAD_SIZE_MB}MB
              </p>
            </>
          )}
        </Dragger>
      )}

      {uploadMutation.error && (
        <div style={{ marginTop: 12 }}>
          <ErrorAlert error={uploadMutation.error} onRetry={() => uploadMutation.reset()} />
        </div>
      )}

      {detected && (
        <div style={{ marginTop: 16 }}>
          <Alert
            type="success"
            showIcon
            icon={<CheckCircleOutlined />}
            message="文件检测完成"
            description={`检测到 ${detected.files.length} 个文件`}
            style={{ marginBottom: 16 }}
          />

          <Descriptions bordered size="small" column={1}>
            <Descriptions.Item label="根目录">{detected.root}</Descriptions.Item>
            <Descriptions.Item label="推断运行类型">
              <Tag color="blue">{detected.run_type}</Tag>
            </Descriptions.Item>
          </Descriptions>

          <Card title={<span><FileSearchOutlined /> 检测到的文件</span>} size="small" style={{ marginTop: 12 }}>
            <List
              size="small"
              dataSource={detected.files}
              renderItem={(file: DetectedFile) => (
                <List.Item>
                  <Text>{file.name}</Text>
                  <Text type="secondary">{formatFileSize(file.size_bytes)}</Text>
                  <Tag>{file.kind}</Tag>
                </List.Item>
              )}
              locale={{ emptyText: '未检测到文件' }}
            />
          </Card>

          {detected.missing_recommended.length > 0 && (
            <Alert
              type="warning"
              showIcon
              icon={<WarningOutlined />}
              message="缺少推荐文件"
              description={
                <div>
                  {detected.missing_recommended.map((f) => (
                    <Tag key={f} color="orange">{f}</Tag>
                  ))}
                  <Text type="secondary" style={{ display: 'block', marginTop: 4 }}>
                    这些文件有助于更完整的诊断，缺失不影响基础规则运行。
                  </Text>
                </div>
              }
              style={{ marginTop: 12 }}
            />
          )}

          <div style={{ marginTop: 12 }}>
            <Button onClick={() => { uploadMutation.reset(); setDetected(null); setDiagnosisId(null); }}>
              重新上传
            </Button>
          </div>
        </div>
      )}
    </Card>
  );
};

export default DiagnosisUploadPanel;