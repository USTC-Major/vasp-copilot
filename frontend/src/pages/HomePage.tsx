// ============================================================
// HomePage — 首页入口：两大板块（智能模式 / 工具箱）+ 最近记录
// ============================================================

import React from 'react';
import { Card, Row, Col, Typography, Space, Button, List, Tag, Divider } from 'antd';
import { useNavigate } from 'react-router-dom';
import {
  RobotOutlined, ToolOutlined, BuildOutlined, BugOutlined, CloudUploadOutlined,
  ArrowRightOutlined, ExperimentOutlined, SettingOutlined,
} from '@ant-design/icons';
import { isFeatureEnabled } from '../config/featureFlags';

const { Title, Text, Paragraph } = Typography;

const AppleBlue = '#0071e3';
const AppleGreen = '#34c759';

const HomePage: React.FC = () => {
  const navigate = useNavigate();
  const fakeHpcEnabled = isFeatureEnabled('ENABLE_FAKE_HPC');

  const recentSessions = [
    { id: 'wf_demo_01', type: 'workflow', label: 'Fe2O3 结构优化 + 静态 + DOS', time: '2026-08-02 10:30' },
    { id: 'diag_demo_01', type: 'diagnosis', label: 'failed_relax 目录诊断', time: '2026-08-01 15:20' },
  ];

  const toolboxEntries = [
    { key: '/workflow', icon: <BuildOutlined />, title: '生成工作流', desc: '上传结构文件，通过 Recipe 生成 relax → static → DOS 完整工作流' },
    { key: '/diagnosis/upload', icon: <BugOutlined />, title: '诊断计算', desc: '上传计算目录 zip，自动检测并诊断 SCF 收敛、参数一致性与作业问题' },
    ...(fakeHpcEnabled
      ? [{ key: '/hpc/deploy', icon: <CloudUploadOutlined />, title: '远程部署', desc: '部署工作流到集群，提交作业并监控状态' }]
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
        {fakeHpcEnabled && (
          <Tag color="warning" style={{ marginTop: 8, fontSize: 13, padding: '4px 14px', borderRadius: 999 }}>
            ⚗ 模拟环境 - Fake HPC 模式
          </Tag>
        )}
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
                以项目为中心：创建项目，在项目内发起计算任务对话，Agent 自动排程并生成输入文件（平行任务并行执行，递进任务等待前置成功后再执行）。配套的全局设置（齿轮）在右上角。
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
        <List
          size="small"
          dataSource={recentSessions}
          renderItem={(item) => (
            <List.Item
              style={{ cursor: 'pointer', borderRadius: 12, padding: '12px 8px' }}
              onClick={() => {
                if (item.type === 'workflow') navigate(`/workflow?id=${item.id}`);
                else navigate(`/diagnosis/${item.id}`);
              }}
            >
              <Space>
                {item.type === 'workflow' ? (
                  <Tag color="blue">工作流</Tag>
                ) : (
                  <Tag color="green">诊断</Tag>
                )}
                <Text>{item.label}</Text>
              </Space>
              <Text type="secondary" style={{ fontSize: 12 }}>{item.time}</Text>
            </List.Item>
          )}
          locale={{ emptyText: '暂无历史记录' }}
        />
      </Card>
    </div>
  );
};

export default HomePage;
