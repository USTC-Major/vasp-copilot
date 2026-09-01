// ============================================================
// AiGeneratePanel — 生成后：下载待提交包 + 标记第 2 步占位状态
// ============================================================

import React from 'react';
import { Card, Alert, Tag, Space, Button, Typography } from 'antd';
import { DownloadOutlined, SendOutlined, CloudSyncOutlined, InfoCircleOutlined } from '@ant-design/icons';
import type { AiJobRecord } from '../../types/ai';
import { AI_JOB_STATUS_MAP } from '../../types/ai';

const { Text, Paragraph } = Typography;

interface AiGeneratePanelProps {
  job: AiJobRecord;
  downloading: boolean;
  marking: boolean;
  onDownload: () => void;
  onMarkStatus: (status: 'submitted' | 'collected') => void;
}

const AiGeneratePanel: React.FC<AiGeneratePanelProps> = ({
  job, downloading, marking, onDownload, onMarkStatus,
}) => {
  const stepOk = (s: string) => job.timeline.some((t) => t.status === s);

  return (
    <div>
      <Card
        title={<Space><InfoCircleOutlined style={{ color: '#0071e3' }} /> 待提交包已就绪</Space>}
        size="small"
        style={{ borderRadius: 16, borderColor: '#0071e3', marginBottom: 16 }}
      >
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          <Tag color={AI_JOB_STATUS_MAP.generated.color}>
            {AI_JOB_STATUS_MAP.generated.label} · workflow {job.workflow_id}
          </Tag>
          <Paragraph style={{ margin: 0 }}>
            这份 zip 包含按 README 正确顺序生成的所有输入文件目录（01_relax / 02_static / …）
            与提交脚本 submit.sh、运行顺序说明 README_run_order.md。下载后在超算上解压运行即可。
          </Paragraph>
          <Button type="primary" size="large" icon={<DownloadOutlined />} loading={downloading} onClick={onDownload}>
            下载待提交包 (ZIP)
          </Button>
        </Space>
      </Card>

      <Card title="第 2 步说明：拿到超算去运行" size="small" style={{ borderRadius: 16 }}>
        <Alert
          type="info"
          showIcon
          message="本期 AI 暂未接入真实 HPC 提交"
          description={job.submit_instructions || '解压后按 README_run_order.md 依次提交各步 submit.sh，跑完后把结果目录回交给 AI 做第 3 步质检。'}
          style={{ marginBottom: 16 }}
        />
        <div style={{ paddingTop: 4, borderTop: '1px dashed rgba(0,0,0,0.1)' }}>
          <Text type="secondary" style={{ display: 'block', margin: '16px 0' }}>
            第 2 步当前为占位：由你在自己的超算上运行后，手动标记状态让流程继续。
          </Text>
          <Space>
            <Button
              size="large"
              icon={<SendOutlined />}
              disabled={stepOk('submitted')}
              loading={marking}
              onClick={() => onMarkStatus('submitted')}
            >
              运行完成，标记「已提交」
            </Button>
            <Button
              type="primary"
              size="large"
              icon={<CloudSyncOutlined />}
              disabled={!stepOk('submitted') || stepOk('collected')}
              loading={marking}
              onClick={() => onMarkStatus('collected')}
            >
              结果已取回，标记「已回收」
            </Button>
          </Space>
        </div>
      </Card>
    </div>
  );
};

export default AiGeneratePanel;
