// ============================================================
// CapabilityConfirmDialog — 独立高危险确认弹窗（上传/提交分别确认）
// ============================================================

import React from 'react';
import { Modal, Descriptions, Alert, Tag, Typography } from 'antd';
import { ExclamationCircleOutlined } from '@ant-design/icons';
import type { GrantCapability } from '../../types/enums';

const { Text } = Typography;

interface CapabilityConfirmDialogProps {
  open: boolean;
  capability: GrantCapability;
  subject: string;
  constraints: {
    cluster_profile_id?: string;
    max_nodes?: number;
    max_tasks?: number;
    max_walltime?: string;
    idempotency_key?: string;
    [key: string]: unknown;
  };
  expiresAt: string;
  onConfirm: () => void;
  onCancel: () => void;
  loading?: boolean;
}

const CAPABILITY_INFO: Record<GrantCapability, {
  title: string;
  description: string;
  riskLevel: 'high' | 'medium';
  actionLabel: string;
}> = {
  HPC_DEPLOY: {
    title: '确认远程部署',
    description: '此操作将在远程集群创建目录并上传输入文件。部署不会提交任何计算任务，但会占用远程存储空间。',
    riskLevel: 'medium',
    actionLabel: '确认部署',
  },
  HPC_SUBMIT: {
    title: '确认提交作业',
    description: '此操作将向远程集群调度器提交计算作业。作业将消耗计算资源配额，请仔细核对资源参数。',
    riskLevel: 'high',
    actionLabel: '确认提交',
  },
  HPC_COLLECT: {
    title: '确认回收结果',
    description: '此操作将从远程集群下载计算结果文件。系统只下载白名单允许的文件，不会下载 POTCAR/WAVECAR 等受限文件。',
    riskLevel: 'medium',
    actionLabel: '确认回收',
  },
};

const CapabilityConfirmDialog: React.FC<CapabilityConfirmDialogProps> = ({
  open,
  capability,
  subject,
  constraints,
  expiresAt,
  onConfirm,
  onCancel,
  loading,
}) => {
  const info = CAPABILITY_INFO[capability];

  return (
    <Modal
      title={
        <span>
          <ExclamationCircleOutlined style={{ color: info.riskLevel === 'high' ? '#ff4d4f' : '#faad14', marginRight: 8 }} />
          {info.title}
        </span>
      }
      open={open}
      onOk={onConfirm}
      onCancel={onCancel}
      confirmLoading={loading}
      okText={info.actionLabel}
      okButtonProps={{ danger: info.riskLevel === 'high' }}
      cancelText="取消"
      width={560}
    >
      <Alert
        type={info.riskLevel === 'high' ? 'error' : 'warning'}
        showIcon
        message={info.description}
        style={{ marginBottom: 16 }}
      />

      <Descriptions bordered size="small" column={1}>
        <Descriptions.Item label="操作权限">
          <Tag color={info.riskLevel === 'high' ? 'error' : 'warning'}>{capability}</Tag>
        </Descriptions.Item>
        <Descriptions.Item label="操作对象">{subject}</Descriptions.Item>
        <Descriptions.Item label="授权过期时间">
          <Text type="warning">{new Date(expiresAt).toLocaleString('zh-CN')}</Text>
        </Descriptions.Item>
        {constraints.max_nodes != null && (
          <Descriptions.Item label="节点数">{constraints.max_nodes}</Descriptions.Item>
        )}
        {constraints.max_tasks != null && (
          <Descriptions.Item label="总核数">{constraints.max_tasks}</Descriptions.Item>
        )}
        {constraints.max_walltime && (
          <Descriptions.Item label="Walltime">{constraints.max_walltime}</Descriptions.Item>
        )}
      </Descriptions>

      <Alert
        type="info"
        showIcon
        message="提示"
        description="此授权为一次性使用，使用后自动失效。请在授权过期前完成操作。"
        style={{ marginTop: 12 }}
      />
    </Modal>
  );
};

export default CapabilityConfirmDialog;