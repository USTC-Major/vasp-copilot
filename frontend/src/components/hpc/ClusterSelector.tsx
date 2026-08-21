// ============================================================
// ClusterSelector — 展示脱敏集群列表
// ============================================================

import React from 'react';
import { Card, List, Tag, Space, Typography, Descriptions } from 'antd';
import { CloudServerOutlined, CheckCircleOutlined, CloseCircleOutlined } from '@ant-design/icons';
import type { ClusterProfile } from '../../types/generated-api';

const { Text } = Typography;

interface ClusterSelectorProps {
  clusters: ClusterProfile[];
  selectedClusterId: string | null;
  onSelect: (clusterId: string) => void;
  loading?: boolean;
}

const ClusterSelector: React.FC<ClusterSelectorProps> = ({
  clusters,
  selectedClusterId,
  onSelect,
  loading,
}) => {
  return (
    <Card title={<span><CloudServerOutlined /> 选择集群</span>} bordered={false}>
      <List
        loading={loading}
        dataSource={clusters}
        renderItem={(cluster) => (
          <List.Item
            onClick={() => onSelect(cluster.cluster_profile_id)}
            style={{
              cursor: 'pointer',
              padding: 16,
              borderRadius: 8,
              border: selectedClusterId === cluster.cluster_profile_id
                ? '2px solid #1677ff'
                : '1px solid #e8e8e8',
              background: selectedClusterId === cluster.cluster_profile_id
                ? '#e6f4ff'
                : '#fff',
              marginBottom: 8,
            }}
          >
            <List.Item.Meta
              title={
                <Space>
                  <Text strong>{cluster.display_name}</Text>
                  <Tag>{cluster.scheduler_type.toUpperCase()}</Tag>
                  {cluster.connector_status === 'available' ? (
                    <Tag color="success" icon={<CheckCircleOutlined />}>可用</Tag>
                  ) : (
                    <Tag color="error" icon={<CloseCircleOutlined />}>不可用</Tag>
                  )}
                </Space>
              }
              description={
                <Descriptions size="small" column={2} style={{ marginTop: 8 }}>
                  <Descriptions.Item label="最大节点">{cluster.limits.max_nodes}</Descriptions.Item>
                  <Descriptions.Item label="最大核数">{cluster.limits.max_tasks}</Descriptions.Item>
                  <Descriptions.Item label="最大Walltime">{cluster.limits.max_walltime}</Descriptions.Item>
                  <Descriptions.Item label="分区">{cluster.allowed_partitions.join(', ')}</Descriptions.Item>
                </Descriptions>
              }
            />
            <Space>
              {cluster.capabilities.map((cap) => (
                <Tag key={cap} color="blue">{cap}</Tag>
              ))}
            </Space>
          </List.Item>
        )}
        locale={{ emptyText: '暂无可用集群' }}
      />
    </Card>
  );
};

export default ClusterSelector;