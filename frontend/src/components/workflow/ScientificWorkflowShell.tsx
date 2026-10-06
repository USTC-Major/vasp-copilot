import React, { useState } from 'react';
import { ConfigProvider, Button, theme } from 'antd';
import { Link } from 'react-router-dom';
import { HomeOutlined, BuildOutlined, BugOutlined, RobotOutlined, ToolOutlined, SettingOutlined, SunOutlined, MoonOutlined } from '@ant-design/icons';
import './scientific-workflow.css';
import { readWorkflowTheme, WORKFLOW_THEME_KEY, type WorkflowTheme } from './workflowTheme';

const links = [
  { to: '/', label: '首页', icon: <HomeOutlined /> },
  { to: '/workflow', label: '生成工作流', icon: <BuildOutlined /> },
  { to: '/toolbox/projects', label: '计算任务', icon: <ToolOutlined /> },
  { to: '/diagnosis/upload', label: '诊断计算', icon: <BugOutlined /> },
  { to: '/ai', label: '智能模式', icon: <RobotOutlined /> },
  { to: '/toolbox/settings', label: '执行设置', icon: <SettingOutlined /> },
];

/** Route-local provider: other tools retain their existing application theme. */
export default function ScientificWorkflowShell({ children }: { children: React.ReactNode }) {
  const [mode, setMode] = useState<WorkflowTheme>(readWorkflowTheme);
  const dark = mode === 'dark';
  const toggleTheme = () => {
    const next = dark ? 'light' : 'dark';
    setMode(next);
    try { localStorage.setItem(WORKFLOW_THEME_KEY, next); } catch { /* A session theme still works without storage. */ }
  };
  return (
    <ConfigProvider theme={{
      inherit: false,
      algorithm: dark ? theme.darkAlgorithm : theme.defaultAlgorithm,
      token: {
        colorPrimary: dark ? '#75A7EE' : '#356EBD', colorInfo: dark ? '#75A7EE' : '#356EBD',
        colorBgBase: dark ? '#15191F' : '#F2F4F7', colorBgContainer: dark ? '#1B2028' : '#FFFFFF',
        colorBgElevated: dark ? '#202731' : '#FFFFFF', colorText: dark ? '#E6EBF3' : '#202B3B',
        colorTextSecondary: dark ? '#A4AFBF' : '#526278', colorBorder: dark ? '#303A48' : '#D4DCE6',
        fontFamily: '"Segoe UI", "Microsoft YaHei", sans-serif', fontSize: 14, fontSizeSM: 12,
        borderRadius: 6, borderRadiusLG: 10, controlHeight: 34,
      },
      components: {
        Card: { headerFontSize: 14, headerHeight: 46, paddingLG: 20 },
        Button: { borderRadius: 6, borderRadiusLG: 6 },
        Modal: { borderRadiusLG: 10 },
        Steps: { titleLineHeight: 22, iconSize: 28 },
      },
    }}>
      <div className="scientific-workflow" data-workflow-theme={mode}>
        <aside className="wf-sidebar" aria-label="工作区导航">
          <Link to="/" className="wf-brand" aria-label="VASP-Copilot 首页">
            <svg viewBox="0 0 40 40" aria-hidden="true"><rect width="40" height="40" rx="10" fill="#15191F" /><path d="M8 10h7l5 17 5-17h7L22.5 32h-5Z" fill="#8AB7FA" /></svg>
            <span>VASP-Copilot<small>材料计算工作区</small></span>
          </Link>
          <nav>{links.map(link => <Link key={link.to} to={link.to} aria-label={link.label} aria-current={link.to === '/workflow' ? 'page' : undefined}><span aria-hidden="true">{link.icon}</span><span>{link.label}</span></Link>)}</nav>
          <div className="wf-sidebar-bottom"><span>WORKFLOW BUILDER</span><p>从结构到计算输入文件</p></div>
        </aside>
        <div className="wf-workspace">
          <header className="wf-topbar">
            <span className="wf-breadcrumb">工具箱 <span>/</span> <strong>生成工作流</strong></span>
            <div className="wf-topbar-actions">
              <Button type="text" icon={dark ? <SunOutlined /> : <MoonOutlined />} onClick={toggleTheme} aria-label={dark ? '切换浅色主题' : '切换深色主题'}>{dark ? '浅色' : '深色'}</Button>
              <Link to="/ai/settings" aria-label="智能设置"><SettingOutlined /></Link>
            </div>
          </header>
          <main className="wf-main">{children}</main>
        </div>
      </div>
    </ConfigProvider>
  );
}
