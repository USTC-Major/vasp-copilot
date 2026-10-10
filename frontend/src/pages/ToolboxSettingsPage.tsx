import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Button, Card, Col, Form, Input, InputNumber, Modal, Row, Select, Space, Spin, Typography, message } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { toolboxApi } from '../api/client';
import type { ToolboxSettings } from '../types/toolbox';
import './scientific-settings.css';

const { Title, Paragraph, Text } = Typography;

interface SettingsForm {
  max_jobs: number;
  poll_interval_seconds: number;
  ssh_name: string;
  ssh_host: string;
  ssh_port: number;
  ssh_username: string;
  ssh_known_hosts_path: string;
  ssh_identity_file: string;
  scheduler_backend: string;
}

/** 服务端设置 -> 表单值；用于加载回填，也用于保存前比对服务端是否已被改动。 */
const toForm = (settings: ToolboxSettings): SettingsForm => ({
  max_jobs: settings.max_jobs,
  poll_interval_seconds: settings.poll_interval_seconds,
  ssh_name: settings.ssh.name,
  ssh_host: settings.ssh.host,
  ssh_port: settings.ssh.port,
  ssh_username: settings.ssh.username,
  ssh_known_hosts_path: settings.ssh.known_hosts_path,
  ssh_identity_file: settings.ssh.identity_file,
  scheduler_backend: settings.ssh.scheduler_backend ?? 'slurm',
});

