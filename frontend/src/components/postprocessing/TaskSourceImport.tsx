import { useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Button, Checkbox, Empty, Select, Space, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { toolboxApi } from '../../api/client';
import { ppApi, type PPDataset, type PPTaskIdentity, type PPTaskPreview } from '../../api/postprocessing';
import TaskSourceDetails from './TaskSourceDetails';
import { taskSelectionError } from './taskSourceSelection';

const terminal = new Set(['completed', 'failed', 'not_converged']);
const message = (error: unknown) => error instanceof Error ? error.message : '操作失败，请重试';
const sizeLabel = (bytes: number) => bytes < 1024 ? `${bytes} bytes` : bytes < 1024 ** 2 ? `${(bytes / 1024).toFixed(1)} KiB` : `${(bytes / 1024 ** 2).toFixed(2)} MiB`;
const fileReason = (reason: string) => ({ PP_TOO_LARGE: '超过单文件上限', SOURCE_NOT_FOUND: '文件缺失', PATH_SYMLINK_ESCAPE: '符号链接不可取回', CONTENT_READ_DENIED: '文件类型或读取权限不允许取回', PP_EMPTY_FILE: '文件为空' }[reason] ?? reason);

type Props = {
  kind: 'dos' | 'band'; title: string; datasets: PPDataset[]; disabled: boolean;
  initial: Partial<PPTaskIdentity>; refreshRequest: number;
  onDataset: (dataset: PPDataset) => void; onBusy: (busy: boolean) => void;
};

export default function TaskSourceImport({ kind, title, datasets, disabled, initial, refreshRequest, onDataset, onBusy }: Props) {
  const [projectId, setProjectId] = useState(initial.project_id ?? '');
  const [taskId, setTaskId] = useState(initial.task_id ?? '');
  const [jobKey, setJobKey] = useState(initial.job_key ?? '');
  const [attemptId, setAttemptId] = useState(initial.attempt_id ?? '');
  const [preview, setPreview] = useState<PPTaskPreview>();
  const [files, setFiles] = useState<string[]>([]);
  const [confirmed, setConfirmed] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState('');
  const generation = useRef(0);
  const abort = useRef<AbortController | null>(null);
  const projects = useQuery({ queryKey: ['pp-source-projects'], queryFn: () => toolboxApi.listProjects(), retry: false });
  const tasks = useQuery({ queryKey: ['pp-source-tasks', projectId], queryFn: () => toolboxApi.listTasks(projectId), enabled: !!projectId, retry: false });
  const detail = useQuery({ queryKey: ['pp-source-detail', projectId, taskId], queryFn: ({ signal }) => toolboxApi.getTaskDetail(projectId, taskId, signal), enabled: !!projectId && !!taskId, retry: false });
  const eligible = useMemo(() => (detail.data?.flow.jobs ?? []).filter(job => terminal.has(job.status) && job.submission_state === 'submitted' && !!job.slurm_id && !!job.attempt_id), [detail.data?.flow.jobs]);
  const job = eligible.find(candidate => candidate.key === jobKey);
  const exactAttempt = !!job && job.attempt_id === attemptId;
  const identity = `${projectId}/${taskId}/${jobKey}/${attemptId}/${job?.attempt_id ?? ''}/${job?.slurm_id ?? ''}/${kind}/${refreshRequest}`;

  useEffect(() => {
    generation.current += 1;
    abort.current?.abort();
    setPreview(undefined); setFiles([]); setConfirmed(false); setError(''); setPreviewing(false);
    return () => { generation.current += 1; abort.current?.abort(); };
  }, [identity]);

  // Explicit refresh sets the same source identity, but always requires a fresh
  // preview and confirmation. An existing cached dataset is never overwritten.
  useEffect(() => {
    if (!refreshRequest) return;
    setProjectId(initial.project_id ?? ''); setTaskId(initial.task_id ?? '');
    setJobKey(initial.job_key ?? ''); setAttemptId(initial.attempt_id ?? '');
  }, [refreshRequest, initial.project_id, initial.task_id, initial.job_key, initial.attempt_id]);

  const cached = datasets.filter(dataset => dataset.kind === kind && dataset.source?.kind === 'task_result' && dataset.source.project_id === projectId && dataset.source.task_id === taskId && dataset.source.job_key === jobKey && dataset.source.attempt_id === attemptId && dataset.download?.status === 'cached');
  const selectionError = preview ? taskSelectionError(preview, files) : '';
  const selectedBytes = preview?.files.filter(file => files.includes(file.name)).reduce((sum, file) => sum + (file.size_bytes ?? 0), 0) ?? 0;
  const controlsDisabled = disabled || importing;

  async function loadPreview() {
    if (!exactAttempt || controlsDisabled || previewing) return;
    const current = ++generation.current;
    const controller = new AbortController(); abort.current = controller;
    setPreviewing(true); setPreview(undefined); setConfirmed(false); setError('');
    try {
      const response = await ppApi.previewTaskSource({ project_id: projectId, task_id: taskId, job_key: jobKey, attempt_id: attemptId, kind }, controller.signal);
      if (generation.current === current && !controller.signal.aborted) {
        setPreview(response.preview); setFiles(response.preview.suggested_files);
      }
    } catch (cause) {
      if (generation.current === current && !controller.signal.aborted) setError(message(cause));
    } finally {
      if (generation.current === current) { setPreviewing(false); abort.current = null; }
    }
  }

  async function importSource(reuseId?: string) {
    if (controlsDisabled || (!reuseId && (!preview || !confirmed || selectionError))) return;
    setImporting(true); onBusy(true); setError('');
    try {
      const response = await ppApi.importTaskSource(reuseId ? { reuse_dataset_id: reuseId } : { preview_id: preview!.id, files, title });
      onDataset(response.dataset);
      if (!reuseId) { setPreview(undefined); setConfirmed(false); }
    } catch (cause) { setError(message(cause)); }
    finally { setImporting(false); onBusy(false); }
  }

  return <div className="pp-task-import">
    <div className="pp-controls pp-single">
      <label>来源项目<Select aria-label="来源项目" value={projectId || undefined} placeholder="选择项目" loading={projects.isFetching} disabled={controlsDisabled} options={projects.data?.projects.map(project => ({ value: project.id, label: `${project.name} · ${project.id}` }))} onChange={value => { setProjectId(value); setTaskId(''); setJobKey(''); setAttemptId(''); }} /></label>
      <label>来源任务<Select aria-label="来源任务" value={taskId || undefined} placeholder="选择任务" loading={tasks.isFetching} disabled={controlsDisabled || !projectId} options={tasks.data?.tasks.map(task => ({ value: task.id, label: `${task.title} · ${task.id}` }))} onChange={value => { setTaskId(value); setJobKey(''); setAttemptId(''); }} /></label>
      <label>来源作业<Select aria-label="来源作业" value={jobKey || undefined} placeholder="选择已提交的终态作业" loading={detail.isFetching} disabled={controlsDisabled || !taskId} options={eligible.map(candidate => ({ value: candidate.key, label: `${candidate.label || candidate.key} · ${candidate.key} · ${candidate.status}` }))} onChange={value => { setJobKey(value); setAttemptId(''); }} /></label>
      <label>执行尝试<Select aria-label="来源执行尝试" value={attemptId || undefined} placeholder="确认当前执行尝试" disabled={controlsDisabled || !job} options={job?.attempt_id ? [{ value: job.attempt_id, label: `${job.attempt_id} · 作业号 ${job.slurm_id}` }] : []} onChange={setAttemptId} /></label>
    </div>
    {[projects.error, tasks.error, detail.error].filter(Boolean).map((cause, index) => <Alert key={index} type="error" title={message(cause)} action={<Button size="small" onClick={() => { void projects.refetch(); if (projectId) void tasks.refetch(); if (taskId) void detail.refetch(); }}>重试读取任务</Button>} />)}
    {detail.data && !eligible.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前没有具备作业号的终态提交可取回" />}
    {attemptId && !exactAttempt && detail.data && <Alert type="warning" title="该执行尝试不再是当前可取回的提交，请重新选择作业与尝试。已保存缓存仍可从分析列表打开。" />}
    <Typography.Paragraph type="secondary">预览只检查该次提交目录的文件名称与大小；确认后才下载。支持当前执行尝试，已缓存历史结果可离线打开。</Typography.Paragraph>
    <Space wrap>
      <Button aria-label={refreshRequest ? '预览远端新快照' : '预览结果文件'} loading={previewing} disabled={!exactAttempt || controlsDisabled} onClick={() => void loadPreview()}>{refreshRequest ? '预览远端新快照' : '预览结果文件'}</Button>
      {previewing && <Button onClick={() => { generation.current += 1; abort.current?.abort(); setPreviewing(false); }}>取消预览</Button>}
      {taskId && <Button size="small" disabled={controlsDisabled || previewing} onClick={() => void detail.refetch()}>刷新任务状态</Button>}
    </Space>
    {cached.length > 0 && <div className="pp-cache-options">
      <Typography.Paragraph type="secondary">该来源已有本地缓存，可直接打开，无需连接超算。</Typography.Paragraph>
      {cached.map(dataset => <Button key={dataset.id} block disabled={controlsDisabled} onClick={() => void importSource(dataset.id)}>打开缓存：{dataset.title}</Button>)}
    </div>}
    {preview && <>
      <TaskSourceDetails source={preview.source} />
      {preview.warnings.map(warning => <Alert key={warning} type="warning" title={warning} />)}
      <Typography.Paragraph>建议组合：{preview.suggested_files.join('＋') || '必需文件缺失，请检查计算输出'}。可取消本次不需要的文件。</Typography.Paragraph>
      <fieldset className="pp-task-files"><legend>选择下载文件</legend>
        {preview.files.map(file => <div key={file.name} className="pp-task-file">
          <Checkbox checked={files.includes(file.name)} disabled={controlsDisabled || !file.available || !file.size_bytes || file.size_bytes > preview.limits.max_file_bytes} onChange={event => { setConfirmed(false); setFiles(previous => event.target.checked ? [...previous, file.name] : previous.filter(name => name !== file.name)); }}>{file.name} · {file.size_bytes === null ? '缺失' : sizeLabel(file.size_bytes)}</Checkbox>
          {file.reason && <Typography.Text type="secondary">{fileReason(file.reason)}</Typography.Text>}
        </div>)}
      </fieldset>
      <Typography.Paragraph type="secondary">已选 {files.length} 个文件，共 {sizeLabel(selectedBytes)}；单文件 ≤{sizeLabel(preview.limits.max_file_bytes)}，批次 ≤{sizeLabel(preview.limits.max_total_bytes)}。预览有效至 {preview.expires_at}。</Typography.Paragraph>
      {selectionError && <Alert type="warning" title={selectionError} />}
      <Checkbox checked={confirmed} disabled={controlsDisabled || !!selectionError} onChange={event => setConfirmed(event.target.checked)}>确认从上述作业与执行尝试下载所选文件，保存为本地独立副本。</Checkbox>
      <Button aria-label="确认下载并缓存" className="pp-confirm-download" type="primary" block loading={importing} disabled={controlsDisabled || !confirmed || !!selectionError} onClick={() => void importSource()}>确认下载并缓存</Button>
    </>}
    {error && <Alert type="error" showIcon title={error} />}
  </div>;
}
