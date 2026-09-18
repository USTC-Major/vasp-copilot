// ============================================================
// HomePage — 首页入口：两大板块（智能模式 / 工具箱）+ 最近记录
// ============================================================

import React, { useMemo } from 'react';
import { Alert, Card, Row, Col, Typography, Space, Button, List, Tag, Divider, Spin } from 'antd';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  RobotOutlined, ToolOutlined, BuildOutlined, BugOutlined, CloudUploadOutlined,
  ArrowRightOutlined, ExperimentOutlined, SettingOutlined,
} from '@ant-design/icons';
import { isFeatureEnabled } from '../config/featureFlags';
import { aiApi, diagnosisApi, workflowsApi } from '../api/client';
import type { HistoryKind } from '../types/history';
import { historyRecordPath, mergeRecentRecords } from '../utils/history';

const { Title, Text, Paragraph } = Typography;

const AppleBlue = '#0071e3';
const AppleGreen = '#34c759';

const HISTORY_LIMIT = 10;

const HISTORY_LABELS: Record<HistoryKind, { label: string; color: string }> = {
  ai_project: { label: '智能项目', color: 'purple' },
  ai_task: { label: '智能任务', color: 'geekblue' },
  workflow: { label: '工作流', color: 'blue' },
  diagnosis: { label: '诊断', color: 'green' },
};

const formatTime = (value: string): string => {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '时间未知';
  return date.toLocaleString('zh-CN', { hour12: false });
};

