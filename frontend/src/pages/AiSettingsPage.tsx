// 全局设置页 — secrets are write-only: status + replace/clear, never reveal.
import React, { useCallback, useEffect, useRef, useState } from "react";
import { Card, Typography, Space, Button, Input, Col, Row, Spin, Switch, Select, message, Alert, Modal } from "antd";
import { Link } from "react-router-dom";
import { LinkOutlined, SafetyCertificateOutlined, RocketOutlined } from "@ant-design/icons";
import ErrorAlert from "../components/common/ErrorAlert";
import SecretInput from "../components/ai/SecretInput";
import type { AiSecretState, AiSettingsOut } from "../types/ai";
import { useAiSettings, useAiSettingsSave, useAiSettingsTest, useAiSecretStatus, useAiSecretUpdate } from "../hooks/useApi";
import PlotPreferencesSettings from "../components/postprocessing/PlotPreferencesSettings";
import ToolboxSettingsPage from "./ToolboxSettingsPage";
import "./scientific-settings.css";

const { Title, Text, Paragraph } = Typography;

const normalizeSecret = (value: AiSecretState | boolean | undefined): AiSecretState => {
  if (typeof value === "boolean") {
    return { configured: value, source: value ? "local_config" : "none", manageable: true };
  }
  return value ?? { configured: false, source: "none", manageable: true };
};

interface Form {
  max_jobs: string;
  poll_interval_seconds: string;
  llm_base_url: string;
  llm_model: string;
  llm_provider: string;
  llm_enable_thinking: boolean;
  llm_api_key: string;
  mp_api_key: string;
  ssh_name: string;
  ssh_host: string;
  ssh_port: string;
  ssh_username: string;
  ssh_known_hosts_path: string;
  ssh_identity_file: string;
  scheduler_backend: "slurm" | "paracloud";
  ssh_password: string;
}

type TestProvider = "llm" | "mp" | "ssh";
type TestResult = { ok: boolean; message: string };

const FORM_FIELDS: (keyof Form)[] = [
  "max_jobs", "poll_interval_seconds", "llm_base_url", "llm_model", "llm_provider",
  "llm_enable_thinking", "ssh_name", "ssh_host", "ssh_port", "ssh_username",
  "ssh_known_hosts_path", "ssh_identity_file", "scheduler_backend",
];

const validateForm = (value: Form): Partial<Record<keyof Form, string>> => {
  const errors: Partial<Record<keyof Form, string>> = {};
  const maxJobs = Number(value.max_jobs);
  if (!/^\d+$/.test(value.max_jobs.trim()) || !Number.isInteger(maxJobs) || maxJobs < 1) {
    errors.max_jobs = "最大作业数必须是至少为 1 的整数";
  }
  const interval = Number(value.poll_interval_seconds);
  if (!/^\d+$/.test(value.poll_interval_seconds.trim()) || !Number.isInteger(interval) || interval < 10 || interval > 3600) {
    errors.poll_interval_seconds = "轮询间隔必须是 10–3600 秒的整数";
  }
  return errors;
};

/** 服务端设置 -> 表单值。密钥字段永远留空（写专用接口，不回显）。 */
const toForm = (settings: AiSettingsOut): Form => ({
  max_jobs: String(settings.max_jobs ?? 20),
  poll_interval_seconds: String(settings.poll_interval_seconds ?? 60),
  llm_base_url: settings.llm.base_url ?? "",
  llm_model: settings.llm.model ?? "",
  llm_provider: settings.llm.provider ?? "auto",
  llm_enable_thinking: settings.llm.enable_thinking ?? false,
  llm_api_key: "",
  mp_api_key: "",
  ssh_name: settings.ssh.name ?? "",
  ssh_host: settings.ssh.host ?? "",
  ssh_port: String(settings.ssh.port ?? 22),
  ssh_username: settings.ssh.username ?? "",
  ssh_known_hosts_path: settings.ssh.known_hosts_path ?? "",
  ssh_identity_file: settings.ssh.identity_file ?? "",
  scheduler_backend: settings.ssh.scheduler_backend ?? "slurm",
  ssh_password: "",
});

