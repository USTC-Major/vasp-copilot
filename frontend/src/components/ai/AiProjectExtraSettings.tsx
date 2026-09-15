// ============================================================
// AiProjectExtraSettings — 项目「额外设置」抽屉（随项目走）
// 条目 = 纯内容要求/指引（没有名字，只有内容，可写任意文字）；
// AI 控制计算任务运行时受到这些条目的要求和指引，且每次实时注入
// system prompt —— 不属于聊天记录，不会被聊天上下文覆盖/裁剪。
// 对接 /ai/v1/projects/:id/settings；增/改/删即自动保存；模板存 localStorage。
// ============================================================

import React, { useEffect, useRef, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Divider,
  Drawer,
  Empty,
  Input,
  Space,
  Typography,
  message,
} from 'antd';
import {
  ArrowDownOutlined,
  ArrowUpOutlined,
  DeleteOutlined,
  PlusOutlined,
} from '@ant-design/icons';
import ErrorAlert from '../common/ErrorAlert';
import {
  useAiProjectSettings,
  useAiProjectSettingsSave,
  useAiProjectSettingsDelete,
} from '../../hooks/useApi';

const AUTO_SAVE_DELAY_MS = 600;
const TEMPLATE_KEY = 'ai_project_accuracy_template';

const TEMPLATE_SAFETY_BOUNDARY =
  '安全边界：本条目只提供计算建议，不构成对文件写入、命令执行、作业提交/取消或远程操作的授权。执行前仍须展示本次精确动作并按系统规则取得确认；不得跳过、代替或复用确认。不得根据材料名称猜测或默认设置 Hubbard U；如可能需要 DFT+U，只提示用户提供元素、轨道与 U 值。';

interface BuiltInTemplate {
  key: string;
  title: string;
  summary: string;
  content: string;
}

const ACCURACY_TEMPLATES: BuiltInTemplate[] = [
  {
    key: 'quick-check',
    title: '快速检查',
    summary: '低成本排查结构、赝势、参数和运行环境',
    content:
      '快速检查建议：目标是尽快发现结构、赝势、K 点、并行参数和运行环境问题，不把结果作为最终科学结论。根据 POTCAR 的 ENMAX 与体系尺寸提出较低成本参数，并明确标注相对正式计算所降低的精度；建议 PREC=Normal、EDIFF 约 1e-4，并根据体系大小评估 LREAL=Auto。金属与非金属分别选择合适的占据与展宽方案，不凭材料名称武断判断。快速检查通过后，再建议常规收敛计算。',
  },
  {
    key: 'routine-convergence',
    title: '常规收敛',
    summary: '面向日常可靠结果的平衡设置',
    content:
      '常规收敛建议：先读取 POTCAR、结构与已有输出，再给出可追溯的参数理由。ENCUT 建议不低于所用赝势最大 ENMAX 的约 1.3 倍，PREC=Accurate、EDIFF 约 1e-6，并用 K 点密度或 KSPACING 做收敛检查；结构优化同时检查力、应力和电子步稳定性。占据与展宽必须根据金属/半导体/绝缘体及任务阶段选择，不将某一种 ISMEAR 机械套用到全部计算。',
  },
  {
    key: 'high-accuracy',
    title: '高精度收敛',
    summary: '以能量、力和 K 点收敛证据为先',
    content:
      '高精度收敛建议：以独立的 ENCUT、K 点密度、电子收敛阈值和必要时的展宽测试证明目标物性已收敛，而不是只套固定参数。可从 PREC=Accurate、LREAL=False、EDIFF 约 1e-7 和更严格的力阈值开始提出候选方案，并报告相邻档位的能量/原子、力与应力差异。对昂贵设置先估算资源与作业时长，再由用户决定是否采用。',
  },
];

