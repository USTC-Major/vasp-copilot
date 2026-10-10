import React, { useContext, useEffect, useState } from 'react';
import { ConfigProvider, Button, theme } from 'antd';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { HomeOutlined, BuildOutlined, BugOutlined, RobotOutlined, ToolOutlined, SettingOutlined, SunOutlined, MoonOutlined, CloudUploadOutlined } from '@ant-design/icons';
import './scientific-workflow.css';
import { hasScientificContent, workspaceLocation } from './scientificNavigation';
import { readWorkflowTheme, WORKFLOW_THEME_KEY, type WorkflowTheme } from './workflowTheme';
import { connectDesktopSettings, useDesktopSettings } from '../../utils/desktopSettings';

const links = [
  { to: '/', label: '首页', icon: <HomeOutlined /> },
  { to: '/ai', label: '智能模式', icon: <RobotOutlined /> },
  { to: '/toolbox/projects', label: '计算任务', icon: <ToolOutlined /> },
  { to: '/toolbox/postprocessing', label: '结果后处理', icon: <ToolOutlined /> },
  { to: '/workflow', label: '生成工作流', icon: <BuildOutlined /> },
  { to: '/diagnosis/upload', label: '诊断计算', icon: <BugOutlined /> },
];

/** Shared navigation theme; other tools keep the enclosing application's content theme. */
export default function ScientificWorkflowShell({ children, auxiliary, fakeHpcEnabled = false }: { children: React.ReactNode; auxiliary?: React.ReactNode; fakeHpcEnabled?: boolean }) {
  const { theme: contentTheme } = useContext(ConfigProvider.ConfigContext);
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const desktop = useDesktopSettings();
  useEffect(() => connectDesktopSettings(() => navigate('/settings')), [navigate]);
  const position = workspaceLocation(pathname);
  const scientificContent = hasScientificContent(pathname);
  const navigation = fakeHpcEnabled ? [...links, { to: '/hpc/deploy', label: '远程部署（离线演示）', icon: <CloudUploadOutlined /> }] : links;
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
      <div className="scientific-shell" data-workflow-theme={mode}>
        <a className="wf-skip-link" href="#workspace-content">跳到主内容</a>
        <aside className="wf-sidebar" aria-label="工作区导航">
          <Link to="/" className="wf-brand" aria-label="VASP-Copilot 首页">
            <img src="/app-icon.svg" alt="" width="34" height="34" />
            <span>VASP-Copilot<small>材料计算工作区</small></span>
          </Link>
          <nav>{navigation.map(link => <Link key={link.to} to={link.to} aria-label={link.label} aria-current={link.to === position.current ? 'page' : undefined}><span aria-hidden="true">{link.icon}</span><span>{link.label}</span></Link>)}</nav>
          <div className="wf-sidebar-bottom"><span>SCIENTIFIC COMPUTING</span><p>结构 · 输入文件 · 计算诊断</p></div>
        </aside>
        <div className="wf-workspace">
          <header className="wf-topbar">
            <span className="wf-breadcrumb" aria-label="当前位置">{position.group}<span>/</span><strong>{position.title}</strong></span>
            <div className="wf-topbar-actions">
              <Button type="text" icon={dark ? <SunOutlined /> : <MoonOutlined />} onClick={toggleTheme} aria-label={dark ? '切换浅色主题' : '切换深色主题'}>{dark ? '浅色' : '深色'}</Button>
              {!desktop && <Link to="/settings" aria-label="设置" aria-current={position.current === '/settings' ? 'page' : undefined}><SettingOutlined /><span>设置</span></Link>}
            </div>
          </header>
          <main id="workspace-content" tabIndex={-1} className={`wf-main${scientificContent ? ' scientific-workflow' : ' wf-tool-content'}`}>
            {scientificContent ? children : <ConfigProvider theme={{ ...contentTheme, inherit: false }}><div className="wf-tool-panel">{children}</div></ConfigProvider>}
          </main>
        </div>
        <div className="scientific-workflow wf-assistant-layer">{auxiliary}</div>
      </div>
    </ConfigProvider>
  );
}