const AiSettingsPage: React.FC<{ embedded?: boolean; onDirtyChange?: (dirty: boolean) => void }> = ({ embedded = false, onDirtyChange }) => {
  const [modal, contextHolder] = Modal.useModal();
  const settingsQuery = useAiSettings(true);
  const secretQuery = useAiSecretStatus(true);
  const saveMutation = useAiSettingsSave();
  const testMutation = useAiSettingsTest();
  const secretMutation = useAiSecretUpdate();

  const settings = settingsQuery.data?.settings;
  const rawSecrets = secretQuery.data?.secrets;
  const secrets = {
    llm: normalizeSecret(rawSecrets?.llm),
    mp: normalizeSecret(rawSecrets?.mp),
    ssh: normalizeSecret(rawSecrets?.ssh),
  };
  const [form, setForm] = useState<Form>({} as Form);
  const [fieldErrors, setFieldErrors] = useState<Partial<Record<keyof Form, string>>>({});
  const [testResults, setTestResults] = useState<Partial<Record<TestProvider, TestResult>>>({});
  const [testNotice, setTestNotice] = useState<string | null>(null);
  // 页面加载时服务端给过的值；保存前与最新值比对，避免用旧表单覆盖他处的改动。
  const loadedRef = useRef<Form | null>(null);
  const [fallbackPinned, setFallbackPinned] = useState(false);
  const fallbackDirty = useRef(false);
  const reportFallbackDirty = useCallback((dirty: boolean) => {
    fallbackDirty.current = dirty;
    if (dirty) setFallbackPinned(true);
    onDirtyChange?.(dirty);
  }, [onDirtyChange]);

  useEffect(() => {
    if (settings) {
      const next = toForm(settings);
      setForm(next);
      loadedRef.current = next;
      setFieldErrors({});
    }
  }, [settings]);

  // Non-secret fields are replaceable (including clearing strings). Secrets use
  // the dedicated write-only endpoint and blank means "leave unchanged".
  const patchFields = ["max_jobs", "poll_interval_seconds", "llm_provider", "llm_base_url", "llm_model", "ssh_name", "ssh_host", "ssh_username", "ssh_port", "ssh_known_hosts_path", "ssh_identity_file"]
    .reduce<Record<string, unknown>>((acc, k) => {
      if (k === "max_jobs" || k === "ssh_port" || k === "poll_interval_seconds") acc[k] = Number(form[k as keyof Form]);
      else acc[k] = (form[k as keyof Form] as unknown as string);
      return acc;
    }, {});
  patchFields.llm_enable_thinking = form.llm_enable_thinking;
  patchFields.scheduler_backend = form.scheduler_backend;
  const confirmOverwrite = (conflicts: string[]) =>
    new Promise<boolean>((resolve) => {
      modal.confirm({
        className: "scientific-settings-modal",
        title: "后台配置已被修改",
        content: `检测到这些字段在别处已更新：${conflicts.join("、")}。继续保存会用本页的值覆盖它。`,
        okText: "仍然覆盖",
        cancelText: "用后台值刷新",
        onOk: () => resolve(true),
        onCancel: () => resolve(false),
      });
    });

  const onSubmit = async () => {
    const errors = validateForm(form);
    if (Object.keys(errors).length > 0) {
      setFieldErrors(errors);
      message.error("请先修正设置中的数值错误");
      return;
    }
    try {
      // 保存前对齐一次服务端：页面加载后若他处改过配置，先确认再覆盖，
      // 避免用旧表单把新的 SSH 用户名等设置写回去。
      const snapshot = loadedRef.current;
      const latest = await settingsQuery.refetch();
      const latestSettings = latest?.data?.settings;
      if (latest?.isError || !latestSettings) {
        message.error("无法确认最新设置，未保存");
        return;
      }
      {
        const latestForm = toForm(latestSettings);
        const conflicts = (Object.keys(patchFields) as (keyof Form)[]).filter((key) => {
          if (!snapshot) return false;
          const serverMoved = String(latestForm[key]) !== String(snapshot[key]);
          return serverMoved && String(form[key]) !== String(latestForm[key]);
        });
        loadedRef.current = latestForm;
        if (conflicts.length && !(await confirmOverwrite(conflicts))) {
          setForm({
            ...latestForm,
            llm_api_key: form.llm_api_key,
            mp_api_key: form.mp_api_key,
            ssh_password: form.ssh_password,
          });
          message.info("已改用后台最新配置，未覆盖");
          return;
        }
      }
      await saveMutation.mutateAsync(patchFields);
      const replacements = [
        ["llm", form.llm_api_key], ["mp", form.mp_api_key], ["ssh", form.ssh_password],
      ] as const;
      for (const [kind, value] of replacements) {
        if (value) await secretMutation.mutateAsync({ kind, action: "replace", value });
      }
      setForm((previous) => ({ ...previous, llm_api_key: "", mp_api_key: "", ssh_password: "" }));
      loadedRef.current = { ...form, llm_api_key: "", mp_api_key: "", ssh_password: "" };
      setTestResults({});
      settingsQuery.refetch();
      secretQuery.refetch();
      setTestNotice(null);
      message.success("设置已保存（仅本地）");
    } catch (err) {
      message.error(err instanceof Error ? err.message : "保存失败");
    }
  };

  const hasUnsavedChanges = useCallback(() => {
    const loaded = loadedRef.current;
    if (!loaded) return false;
    if (form.llm_api_key || form.mp_api_key || form.ssh_password) return true;
    return FORM_FIELDS.some((key) => String(form[key]) !== String(loaded[key]));
  }, [form]);
  useEffect(() => {
    if (!embedded || (settings && rawSecrets && !fallbackPinned)) onDirtyChange?.(hasUnsavedChanges());
  }, [hasUnsavedChanges, onDirtyChange, embedded, settings, rawSecrets, fallbackPinned]);

  const test = async (provider: TestProvider) => {
    if (hasUnsavedChanges()) {
      setTestNotice("当前表单有未保存修改。请先点击“保存设置”，再测试已保存配置。");
      return;
    }
    try {
      const res = await testMutation.mutateAsync(provider);
      const result = { ok: Boolean(res?.ok), message: res?.message || "测试完成，但服务未返回详细说明" };
      setTestResults((previous) => ({ ...previous, [provider]: result }));
      setTestNotice(null);
    } catch (err) {
      const result = { ok: false, message: err instanceof Error ? err.message : "测试失败" };
      setTestResults((previous) => ({ ...previous, [provider]: result }));
    }
  };

  const clearSecret = (kind: "llm" | "mp" | "ssh") => async () => {
    try {
      await secretMutation.mutateAsync({ kind, action: "clear" });
      await secretQuery.refetch();
      setTestResults({});
      message.success("密钥已清除");
    } catch (err) {
      message.error(err instanceof Error ? err.message : "清除失败");
    }
  };

  if (embedded && (settingsQuery.isLoading || secretQuery.isLoading || settingsQuery.error || secretQuery.error || !settings || !rawSecrets || fallbackPinned)) {
    return <>{contextHolder}<ToolboxSettingsPage embedded onDirtyChange={reportFallbackDirty} modelNotice={<>
      <Alert type="info" showIcon message={settings && rawSecrets && !settingsQuery.error && !secretQuery.error ? "模型配置已可读取" : "模型配置尚未就绪"}
        description="基础材料密钥、超算与执行设置仍可使用。不会等待模型服务才开放基础设置。" />
      <Button onClick={async () => {
        if (fallbackDirty.current) { message.warning("请先保存或放弃当前执行设置修改，再读取模型配置。"); return; }
        const [latest, latestSecrets] = await Promise.all([settingsQuery.refetch(), secretQuery.refetch()]);
        if (!latest.isError && !latestSecrets.isError) setFallbackPinned(false);
      }}>重试读取模型配置</Button>
    </>} /><PlotPreferencesSettings /></>;
  }
  if (settingsQuery.isLoading || secretQuery.isLoading) return <div className="scientific-settings settings-loading" role="status">{contextHolder}<Spin aria-label="智能设置加载中" /><Text type="secondary">正在读取智能设置与凭据状态…</Text></div>;
  if (settingsQuery.error || secretQuery.error || !settings || !rawSecrets) {
    return <div className="scientific-settings settings-unavailable">
      {contextHolder}
      <div className="settings-heading"><Title level={1}>智能体设置</Title></div>
      <Alert type="info" showIcon message="智能模式是可选服务，当前无法读取智能设置" description={<>智能服务未启动或暂时不可达时，仍可前往 <Link to="/toolbox/settings">Toolbox 执行设置</Link> 使用基础计算功能。恢复智能服务后可手动重试读取。</>} />
      {settingsQuery.error && <ErrorAlert error={settingsQuery.error} title="智能设置读取失败" />}
      {secretQuery.error && <ErrorAlert error={secretQuery.error} title="凭据状态读取失败" />}
      <Button onClick={() => { void settingsQuery.refetch(); void secretQuery.refetch(); }}>重试读取设置</Button>
      <Link to="/toolbox/potcar">管理本地 POTCAR 赝势库</Link>
      <PlotPreferencesSettings />
    </div>;
  }

  const section = (title: string, icon: React.ReactNode, children: React.ReactNode) => (
    <Row gutter={24}>
      <Col span={24}>
        <Card className="settings-section" title={<Space><>{icon}</><span>{title}</span></Space>} styles={{ header: { whiteSpace: "normal" }, title: { whiteSpace: "normal", overflowWrap: "anywhere" } }}>
          {children}
        </Card>
      </Col>
    </Row>
  );

  const set = (k: keyof Form) => (e: React.ChangeEvent<HTMLInputElement>) => {
    setForm((p) => ({ ...p, [k]: e.target.value }));
    setFieldErrors((previous) => ({ ...previous, [k]: undefined }));
    setTestResults({});
    setTestNotice(null);
  };

  const setNumber = (k: "max_jobs" | "poll_interval_seconds") => (e: React.ChangeEvent<HTMLInputElement>) => {
    setForm((p) => ({ ...p, [k]: e.target.value }));
    setFieldErrors((previous) => ({ ...previous, [k]: undefined }));
    setTestResults({});
    setTestNotice(null);
  };

  const testResult = (provider: TestProvider) => {
    const result = testResults[provider];
    if (!result) return null;
    return <Alert type={result.ok ? "success" : "error"} showIcon message={result.message} style={{ marginTop: 12, overflowWrap: "anywhere" }} />;
  };

  const testButton = (provider: TestProvider, label: string) => (
    <Button onClick={() => test(provider)} loading={testMutation.isPending && testMutation.variables === provider} style={{ maxWidth: "100%", height: "auto", minHeight: 32, whiteSpace: "normal" }}>
      {label}
    </Button>
  );

  return (
    <div className="scientific-settings settings-ai">
      {contextHolder}
      <div className="settings-heading settings-heading-actions">
        <div className="settings-heading-copy">
          {!embedded && <Title level={1}>智能体设置</Title>}
          <Paragraph type="secondary" style={{ margin: 0 }}>
            所有私人信息仅本地保存。已保存密钥不可查看或复制，只能整体替换或清除。
          </Paragraph>
        </div>
        <Button type="primary" size="large" onClick={onSubmit} loading={saveMutation.isPending}>保存设置</Button>
      </div>

      <Paragraph><Link to="/toolbox/potcar">管理本地 POTCAR 赝势库</Link>（独立于模型配置，同一登记供工具复用）</Paragraph>
      {embedded && <Title level={2} id="settings-models" tabIndex={-1}>模型与材料</Title>}
      {section("LLM", <LinkOutlined />, (
        <Row gutter={[16, 16]}>
          <Col span={24}><label htmlFor="settings-llm_base_url"><Text strong>接口地址</Text></label><Input id="settings-llm_base_url" value={form.llm_base_url} onChange={set("llm_base_url")} placeholder="https://api.openai.com/v1" /></Col>
          <Col xs={24} sm={12}><label htmlFor="settings-llm_model"><Text strong>模型名称</Text></label><Input id="settings-llm_model" value={form.llm_model} onChange={set("llm_model")} placeholder="gpt-4o" /></Col>
          <Col xs={24} sm={12}><label htmlFor="settings-llm_provider"><Text strong>provider</Text></label><Input id="settings-llm_provider" value={form.llm_provider} onChange={set("llm_provider")} placeholder="auto" /></Col>
          <Col span={24} role="group" aria-labelledby="llm-key-label"><Text id="llm-key-label" strong>API Key</Text><SecretInput hasSecret={secrets.llm.configured} manageable={secrets.llm.manageable} source={secrets.llm.source} value={form.llm_api_key} onChange={(v) => { setForm((p) => ({ ...p, llm_api_key: v })); setTestResults({}); setTestNotice(null); }} onClear={clearSecret("llm")} placeholder={secrets.llm.configured ? "输入新值以整体替换" : "未配置 LLM key，填写后保存" } /></Col>
          <Col span={24}><Space wrap><Switch aria-label="深度思考" aria-describedby="thinking-help" checked={form.llm_enable_thinking} onChange={(v) => { setForm((p) => ({ ...p, llm_enable_thinking: v })); setTestResults({}); setTestNotice(null); }} />
            <Text strong>深度思考</Text></Space><Text id="thinking-help" type="secondary" style={{ display: "block", fontSize: 12, marginTop: 4 }}>开启后请求体携带 thinking 参数，模型输出增量思考过程（是否支持以接入模型/网关为准）。关闭此选项会关闭官方 DeepSeek 接口的思考模式；其他接口的关闭效果取决于提供方默认设置。</Text></Col>
          <Col span={24}>{testButton("llm", "测试 LLM（已保存配置）")}{testResult("llm")}</Col>
        </Row>
      ))}

      {section("Materials Project", <SafetyCertificateOutlined />, (
        <Row gutter={[16, 16]}>
          <Col span={24} role="group" aria-labelledby="mp-key-label" aria-describedby="mp-key-help"><Text id="mp-key-label" strong>MP API Key</Text><SecretInput hasSecret={secrets.mp.configured} manageable={secrets.mp.manageable} source={secrets.mp.source} value={form.mp_api_key} onChange={(v) => { setForm((p) => ({ ...p, mp_api_key: v })); setTestResults({}); setTestNotice(null); }} onClear={clearSecret("mp")} placeholder={secrets.mp.configured ? "输入新值以整体替换" : "未配置，填写后保存" } /><Text id="mp-key-help" type="secondary" style={{ display: "block", fontSize: 12, marginTop: 4 }}>用于 Materials Project 材料搜索与结构导入；密钥可替换或清除。</Text></Col>
          <Col span={24}>{testButton("mp", "测试已保存的 Materials Project 配置")}{testResult("mp")}</Col>
        </Row>
      ))}

      {embedded && <Title level={2} id="settings-execution" tabIndex={-1}>超算与执行</Title>}
      {section("超算 SSH 直连", <RocketOutlined />, (
        <Row gutter={[16, 16]}>
          <Col xs={24} md={8}><label htmlFor="settings-ssh_name"><Text strong>连接名称</Text></label><Input id="settings-ssh_name" value={form.ssh_name} onChange={set("ssh_name")} placeholder="如：超算A" /></Col>
          <Col xs={24} md={8}><label htmlFor="settings-ssh_host"><Text strong>主机地址</Text></label><Input id="settings-ssh_host" value={form.ssh_host} onChange={set("ssh_host")} placeholder="如：login.hpc.example.com" /></Col>
          <Col xs={24} md={8}><label htmlFor="settings-ssh_port"><Text strong>端口</Text></label><Input id="settings-ssh_port" value={form.ssh_port} onChange={set("ssh_port")} /></Col>
          <Col xs={24} sm={12}><label htmlFor="settings-ssh_username"><Text strong>用户名</Text></label><Input id="settings-ssh_username" value={form.ssh_username} onChange={set("ssh_username")} /></Col>
          <Col xs={24} sm={12} role="group" aria-labelledby="ssh-password-label" aria-describedby="ssh-password-help"><Text id="ssh-password-label" strong>密码</Text><SecretInput hasSecret={secrets.ssh.configured} manageable={secrets.ssh.manageable} source={secrets.ssh.source} value={form.ssh_password} onChange={(v) => { setForm((p) => ({ ...p, ssh_password: v })); setTestResults({}); setTestNotice(null); }} onClear={clearSecret("ssh")} placeholder={secrets.ssh.configured ? "输入新值以整体替换" : "未配置密码，填写后保存" } /><Text id="ssh-password-help" type="secondary" style={{ display: "block", fontSize: 12, marginTop: 4 }}>密码只可替换或清除，不回显已保存值。</Text></Col>
          <Col span={24}><label htmlFor="settings-ssh_known_hosts_path"><Text strong>known_hosts 路径</Text></label><Input id="settings-ssh_known_hosts_path" value={form.ssh_known_hosts_path} onChange={set("ssh_known_hosts_path")} aria-describedby="known-hosts-help" placeholder="留空则使用系统 known_hosts" /><Text id="known-hosts-help" type="secondary" style={{ display: "block", fontSize: 12, marginTop: 4 }}>SSH 仅信任系统或指定 known_hosts 中的主机密钥；未知或不匹配会在认证前拒绝。</Text></Col>
          <Col span={24}><label htmlFor="settings-ssh_identity_file"><Text strong>SSH 密钥文件路径（可选）</Text></label><Input id="settings-ssh_identity_file" aria-label="SSH 密钥文件路径" value={form.ssh_identity_file} onChange={set("ssh_identity_file")} aria-describedby="identity-file-help" placeholder="后端所在电脑上的绝对路径；只填路径，不粘贴私钥" /><Text id="identity-file-help" type="secondary" style={{ display: "block", fontSize: 12, marginTop: 4 }}>后端所在电脑上的绝对路径；只填路径，不粘贴私钥。填写密钥路径时仅使用该密钥，不回退密码或自动寻找其他密钥。当前不支持需口令解锁的密钥；换电脑需重新配置当地路径。</Text></Col>
          <Col span={24}><label htmlFor="settings-scheduler"><Text strong>调度平台</Text></label><Select id="settings-scheduler" aria-label="调度平台" aria-describedby="scheduler-help" style={{ width: "100%" }} value={form.scheduler_backend} onChange={(v) => { setForm(p => ({ ...p, scheduler_backend: v })); setTestResults({}); setTestNotice(null); }} options={[{ value: "slurm", label: "标准 Slurm（sbatch / squeue）" }, { value: "paracloud", label: "ParaCloud 云超算（cbatch / cqueue）" }]} /><Text id="scheduler-help" type="secondary" style={{ display: "block", fontSize: 12, marginTop: 4 }}>按实际平台选择，不能仅凭命令存在判断。更换平台或SSH身份后必须重新预检和确认；已有作业应保持原连接配置。</Text></Col>
          <Col span={24}>{testButton("ssh", "测试已保存的 SSH 配置")}{testResult("ssh")}</Col>
        </Row>
      ))}

      {section("作业执行／监控", <RocketOutlined />, (
        <Row gutter={[16, 16]}>
          <Col xs={24} sm={12}>
            <label htmlFor="max-jobs"><Text strong>最大作业数</Text></label>
            <Input id="max-jobs" aria-describedby={fieldErrors.max_jobs ? "max-jobs-help max-jobs-error" : "max-jobs-help"} aria-invalid={Boolean(fieldErrors.max_jobs)} type="number" min={1} step={1} value={form.max_jobs} onChange={setNumber("max_jobs")} style={{ width: "100%" }} aria-label="最大作业数" />
            <Text id="max-jobs-help" type="secondary" style={{ display: "block", fontSize: 12, marginTop: 4 }}>本软件提交时参考该超算账号排队和运行中的作业数量，并按此上限限制新提交。至少为 1。</Text>
            {fieldErrors.max_jobs && <Text id="max-jobs-error" type="danger" style={{ display: "block" }}>{fieldErrors.max_jobs}</Text>}
          </Col>
          <Col xs={24} sm={12}>
            <label htmlFor="poll-interval"><Text strong>监控轮询间隔（秒）</Text></label>
            <Input id="poll-interval" aria-describedby={fieldErrors.poll_interval_seconds ? "poll-interval-help poll-interval-error" : "poll-interval-help"} aria-invalid={Boolean(fieldErrors.poll_interval_seconds)} type="number" min={10} max={3600} step={1} value={form.poll_interval_seconds} onChange={setNumber("poll_interval_seconds")} style={{ width: "100%" }} aria-label="监控轮询间隔（秒）" />
            <Text id="poll-interval-help" type="secondary" style={{ display: "block", fontSize: 12, marginTop: 4 }}>影响已提交作业的状态查询频率；范围 10–3600 秒，默认 60 秒。</Text>
            {fieldErrors.poll_interval_seconds && <Text id="poll-interval-error" type="danger" style={{ display: "block" }}>{fieldErrors.poll_interval_seconds}</Text>}
          </Col>
          <Col span={24}><Text type="secondary" style={{ fontSize: 12 }}>作业执行仍需按当前流程人工准备、预检和确认；设置页不会自动提交作业。</Text></Col>
        </Row>
      ))}

      <PlotPreferencesSettings />
      {testNotice && <Alert type="warning" showIcon message={testNotice} style={{ marginBottom: 16 }} />}
    </div>
  );
};

export default AiSettingsPage;
