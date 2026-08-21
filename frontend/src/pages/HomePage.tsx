// ============================================================
// HomePage — 入口页，两主流程入口卡片 + 最近 session 历史
// ============================================================

import React from 'react';
import { Card, Row, Col, Typography, Space, Button, List, Tag } from 'antd';
import { useNavigate } from 'react-router-dom';
import {
  BuildOutlined, BugOutlined, CloudUploadOutlined,
  ArrowRightOutlined, ExperimentOutlined,
} from '@ant-design/icons';
import { isFeatureEnabled } from '../config/featureFlags';

const { Title, Text, Paragraph } = Typography;

const AppleBlue = '#0071e3';
const AppleGreen = '#34c759';
const ApplePurple = '#af52de';

const HomePage: React.FC = () => {
  const navigate = useNavigate();
  const fakeHpcEnabled = isFeatureEnabled('ENABLE_FAKE_HPC');

  const recentSessions = [
    { id: 'wf_demo_01', type: 'workflow', label: 'Fe2O3 结构优化 + 静态 + DOS', time: '2026-08-02 10:30' },
    { id: 'diag_demo_01', type: 'diagnosis', label: 'failed_relax 目录诊断', time: '2026-08-01 15:20' },
  ];

  return (
    <div style={{ maxWidth: 960, margin: '0 auto', padding: '32px 16px' }}>
      {/* 标题 */}
      <div style={{ textAlign: 'center', marginBottom: 48 }}>
        <Title level={1} style={{ fontWeight: 700, letterSpacing: '-0.5px', marginBottom: 12 }}>
          <ExperimentOutlined style={{ marginRight: 10, color: AppleBlue, fontSize: 36 }} />
          VASP-Copilot / VASP-Doctor+
        </Title>
        <Paragraph style={{ fontSize: 17, maxWidth: 600, margin: '0 auto', color: '#6e6e73' }}>
          面向材料计算初学者的 VASP 输入文件生成与计算结果诊断平台
        </Paragraph>
        {fakeHpcEnabled && (
          <Tag color="warning" style={{ marginTop: 12, fontSize: 13, padding: '4px 14px', borderRadius: 999 }}>
            ⚠ 模拟环境 — Fake HPC 模式
          </Tag>
        )}
      </div>

      {/* 两主流程入口 */}
      <Row gutter={[28, 28]}>
        <Col xs={24} md={12}>
          <Card
            hoverable
            style={{ height: '100%' }}
            onClick={() => navigate('/workflow')}
          >
            <Space direction="vertical" size="large" style={{ width: '100%' }}>
              <div style={{ textAlign: 'center' }}>
                <BuildOutlined style={{ fontSize: 56, color: AppleBlue }} />
              </div>
              <div style={{ textAlign: 'center' }}>
                <Title level={4} style={{ fontWeight: 600 }}>生成工作流</Title>
                <Paragraph type="secondary" style={{ fontSize: 15 }}>
                  上传 POSCAR/CIF 结构文件，选择计算任务，通过 Recipe 系统生成
                  relax → static → DOS 完整工作流及输入文件
                </Paragraph>
              </div>
              <Button type="primary" block size="large" icon={<ArrowRightOutlined />}>
                开始生成
              </Button>
            </Space>
          </Card>
        </Col>

        <Col xs={24} md={12}>
          <Card
            hoverable
            style={{ height: '100%' }}
            onClick={() => navigate('/diagnosis/upload')}
          >
            <Space direction="vertical" size="large" style={{ width: '100%' }}>
              <div style={{ textAlign: 'center' }}>
                <BugOutlined style={{ fontSize: 56, color: AppleGreen }} />
              </div>
              <div style={{ textAlign: 'center' }}>
                <Title level={4} style={{ fontWeight: 600 }}>诊断计算 (VASP-Doctor+)</Title>
                <Paragraph type="secondary" style={{ fontSize: 15 }}>
                  上传已有计算目录 zip，自动检测文件，诊断 SCF 收敛、参数一致性、
                  磁矩问题和作业资源问题
                </Paragraph>
              </div>
              <Button type="primary" block size="large" icon={<ArrowRightOutlined />} style={{ background: AppleGreen, borderColor: AppleGreen, boxShadow: 'none' }}>
                开始诊断
              </Button>
            </Space>
          </Card>
        </Col>
      </Row>

      {/* HPC 入口 (Fake HPC 模式下显示) */}
      {fakeHpcEnabled && (
        <Row gutter={[28, 28]} style={{ marginTop: 28 }}>
          <Col xs={24} md={12}>
            <Card
              hoverable
              onClick={() => navigate('/hpc/deploy')}
            >
              <Space direction="vertical" style={{ width: '100%', textAlign: 'center' }}>
                <CloudUploadOutlined style={{ fontSize: 40, color: ApplePurple }} />
                <Title level={5} style={{ fontWeight: 600 }}>远程部署 (模拟)</Title>
                <Text type="secondary">部署工作流到集群，提交作业，监控状态</Text>
              </Space>
            </Card>
          </Col>
        </Row>
      )}

      {/* 最近 Session */}
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