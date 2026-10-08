// ============================================================
// ParameterConfirmForm — 参数确认表单（磁性、DFT+U、SOC、精度、调度器）
//
// 科研安全约束：
// - DFT+U 默认关闭，条目全空，系统不预填任何 U/J 数值；
// - 前端不声明普适的科研取值范围，只做“合法有限数值”校验；
// - 异常或负值仅显示 warning，不改写、不阻止，由用户勾选确认负责；
// - 任一字段修改后自动失效该条目的 confirmed_by_user，须重新确认。
// ============================================================

import React, { useCallback, useEffect, useRef } from 'react';
import { Card, Form, Select, Switch, InputNumber, Input, Button, Space, Divider, Alert, Checkbox, Typography } from 'antd';
import { ArrowLeftOutlined, InfoCircleOutlined } from '@ant-design/icons';
import type { WorkflowTask } from '../../types/enums';
import type {
  WorkflowElectronicType,
  WorkflowPrecision,
  WorkflowSchedulerType,
} from '../../types/workflow-contract';

const { Text } = Typography;

export interface DftuEntryFormData {
  element?: string;
  l?: number;
  u_eff_ev?: number | null;
  u_ev?: number | null;
  j_ev?: number | null;
  source_note?: string | null;
  confirmed_by_user: boolean;
}

export type DftuForm = 'dudarev' | 'liechtenstein' | 'legacy';

export interface ParameterConfirmFormData {
  electronic_type: WorkflowElectronicType;
  magnetic: boolean;
  soc: boolean;
  precision: WorkflowPrecision;
  tasks: WorkflowTask[];
  dftu: {
    enabled: boolean;
    form?: DftuForm;
    input_mode?: 'u_eff' | 'u_j';
    entries: DftuEntryFormData[];
  };
  scheduler: {
    type: WorkflowSchedulerType;
    nodes: number;
    tasks_per_node: number;
    walltime: string;
    vasp_binary_hint: string;
  };
}

interface ParameterConfirmFormProps {
  elements: string[];
  transitionMetals: string[];
  initialValues?: Partial<ParameterConfirmFormData>;
  onSubmit: (data: ParameterConfirmFormData) => void;
  onDraftChange?: (data: ParameterConfirmFormData) => void;
  onBack?: () => void;
  isGenerating: boolean;
  bandWorkflowStatus?: 'loading' | 'error' | 'enabled' | 'disabled';
  onRetryBandCapability?: () => void;
}

const TASK_OPTIONS: { label: string; value: WorkflowTask }[] = [
  { label: '结构优化 (relax)', value: 'relax' },
  { label: '静态计算 (static)', value: 'static' },
  { label: '态密度 (DOS)', value: 'dos' },
  { label: '能带 (band)', value: 'band' },
];

/** 合法有限数值校验：不设科研性取值范围（不限制正负与上下界）。 */
const finiteValueRules = (label: string) => [
  {
    validator: (_: unknown, value: unknown) => {
      if (value === undefined || value === null || value === '') {
        return Promise.reject(new Error(`${label}为必填项`));
      }
      if (typeof value !== 'number' || !Number.isFinite(value)) {
        return Promise.reject(new Error(`${label}必须是合法有限数值`));
      }
      return Promise.resolve();
    },
  },
];

const initialDftuForm = (initialValues?: Partial<ParameterConfirmFormData>): DftuForm | undefined => {
  const dftu = initialValues?.dftu;
  if (dftu?.form) return dftu.form;
  return dftu?.entries?.some((entry) => entry.u_ev != null || entry.j_ev != null) ? 'legacy' : undefined;
};

/** 形式、element/L 及各科学输入共同决定确认指纹。 */
const entryFingerprint = (entry: DftuEntryFormData | undefined, selectedForm: DftuForm | undefined) =>
  JSON.stringify([selectedForm, entry?.element ?? null, entry?.l ?? null,
    entry?.u_eff_ev ?? null, entry?.u_ev ?? null, entry?.j_ev ?? null]);

/**
 * vasp_binary_hint 安全白名单：该字段会进入生成的 shell 脚本，
 * 仅接受单个安全的可执行文件名或 POSIX 路径 token。
 * 允许：A-Z a-z 0-9 下划线 点 斜杠 冒号 加号 连字符；
 * 拒绝：空白/换行、; & | $ > <、反引号、引号、括号、反斜杠等一切 shell 运算符。
 */
const SAFE_BINARY_TOKEN = /^[A-Za-z0-9_./:+-]+$/;

