// ============================================================
// ParameterConfirmForm — 参数确认表单（磁性、DFT+U、SOC、精度、调度器）
// ============================================================

import React from 'react';
import { Card, Form, Select, Switch, InputNumber, Input, Button, Space, Divider, Alert } from 'antd';
import { ArrowLeftOutlined, InfoCircleOutlined } from '@ant-design/icons';
import type { WorkflowTask } from '../../types/enums';

interface ParameterConfirmFormData {
  electronic_type: 'metal' | 'semiconductor' | 'unknown';
  magnetic: boolean;
  soc: boolean;
  precision: 'quick' | 'standard' | 'high';
  tasks: WorkflowTask[];
  dftu: {
    enabled: boolean;
    entries: { element: string; l: number; u_ev: number; j_ev: number }[];
  };
  scheduler: {
    nodes: number;
    tasks_per_node: number;
    walltime: string;
  };
}

interface ParameterConfirmFormProps {
  elements: string[];
  transitionMetals: string[];
  initialValues?: Partial<ParameterConfirmFormData>;
  onSubmit: (data: ParameterConfirmFormData) => void;
  onBack?: () => void;
  isGenerating: boolean;
}

const TASK_OPTIONS: { label: string; value: WorkflowTask }[] = [
  { label: '结构优化 (relax)', value: 'relax' },
  { label: '静态计算 (static)', value: 'static' },
  { label: '态密度 (DOS)', value: 'dos' },
  { label: '能带 (band)', value: 'band' },
];

const ParameterConfirmForm: React.FC<ParameterConfirmFormProps> = ({
  elements,
  transitionMetals,
  initialValues,
  onSubmit,
  onBack,
  isGenerating,
}) => {
  const [form] = Form.useForm<ParameterConfirmFormData>();

  const handleFinish = (values: ParameterConfirmFormData) => {
    onSubmit(values);
  };

  const dftuEnabled = Form.useWatch(['dftu', 'enabled'], form);

  const defaultDftuEntries = elements
    .filter((el) => transitionMetals.includes(el))
    .map((el) => ({
      element: el,
      l: 2,
      u_ev: 4.0,
      j_ev: 0.0,
    }));

  return (
    <Card title="确认计算参数" bordered={false}>
      {transitionMetals.length > 0 && (
        <Alert
          type="info"
          showIcon
          icon={<InfoCircleOutlined />}
          message="该体系含过渡金属，请确认磁性和 DFT+U 设置"
          style={{ marginBottom: 16 }}
        />
      )}

      <Form
        form={form}
        layout="vertical"
        onFinish={handleFinish}
        initialValues={{
          electronic_type: 'unknown',
          magnetic: transitionMetals.length > 0,
          soc: false,
          precision: 'standard',
          tasks: ['relax', 'static', 'dos'],
          dftu: {
            enabled: transitionMetals.length > 0,
            entries: defaultDftuEntries,
          },
          scheduler: {
            nodes: 1,
            tasks_per_node: 32,
            walltime: '12:00:00',
          },
          ...initialValues,
        }}
      >
        <Form.Item label="计算任务" name="tasks" rules={[{ required: true, message: '请选择至少一个计算任务' }]}>
          <Select mode="multiple" options={TASK_OPTIONS} placeholder="选择计算任务" />
        </Form.Item>

        <Form.Item label="电子类型" name="electronic_type" rules={[{ required: true }]}>
          <Select
            options={[
              { label: '金属 (metal)', value: 'metal' },
              { label: '半导体 (semiconductor)', value: 'semiconductor' },
              { label: '不确定', value: 'unknown' },
            ]}
          />
        </Form.Item>

        <Form.Item label="精度档位" name="precision">
          <Select
            options={[
              { label: '快速 (quick)', value: 'quick' },
              { label: '标准 (standard)', value: 'standard' },
              { label: '高精度 (high)', value: 'high' },
            ]}
          />
        </Form.Item>

        <Space size="large">
          <Form.Item label="磁性计算" name="magnetic" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Form.Item label="自旋轨道耦合 (SOC)" name="soc" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Space>

        {/* DFT+U */}
        <Divider>DFT+U 设置</Divider>
        <Form.Item name={['dftu', 'enabled']} label="启用 DFT+U" valuePropName="checked">
          <Switch />
        </Form.Item>

        {dftuEnabled && (
          <Form.List name={['dftu', 'entries']}>
            {(fields, { add, remove }) => (
              <>
                {fields.map(({ key, name, ...rest }) => (
                  <Space key={key} align="baseline" wrap>
                    <Form.Item {...rest} name={[name, 'element']} label="元素">
                      <Select style={{ width: 100 }} options={elements.map((e) => ({ label: e, value: e }))} />
                    </Form.Item>
                    <Form.Item {...rest} name={[name, 'l']} label="L">
                      <Select style={{ width: 80 }} options={[{ label: 'd (2)', value: 2 }, { label: 'f (3)', value: 3 }]} />
                    </Form.Item>
                    <Form.Item {...rest} name={[name, 'u_ev']} label="U (eV)">
                      <InputNumber min={0} max={10} step={0.5} style={{ width: 100 }} />
                    </Form.Item>
                    <Form.Item {...rest} name={[name, 'j_ev']} label="J (eV)">
                      <InputNumber min={0} max={5} step={0.5} style={{ width: 100 }} />
                    </Form.Item>
                    <Button type="link" danger onClick={() => remove(name)}>删除</Button>
                  </Space>
                ))}
                <Button type="dashed" onClick={() => add({ element: '', l: 2, u_ev: 0, j_ev: 0 })} block>
                  添加 DFT+U 条目
                </Button>
              </>
            )}
          </Form.List>
        )}

        <Divider>调度器设置</Divider>
        <Space wrap>
          <Form.Item name={['scheduler', 'nodes']} label="节点数">
            <InputNumber min={1} max={64} style={{ width: 100 }} />
          </Form.Item>
          <Form.Item name={['scheduler', 'tasks_per_node']} label="每节点核数">
            <InputNumber min={1} max={128} style={{ width: 100 }} />
          </Form.Item>
          <Form.Item name={['scheduler', 'walltime']} label="Walltime">
            <Input placeholder="HH:MM:SS" style={{ width: 120 }} />
          </Form.Item>
        </Space>

        <Form.Item style={{ marginTop: 24, marginBottom: 0 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <Button icon={<ArrowLeftOutlined />} size="large" onClick={onBack}>
              上一步
            </Button>
            <Button type="primary" htmlType="submit" loading={isGenerating} size="large">
              下一步：工作流计划
            </Button>
          </div>
        </Form.Item>
      </Form>
    </Card>
  );
};

export default ParameterConfirmForm;