// ============================================================
// HpcDeploymentPage — 选择集群、生成部署计划、preflight 检查、授权上传
// ============================================================

import React, { useState, useCallback } from 'react';
import { Steps, Button, Card, Descriptions, Tag, Alert, Typography, Result } from 'antd';
import { CloudUploadOutlined, SafetyOutlined } from '@ant-design/icons';
import ClusterSelector from '../components/hpc/ClusterSelector';
import CapabilityConfirmDialog from '../components/hpc/CapabilityConfirmDialog';
import ErrorAlert from '../components/common/ErrorAlert';
import StatusBadge from '../components/common/StatusBadge';
import {
  useClusters, useDeploymentPlan,
  useDeploymentAuthorize, useDeploymentExecute,
} from '../hooks/useApi';
import type { ClusterProfile, RemoteDeploymentPlan } from '../types/generated-api';

const { Title, Text } = Typography;

const HpcDeploymentPage: React.FC = () => {
  const [step, setStep] = useState<'select' | 'plan' | 'confirm' | 'executing' | 'done'>('select');
  const [selectedCluster, setSelectedCluster] = useState<ClusterProfile | null>(null);
  const [deploymentPlan, setDeploymentPlan] = useState<RemoteDeploymentPlan | null>(null);
  const [deploymentId, setDeploymentId] = useState<string | null>(null);
  const [showAuthDialog, setShowAuthDialog] = useState(false);
  const [authorized, setAuthorized] = useState(false);

  const { data: clustersData, isLoading: clustersLoading } = useClusters();
  const planMutation = useDeploymentPlan();
  const authMutation = useDeploymentAuthorize();
  const executeMutation = useDeploymentExecute();

  const handleSelectCluster = useCallback(async (clusterId: string) => {
    const cluster = clustersData?.clusters.find((c) => c.cluster_profile_id === clusterId);
    if (!cluster) return;
    setSelectedCluster(cluster);

    try {
      const plan = await planMutation.mutateAsync({
        workflow_id: 'wf_01',
        cluster_profile_id: clusterId,
      });
      setDeploymentPlan(plan);
      setDeploymentId(plan.deployment_id);
      setStep('plan');
    } catch { /* handled by error display */ }
  }, [clustersData, planMutation]);

  const handleAuthorize = useCallback(async () => {
    if (!deploymentId) return;
    try {
      await authMutation.mutateAsync(deploymentId);
      setAuthorized(true);
      setShowAuthDialog(false);
    } catch { /* handled by error display */ }
  }, [deploymentId, authMutation]);

  const handleExecute = useCallback(async () => {
    if (!deploymentId) return;
    setStep('executing');
    try {
      await executeMutation.mutateAsync(deploymentId);
      setStep('done');
    } catch { /* handled by error display */ }
  }, [deploymentId, executeMutation]);

  return (
    <div style={{ maxWidth: 960, margin: '0 auto', padding: '24px 16px' }}>
      <Title level={3}>
        <CloudUploadOutlined style={{ marginRight: 8, color: '#722ed1' }} />
        远程部署
        <Tag color="warning" style={{ marginLeft: 8 }}>模拟环境</Tag>
      </Title>

      <Alert
        type="warning"
        showIcon
        message="模拟环境 (Fake HPC)"
        description="当前使用 Fake HPC Connector，所有远程操作均为模拟，不会实际连接到集群。"
        style={{ marginBottom: 24 }}
      />

      <Steps
        current={step === 'select' ? 0 : step === 'plan' ? 1 : step === 'confirm' ? 2 : step === 'executing' ? 3 : 4}
        items={[
          { title: '选择集群' },
          { title: '部署计划' },
          { title: '确认授权' },
          { title: '执行部署' },
          { title: '完成' },
        ]}
        style={{ marginBottom: 32 }}
      />

      {/* Step 1: 选择集群 */}
      {step === 'select' && (
        <ClusterSelector
          clusters={clustersData?.clusters || []}
          selectedClusterId={selectedCluster?.cluster_profile_id || null}
          onSelect={handleSelectCluster}
          loading={clustersLoading}
        />
      )}

      {/* Step 2: 部署计划 */}
      {step === 'plan' && deploymentPlan && (
        <Card title="部署计划" bordered={false}>
          <Descriptions bordered size="small" column={1}>
            <Descriptions.Item label="目标路径">{deploymentPlan.target_relative_path}</Descriptions.Item>
            <Descriptions.Item label="文件数">{deploymentPlan.file_count}</Descriptions.Item>
            <Descriptions.Item label="总大小">{deploymentPlan.total_bytes} bytes</Descriptions.Item>
            <Descriptions.Item label="覆盖模式">{deploymentPlan.overwrite ? '允许覆盖' : '禁止覆盖'}</Descriptions.Item>
            <Descriptions.Item label="HASH">{deploymentPlan.bundle_sha256}</Descriptions.Item>
            <Descriptions.Item label="所需权限">
              <Tag color="orange">{deploymentPlan.required_capability}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="预检">
              <StatusBadge
                status={deploymentPlan.preflight.passed ? 'passed' : 'failed'}
                type="workflow"
              />
            </Descriptions.Item>
          </Descriptions>

          <Card title="操作列表" size="small" style={{ marginTop: 12 }}>
            {deploymentPlan.operations.map((op) => (
              <div key={op.operation_id} style={{ marginBottom: 4 }}>
                <Tag color={op.type === 'create_directory' ? 'blue' : 'green'}>{op.type}</Tag>
                <Text code>{op.relative_path}</Text>
                {op.size_bytes != null && (
                  <Text type="secondary" style={{ marginLeft: 8 }}>{op.size_bytes} B</Text>
                )}
              </div>
            ))}
          </Card>

          <div style={{ marginTop: 16, display: 'flex', gap: 8 }}>
            <Button onClick={() => { setStep('select'); setDeploymentPlan(null); }}>
              重新选择
            </Button>
            <Button
              type="primary"
              icon={<SafetyOutlined />}
              onClick={() => setShowAuthDialog(true)}
              disabled={!deploymentPlan.preflight.passed}
            >
              授权部署
            </Button>
          </div>
        </Card>
      )}

      {/* 执行中 */}
      {step === 'executing' && (
        <Result
          status="info"
          title="部署中..."
          subTitle="正在向远程集群上传文件"
        />
      )}

      {/* 完成 */}
      {step === 'done' && (
        <Result
          status="success"
          title="部署完成"
          subTitle={`文件已部署到 ${deploymentPlan?.target_relative_path || ''}`}
          extra={[
            <Button key="new" onClick={() => {
              setStep('select');
              setSelectedCluster(null);
              setDeploymentPlan(null);
              setDeploymentId(null);
              setAuthorized(false);
            }}>新部署</Button>,
          ]}
        />
      )}

      {/* 错误展示 */}
      {(planMutation.error || authMutation.error || executeMutation.error) && (
        <div style={{ marginTop: 16 }}>
          <ErrorAlert
            error={planMutation.error || authMutation.error || executeMutation.error!}
          />
        </div>
      )}

      {/* 授权弹窗 */}
      {deploymentPlan && (
        <CapabilityConfirmDialog
          open={showAuthDialog}
          capability={deploymentPlan.required_capability}
          subject={deploymentPlan.target_relative_path}
          constraints={{
            cluster_profile_id: deploymentPlan.cluster_profile_id,
          }}
          expiresAt={deploymentPlan.preflight.expires_at || ''}
          onConfirm={async () => {
            await handleAuthorize();
            if (authorized) handleExecute();
          }}
          onCancel={() => setShowAuthDialog(false)}
          loading={authMutation.isPending}
        />
      )}
    </div>
  );
};

export default HpcDeploymentPage;