const WORKFLOW_TEMPLATES: BuiltInTemplate[] = [
  {
    key: 'relax-static-dos',
    title: 'relax → static → DOS',
    summary: '逐阶段继承已收敛结构与电荷密度',
    content:
      '计算套路：先做结构优化 relax，确认几何、力和电子步收敛后，以最终 CONTCAR 作为 static 输入；static 收敛后再做 DOS，并检查 NBANDS、能量窗口和投影设置是否覆盖目标。DOS 阶段仅在已确认适合的绝缘体/半导体情形考虑四面体方法；金属体系使用合适的小展宽方案。每个阶段都先展示输入差异、依赖关系和资源估算，写入或提交仍逐次确认。',
  },
  {
    key: 'relax-static-band',
    title: 'relax → static → band',
    summary: '自洽电荷后沿明确高对称路径计算能带',
    content:
      '计算套路：先完成 relax，再以收敛结构做均匀 K 网格的 static 自洽计算；随后基于已收敛 CHGCAR 做 band 非自洽计算，明确晶体标准化方式、高对称路径、点数与 ICHARG 等继承关系。band 阶段不得使用 ISMEAR=-5 四面体方法，应按体系与非自洽路径选择稳定的占据设置。生成前检查 CHGCAR/WAVECAR 兼容性，每个阶段的文件写入与作业提交仍逐次确认。',
  },
  {
    key: 'failure-diagnosis',
    title: '失败诊断',
    summary: '先取证分类，再给最小参数修改建议',
    content:
      '失败诊断套路：先只读检查 OUTCAR、OSZICAR、vasprun.xml、调度器输出和最近一次输入，区分电子不收敛、离子步异常、内存/时间不足、并行配置、赝势/结构或文件继承问题。列出直接证据、最可能根因、备选解释与最小修改方案；不要未经证据同时改动多项参数。任何输入覆盖、重启、清理文件、作业取消或重新提交，都必须先展示精确动作与影响并取得本次确认。',
  },
];

function templateEntry(template: BuiltInTemplate): string {
  return `${template.content}\n\n${TEMPLATE_SAFETY_BOUNDARY}`;
}

function toEntries(accuracy: unknown): string[] {
  if (Array.isArray(accuracy)) return accuracy.map((e) => String(e));
  return [];
}

