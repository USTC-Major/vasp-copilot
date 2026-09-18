import React from 'react';
import { Alert, Button, Card, Descriptions, List, Result, Space, Spin, Tag, Typography } from 'antd';
import { ArrowLeftOutlined } from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import { useNavigate, useParams } from 'react-router-dom';
import { ApiError, workflowsApi } from '../api/client';

const { Text, Title } = Typography;

const WorkflowHistoryPage: React.FC = () => {
  const { id = '' } = useParams();
  const navigate = useNavigate();
  const workflow = useQuery({
    queryKey: ['workflow-history', id],
    queryFn: () => workflowsApi.get(id),
    enabled: Boolean(id),
    retry: false,
  });

  if (workflow.isLoading) {
    return <div style={{ textAlign: 'center', padding: 48 }}><Spin /></div>;
  }
  const error = workflow.error;
  const unavailable = error instanceof ApiError
    && (error.status === 404 || error.code === 'WORKFLOW_NOT_FOUND');
  if (workflow.isError && unavailable) {
    return (
      <Result
        status="warning"
        title="工作流记录不可用或已过期"
        subTitle="工作流历史是当前后端进程的 TTL 快照；服务重启或超过保留期后无法恢复。"
        extra={<Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/')}>返回首页</Button>}
      />
    );
  }
  if (workflow.isError || !workflow.data) {
    return (
      <Result
        status="error"
        title="工作流详情加载失败"
        subTitle={error instanceof Error ? error.message : '历史服务暂不可用，请稍后重试。'}
        extra={(
          <Space>
            <Button type="primary" onClick={() => workflow.refetch()}>重试</Button>
            <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/')}>返回首页</Button>
          </Space>
        )}
      />
    );
  }

  const data = workflow.data;
  const steps = data.plan?.steps ?? [];
  return (
    <div style={{ maxWidth: 900, margin: '0 auto' }}>
      <Space direction="vertical" size="large" style={{ width: '100%' }}>
        <Button type="text" icon={<ArrowLeftOutlined />} onClick={() => navigate('/')}>返回首页</Button>
        <Card>
          <Title level={3}>工作流详情</Title>
          <Alert
            type="info"
            showIcon
            message="只读 TTL 快照"
            description="此页显示后端当前仍可访问的真实工作流元数据，不会伪造旧会话恢复能力。"
            style={{ marginBottom: 20 }}
          />
          <Descriptions column={1} bordered size="small">
            <Descriptions.Item label="工作流 ID"><Text code>{data.workflow_id}</Text></Descriptions.Item>
            <Descriptions.Item label="状态"><Tag color={data.workflow_status === 'generated' ? 'green' : 'blue'}>{data.workflow_status}</Tag></Descriptions.Item>
          </Descriptions>
        </Card>
        <Card title="计算步骤">
          <List
            dataSource={steps}
            locale={{ emptyText: '该快照没有可显示的步骤元数据' }}
            renderItem={(step) => (
              <List.Item>
                <Space>
                  <Tag>{String(step.task ?? step.step_id ?? '步骤')}</Tag>
                  <Text>{String(step.label ?? step.task ?? step.step_id ?? '')}</Text>
                </Space>
              </List.Item>
            )}
          />
        </Card>
      </Space>
    </div>
  );
};

export default WorkflowHistoryPage;
