import { useEffect, useState } from 'react';
import { Alert, Button, Card, Modal, Space, Typography } from 'antd';
import { Link, useBlocker, useLocation } from 'react-router-dom';
import AiSettingsPage from './AiSettingsPage';
import PlotPreferencesSettings from '../components/postprocessing/PlotPreferencesSettings';
import { openDesktopLaunchSettings, useDesktopSettings } from '../utils/desktopSettings';
import './scientific-settings.css';

export default function SettingsCenterPage() {
  const desktop = useDesktopSettings();
  const [businessDirty, setBusinessDirty] = useState(false);
  const [paletteDirty, setPaletteDirty] = useState(false);
  const dirty = businessDirty || paletteDirty;
  const blocker = useBlocker(({ currentLocation, nextLocation }) => dirty && currentLocation.pathname !== nextLocation.pathname);
  const { hash } = useLocation();
  useEffect(() => {
    const beforeUnload = (event: BeforeUnloadEvent) => { if (dirty) { event.preventDefault(); event.returnValue = ''; } };
    window.addEventListener('beforeunload', beforeUnload);
    return () => window.removeEventListener('beforeunload', beforeUnload);
  }, [dirty]);
  useEffect(() => {
    if (hash !== '#settings-models' && hash !== '#settings-execution' && hash !== '#settings-runtime') return;
    const scroll = () => {
      const target = document.getElementById(hash.slice(1));
      if (!target) return false;
      target.scrollIntoView?.({ block: 'start' }); target.focus({ preventScroll: true }); return true;
    };
    if (scroll()) return;
    const observer = new MutationObserver(() => { if (scroll()) observer.disconnect(); });
    observer.observe(document.getElementById('settings-center')!, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, [hash]);
  return <div id="settings-center" className="scientific-settings">
    <div className="settings-heading"><Typography.Title level={1}>设置</Typography.Title></div>
    <Space wrap style={{ marginBottom: 16 }}>
      <Link to="#settings-runtime">启动与运行</Link><Link to="#settings-models">模型与材料</Link><Link to="#settings-execution">超算与执行</Link>
    </Space>
    <section id="settings-runtime" tabIndex={-1}>
      <Typography.Title level={2}>启动与运行</Typography.Title>
      <Card className="settings-section" title="安装目录、Python 与智能模式服务">
        <Typography.Paragraph>沿用桌面启动配置的保存与诊断。更改需要重启服务时会先说明影响并确认。</Typography.Paragraph>
        {desktop ? <Button onClick={openDesktopLaunchSettings}>打开本地启动配置</Button>
          : <Alert type="info" showIcon message="此项需在桌面程序中操作" description="独立浏览器不读写本机启动配置。请在 VASP-Copilot.exe 的设置中配置安装目录、Python 和服务开关。" />}
      </Card>
    </section>
    <AiSettingsPage embedded onDirtyChange={setBusinessDirty} />
    <PlotPreferencesSettings onDirtyChange={setPaletteDirty} />
    <Modal open={blocker.state === 'blocked'} title="设置尚未保存" okText="放弃修改并离开" cancelText="继续编辑"
      onOk={() => { setBusinessDirty(false); setPaletteDirty(false); if (blocker.state === 'blocked') blocker.proceed(); }}
      onCancel={() => { if (blocker.state === 'blocked') blocker.reset(); }}>
      当前修改尚未保存。离开不会自动保存或调用连接测试。
    </Modal>
  </div>;
}