const ToolboxSettingsPage: React.FC<{ embedded?: boolean; modelNotice?: React.ReactNode; onDirtyChange?: (dirty: boolean) => void }> = ({ embedded = false, modelNotice, onDirtyChange }) => {
  const [modal, contextHolder] = Modal.useModal();
  const [form] = Form.useForm<SettingsForm>();
  const settingsQuery = useQuery({ queryKey: ['toolboxSettings'], queryFn: () => toolboxApi.getSettings(), retry: false });
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [sshPassword, setSshPassword] = useState('');
  const [mpSecret, setMpSecret] = useState('');
  // 页面加载时服务端给过的值，用于保存前判断“是否已被他处改动”。
  const loadedRef = useRef<SettingsForm | null>(null);
  const pendingSecrets = useRef(false);
  pendingSecrets.current = Boolean(sshPassword || mpSecret);
  const reportDirty = useCallback(() => {
    const snapshot = loadedRef.current;
    const values = form.getFieldsValue();
    onDirtyChange?.(Boolean(sshPassword || mpSecret || (snapshot && (Object.keys(snapshot) as (keyof SettingsForm)[])
      .some(key => String(values[key]) !== String(snapshot[key])))));
  }, [form, sshPassword, mpSecret, onDirtyChange]);
  useEffect(reportDirty, [reportDirty]);

  useEffect(() => {
    const settings = settingsQuery.data?.settings;
    if (!settings) return;
    const next = toForm(settings);
    form.setFieldsValue(next);
    loadedRef.current = next;
    onDirtyChange?.(pendingSecrets.current);
  }, [form, settingsQuery.data, onDirtyChange]);

  const confirmOverwrite = (conflicts: string[]) =>
    new Promise<boolean>((resolve) => {
      modal.confirm({
        className: 'scientific-settings-modal',
        title: '后台配置已被修改',
        content: `检测到这些字段在别处已更新：${conflicts.join('、')}。继续保存会用本页的值覆盖它。`,
        okText: '仍然覆盖',
        cancelText: '用后台值刷新',
        onOk: () => resolve(true),
        onCancel: () => resolve(false),
      });
    });

  const save = async (values: SettingsForm) => {
    if (!settingsQuery.data?.settings || settingsQuery.isError) return;
    setSaving(true);
    try {
      // 保存前对齐服务端：页面加载后若他处改过配置，先确认再覆盖，
      // 避免把旧的 SSH 用户名/主机等设置写回去（AI 设置页同款保护）。
      const snapshot = loadedRef.current;
      const latest = await settingsQuery.refetch();
      const latestSettings = latest?.data?.settings;
      if (latest?.isError || !latestSettings) {
        message.error('无法确认最新设置，未保存');
        return;
      }
      const latestForm = toForm(latestSettings);
      const conflicts = (Object.keys(values) as (keyof SettingsForm)[])
        .filter((key) => snapshot
          && String(latestForm[key]) !== String(snapshot[key])
          && String(values[key]) !== String(latestForm[key]));
      loadedRef.current = latestForm;
      if (conflicts.length && !(await confirmOverwrite(conflicts))) {
        form.setFieldsValue(latestForm);
        message.info('已改用后台最新配置，未覆盖');
        return;
      }
      await toolboxApi.saveSettings({ ...values });
      message.success('Toolbox 执行设置已保存');
      await settingsQuery.refetch();
    } catch (error) {
      message.error(error instanceof Error ? error.message : '保存设置失败');
    } finally {
      setSaving(false);
    }
  };

  const testSsh = async () => {
    if (!settingsQuery.data?.settings || settingsQuery.isError) return;
    setTesting(true);
    try {
      const response = await toolboxApi.testSsh();
      message[response.ok ? 'success' : 'warning'](response.message);
    } catch (error) {
      message.error(error instanceof Error ? error.message : 'SSH 测试失败');
    } finally {
      setTesting(false);
    }
  };

  const saveSecret = async (kind: 'ssh' | 'mp', value: string) => {
    if (!settingsQuery.data?.settings || settingsQuery.isError) return;
    try {
      await toolboxApi.setSecret(kind, value);
      message.success(value ? '凭据已安全保存，不会回显' : '凭据已清除');
      if (kind === 'ssh') setSshPassword('');
      else setMpSecret('');
      await settingsQuery.refetch();
    } catch (error) {
      message.error(error instanceof Error ? error.message : '凭据更新失败');
    }
  };

  if (settingsQuery.isPending) return <div className="scientific-settings settings-loading" role="status">{contextHolder}{embedded && <><Title level={2} id="settings-models" tabIndex={-1}>模型与材料</Title>{modelNotice}<Title level={2} id="settings-execution" tabIndex={-1}>超算与执行</Title></>}<Spin aria-label="Toolbox 设置加载中" /><Text type="secondary">正在读取执行设置…</Text></div>;
  if (settingsQuery.isError || !settingsQuery.data?.settings) {
    return <div className="scientific-settings settings-unavailable">
      {contextHolder}
      {embedded && <><Title level={2} id="settings-models" tabIndex={-1}>模型与材料</Title>{modelNotice}</>}
      <div className="settings-heading"><Title level={embedded ? 2 : 1} id={embedded ? 'settings-execution' : undefined} tabIndex={-1}>{embedded ? '超算与执行' : 'Toolbox 执行设置'}</Title></div>
      <Alert type="error" showIcon message="无法读取 Toolbox 设置" description="设置尚未读取成功，请检查 Toolbox 服务后重试。" />
      <Button onClick={() => void settingsQuery.refetch()} loading={settingsQuery.isFetching}>重试读取设置</Button>
      <Link to="/toolbox/potcar">管理本地 POTCAR 赝势库</Link>
    </div>;
  }

  return (
    <div className="scientific-settings settings-toolbox">
      {contextHolder}
      {!embedded && <div className="settings-heading"><Title level={1}>Toolbox 执行设置</Title><Paragraph type="secondary">这些设置不依赖模型或智能模式。SSH 测试只检查连接，不提交作业。</Paragraph></div>}
      <Paragraph><Link to="/toolbox/potcar">管理本地 POTCAR 赝势库</Link>（登记、扫描与默认库，不改变 SSH 或模型设置）</Paragraph>
      {embedded && <Title level={2} id="settings-models" tabIndex={-1}>模型与材料</Title>}
      {modelNotice}
      <Row gutter={[18, 18]}>
        <Col xs={24} lg={12}>
          <Card title="Materials Project 密钥">
            <Text type="secondary">仅用于既有 MP 搜索与导入；基础本地任务不要求配置。</Text>
            <Space.Compact className="settings-secret-controls" style={{ width: '100%', marginTop: 12 }}><Input.Password aria-label="新的 Materials Project 密钥" value={mpSecret} onChange={(event) => setMpSecret(event.target.value)} placeholder={settingsQuery.data?.settings.materials_project.configured ? '已配置；输入新值可替换' : '未配置'} /><Button disabled={!mpSecret} onClick={() => void saveSecret('mp', mpSecret)}>保存</Button><Button danger onClick={() => void saveSecret('mp', '')}>清除</Button></Space.Compact>
          </Card>
        </Col>
      </Row>
      {embedded && <Title level={2} id="settings-execution" tabIndex={-1}>超算与执行</Title>}
      <Form form={form} layout="vertical" onValuesChange={reportDirty} onFinish={(values) => void save(values)}>
        <Row gutter={[18, 18]}>
          <Col xs={24} lg={10}>
            <Card title="监控与并发">
              <Form.Item name="max_jobs" label="最大同时作业数" rules={[{ required: true }]}><InputNumber min={1} precision={0} style={{ width: '100%' }} /></Form.Item>
              <Form.Item name="poll_interval_seconds" label="后台轮询间隔（秒）" extra="允许 10–3600 秒；任务页面每 5 秒读取已保存状态，不会因此触发 SSH。" rules={[{ required: true }, { type: 'number', min: 10, max: 3600 }]}><InputNumber min={10} max={3600} precision={0} style={{ width: '100%' }} /></Form.Item>
            </Card>
          </Col>
          <Col xs={24} lg={14}>
            <Card title="SSH 与调度器">
              <Row gutter={12}>
                <Col xs={24} sm={12}><Form.Item name="ssh_name" label="连接名称"><Input /></Form.Item></Col>
                <Col xs={24} sm={12}><Form.Item name="scheduler_backend" label="调度器"><Select options={[{ value: 'slurm', label: 'Slurm' }, { value: 'paracloud', label: 'ParaCloud' }]} /></Form.Item></Col>
                <Col xs={24} sm={16}><Form.Item name="ssh_host" label="主机"><Input /></Form.Item></Col>
                <Col xs={24} sm={8}><Form.Item name="ssh_port" label="端口"><InputNumber min={1} max={65535} style={{ width: '100%' }} /></Form.Item></Col>
                <Col span={24}><Form.Item name="ssh_username" label="用户名"><Input /></Form.Item></Col>
                <Col span={24}><Form.Item name="ssh_known_hosts_path" label="known_hosts 路径"><Input /></Form.Item></Col>
                <Col span={24}><Form.Item name="ssh_identity_file" label="私钥路径"><Input /></Form.Item></Col>
              </Row>
              <Space wrap><Button htmlType="submit" type="primary" loading={saving}>保存执行设置</Button><Button loading={testing} onClick={() => void testSsh()}>测试 SSH 连接</Button></Space>
            </Card>
          </Col>
        </Row>
      </Form>
      <Row gutter={[18, 18]}>
        <Col xs={24} lg={12}>
          <Card title="SSH 密码">
            <Text type="secondary">密码写入系统凭据存储，页面不会读取或回显。</Text>
            <Space.Compact className="settings-secret-controls" style={{ width: '100%', marginTop: 12 }}><Input.Password aria-label="新的 SSH 密码" value={sshPassword} onChange={(event) => setSshPassword(event.target.value)} placeholder="输入新密码以替换" /><Button disabled={!sshPassword} onClick={() => void saveSecret('ssh', sshPassword)}>保存</Button><Button danger onClick={() => void saveSecret('ssh', '')}>清除</Button></Space.Compact>
          </Card>
        </Col>

      </Row>
    </div>
  );
};

export default ToolboxSettingsPage;
