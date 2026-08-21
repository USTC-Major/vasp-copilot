// ============================================================
// ParameterProvenanceDrawer — 参数来源侧边抽屉
// ============================================================

import React from 'react';
import { Drawer, Table, Tag, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import type { ParameterProvenance } from '../../types/generated-api';
import StatusBadge from '../common/StatusBadge';

const { Text } = Typography;

interface ParameterProvenanceDrawerProps {
  open: boolean;
  onClose: () => void;
  provenance: ParameterProvenance[];
  title?: string;
}

const SOURCE_LABELS: Record<string, string> = {
  recipe: 'Recipe 初始推荐',
  derived_function: '确定性派生',
  user_patch: '用户修改',
  rule_fix: '规则修复',
  scheduler_profile: '集群经验设置',
};

const ParameterProvenanceDrawer: React.FC<ParameterProvenanceDrawerProps> = ({
  open,
  onClose,
  provenance,
  title = '参数来源追溯',
}) => {
  const columns: ColumnsType<ParameterProvenance> = [
    {
      title: '参数',
      dataIndex: 'parameter',
      key: 'parameter',
      width: 120,
      render: (text: string) => <Text code>{text}</Text>,
    },
    {
      title: '值',
      dataIndex: 'value',
      key: 'value',
      width: 120,
      render: (value: unknown) => <Text>{JSON.stringify(value)}</Text>,
    },
    {
      title: '来源类型',
      dataIndex: 'source_type',
      key: 'source_type',
      width: 130,
      render: (type: string) => (
        <Tag color={
          type === 'recipe' ? 'blue' :
          type === 'derived_function' ? 'green' :
          type === 'user_patch' ? 'orange' :
          type === 'rule_fix' ? 'red' :
          'default'
        }>
          {SOURCE_LABELS[type] || type}
        </Tag>
      ),
    },
    {
      title: '来源 ID',
      dataIndex: 'source_id',
      key: 'source_id',
      width: 150,
      render: (text: string, record: ParameterProvenance) => (
        <Text style={{ fontSize: 12 }}>
          {text}
          {record.source_revision && (
            <Text type="secondary"> @{record.source_revision}</Text>
          )}
        </Text>
      ),
    },
    {
      title: '覆盖',
      key: 'overrode',
      width: 200,
      render: (_: unknown, record: ParameterProvenance) => {
        if (!record.overrode) return <Text type="secondary">—</Text>;
        return (
          <div style={{ fontSize: 12 }}>
            <Tag color="volcano">已覆盖</Tag>
            <Text>
              {record.overrode.source_type}: {JSON.stringify(record.overrode.value)}
            </Text>
          </div>
        );
      },
    },
    {
      title: '确认',
      key: 'confirmed',
      width: 80,
      render: (_: unknown, record: ParameterProvenance) => (
        record.requires_confirmation ? (
          <StatusBadge status={record.confirmed ? 'confirmed' : 'pending'} type="workflow" />
        ) : (
          <Text type="secondary">—</Text>
        )
      ),
    },
  ];

  return (
    <Drawer
      title={`参数来源追溯 — ${title}`}
      placement="right"
      width={900}
      open={open}
      onClose={onClose}
    >
      <Table
        dataSource={provenance}
        columns={columns}
        rowKey="parameter"
        size="small"
        pagination={false}
        locale={{ emptyText: '暂无参数来源信息' }}
      />
    </Drawer>
  );
};

export default ParameterProvenanceDrawer;