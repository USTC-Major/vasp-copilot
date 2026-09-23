import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Button, Card, Select, Space, Typography, message } from 'antd';
import { toolboxApi } from '../../api/client';
import type { ToolboxJob, ToolboxResultName } from '../../types/toolbox';

const { Text } = Typography;
const names: ToolboxResultName[] = ['OUTCAR', 'OSZICAR', 'CONTCAR'];
const terminal = new Set(['completed', 'failed', 'not_converged']);

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

interface Props {
  projectId: string;
  taskId: string;
  jobs: ToolboxJob[];
  report: string;
  onRefresh?: () => void | Promise<unknown>;
}

const ToolboxResultsPanel: React.FC<Props> = ({ projectId, taskId, jobs, report, onRefresh }) => {
  const [jobKey, setJobKey] = useState('');
  const [busy, setBusy] = useState<ToolboxResultName | null>(null);
  const [error, setError] = useState('');
  const generation = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const eligible = useMemo(() => jobs.filter((job) => terminal.has(job.status)
    && job.submission_state === 'submitted' && !!job.slurm_id && !!job.attempt_id), [jobs]);
  const selected = eligible.find((job) => job.key === jobKey) ?? (eligible.length === 1 ? eligible[0] : undefined);
  const identity = `${projectId}/${taskId}/${jobs.map((job) => `${job.key}:${job.attempt_id ?? ''}`).join('|')}`;

  useEffect(() => {
    generation.current += 1;
    controller.current?.abort();
    controller.current = null;
    setJobKey('');
    setBusy(null);
    setError('');
    return () => {
      generation.current += 1;
      controller.current?.abort();
    };
  }, [identity]);

  const download = async (name: ToolboxResultName) => {
    if (!selected?.attempt_id || busy) return;
    const current = ++generation.current;
    const abort = new AbortController();
    controller.current = abort;
    setBusy(name);
    setError('');
    try {
      const blob = await toolboxApi.downloadResult(projectId, taskId, selected.key, selected.attempt_id, name, abort.signal);
      if (current === generation.current && !abort.signal.aborted) {
        saveBlob(blob, name);
        message.success(`${name} 已发起下载`);
      }
    } catch (cause) {
      if (current === generation.current && !abort.signal.aborted) {
        setError(cause instanceof Error ? cause.message : '结果下载失败');
      }
    } finally {
      if (current === generation.current) {
        setBusy(null);
        controller.current = null;
        void onRefresh?.();
      }
    }
  };

  return <Card title="计算结果取回" size="small">
    <Space direction="vertical" style={{ width: '100%' }}>
      <Text type="secondary">仅在作业终态后取回该次提交目录当前存在的 OUTCAR、OSZICAR、CONTCAR；单文件上限 32 MiB。文件完整性不证明由本次计算产生或计算已收敛。WAVECAR、CHGCAR 不在此入口。</Text>
      {eligible.length > 1 && <Select aria-label="选择结果作业" style={{ width: 320 }} placeholder="选择已结束的计算作业"
        disabled={!!busy}
        value={jobKey || undefined} onChange={setJobKey}
        options={eligible.map((job) => ({ value: job.key, label: `${job.label || job.key} · ${job.attempt_id}` }))} />}
      {!eligible.length && <Text type="secondary">当前没有可取回的终态提交。请先确认作业号与状态。</Text>}
      <Space wrap>
        {names.map((name) => <Button key={name} aria-label={`下载 ${name}`} disabled={!selected || !!busy}
          loading={busy === name} onClick={() => void download(name)}>下载 {name}</Button>)}
      </Space>
      {report && <Button onClick={() => saveBlob(new Blob([report], { type: 'text/markdown;charset=utf-8' }), 'toolbox-report.md')}>
        保存当前任务报告（Markdown）
      </Button>}
      {error && <Alert type="error" showIcon message={error} />}
    </Space>
  </Card>;
};

export default ToolboxResultsPanel;
