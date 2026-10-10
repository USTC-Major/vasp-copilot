import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Button, Checkbox, Select, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { toolboxApi } from '../../api/client';
import { energyApi, type EnergyCollection, type EnergyTaskPreview } from '../../api/energy';
import { energyError } from './energyDraft';

const allowedStates = new Set(['queued', 'running', 'completed', 'failed', 'not_converged', 'cancelled']);
const stateLabels: Record<string, string> = { queued: '排队中', running: '运行中', completed: '已完成', failed: '失败', not_converged: '未收敛', cancelled: '已取消' };
type Props = { disabled: boolean; collections: EnergyCollection[]; onImport: (previewId: string) => Promise<boolean>; onReuse: (collectionId: string, sampleId: string) => Promise<boolean> };
export default function EnergyTaskSource({ disabled, collections, onImport, onReuse }: Props) {
  const [projectId, setProjectId] = useState('');
  const [taskId, setTaskId] = useState('');
  const [jobKey, setJobKey] = useState('');
  const [attemptId, setAttemptId] = useState('');
  const [preview, setPreview] = useState<EnergyTaskPreview>();
  const [confirmed, setConfirmed] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [error, setError] = useState('');
  const generation = useRef(0);
  const abort = useRef<AbortController | null>(null);
  const projects = useQuery({ queryKey: ['energy-source-projects'], queryFn: () => toolboxApi.listProjects(), retry: false });
  const tasks = useQuery({ queryKey: ['energy-source-tasks', projectId], queryFn: () => toolboxApi.listTasks(projectId), enabled: !!projectId, retry: false });
  const detail = useQuery({ queryKey: ['energy-source-detail', projectId, taskId], queryFn: ({ signal }) => toolboxApi.getTaskDetail(projectId, taskId, signal), enabled: !!projectId && !!taskId, retry: false });
  const jobs = (detail.data?.flow.jobs ?? []).filter(job => allowedStates.has(job.status) && job.submission_state === 'submitted' && !!job.slurm_id && !!job.attempt_id);
  const job = jobs.find(item => item.key === jobKey);
  const currentAttempt = !!job && job.attempt_id === attemptId;
  const identity = `${projectId}/${taskId}/${jobKey}/${attemptId}/${job?.attempt_id ?? ''}/${job?.slurm_id ?? ''}`;
  const cancelPreview = useCallback(() => { generation.current++; abort.current?.abort(); }, []);
  useEffect(() => {
    cancelPreview(); setPreview(undefined); setConfirmed(false); setPreviewing(false); setError('');
    return cancelPreview;
  }, [identity, cancelPreview]);
  async function loadPreview() {
    if (!currentAttempt || disabled) return;
    const current = ++generation.current;
    const controller = new AbortController(); abort.current = controller;
    setPreviewing(true); setError(''); setPreview(undefined); setConfirmed(false);
    try {
      const response = await energyApi.previewTask({ project_id: projectId, task_id: taskId, job_key: jobKey, attempt_id: attemptId }, controller.signal);
      if (generation.current === current && !controller.signal.aborted) setPreview(response.preview);
    } catch (cause) { if (generation.current === current && !controller.signal.aborted) setError(energyError(cause)); }
    finally { if (generation.current === current) setPreviewing(false); }
  }
  const available = preview?.files.some(file => file.name === 'OUTCAR' && file.available && !!file.size_bytes && file.size_bytes <= 64 * 1024 ** 2);
  const cached = collections.flatMap(collection => collection.samples.filter(sample => sample.source.kind === 'task_result').map(sample => ({ collectionId: collection.id, collectionTitle: collection.title, sample })));
  return <div className="energy-import-form">
    <div className="energy-task-grid">
      <label className="energy-field">来源项目<Select aria-label="能量来源项目" value={projectId || undefined} placeholder="选择项目" disabled={disabled} loading={projects.isFetching} options={projects.data?.projects.map(project => ({ value: project.id, label: project.name }))} onChange={value => { setProjectId(value); setTaskId(''); setJobKey(''); setAttemptId(''); }} /></label>
      <label className="energy-field">来源任务<Select aria-label="能量来源任务" value={taskId || undefined} placeholder="选择任务" disabled={disabled || !projectId} loading={tasks.isFetching} options={tasks.data?.tasks.map(task => ({ value: task.id, label: task.title }))} onChange={value => { setTaskId(value); setJobKey(''); setAttemptId(''); }} /></label>
      <label className="energy-field">已提交作业<Select aria-label="能量来源作业" value={jobKey || undefined} placeholder="包含运行中与未完成作业" disabled={disabled || !taskId} loading={detail.isFetching} options={jobs.map(item => ({ value: item.key, label: `${item.label || item.key} · ${stateLabels[item.status] ?? '状态未知'}` }))} onChange={value => { setJobKey(value); setAttemptId(''); }} /></label>
      <label className="energy-field">当前执行尝试<Select aria-label="能量来源执行尝试" value={attemptId || undefined} placeholder="明确确认当前尝试" disabled={disabled || !job} options={job?.attempt_id ? [{ value: job.attempt_id, label: `${job.attempt_id} · 作业号 ${job.slurm_id}` }] : []} onChange={setAttemptId} /></label>
    </div>
    {[projects.error, tasks.error, detail.error].filter(Boolean).map((cause, index) => <Alert key={index} type="error" title={energyError(cause)} />)}
    {attemptId && !currentAttempt && <Alert type="warning" title="该执行尝试已经变化，请重新选择作业与当前尝试。旧缓存仍可离线复用。" />}
    <Typography.Text type="secondary">运行中、失败或未收敛作业可尝试读取稳定快照。传输期间文件变化会失败；取回不会停止作业。任务结束与科学收敛仍在确认表分别核对。</Typography.Text>
    <Button disabled={disabled || !currentAttempt} loading={previewing} onClick={() => void loadPreview()}>预览能量 OUTCAR 快照</Button>
    {taskId && <Button size="small" disabled={disabled || previewing} onClick={() => void detail.refetch()}>刷新能量来源任务状态</Button>}
    {preview && <>
      <div className="energy-details"><dl><dt>项目／任务</dt><dd>{projectId} / {taskId}</dd><dt>作业／尝试</dt><dd>{jobKey} / {attemptId}</dd><dt>作业号</dt><dd>{String(preview.source.slurm_id ?? '未知')}</dd><dt>有效至</dt><dd>{preview.expires_at}</dd></dl></div>
      {preview.warnings.map(warning => <Alert key={warning} type="warning" title={warning} />)}
      {preview.files.map(file => <Typography.Text key={file.name}>{file.name} · {file.size_bytes == null ? '缺失' : `${(file.size_bytes / 1024 ** 2).toFixed(2)} MiB`} · {file.available ? '可取回' : '不可取回'}{file.reason ? `（${file.reason}）` : ''}</Typography.Text>)}
      <Checkbox checked={confirmed} disabled={disabled || !available} onChange={event => setConfirmed(event.target.checked)}>确认从上述作业与执行尝试取回 OUTCAR，保存为不可变本地快照。</Checkbox>
      <Button type="primary" disabled={disabled || !confirmed || !available} onClick={() => void onImport(preview.id).then(succeeded => { if (succeeded) { setPreview(undefined); setConfirmed(false); } })}>确认取回能量快照</Button>
    </>}
    {error && <Alert type="error" title={error} />}
    {cached.length > 0 && <details className="energy-details"><summary>复用已缓存任务样本（无需连接超算）</summary>
      {cached.map(({ collectionId, collectionTitle, sample }) => <Button key={`${collectionId}/${sample.id}`} block disabled={disabled} onClick={() => void onReuse(collectionId, sample.id)}>{collectionTitle} · {sample.name} · {sample.id.slice(-8)}</Button>)}
    </details>}
  </div>;
}
