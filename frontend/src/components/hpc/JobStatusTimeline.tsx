// ============================================================
// JobStatusTimeline — HPC 作业状态时间线
// ============================================================

import React from 'react';
import { Timeline, Tag, Typography } from 'antd';
import {
  ClockCircleOutlined, SyncOutlined, CheckCircleOutlined,
  CloseCircleOutlined, ExclamationCircleOutlined, StopOutlined,
} from '@ant-design/icons';
import type { HpcJobStatus } from '../../types/enums';
import { HPC_JOB_STATUS_MAP } from '../../types/enums';

const { Text } = Typography;

interface TimelineEvent {
  status: HpcJobStatus;
  timestamp: string;
  label: string;
  detail?: string;
}

interface JobStatusTimelineProps {
  events: TimelineEvent[];
  currentStatus: HpcJobStatus;
}

const STATUS_ICON: Record<string, React.ReactNode> = {
  draft: <ClockCircleOutlined />,
  ready_for_confirmation: <ExclamationCircleOutlined style={{ color: '#faad14' }} />,
  authorized: <CheckCircleOutlined style={{ color: '#1677ff' }} />,
  submitting: <SyncOutlined spin />,
  submitted: <CheckCircleOutlined style={{ color: '#52c41a' }} />,
  pending: <ClockCircleOutlined style={{ color: '#faad14' }} />,
  running: <SyncOutlined spin style={{ color: '#1677ff' }} />,
  completed: <CheckCircleOutlined style={{ color: '#52c41a' }} />,
  failed: <CloseCircleOutlined style={{ color: '#ff4d4f' }} />,
  cancelled: <StopOutlined style={{ color: '#999' }} />,
  timeout: <ExclamationCircleOutlined style={{ color: '#faad14' }} />,
  out_of_memory: <CloseCircleOutlined style={{ color: '#ff4d4f' }} />,
  unknown: <ExclamationCircleOutlined style={{ color: '#999' }} />,
};

const JobStatusTimeline: React.FC<JobStatusTimelineProps> = ({ events, currentStatus }) => {
  if (!events.length) {
    return <Text type="secondary">暂无状态时间线</Text>;
  }

  const config = HPC_JOB_STATUS_MAP[currentStatus];

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <Text strong>当前状态: </Text>
        <Tag color={config?.color || 'default'}>
          {STATUS_ICON[currentStatus]} {config?.label || currentStatus}
        </Tag>
      </div>

      <Timeline
        items={events.map((event) => {
          const isCurrent = event.status === currentStatus;
          return {
            color: isCurrent ? '#1677ff' : event.status === 'failed' || event.status === 'out_of_memory' ? '#ff4d4f' : '#52c41a',
            dot: STATUS_ICON[event.status],
            children: (
              <div>
                <div>
                  <Text strong={isCurrent}>{event.label}</Text>
                  {isCurrent && <Tag color="processing" style={{ marginLeft: 8 }}>进行中</Tag>}
                </div>
                <div>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {new Date(event.timestamp).toLocaleString('zh-CN')}
                  </Text>
                </div>
                {event.detail && (
                  <div>
                    <Text type="secondary" style={{ fontSize: 12 }}>{event.detail}</Text>
                  </div>
                )}
              </div>
            ),
          };
        })}
      />
    </div>
  );
};

export default JobStatusTimeline;