// ============================================================
// StructureUploadPanel — 上传 POSCAR/CIF 文件，展示结构摘要
// ============================================================

import React, { useState, useCallback } from 'react';
import { Upload, Card, Descriptions, Alert, Tag, Spin, Button, Typography, Space } from 'antd';
import { InboxOutlined, CheckCircleOutlined } from '@ant-design/icons';
import type { UploadProps } from 'antd';
import { useFileUpload, useStructureAnalysis } from '../../hooks/useApi';
import { EmpiricalFormula, reduceCounts } from '../common/EmpiricalFormula';
import ErrorAlert from '../common/ErrorAlert';
import type { StructureSummary } from '../../types/generated-api';
import { getFeatureFlags } from '../../config/featureFlags';

const { Dragger } = Upload;
const { Text } = Typography;

interface StructureUploadPanelProps {
  onStructureAnalyzed: (fileId: string, summary: StructureSummary) => void;
}

const StructureUploadPanel: React.FC<StructureUploadPanelProps> = ({ onStructureAnalyzed }) => {
  const [, setUploadedFileId] = useState<string | null>(null);
  const [summary, setSummary] = useState<StructureSummary | null>(null);

  const uploadMutation = useFileUpload();
  const analyzeMutation = useStructureAnalysis();

  const handleUpload = useCallback(async (file: File) => {
    const flags = getFeatureFlags();
    if (file.size > flags.MAX_UPLOAD_SIZE_MB * 1024 * 1024) {
      return false;
    }
    try {
      const result = await uploadMutation.mutateAsync({ file, purpose: 'structure' });
      setUploadedFileId(result.file.file_id);

      const analysis = await analyzeMutation.mutateAsync({ fileId: result.file.file_id });
      setSummary(analysis.summary);
      onStructureAnalyzed(analysis.summary.structure_id, analysis.summary);
    } catch {
      // error handled by form
    }
    return false; // prevent default upload behavior
  }, [uploadMutation, analyzeMutation, onStructureAnalyzed]);

  const uploadProps: UploadProps = {
    name: 'file',
    multiple: false,
    accept: '.poscar,.cif,.POSCAR,.CIF',
    maxCount: 1,
    showUploadList: false,
    beforeUpload: (file) => {
      handleUpload(file);
      return false;
    },
  };

  const isLoading = uploadMutation.isPending || analyzeMutation.isPending;
  const lattice = summary?.lattice;
  const reducedCounts = summary ? reduceCounts(summary.counts) : [];

  return (
    <Card title="上传结构文件" bordered={false}>
      {!summary && (
        <Dragger {...uploadProps} disabled={isLoading}>
          {isLoading ? (
            <div style={{ padding: 24 }}>
              <Spin size="large" />
              <p style={{ marginTop: 12, color: '#999' }}>
                {uploadMutation.isPending ? '上传中...' : '解析结构中...'}
              </p>
            </div>
          ) : (
            <>
              <p className="ant-upload-drag-icon">
                <InboxOutlined />
              </p>
              <p className="ant-upload-text">点击或拖拽 POSCAR / CIF 文件到此区域</p>
              <p className="ant-upload-hint">
                支持 POSCAR 和 CIF 格式，最大 {getFeatureFlags().MAX_UPLOAD_SIZE_MB}MB
              </p>
            </>
          )}
        </Dragger>
      )}

      {(uploadMutation.error || analyzeMutation.error) && (
        <div style={{ marginTop: 12 }}>
          <ErrorAlert
            error={uploadMutation.error || analyzeMutation.error!}
            onRetry={() => {
              uploadMutation.reset();
              analyzeMutation.reset();
              setUploadedFileId(null);
              setSummary(null);
            }}
          />
        </div>
      )}

      {summary && (
        <div style={{ marginTop: 16 }}>
          <Alert
            type="success"
            showIcon
            icon={<CheckCircleOutlined />}
            message="结构解析完成"
            style={{ marginBottom: 16 }}
          />
          <Descriptions
            bordered
            size="small"
            column={{ xs: 1, sm: 2 }}
            title="结构摘要"
          >
            <Descriptions.Item label="化学式">
              <Text strong>
                <EmpiricalFormula elements={summary.elements} counts={reducedCounts} />
              </Text>
            </Descriptions.Item>
            <Descriptions.Item label="原子数">{summary.atom_count}</Descriptions.Item>
            <Descriptions.Item label="元素">
              {summary.elements.map((el, i) => (
                <Tag key={el} style={{ marginBottom: 4 }}>
                  {el} : {summary.counts[i]}
                </Tag>
              ))}
            </Descriptions.Item>
            <Descriptions.Item label="坐标模式">
              {summary.coordinate_mode === 'direct' ? '分数坐标' : '笛卡尔坐标'}
            </Descriptions.Item>
            <Descriptions.Item label="晶格常数">
              <Space size={20} wrap>
                <span>a = {lattice?.a.toFixed(3) ?? '—'} Å</span>
                <span>b = {lattice?.b.toFixed(3) ?? '—'} Å</span>
                <span>c = {lattice?.c.toFixed(3) ?? '—'} Å</span>
              </Space>
            </Descriptions.Item>
            <Descriptions.Item label="体积">
              {lattice?.volume.toFixed(2) ?? '—'} Å³
            </Descriptions.Item>
            <Descriptions.Item label="晶格角度">
              <Space size={20} wrap>
                <span>α = {lattice?.alpha.toFixed(3) ?? '—'}°</span>
                <span>β = {lattice?.beta.toFixed(3) ?? '—'}°</span>
                <span>γ = {lattice?.gamma.toFixed(3) ?? '—'}°</span>
              </Space>
            </Descriptions.Item>
            <Descriptions.Item label="选择性动力学">
              {summary.selective_dynamics ? '是' : '否'}
            </Descriptions.Item>
          </Descriptions>

          {summary.transition_metals.length > 0 && summary.magnetism_hint === 'possible' && (
            <Alert
              type="warning"
              showIcon
              message="磁性提醒"
              description={`该体系含有过渡金属 ${summary.transition_metals.join(', ')}，可能需要设置磁性计算。请在下步确认磁性参数。`}
              style={{ marginTop: 12 }}
            />
          )}

          {summary.warnings.length > 0 && (
            <div style={{ marginTop: 12 }}>
              {summary.warnings.map((w, i) => (
                <Alert
                  key={i}
                  type={w.severity === 'critical' || w.severity === 'high' ? 'error' : 'warning'}
                  showIcon
                  message={w.message}
                  style={{ marginBottom: 4 }}
                />
              ))}
            </div>
          )}

          <div style={{ marginTop: 12 }}>
            <Button
              onClick={() => {
                uploadMutation.reset();
                analyzeMutation.reset();
                setUploadedFileId(null);
                setSummary(null);
              }}
            >
              重新上传
            </Button>
          </div>
        </div>
      )}
    </Card>
  );
};

export default StructureUploadPanel;