// 全局设置页 — 对接真实 /ai/v1/settings（GET/PUT/test）+ 密钥回显（掩码 + 点眼睛取原文）
import React, { useEffect, useState } from "react";
import { Card, Typography, Space, Button, Input, Col, Row, Spin, Collapse, Switch, message } from "antd";
import { LinkOutlined, SafetyCertificateOutlined, RocketOutlined } from "@ant-design/icons";
import ErrorAlert from "../components/common/ErrorAlert";
import SecretInput from "../components/ai/SecretInput";
import { useAiSettings, useAiSettingsSave, useAiSettingsTest, useAiSecretStatus, useAiSecretReveal } from "../hooks/useApi";

const { Title, Text, Paragraph } = Typography;

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
  ssh_password: string;
}

const AiSettingsPage: React.FC = () => {
  const settingsQuery = useAiSettings(true);
  const secretQuery = useAiSecretStatus(true);
  const saveMutation = useAiSettingsSave();
  const testMutation = useAiSettingsTest();
  const revealMutation = useAiSecretReveal();

  const settings = settingsQuery.data?.settings;
  const secrets = secretQuery.data?.secrets ?? { llm: false, mp: false, ssh: false };
  const [form, setForm] = useState<Form>({} as Form);

  useEffect(() => {
    if (settings) {
      setForm({
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
        ssh_password: "",
      });
    }
  }, [settings, settingsQuery.data]);

  // 收集非空字段作为 PUT patch；密钥字段只在用户新填了内容时才覆盖，留空=不修改。
  const patchFields = ["max_jobs", "poll_interval_seconds", "llm_base_url", "llm_model", "ssh_name", "ssh_host", "ssh_username", "ssh_port"]
    .filter((k) => form[k as keyof Form] !== "")
    .reduce<Record<string, unknown>>((acc, k) => {
      if (k === "max_jobs" || k === "ssh_port" || k === "poll_interval_seconds") acc[k] = Number(form[k as keyof Form]);
      else acc[k] = (form[k as keyof Form] as unknown as string);
      return acc;
    }, {});
  if (form.llm_api_key) patchFields.llm_api_key = form.llm_api_key;
  patchFields.llm_enable_thinking = form.llm_enable_thinking;
  if (form.mp_api_key) patchFields.mp_api_key = form.mp_api_key;
  if (form.ssh_password) patchFields.ssh_password = form.ssh_password;

  const onSubmit = async () => {
    try {
      await saveMutation.mutateAsync(patchFields);
      settingsQuery.refetch();
      secretQuery.refetch();
      message.success("设置已保存（仅本地）");
    } catch (err) {
      message.error(err instanceof Error ? err.message : "保存失败");
    }
  };

  const test = async (provider: string) => {
    try {
      const res = await testMutation.mutateAsync(provider);
      message.info(`${provider.toUpperCase()} 连通测试：${res.message}`);
    } catch (err) {
      message.error(err instanceof Error ? err.message : "测试失败");
    }
  };

  const reveal = (kind: "llm" | "mp" | "ssh") => async () => {
    const r = await revealMutation.mutateAsync(kind);
    return r.value;
  };

  if (settingsQuery.isLoading) return <Spin style={{ display: "block", margin: "80px auto" }} />;
  if (settingsQuery.error) {
    return <ErrorAlert error={settingsQuery.error} onRetry={settingsQuery.refetch} title="设置加载失败" />;
  }

  const section = (title: string, icon: React.ReactNode, children: React.ReactNode) => (
    <Row gutter={24}>
      <Col span={24}>
        <Card title={<Space><>{icon}</><span>{title}</span></Space>} style={{ marginBottom: 16 }}>
          {children}
        </Card>
      </Col>
    </Row>
  );

  const set = (k: keyof Form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm((p) => ({ ...p, [k]: e.target.value }));

  return (
    <div style={{ maxWidth: 860, margin: "0 auto", padding: "8px 0" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 20 }}>
        <div>
          <Title level={3} style={{ marginBottom: 4 }}>智能体设置</Title>
          <Paragraph type="secondary" style={{ margin: 0 }}>
            所有私人信息（MP key、LLM 密钥、SSH 密码）仅本地保存，不上传、不进项目；已保存后只显示掩码，点眼睛才临时显示原文。
          </Paragraph>
        </div>
        <Button type="primary" size="large" onClick={onSubmit} loading={saveMutation.isPending}>保存设置</Button>
      </div>

      {section("LLM 模型", <LinkOutlined />, (
        <Row gutter={16}>
          <Col span={24}><Text strong>接口地址</Text><Input value={form.llm_base_url} onChange={set("llm_base_url")} placeholder="https://api.openai.com/v1" /></Col>
          <Col span={12}><Text strong>模型名称</Text><Input value={form.llm_model} onChange={set("llm_model")} placeholder="gpt-4o" /></Col>
          <Col span={12}><Text strong>provider</Text><Input value={form.llm_provider} onChange={set("llm_provider")} placeholder="auto" /></Col>
          <Col span={24}><Text strong>API Key</Text><SecretInput hasSecret={secrets.llm} value={form.llm_api_key} onChange={(v) => setForm((p) => ({ ...p, llm_api_key: v }))} onReveal={reveal("llm")} placeholder={secrets.llm ? "已保存（点击眼睛临时查看原文）" : "未配置 LLM key，填写后保存" } /></Col>
          <Col span={24}><Space><Switch checked={form.llm_enable_thinking} onChange={(v) => setForm((p) => ({ ...p, llm_enable_thinking: v }))} />
            <Text strong>深度思考</Text><Text type="secondary" style={{ fontSize: 12 }}>开启后请求体携带 thinking 参数，模型输出增量思考过程（是否支持以接入模型/网关为准）。</Text></Space></Col>
        </Row>
      ))}

      {section("超算 SSH 直连", <RocketOutlined />, (
        <Row gutter={16}>
          <Col span={8}><Text strong>连接名称</Text><Input value={form.ssh_name} onChange={set("ssh_name")} placeholder="如：超算A" /></Col>
          <Col span={8}><Text strong>主机地址</Text><Input value={form.ssh_host} onChange={set("ssh_host")} placeholder="如：login.hpc.example.com" /></Col>
          <Col span={8}><Text strong>端口</Text><Input value={form.ssh_port} onChange={set("ssh_port")} /></Col>
          <Col span={12}><Text strong>用户名</Text><Input value={form.ssh_username} onChange={set("ssh_username")} /></Col>
          <Col span={12}><Text strong>密码</Text><SecretInput hasSecret={secrets.ssh} value={form.ssh_password} onChange={(v) => setForm((p) => ({ ...p, ssh_password: v }))} onReveal={reveal("ssh")} placeholder={secrets.ssh ? "已保存（点击眼睛临时查看原文）" : "未配置密码，填写后保存" } /></Col>
          <Col span={24}><Text type="secondary" style={{ fontSize: 12 }}>说明：SSH 直连需要自建连接配置；密码经系统凭据管理器保存（不入项目），已保存后只显示掩码，点眼睛临时查看原文。</Text></Col>
        </Row>
      ))}

      {section("Materials Project & 作业数", <SafetyCertificateOutlined />, (
        <Row gutter={16}>
          <Col span={12}><Text strong>MP API Key</Text><SecretInput hasSecret={secrets.mp} value={form.mp_api_key} onChange={(v) => setForm((p) => ({ ...p, mp_api_key: v }))} onReveal={reveal("mp")} placeholder={secrets.mp ? "已保存（点击眼睛临时查看原文）" : "未配置，填写后保存" } /></Col>
          <Col span={12}><Text strong>最大作业数</Text><Input value={form.max_jobs} onChange={set("max_jobs")} /></Col>
          <Col span={24}><Text type="secondary" style={{ fontSize: 12 }}>最大作业数 = 同一超算账号「排队 + 运行中」总数上限，全局生效。</Text></Col>
          <Col span={12}><Text strong>监控轮询间隔（秒）</Text><Input value={form.poll_interval_seconds} onChange={set("poll_interval_seconds")} placeholder="60" /></Col>
          <Col span={24}><Text type="secondary" style={{ fontSize: 12 }}>提交后 AI 按此间隔自动检查超算作业状态（排队/运行/完成/补提后续），直到全部结束并生成报告；下限 10 秒。</Text></Col>
        </Row>
      ))}

      <Collapse defaultActiveKey={["1"]} items={[{ key: "1", label: "快速连通测试", children: (
        <Space direction="vertical" style={{ width: "100%" }}>
          <Button onClick={() => test("llm")} loading={testMutation.isPending && testMutation.variables === "llm"}>测试 LLM</Button>
          <Button onClick={() => test("mp")} loading={testMutation.isPending && testMutation.variables === "mp"}>测试 Materials Project</Button>
          <Button onClick={() => test("ssh")} loading={testMutation.isPending && testMutation.variables === "ssh"}>测试 SSH 连接</Button>
        </Space>
      ) }]} />
    </div>
  );
};

export default AiSettingsPage;