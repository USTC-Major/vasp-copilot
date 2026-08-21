// ============================================================
// App — Layout 壳，玻璃拟态 Header + Outlet
// ============================================================

import React, { useState } from 'react';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import { Layout, Menu, Typography, Space, Tag, Button } from 'antd';
import {
  HomeOutlined, BuildOutlined, BugOutlined,
  CloudUploadOutlined, SettingOutlined,
} from '@ant-design/icons';
import { isFeatureEnabled } from './config/featureFlags';
import LlmSettingsModal from './components/settings/LlmSettingsModal';
import ChatPanel from './components/chat/ChatPanel';

const { Header, Content, Footer } = Layout;
const { Text } = Typography;

const App: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const fakeHpcEnabled = isFeatureEnabled('ENABLE_FAKE_HPC');
  const [settingsOpen, setSettingsOpen] = useState(false);

  const menuItems = [
    { key: '/', icon: <HomeOutlined />, label: '首页' },
    { key: '/workflow', icon: <BuildOutlined />, label: '生成工作流' },
    { key: '/diagnosis/upload', icon: <BugOutlined />, label: '诊断计算' },
    ...(fakeHpcEnabled ? [
      { key: '/hpc/deploy', icon: <CloudUploadOutlined />, label: '远程部署' },
    ] : []),
  ];

  const selectedKey = menuItems.find((item) =>
    location.pathname === item.key || location.pathname.startsWith(item.key + '/')
  )?.key || '/';

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header style={{
        display: 'flex',
        alignItems: 'center',
        background: 'rgba(255,255,255,0.72)',
        backdropFilter: 'blur(20px) saturate(180%)',
        WebkitBackdropFilter: 'blur(20px) saturate(180%)',
        borderBottom: '1px solid rgba(0,0,0,0.06)',
        padding: '0 28px',
        position: 'sticky',
        top: 0,
        zIndex: 100,
        boxShadow: '0 2px 12px rgba(0,0,0,0.04)',
      }}>
        <div style={{ color: '#1d1d1f', fontSize: 17, fontWeight: 600, letterSpacing: '0.2px', marginRight: 36, whiteSpace: 'nowrap' }}>
          ⚛ VASP-Copilot
        </div>
        <Menu
          theme="light"
          mode="horizontal"
          selectedKeys={[selectedKey]}
          items={menuItems}
          onClick={({ key }) => navigate(key)}
          style={{ flex: 1, minWidth: 0, background: 'transparent', borderBottom: 'none', fontWeight: 500 }}
        />
        {fakeHpcEnabled && (
          <Tag color="warning" style={{ marginLeft: 8, borderRadius: 999, padding: '2px 12px' }}>模拟环境</Tag>
        )}
        <Button
          type="text"
          icon={<SettingOutlined />}
          style={{ color: '#1d1d1f', marginLeft: 8, borderRadius: 8 }}
          onClick={() => setSettingsOpen(true)}
        >
          模型设置
        </Button>
      </Header>
      <Content style={{ padding: '32px 24px', maxWidth: 1400, margin: '0 auto', width: '100%' }}>
        <Outlet />
      </Content>
      <Footer style={{ textAlign: 'center', color: '#6e6e73', borderTop: '1px solid rgba(0,0,0,0.04)' }}>
        <Space>
          <Text type="secondary">VASP-Copilot / VASP-Doctor+ MVP</Text>
          <Text type="secondary">|</Text>
          <Text type="secondary">面向材料计算初学者的训练与排错助手</Text>
        </Space>
      </Footer>
      <LlmSettingsModal open={settingsOpen} onClose={() => setSettingsOpen(false)} />
      <ChatPanel onOpenSettings={() => setSettingsOpen(true)} />
    </Layout>
  );
};

export default App;