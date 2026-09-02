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
  Drawer,
  Empty,
  Input,
  Space,
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

  useEffect(() => {
    if (dataQuery.data?.settings) {
      const loaded = toEntries(dataQuery.data.settings.accuracy);
      setEntries(loaded);
      lastSavedRef.current = JSON.stringify(loaded.map((e) => e.trim()).filter(Boolean));
      setHasLoaded(true);
    }
  }, [dataQuery.data]);

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
        dataQuery.refetch();
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

  const TEMPLATE_KEY = 'ai_project_accuracy_template';
  const saveAsTemplate = () => {
    const cleaned = entries.map((e) => e.trim()).filter(Boolean);
    if (cleaned.length === 0) {
      message.warning('当前无条目，无需保存模板');
      return;
    }
    localStorage.setItem(TEMPLATE_KEY, JSON.stringify({ accuracy: cleaned, saved_at: Date.now() }));
    message.success('已保存为模板（本机 localStorage）');
  };

  const applyTemplate = () => {
    const raw = localStorage.getItem(TEMPLATE_KEY);
    if (!raw) {
      message.info('尚无已保存的模板');
      return;
    }
    try {
      const parsed = JSON.parse(raw) as { accuracy?: unknown };
      setEntries(toEntries(parsed.accuracy));
      message.success('已应用模板（自动保存）');
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
          <Button onClick={applyTemplate}>应用模板</Button>
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