const AiProjectExtraSettings: React.FC<{
  projectId: string;
  open: boolean;
  onClose: () => void;
}> = ({ projectId, open, onClose }) => {
  const dataQuery = useAiProjectSettings(projectId, open);
  const saveMutation = useAiProjectSettingsSave();
  const deleteMutation = useAiProjectSettingsDelete();

  const [entries, setEntries] = useState<string[]>([]);
  const [newEntry, setNewEntry] = useState('');
  const [hasLoaded, setHasLoaded] = useState(false);
  const [saveState, setSaveState] = useState<'idle' | 'saving' | 'saved'>('idle');
  const lastSavedRef = useRef<string | null>(null);
  const loadedProjectRef = useRef<string | null>(null);

  useEffect(() => {
    loadedProjectRef.current = null;
    lastSavedRef.current = null;
    setEntries([]);
    setNewEntry('');
    setHasLoaded(false);
    setSaveState('idle');
  }, [projectId]);

  useEffect(() => {
    if (!dataQuery.data?.settings || dataQuery.data.project_id !== projectId) return;
    // 查询回拉只负责首次装载，不能覆盖用户已开始的新一轮编辑。
    if (loadedProjectRef.current === projectId) return;
    const loaded = toEntries(dataQuery.data.settings.accuracy);
    setEntries(loaded);
    lastSavedRef.current = JSON.stringify(loaded.map((e) => e.trim()).filter(Boolean));
    loadedProjectRef.current = projectId;
    setHasLoaded(true);
  }, [dataQuery.data, projectId]);

  // 增/改/删条目即自动保存（防抖）：有内容 → PUT；全部清空 → DELETE。
  useEffect(() => {
    if (!hasLoaded) return;
    const cleaned = entries.map((e) => e.trim()).filter(Boolean);
    const snapshot = JSON.stringify(cleaned);
    if (snapshot === lastSavedRef.current) return;
    setSaveState('saving');
    const timer = window.setTimeout(async () => {
      try {
        if (cleaned.length === 0) {
          await deleteMutation.mutateAsync(projectId);
        } else {
          await saveMutation.mutateAsync({ projectId, accuracy: cleaned });
        }
        lastSavedRef.current = JSON.stringify(cleaned);
        setSaveState('saved');
      } catch (err) {
        setSaveState('idle');
        message.error(err instanceof Error ? err.message : '保存失败');
      }
    }, AUTO_SAVE_DELAY_MS);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entries, hasLoaded, projectId, saveMutation, deleteMutation]);

  const addEntry = () => {
    const text = newEntry.trim();
    if (!text) {
      message.warning('请输入条目内容');
      return;
    }
    setEntries((prev) => [...prev, text]);
    setNewEntry('');
  };

  const updateEntry = (idx: number, text: string) => {
    setEntries((prev) => prev.map((e, i) => (i === idx ? text : e)));
  };

  const removeEntry = (idx: number) => {
    setEntries((prev) => prev.filter((_, i) => i !== idx));
  };

  const moveEntry = (idx: number, delta: number) => {
    setEntries((prev) => {
      const next = [...prev];
      const to = idx + delta;
      if (to < 0 || to >= next.length) return prev;
      [next[idx], next[to]] = [next[to], next[idx]];
      return next;
    });
  };

  const clearAll = () => {
    setEntries([]);
  };

  const appendBuiltInTemplate = (template: BuiltInTemplate) => {
    setEntries((prev) => [...prev, templateEntry(template)]);
    message.success(`已追加「${template.title}」，将自动保存`);
  };

  const saveAsTemplate = () => {
    const cleaned = entries.map((e) => e.trim()).filter(Boolean);
    if (cleaned.length === 0) {
      message.warning('当前无条目，无需保存模板');
      return;
    }
    localStorage.setItem(TEMPLATE_KEY, JSON.stringify({ accuracy: cleaned, saved_at: Date.now() }));
    message.success('已保存为模板（本机 localStorage）');
  };

  const appendSavedTemplate = () => {
    const raw = localStorage.getItem(TEMPLATE_KEY);
    if (!raw) {
      message.info('尚无已保存的模板');
      return;
    }
    try {
      const parsed = JSON.parse(raw) as { accuracy?: unknown };
      const saved = toEntries(parsed.accuracy).map((entry) => entry.trim()).filter(Boolean);
      if (saved.length === 0) {
        message.info('本机模板没有可追加的条目');
        return;
      }
      setEntries((prev) => [...prev, ...saved]);
      message.success(`已追加本机模板的 ${saved.length} 条设置（自动保存）`);
    } catch {
      message.warning('模板数据损坏，请重新保存');
    }
  };

  const error = dataQuery.error || saveMutation.error || deleteMutation.error;
  const hasConfig = entries.some((e) => e.trim() !== '');

  return (
    <Drawer
      title="额外设置 · 计算任务要求与指引"
      width={640}
      open={open}
      onClose={onClose}
      extra={
        <Space>
          <span
            style={{
              fontSize: 13,
              minWidth: 76,
              textAlign: 'right',
              color: saveState === 'saved' ? '#52c41a' : '#999',
            }}
          >
            {saveState === 'saving' ? '自动保存中…' : saveState === 'saved' ? '已自动保存' : '改动即自动保存'}
          </span>
          <button
            type="button"
            onClick={clearAll}
            style={{ all: 'unset', color: '#ff3b30', cursor: 'pointer', fontSize: 13 }}
          >
            清空
          </button>
          <Button onClick={saveAsTemplate}>存为模板</Button>
          <Button onClick={appendSavedTemplate}>追加本机模板</Button>
        </Space>
      }
    >
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="这些条目是 AI 控制计算任务运行时的要求与指引"
        description="每一条只有内容、没有名字，可填写任何要求（如计算精度、流程偏好、禁忌等）。新增、修改或删除条目会立即自动保存，无需手动点击保存。AI 在本项目规划作业、生成输入、判断与提交时都受这些条目约束；它们每次对话都会实时注入 AI，不属于聊天记录，不会被聊天上下文覆盖或裁剪。"
      />
      {error && <ErrorAlert error={error} title="加载/保存失败" onRetry={dataQuery.refetch} />}

      <Card size="small" style={{ marginBottom: 16, background: '#fafafa' }}>
        <Typography.Text strong>内置常用模板</Typography.Text>
        <Typography.Paragraph type="secondary" style={{ fontSize: 12, margin: '4px 0 10px' }}>
          模板不会自动启用，也不会替换已有条目。请先阅读摘要，再明确点击“追加”；追加后仍可逐条修改或删除。
        </Typography.Paragraph>
        <Typography.Text style={{ fontSize: 12 }}>精度建议</Typography.Text>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 6 }}>
          {ACCURACY_TEMPLATES.map((template) => (
            <Button
              key={template.key}
              size="small"
              title={template.summary}
              onClick={() => appendBuiltInTemplate(template)}
            >
              追加「{template.title}」
            </Button>
          ))}
        </div>
        <Divider style={{ margin: '10px 0' }} />
        <Typography.Text style={{ fontSize: 12 }}>计算套路与诊断</Typography.Text>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 6 }}>
          {WORKFLOW_TEMPLATES.map((template) => (
            <Button
              key={template.key}
              size="small"
              title={template.summary}
              onClick={() => appendBuiltInTemplate(template)}
            >
              追加「{template.title}」
            </Button>
          ))}
        </div>
        <Alert
          type="warning"
          showIcon
          style={{ marginTop: 12 }}
          message="模板只追加建议，不代表操作授权"
          description="模板不会替你确认文件写入或作业提交，也不会为材料猜测 Hubbard U；具体参数仍需结合结构、赝势和收敛证据确认。"
        />
      </Card>

      <div style={{ marginBottom: 12, display: 'flex', gap: 8 }}>
        <Input.TextArea
          placeholder="新增一条要求/指引，内容可任意填写、可写很多行…"
          value={newEntry}
          onChange={(e) => setNewEntry(e.target.value)}
          autoSize={{ minRows: 2, maxRows: 6 }}
          style={{ flex: 1 }}
        />
        <Button type="primary" ghost icon={<PlusOutlined />} onClick={addEntry}>
          新增条目
        </Button>
      </div>

      {!hasConfig && (
        <Empty
          description="尚未配置；点「新增条目」写下内容即自动保存，AI 控制本任务运行时会遵循它"
          style={{ padding: '20px 0' }}
        />
      )}

      {entries.map((text, idx) => (
        <Card
          key={idx}
          size="small"
          style={{ marginBottom: 8 }}
          extra={
            <Space>
              <Button type="text" size="small" icon={<ArrowUpOutlined />}
                disabled={idx === 0} onClick={() => moveEntry(idx, -1)} />
              <Button type="text" size="small" icon={<ArrowDownOutlined />}
                disabled={idx === entries.length - 1}
                onClick={() => moveEntry(idx, 1)} />
              <Button type="text" size="small" danger
                icon={<DeleteOutlined />} onClick={() => removeEntry(idx)} />
            </Space>
          }
        >
          <div style={{ fontSize: 12, color: '#999', marginBottom: 4 }}>
            条目 {idx + 1}（只有内容，没有名字 · 内容可写很多行、回车换行）
          </div>
          <Input.TextArea
            autoSize={{ minRows: 2, maxRows: 10 }}
            placeholder={`条目 ${idx + 1}（只有内容，没有名字）`}
            value={text}
            onChange={(e) => updateEntry(idx, e.target.value)}
          />
        </Card>
      ))}
    </Drawer>
  );
};

export default AiProjectExtraSettings;
