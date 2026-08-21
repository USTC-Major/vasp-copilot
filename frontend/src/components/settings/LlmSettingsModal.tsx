// ============================================================
// LlmSettingsModal — 模型设置（前端自定义 base_url / api_key / 模型名）
// ============================================================

import React, { useEffect, useState } from 'react';
import {
  Modal, Form, Input, InputNumber, Switch, Button, Space, Tag, Typography, message, Spin,
} from 'antd';
import { useQueryClient } from '@tanstack/react-query';
import {
  useLlmConfig, useLlmConfigSave, useLlmConfigReset, useLlmConfigTest,
} from '../../hooks/useApi';

const { Text } = Typography;

interface LlmSettingsModalProps {
  open: boolean;
  onClose: () => void;
}

interface FormValues {
  enabled: boolean;
  base_url: string;
  api_key: string;
  model: string;
  timeout_seconds: number;
  max_tokens: number;
  temperature: number;
}

const LlmSettingsModal: React.FC<LlmSettingsModalProps> = ({ open, onClose }) => {
  const [form] = Form.useForm<FormValues>();
  const queryClient = useQueryClient();
  const { data: config, isLoading } = useLlmConfig(open);
  const saveMutation = useLlmConfigSave();
  const resetMutation = useLlmConfigReset();
  const testMutation = useLlmConfigTest();
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null);

  // 打开弹窗时用后端当前配置回填表单
  useEffect(() => {
    if (open && config) {
      form.setFieldsValue({
        enabled: config.enabled,
        base_url: config.base_url,
        api_key: '',
        model: config.model,
        timeout_seconds: config.timeout_seconds,
        max_tokens: config.max_tokens,
        temperature: config.temperature,
      });
      setTestResult(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, config, form]);

  const handleSave = async () => {
    const values = await form.validateFields();
    const res = await saveMutation.mutateAsync({
      enabled: values.enabled,
      base_url: values.base_url.trim(),
      api_key: values.api_key,
      model: values.model.trim(),
      timeout_seconds: values.timeout_seconds,
      max_tokens: values.max_tokens,
      temperature: values.temperature,
    });
    queryClient.setQueryData(['llmConfig'], res);
    message.success('模型配置已保存并生效');
    onClose();
  };

  const handleTest = async () => {
    const values = await form.validateFields();
    const res = await testMutation.mutateAsync({
      enabled: values.enabled,
      base_url: values.base_url.trim(),
      api_key: values.api_key,
      model: values.model.trim(),
      timeout_seconds: values.timeout_seconds,
    });
    setTestResult({ ok: res.ok, message: res.message });
  };

  const handleReset = async () => {
    const res = await resetMutation.mutateAsync();
    queryClient.setQueryData(['llmConfig'], res);
    form.setFieldsValue({
      enabled: res.enabled,
      base_url: res.base_url,
      api_key: '',
      model: res.model,
      timeout_seconds: res.timeout_seconds,
      max_tokens: res.max_tokens,
      temperature: res.temperature,
    });
    message.info('已恢复为后端环境默认配置');
  };

  return (
    <Modal
      title="模型设置"
      open={open}
      onCancel={onClose}
      width={560}
      footer={
        <Space>
          <Button onClick={handleTest} loading={testMutation.isPending}>测试连接</Button>
          <Button onClick={handleReset} loading={resetMutation.isPending}>恢复默认</Button>
          <Button onClick={onClose}>取消</Button>
          <Button type="primary" onClick={handleSave} loading={saveMutation.isPending}>保存</Button>
        </Space>
      }
    >
      {isLoading ? (
        <div style={{ textAlign: 'center', padding: 24 }}>
          <Spin />
        </div>
      ) : (
        <>
          <Space wrap style={{ marginBottom: 16 }}>
            <Text type="secondary">配置来源：</Text>
            <Tag color={config?.source === 'runtime' ? 'blue' : 'default'}>
              {config?.source === 'runtime' ? '运行时（前端设置）' : '环境（.env）'}
            </Tag>
            {config?.usable ? <Tag color="green">已启用</Tag> : <Tag color="orange">未启用</Tag>}
            {config?.api_key_set ? (
              <Text type="secondary">已有密钥 {config?.api_key_masked}</Text>
            ) : (
              <Text type="secondary">未配置密钥</Text>
            )}
          </Space>

          {testResult && (
            <div style={{ marginBottom: 12 }}>
              <Tag color={testResult.ok ? 'green' : 'red'}>
                {testResult.ok ? '连接成功' : '连接失败'}
              </Tag>
              <Text type="secondary">{testResult.message}</Text>
            </div>
          )}

          <Form
            form={form}
            layout="vertical"
            initialValues={{
              enabled: true,
              base_url: 'http://127.0.0.1:8001/v1',
              api_key: '',
              model: '',
              timeout_seconds: 30,
              max_tokens: 1024,
              temperature: 0.2,
            }}
          >
            <Form.Item
              label="启用 LLM 解释"
              name="enabled"
              valuePropName="checked"
              extra="关闭后所有解释/追问都返回提示。"
            >
              <Switch />
            </Form.Item>
            <Form.Item
              label="接口地址 LLM_BASE_URL"
              name="base_url"
              rules={[{ required: true, message: '请输入 OpenAI 兼容的 /v1 接口地址' }]}
            >
              <Input placeholder="如 http://<服务器IP>:8000/v1 或 http://<IP>:11434/v1（Ollama）" />
            </Form.Item>
            <Form.Item
              label="API Key"
              name="api_key"
              extra="留空表示沿用已保存的密钥；本地服务通常任意非空值即可（如 ollama）。"
            >
              <Input.Password placeholder="sk-... 或任意非空字符串" autoComplete="new-password" />
            </Form.Item>
            <Form.Item
              label="模型名称 LLM_MODEL"
              name="model"
              rules={[{ required: true, message: '请输入模型名称' }]}
            >
              <Input placeholder="如 qwen2.5-72b-instruct / deepseek-v3" />
            </Form.Item>
            <Space size="large" wrap>
              <Form.Item label="超时（秒）" name="timeout_seconds">
                <InputNumber min={5} max={600} />
              </Form.Item>
              <Form.Item label="最大输出 token" name="max_tokens">
                <InputNumber min={16} max={8192} />
              </Form.Item>
              <Form.Item label="温度" name="temperature">
                <InputNumber min={0} max={2} step={0.1} />
              </Form.Item>
            </Space>
          </Form>
        </>
      )}
    </Modal>
  );
};

export default LlmSettingsModal;