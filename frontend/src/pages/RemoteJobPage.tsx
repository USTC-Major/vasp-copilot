// ============================================================
// RemoteJobPage — 作业状态时间线、结果回收入口
// ============================================================

import React, { useState, useCallback } from 'react';
import { useParams } from 'react-router-dom';
import { Typography, Card, Button, Spin, Space, Tag, Descriptions } from 'antd';
import { SyncOutlined } from '@ant-design/icons';
import JobStatusTimeline from '../components/hpc/JobStatusTimeline';
import ResultCollectionPanel from '../components/hpc/ResultCollectionPanel';
import CapabilityConfirmDialog from '../components/hpc/CapabilityConfirmDialog';
import ErrorAlert from '../components/common/ErrorAlert';
import EmptyState from '../components/common/EmptyState';
import StatusBadge from '../components/common/StatusBadge';
import { useRemoteJob, useJobAuthorize, useJobSubmit } from '../hooks/useApi';

const { Title } = Typography;

const RemoteJobPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const [showSubmitDialog, setShowSubmitDialog] = useState(false);
  const [showCollectDialog, setShowCollectDialog] = useState(false);
  const [authorized, setAuthorized] = useState(false);

  const { data, isLoading, error, refetch } = useRemoteJob(id || null);
  const authMutation = useJobAuthorize();
  const submitMutation = useJobSubmit();

  const handleSubmitJob = useCallback(async () => {
    if (!id) return;
    try {
      setShowSubmitDialog(false);
      await authMutation.mutateAsync(id);
      await submitMutation.mutateAsync(id);
    } catch { /* handled by error */ }
  }, [id, authMutation, submitMutation]);

  if (isLoading) {
    return (
      <div style={{ textAlign: 'center', padding: 100 }}>
        <Spin size="large" tip="加载作业状态..." />
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
    return <EmptyState title="未找到作业" description="作业 ID 可能不存在" />;
  }

  return (
    <div style={{ maxWidth: 960, margin: '0 auto', padding: '24px 16px' }}>
      <Title level={3}>
        <SyncOutlined style={{ marginRight: 8 }} />
        远程作业
        <StatusBadge status={data.hpc_job_status} type="hpc_job" />
        <Tag color="warning" style={{ marginLeft: 8 }}>模拟环境</Tag>
      </Title>

      <Card style={{ marginBottom: 16 }}>
        <Descriptions bordered size="small" column={2}>
          <Descriptions.Item label="作业 ID">{data.remote_job_id}</Descriptions.Item>
          <Descriptions.Item label="调度器">{data.scheduler_type.toUpperCase()}</Descriptions.Item>
          <Descriptions.Item label="调度器作业 ID">{data.scheduler_job_id || '—'}</Descriptions.Item>
          <Descriptions.Item label="步骤">{data.step_id}</Descriptions.Item>
          <Descriptions.Item label="提交时间">{new Date(data.submitted_at).toLocaleString('zh-CN')}</Descriptions.Item>
          <Descriptions.Item label="最后同步">{new Date(data.last_synced_at).toLocaleString('zh-CN')}</Descriptions.Item>
        </Descriptions>
      </Card>

      <Card title="状态时间线" style={{ marginBottom: 16 }}>
        <JobStatusTimeline
          currentStatus={data.hpc_job_status}
          events={[
            { status: 'draft', timestamp: data.submitted_at, label: '创建草稿' },
            { status: 'ready_for_confirmation', timestamp: data.submitted_at, label: '等待确认' },
            { status: 'authorized', timestamp: data.submitted_at, label: '已授权' },
            { status: 'submitted', timestamp: data.submitted_at, label: '已提交' },
            { status: 'pending', timestamp: data.last_synced_at, label: '排队中' },
            { status: 'running', timestamp: data.last_synced_at, label: '运行中' },
          ]}
        />
      </Card>

      {/* 操作按钮 */}
      <Card style={{ marginBottom: 16 }}>
        <Space size="large">
          {data.hpc_job_status === 'ready_for_confirmation' && (
            <Button
              type="primary"
              onClick={() => setShowSubmitDialog(true)}
            >
              提交作业
            </Button>
          )}
          {data.hpc_job_status === 'draft' && (
            <Button type="primary" disabled>
              等待部署完成后提交
            </Button>
          )}
          <Button
            icon={<SyncOutlined />}
            onClick={() => refetch()}
          >
            刷新状态
          </Button>
        </Space>
      </Card>

      {/* 结果回收（仅 terminal 状态显示） */}
      {data.collectable && (
        <ResultCollectionPanel
          files={[
            { relative_path: 'INCAR', size_bytes: 421, sha256: '...' },
            { relative_path: 'OSZICAR', size_bytes: 2048, sha256: '...' },
            { relative_path: 'OUTCAR', size_bytes: 50000, sha256: '...' },
          ]}
          excluded={[
            { relative_path: 'POTCAR', reason: '策略禁止回收' },
            { relative_path: 'WAVECAR', reason: '策略禁止回收' },
          ]}
          partial={true}
          collectable={true}
          onCollect={() => {}}
          onAuthorize={() => setShowCollectDialog(true)}
          authorized={authorized}
        />
      )}

      {/* 提交授权弹窗 */}
      <CapabilityConfirmDialog
        open={showSubmitDialog}
        capability="HPC_SUBMIT"
        subject={`step: ${data.step_id}`}
        constraints={{
          max_nodes: 1,
          max_tasks: 32,
          max_walltime: '12:00:00',
        }}
        expiresAt={new Date(Date.now() + 3600000).toISOString()}
        onConfirm={handleSubmitJob}
        onCancel={() => setShowSubmitDialog(false)}
        loading={authMutation.isPending || submitMutation.isPending}
      />

      {/* 回收授权弹窗 */}
      <CapabilityConfirmDialog
        open={showCollectDialog}
        capability="HPC_COLLECT"
        subject={`作业 ${data.remote_job_id}`}
        constraints={{}}
        expiresAt={new Date(Date.now() + 3600000).toISOString()}
        onConfirm={() => { setShowCollectDialog(false); setAuthorized(true); }}
        onCancel={() => setShowCollectDialog(false)}
      />
    </div>
  );
};

export default RemoteJobPage;