const ParameterConfirmForm: React.FC<ParameterConfirmFormProps> = ({
  elements,
  transitionMetals,
  initialValues,
  onSubmit,
  onDraftChange,
  onBack,
  isGenerating,
  bandWorkflowStatus = 'loading',
  onRetryBandCapability,
}) => {
  const [form] = Form.useForm<ParameterConfirmFormData>();
  // AntD initialValues are a mount-time seed, not a stream of later draft edits.
  const seed = useRef(initialValues).current;

  const dftuEnabled = Form.useWatch(['dftu', 'enabled'], form);
  const dftuForm: DftuForm | undefined = Form.useWatch(['dftu', 'form'], form) ?? initialDftuForm(initialValues);
  const watchedEntries: DftuEntryFormData[] = Form.useWatch(['dftu', 'entries'], form) ?? [];
  const watchedTasks: WorkflowTask[] = Form.useWatch('tasks', form) ?? [];
  const watchedSoc: boolean = Form.useWatch('soc', form) ?? false;
  const bandSelected = watchedTasks.includes('band');
  const bandCompatibilityIssues = [
    ...(watchedTasks.includes('relax')
      ? ['当前不支持在同一批任务中包含 relax。请先用 relax 完成弛豫，将生成的 CONTCAR 作为新结构上传，再生成 static → band。']
      : []),
    ...(watchedSoc ? ['当前批次不支持 SOC 与 band 同时计算，请关闭 SOC 后再提交。'] : []),
    ...(!watchedTasks.includes('static') ? ['band 必须包含 static 上游任务，请同时选择 static，按 static → band 生成。'] : []),
  ];
  const bandSubmissionBlocked = bandSelected && (
    bandWorkflowStatus !== 'enabled' || bandCompatibilityIssues.length > 0
  );

  // 确认失效机制：记录每条条目被确认时的值指纹；字段变化后自动复位确认。
  const fingerprints = useRef(new Map<number, string>());
  const lastEntryCount = useRef(initialValues?.dftu?.entries?.length ?? 0);
  const previousForm = useRef<DftuForm | undefined>(initialDftuForm(initialValues));

  // Seed saved confirmations before the first user edit, including old records.
  useEffect(() => {
    (seed?.dftu?.entries ?? []).forEach((entry, idx) => {
      if (entry.confirmed_by_user) fingerprints.current.set(idx, entryFingerprint(entry, initialDftuForm(seed)));
    });
  }, [seed]);

  const handleValuesChange = useCallback(
    (_changed: unknown, all: ParameterConfirmFormData) => {
      const entries = all?.dftu?.entries ?? [];
      const selectedForm = all?.dftu?.form;
      if (selectedForm !== previousForm.current) {
        previousForm.current = selectedForm;
        fingerprints.current.clear();
        form.setFieldValue(['dftu', 'input_mode'],
          selectedForm === 'dudarev' ? 'u_eff' : selectedForm === 'liechtenstein' ? 'u_j' : undefined);
        entries.forEach((entry, idx) => form.setFieldValue(['dftu', 'entries', idx], {
          ...entry, u_eff_ev: undefined, u_ev: undefined, j_ev: undefined,
          source_note: undefined, confirmed_by_user: false,
        }));
        onDraftChange?.(structuredClone(form.getFieldsValue(true)));
        return;
      }
      if (entries.length !== lastEntryCount.current) {
        // 增删条目会使索引语义变化：全部指纹作废，要求重新确认。
        fingerprints.current.clear();
        lastEntryCount.current = entries.length;
        entries.forEach((entry, idx) => {
          if (entry?.confirmed_by_user) form.setFieldValue(['dftu', 'entries', idx, 'confirmed_by_user'], false);
        });
        onDraftChange?.(structuredClone(form.getFieldsValue(true)));
        return;
      }
      entries.forEach((entry, idx) => {
        if (!entry?.confirmed_by_user) {
          fingerprints.current.delete(idx);
          return;
        }
        const fp = entryFingerprint(entry, selectedForm);
        const prev = fingerprints.current.get(idx);
        if (prev === undefined) {
          // Newly checked rows capture the values the user just confirmed.
          // Saved confirmations are separately seeded from initialValues above.
          fingerprints.current.set(idx, fp);
          return;
        }
        if (prev !== fp) {
          // 确认后修改形式、element/L 或当前形式数值：立即失效确认。
          fingerprints.current.delete(idx);
          form.setFieldValue(['dftu', 'entries', idx, 'confirmed_by_user'], false);
        }
      });
      // Read after programmatic confirmation invalidation; `all` still has the old flags.
      onDraftChange?.(structuredClone(form.getFieldsValue(true)));
    },
    [form, onDraftChange]
  );

  const handleFinish = (values: ParameterConfirmFormData) => {
    if (values.dftu.enabled && (values.dftu.entries?.length ?? 0) === 0) return;
    // Form submit guard supplements the disabled submit button and page-level guard.
    if (values.tasks.includes('band') && (
      bandWorkflowStatus !== 'enabled' ||
      values.tasks.includes('relax') ||
      !values.tasks.includes('static') ||
      values.soc
    )) return;
    const selectedForm = values.dftu.form ?? initialDftuForm(initialValues);
    if (values.dftu.enabled && !selectedForm) return;
    const submittedDftu: ParameterConfirmFormData['dftu'] = values.dftu.enabled
      ? {
          enabled: true,
          form: selectedForm,
          ...(selectedForm === 'legacy' ? {} : { input_mode: selectedForm === 'dudarev' ? 'u_eff' as const : 'u_j' as const }),
          entries: (values.dftu.entries ?? []).map(({ u_eff_ev, u_ev, j_ev, ...entry }) =>
            selectedForm === 'dudarev' ? { ...entry, u_eff_ev } : { ...entry, u_ev, j_ev }),
        }
      : { enabled: false, entries: [] };
    // Keep the prerequisite visible in the requested task sequence even if the user selected band first.
    const submittedValues = {
      ...values,
      dftu: submittedDftu,
      tasks: values.tasks.includes('band')
        ? ['static', 'band', ...values.tasks.filter((task) => task !== 'static' && task !== 'band')] as WorkflowTask[]
        : values.tasks,
    };
    onSubmit(submittedValues);
  };

  // 异常值警告：不阻止、不改写，仅提示用户自行确认（科研决策归用户）。
  // U 与 J 分别检查、分别说明：U ≤ 0 不常见；J 为负不常见。
  const activeU = (entry: DftuEntryFormData) => dftuForm === 'dudarev' ? entry.u_eff_ev : entry.u_ev;
  const abnormalUEntries = watchedEntries
    .map((entry, idx) => ({ idx, entry }))
    .filter(({ entry }) => typeof activeU(entry) === 'number' && (activeU(entry) as number) <= 0);
  const abnormalJEntries = watchedEntries
    .map((entry, idx) => ({ idx, entry }))
    .filter(({ entry }) => typeof entry?.j_ev === 'number' && (entry.j_ev as number) < 0);

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

      {bandWorkflowStatus === 'loading' && (
        <Alert
          type="info"
          showIcon
          message="正在读取服务端功能配置，完成前能带选项不可用。"
          style={{ marginBottom: 16 }}
        />
      )}
      {bandWorkflowStatus === 'error' && (
        <Alert
          type="warning"
          showIcon
          message="服务端功能配置读取失败，能带暂不可用。"
          action={onRetryBandCapability && (
            <Button size="small" onClick={onRetryBandCapability}>重试读取</Button>
          )}
          style={{ marginBottom: 16 }}
        />
      )}
      {bandWorkflowStatus === 'disabled' && (
        <Alert
          type="info"
          showIcon
          message="服务端当前未开放能带工作流；其他计算任务仍可使用。"
          style={{ marginBottom: 16 }}
        />
      )}
      {bandSelected && (
        <Alert
          type={bandCompatibilityIssues.length > 0 || bandWorkflowStatus !== 'enabled' ? 'warning' : 'info'}
          showIcon
          message={bandCompatibilityIssues.length > 0
            ? '当前能带任务组合不能提交'
            : bandWorkflowStatus === 'enabled'
              ? '能带任务设置'
              : '能带当前不可提交'}
          description={bandCompatibilityIssues.length > 0
            ? bandCompatibilityIssues.join(' ')
            : bandWorkflowStatus === 'enabled'
              ? '本批使用默认 PBE 设置；若启用 DFT+U，band 沿用本批现有 PBE+U 设置。任务链为 static → band。'
              : '请等待配置读取完成、重试读取，或移除 band 后提交其他任务。'}
          style={{ marginBottom: 16 }}
        />
      )}

      <Form
        form={form}
        layout="vertical"
        onFinish={handleFinish}
        onValuesChange={handleValuesChange}
        initialValues={{
          electronic_type: 'unknown',
          magnetic: transitionMetals.length > 0,
          soc: false,
          precision: 'standard',
          tasks: ['relax', 'static', 'dos'],
          scheduler: {
            type: 'slurm',
            nodes: 1,
            tasks_per_node: 32,
            walltime: '12:00:00',
            vasp_binary_hint: 'vasp_std',
          },
          ...initialValues,
          dftu: {
            enabled: false,
            form: initialDftuForm(initialValues),
            ...(initialDftuForm(initialValues) === 'dudarev' ? { input_mode: 'u_eff' as const } :
              initialDftuForm(initialValues) === 'liechtenstein' ? { input_mode: 'u_j' as const } : {}),
            entries: [],
            ...initialValues?.dftu,
          },
        }}
      >
        <Form.Item label="计算任务" name="tasks" rules={[{ required: true, message: '请选择至少一个计算任务' }]}>
          <Select
            mode="multiple"
            options={TASK_OPTIONS.map((option) => option.value === 'band'
              ? {
                  ...option,
                  disabled: bandWorkflowStatus !== 'enabled',
                  title: bandWorkflowStatus === 'loading'
                    ? '正在读取服务端功能配置'
                    : bandWorkflowStatus === 'error'
                      ? '功能配置读取失败，可重试'
                      : bandWorkflowStatus === 'disabled'
                        ? '服务端当前未开放能带工作流'
                        : undefined,
                }
              : option)}
            placeholder="选择计算任务"
          />
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

        <Form.Item label="精度档位" name="precision"
          extra="标准档位对静态自洽（SCF）和 DOS 使用更严格的电子收敛阈值与更密的 k 点；实际收敛仍需按体系验证。">
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
        <Text type="secondary" style={{ display: 'block', marginBottom: 12 }}>
          Ueff 或 U/J 及轨道 L 由用户指定；系统不判断其科学适用性，也不会自行调整取值。
          当前 MVP 仅支持 d（L=2）和 f（L=3）轨道。
        </Text>
        <Form.Item name={['dftu', 'enabled']} label="启用 DFT+U" valuePropName="checked">
          <Switch aria-label="启用 DFT+U" />
        </Form.Item>

        {dftuEnabled && (
          <>
            <Form.Item name={['dftu', 'form']} label="DFT+U 形式" rules={[{ required: true, message: '请选择 DFT+U 形式' }]}>
              <Select
                aria-label="DFT+U 形式"
                options={[
                  { label: 'Dudarev（Ueff）', value: 'dudarev' },
                  { label: 'Liechtenstein（U/J）', value: 'liechtenstein' },
                  ...(dftuForm === 'legacy' ? [{ label: '旧版 Dudarev U/J 输入（保留原值）', value: 'legacy' }] : []),
                ]}
              />
            </Form.Item>
            {!dftuForm && <Text type="secondary">请先选择 DFT+U 形式，再填写对应的参数。</Text>}
            <Form.List name={['dftu', 'entries']}>
              {(fields, { add, remove }) => (
                <>
                  {fields.map(({ key, name, ...rest }) => (
                    <Space key={key} align="baseline" wrap>
                      <Form.Item
                        {...rest}
                        name={[name, 'element']}
                        label="元素"
                        rules={[
                          { required: true, message: '请选择元素' },
                          {
                            validator: (_, value) => {
                              if (!value) return Promise.resolve();
                              const all = (form.getFieldValue(['dftu', 'entries']) ?? []) as DftuEntryFormData[];
                              const dupCount = all.filter((e) => e?.element === value).length;
                              return dupCount > 1
                                ? Promise.reject(new Error('同一元素不能重复配置'))
                                : Promise.resolve();
                            },
                          },
                        ]}
                      >
                        <Select style={{ width: 100 }} options={elements.map((e) => ({ label: e, value: e }))} placeholder="元素" />
                      </Form.Item>
                      <Form.Item {...rest} name={[name, 'l']} label="L" rules={[{ required: true, message: '请选择 L' }]}>
                        <Select
                          style={{ width: 110 }}
                          options={[
                            { label: 'd (L=2)', value: 2 },
                            { label: 'f (L=3)', value: 3 },
                          ]}
                          placeholder="轨道"
                        />
                      </Form.Item>
                      {dftuForm === 'dudarev' ? (
                        <Form.Item {...rest} name={[name, 'u_eff_ev']} label="Ueff (eV)" rules={finiteValueRules('Ueff')}>
                          <InputNumber step={0.1} style={{ width: 100 }} placeholder="Ueff 值" />
                        </Form.Item>
                      ) : dftuForm ? (
                        <>
                          <Form.Item {...rest} name={[name, 'u_ev']} label={dftuForm === 'legacy' ? '旧版 U (eV)' : 'U (eV)'} rules={finiteValueRules('U')}>
                            <InputNumber step={0.1} style={{ width: 100 }} placeholder="U 值" />
                          </Form.Item>
                          <Form.Item {...rest} name={[name, 'j_ev']} label="J (eV)" rules={finiteValueRules('J')}>
                            <InputNumber step={0.1} style={{ width: 100 }} placeholder="J 值" />
                          </Form.Item>
                        </>
                      ) : null}
                      <Form.Item
                        {...rest}
                        name={[name, 'confirmed_by_user']}
                        valuePropName="checked"
                        rules={[
                          {
                            validator: (_, value) =>
                              value === true
                                ? Promise.resolve()
                                : Promise.reject(new Error('请确认该条目的形式、L 与输入值')),
                          },
                        ]}
                      >
                        <Checkbox>我已确认该条目的形式、L 与输入值</Checkbox>
                      </Form.Item>
                      <Button type="link" danger onClick={() => remove(name)}>删除</Button>
                    </Space>
                  ))}
                  <Button
                    type="dashed"
                    disabled={!dftuForm}
                    onClick={() =>
                      add({ element: undefined, l: undefined, u_eff_ev: undefined, u_ev: undefined, j_ev: undefined, confirmed_by_user: false })
                    }
                    block
                  >
                    添加 DFT+U 条目
                  </Button>
                </>
              )}
            </Form.List>
            {watchedEntries.length === 0 && (
              <Alert
                type="warning"
                showIcon
                message="已启用 DFT+U 但尚未添加条目，无法生成工作流"
                style={{ marginTop: 12 }}
              />
            )}
            {abnormalUEntries.length > 0 && (
              <Alert
                type="warning"
                showIcon
                message={`条目 ${abnormalUEntries.map(({ idx }) => idx + 1).join('、')} 的 ${dftuForm === 'dudarev' ? 'Ueff' : 'U'} 值不常见（≤0）。系统不会改写你的输入，请确认这符合你的研究意图。`}
                style={{ marginTop: 12 }}
              />
            )}
            {abnormalJEntries.length > 0 && (
              <Alert
                type="warning"
                showIcon
                message={`条目 ${abnormalJEntries.map(({ idx }) => idx + 1).join('、')} 的 J 值为负（<0）。系统不会改写你的输入，请确认这符合你的研究意图。`}
                style={{ marginTop: 12 }}
              />
            )}
          </>
        )}

        <Divider>调度器设置</Divider>
        <Space wrap>
          <Form.Item name={['scheduler', 'type']} label="调度器类型" rules={[{ required: true }]}>
            <Select
              style={{ width: 140 }}
              options={[
                { label: 'Slurm', value: 'slurm' },
                { label: 'Cbatch', value: 'cbatch' },
                { label: '通用 (generic)', value: 'generic' },
              ]}
            />
          </Form.Item>
          <Form.Item name={['scheduler', 'nodes']} label="节点数" rules={[{ required: true }]}>
            <InputNumber min={1} max={64} style={{ width: 100 }} />
          </Form.Item>
          <Form.Item name={['scheduler', 'tasks_per_node']} label="每节点核数" rules={[{ required: true }]}>
            <InputNumber min={1} max={128} style={{ width: 100 }} />
          </Form.Item>
          <Form.Item
            name={['scheduler', 'walltime']}
            label="Walltime"
            rules={[
              { required: true, message: '请填写 Walltime' },
              { pattern: /^\d{2}:\d{2}:\d{2}$/, message: '格式必须为 HH:MM:SS' },
            ]}
          >
            <Input placeholder="HH:MM:SS" style={{ width: 120 }} />
          </Form.Item>
          <Form.Item
            name={['scheduler', 'vasp_binary_hint']}
            label="VASP 可执行文件"
            rules={[
              { required: true, message: '请填写 VASP 可执行文件' },
              {
                pattern: SAFE_BINARY_TOKEN,
                message: '仅允许安全的可执行文件名或 POSIX 路径，不允许 shell 运算符',
              },
            ]}
          >
            <Input placeholder="vasp_std" style={{ width: 240 }} />
          </Form.Item>
        </Space>

        <Form.Item style={{ marginTop: 24, marginBottom: 0 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <Button icon={<ArrowLeftOutlined />} size="large" onClick={onBack}>
              上一步
            </Button>
            <Button type="primary" htmlType="submit" loading={isGenerating} disabled={bandSubmissionBlocked} size="large">
              下一步：确认摘要
            </Button>
          </div>
        </Form.Item>
      </Form>
    </Card>
  );
};

export default ParameterConfirmForm;