const HomePage: React.FC = () => {
  const navigate = useNavigate();
  const fakeHpcEnabled = isFeatureEnabled('ENABLE_FAKE_HPC');
  const aiHistory = useQuery({
    queryKey: ['recent-history', 'ai'],
    queryFn: () => aiApi.recentHistory(HISTORY_LIMIT),
    retry: false,
    refetchOnMount: 'always',
  });
  const workflowHistory = useQuery({
    queryKey: ['recent-history', 'workflows'],
    queryFn: () => workflowsApi.recent(HISTORY_LIMIT),
    retry: false,
    refetchOnMount: 'always',
  });
  const diagnosisHistory = useQuery({
    queryKey: ['recent-history', 'diagnoses'],
    queryFn: () => diagnosisApi.recent(HISTORY_LIMIT),
    retry: false,
    refetchOnMount: 'always',
  });
  const recentSessions = useMemo(() => mergeRecentRecords([
    aiHistory.data?.records ?? [],
    workflowHistory.data?.records ?? [],
    diagnosisHistory.data?.records ?? [],
  ]), [aiHistory.data, workflowHistory.data, diagnosisHistory.data]);
  const historySources = [
    { name: '智能模式', query: aiHistory },
    { name: '工作流', query: workflowHistory },
    { name: '诊断', query: diagnosisHistory },
  ];
  const failedSources = historySources.filter(({ query }) => query.isError);
  const historyLoading = historySources.some(({ query }) => query.isLoading);
  const mockHistory = historySources.some(({ query }) =>
    query.data?.demo === true || query.data?.records.some((record) => record.demo === true));

  const toolboxEntries = [
    { key: '/workflow', icon: <BuildOutlined />, title: '生成工作流', desc: '上传结构文件，通过 Recipe 生成 relax → static → DOS 完整工作流' },
    { key: '/diagnosis/upload', icon: <BugOutlined />, title: '诊断计算', desc: '上传计算目录 zip，自动检测并诊断 SCF 收敛、参数一致性与作业问题' },
    ...(fakeHpcEnabled
      ? [{ key: '/hpc/deploy', icon: <CloudUploadOutlined />, title: '远程部署（离线演示）', desc: 'Fake HPC 工具箱演示：不连接真实集群，不代表智能任务的运行环境' }]
      : []),
  ];

  return (
    <div style={{ maxWidth: 1080, margin: '0 auto', padding: '32px 16px' }}>
      {/* 页首标题 */}
      <div style={{ textAlign: 'center', marginBottom: 40 }}>
        <Title level={1} style={{ fontWeight: 700, letterSpacing: '-0.5px', marginBottom: 12 }}>
          <ExperimentOutlined style={{ marginRight: 10, color: AppleBlue, fontSize: 36 }} />
          VASP-Copilot / VASP-Doctor+
        </Title>
        <Paragraph style={{ fontSize: 17, maxWidth: 600, margin: '0 auto 16px', color: '#6e6e73' }}>
          面向材料计算初学者的 VASP 输入文件生成与计算结果诊断平台
        </Paragraph>
        <Tag style={{ marginTop: 8, fontSize: 13, padding: '4px 14px', borderRadius: 999 }}>
          运行环境以具体智能任务的 Real / Fake / None 标识为准
        </Tag>
      </div>

      {/* 两大板块 */}
      <Row gutter={[28, 28]} align="stretch">

        {/* 板块一：智能模式 */}
        <Col xs={24} md={12}>
          <Card hoverable style={{ height: '100%' }}>
            <Space direction="vertical" size="middle" style={{ width: '100%' }}>
              <div style={{ textAlign: 'center' }}>
                <RobotOutlined style={{ fontSize: 48, color: AppleBlue }} />
              </div>
              <div style={{ textAlign: 'center' }}>
                <Title level={3} style={{ marginTop: 4, marginBottom: 8, fontWeight: 700 }}>智能模式</Title>
                <Paragraph type="secondary" style={{ fontSize: 14, marginBottom: 16, textAlign: 'left' }}>
                以项目为中心：Agent 协助规划、排程并生成输入文件；任何文件写入与作业提交都按本次精确内容确认。递进任务等待前置成功后重新预检与确认，不会自动补提。配套的全局设置（齿轮）在右上角。
                </Paragraph>
                <Button type="primary" size="large" block icon={<ArrowRightOutlined />} onClick={() => navigate('/ai')}>
                  进入智能模式
                </Button>
                <Divider style={{ margin: '20px 0 8px', borderColor: 'rgba(0,0,0,0.06)' }} />
                <Button type="text" icon={<SettingOutlined />} onClick={() => navigate('/ai/settings')} style={{ color: '#86868b' }}>
                  智能设置（右上角齿轮）
                </Button>
              </div>
            </Space>
          </Card>
        </Col>

        {/* 板块二：工具箱 */}
        <Col xs={24} md={12}>
          <Card style={{ height: '100%' }}>
            <Space direction="vertical" size="middle" style={{ width: '100%' }}>
              <div style={{ textAlign: 'center' }}>
                <ToolOutlined style={{ fontSize: 48, color: AppleGreen }} />
              </div>
              <div style={{ textAlign: 'center' }}>
                <Title level={3} style={{ marginTop: 4, marginBottom: 8, fontWeight: 700 }}>工具箱</Title>
                <Paragraph type="secondary" style={{ fontSize: 14, marginBottom: 16 }}>
                  三个独立小工具的人工集合，面向已有文件与集群操作。
                </Paragraph>
              </div>
              {toolboxEntries.map((entry) => (
                <Button
                  key={entry.key}
                  type="default"
                  size="large"
                  block
                  onClick={() => navigate(entry.key)}
                  style={{ height: 'auto', display: 'flex', alignItems: 'center', padding: '12px 16px' }}
                >
                  <Space style={{ flex: 1, marginLeft: 8 }} direction="vertical" size={2}>
                    <Space size={8}>
                      {entry.icon}
                      <Text strong style={{ fontSize: 15 }}>{entry.title}</Text>
                    </Space>
                    <Text type="secondary" style={{ fontSize: 13 }}>{entry.desc}</Text>
                  </Space>
                  <ArrowRightOutlined style={{ color: '#86868b' }} />
                </Button>
              ))}
            </Space>
          </Card>
        </Col>
      </Row>

      {/* 最近记录 */}
      <Card title="最近记录" style={{ marginTop: 28 }}>
        <Paragraph type="secondary" style={{ marginTop: -4, marginBottom: 12 }}>
          智能模式记录持久保存；工作流与诊断仅显示当前进程中尚未过期的 TTL 记录，不等于完整历史。
        </Paragraph>
        {mockHistory && (
          <Alert type="warning" showIcon message="演示数据" description="当前最近记录来自显式 Mock 模式，不是真实任务历史。" style={{ marginBottom: 12 }} />
        )}
        {failedSources.map(({ name }) => (
          <Alert
            key={name}
            type="warning"
            showIcon
            message={`${name}历史暂不可用`}
            description="其他数据源的记录仍会正常显示。"
            style={{ marginBottom: 8 }}
          />
        ))}
        {historyLoading && recentSessions.length === 0 ? <Spin size="small" /> : (
        <List
          size="small"
          dataSource={recentSessions}
          renderItem={(item) => (
            <List.Item
              style={{ cursor: 'pointer', borderRadius: 12, padding: '12px 8px' }}
              onClick={() => navigate(historyRecordPath(item))}
            >
              <Space>
                <Tag color={HISTORY_LABELS[item.kind].color}>{HISTORY_LABELS[item.kind].label}</Tag>
                <Text>{item.title}</Text>
                {item.project_name && <Text type="secondary">{item.project_name}</Text>}
                <Tag>{item.status}</Tag>
                {item.execution_mode && (
                  <Tag color={item.execution_mode === 'Real' ? 'green' : item.execution_mode === 'Fake' ? 'gold' : 'default'}>
                    {item.execution_mode}
                  </Tag>
                )}
                {item.demo && <Tag color="warning">演示</Tag>}
              </Space>
              <Text type="secondary" style={{ fontSize: 12 }}>{formatTime(item.updated_at)}</Text>
            </List.Item>
          )}
          locale={{ emptyText: failedSources.length === historySources.length ? '历史服务暂不可用' : '暂无可显示的真实记录' }}
        />
        )}
      </Card>
    </div>
  );
};

export default HomePage;
