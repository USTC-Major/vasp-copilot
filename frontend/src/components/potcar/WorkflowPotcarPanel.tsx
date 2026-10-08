import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Card, Checkbox, Empty, Radio, Select, Space, Tag, Typography } from 'antd';
import { potcarApi } from '../../api/potcar';
import { ApiError } from '../../api/client';
import type { WorkflowPlan } from '../../types/generated-api';
import type { PotcarArtifact, PotcarLibrary, PotcarPreview, WorkflowPotcarChoice, WorkflowPotcarState } from '../../types/potcar';
import PotcarSelection from './PotcarSelection';

const { Paragraph, Text } = Typography;
const states = { omitted: '未包含 / 待补齐', pending_confirmation: '待确认', generating: '生成中', generated: '已生成', failed: '失败' };
export default function WorkflowPotcarPanel({ plan, draftKey, choice, onChoice, preparePlan, onPlanPrepared, generation, disabled }: {
  plan: WorkflowPlan; draftKey: string; choice: WorkflowPotcarChoice; onChoice: (value: WorkflowPotcarChoice) => void;
  preparePlan: () => Promise<WorkflowPlan>; onPlanPrepared: (value: WorkflowPlan) => void;
  generation?: WorkflowPotcarState; disabled: boolean;
}) {
  const [libraries, setLibraries] = useState<PotcarLibrary[]>([]);
  const [libraryId, setLibraryId] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [preview, setPreview] = useState<PotcarPreview | null>(null);
  const [ids, setIds] = useState<(string | null)[]>([]);
  const [manualIds, setManualIds] = useState<(string | null)[]>([]);
  const [dirty, setDirty] = useState(true);
  const [confirmed, setConfirmed] = useState(false);
  const [artifact, setArtifact] = useState<PotcarArtifact | null>(null);
  const [busy, setBusy] = useState<'libraries' | 'preview' | 'assemble' | null>(null);
  const [failure, setFailure] = useState<{ code: string; message: string } | null>(null);
  const epoch = useRef(0);
  const busyRef = useRef(false);
  const controller = useRef<AbortController | null>(null);
  const key = useRef<string | null>(null);
  const ownReplan = useRef<{ workflowId: string; revision: number; draftKey: string; mode: WorkflowPotcarChoice['mode']; epoch: number } | null>(null);
  const idempotency = useRef<string | null>(null);
  const callback = useRef(onChoice); callback.current = onChoice;
  const library = libraries.find(item => item.library_id === libraryId);
  const ready = !!library && library.reachable && library.index_revision !== null && !['running', 'queued'].includes(library.scan?.status ?? '');
  const invalidate = (clear = true) => {
    ++epoch.current; controller.current?.abort(); busyRef.current = false; setBusy(null);
    ownReplan.current = null;
    setDirty(true); setConfirmed(false); setArtifact(null); idempotency.current = null; setFailure(null);
    if (clear) { setPreview(null); setIds([]); setManualIds([]); }
    callback.current({ mode: 'include' });
  };
  useEffect(() => {
    const next = `${plan.workflow_id}:${plan.revision}:${draftKey}:${choice.mode}`;
    if (key.current === next) return;
    const prepared = ownReplan.current;
    const isOwnReplan = prepared?.workflowId === plan.workflow_id && prepared.revision === plan.revision && prepared.draftKey === draftKey && prepared.mode === choice.mode && prepared.epoch === epoch.current;
    ownReplan.current = null;
    key.current = next;
    if (isOwnReplan) return;
    ++epoch.current; controller.current?.abort(); busyRef.current = false; setBusy(null);
    setPreview(null); setIds([]); setManualIds([]); setDirty(true); setConfirmed(false); setArtifact(null); setFailure(null); idempotency.current = null;
    if (choice.mode === 'include') callback.current({ mode: 'include' });
  }, [plan.workflow_id, plan.revision, draftKey, choice.mode]);
  useEffect(() => () => { ++epoch.current; ownReplan.current = null; controller.current?.abort(); }, []);
  useEffect(() => {
    if (artifact && choice.mode === 'include' && choice.artifact_id !== artifact.artifact_id) invalidate(false);
  }, [artifact, choice.mode, choice.artifact_id]);
  const loadLibraries = async (initial = false) => {
    if (busyRef.current || disabled) return;
    if (!initial) invalidate();
    const token = epoch.current; const abort = new AbortController(); controller.current = abort;
    busyRef.current = true; setBusy('libraries'); setFailure(null);
    try {
      const result = await potcarApi.libraries(abort.signal);
      if (abort.signal.aborted || token !== epoch.current) return;
      setLibraries(result.libraries); setLoaded(true);
      setLibraryId(current => result.libraries.some(item => item.library_id === current) ? current : initial ? result.default_library_id ?? result.libraries[0]?.library_id ?? null : null);
    } catch (error) { if (!abort.signal.aborted && token === epoch.current) setFailure(problem(error)); }
    finally { if (token === epoch.current) { busyRef.current = false; setBusy(null); } }
  };
  // Fetch only when entering include; draft edits must not restart or loop failed loads.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { if (choice.mode === 'include' && !loaded) void loadLibraries(true); }, [choice.mode, loaded]);
  useEffect(() => {
    if (!preview || dirty || artifact) return;
    const timeout = window.setTimeout(() => { invalidate(false); setFailure({ code: 'POTCAR_PREVIEW_EXPIRED', message: '预览已到期，请刷新并重新核对。' }); }, Math.max(0, Date.parse(preview.expires_at) - Date.now()));
    return () => window.clearTimeout(timeout);
  }, [preview, dirty, artifact]);
  useEffect(() => {
    if (!artifact) return;
    const timeout = window.setTimeout(() => { invalidate(false); setFailure({ code: 'POTCAR_ARTIFACT_EXPIRED', message: '已确认产物到期，请重新预览并生成。' }); }, Math.max(0, Date.parse(artifact.expires_at) - Date.now()));
    return () => window.clearTimeout(timeout);
  }, [artifact]);
  const refresh = async () => {
    if (busyRef.current || disabled || !ready) return;
    invalidate(false); const token = epoch.current;
    const abort = new AbortController(); controller.current = abort; busyRef.current = true; setBusy('preview');
    try {
      // Save the current mode and patches before asking the server to render final POSCAR.
      const current = await preparePlan();
      if (abort.signal.aborted || token !== epoch.current) return;
      ownReplan.current = { workflowId: current.workflow_id, revision: current.revision, draftKey, mode: choice.mode, epoch: token }; onPlanPrepared(current);
      const result = await potcarApi.workflowPreview(current.workflow_id, { revision: current.revision, library_id: library!.library_id, index_revision: library!.index_revision!, ...(preview ? { dataset_ids: manualIds } : {}) }, abort.signal);
      if (abort.signal.aborted || token !== epoch.current) return;
      if (result.workflow_binding?.workflow_id !== current.workflow_id || result.workflow_binding.revision !== current.revision) throw new ApiError('POTCAR_BINDING_MISMATCH', '预览未绑定当前工作流计划，请重新读取。');
      setPreview(result); setIds(result.rows.map(row => row.dataset_id)); setManualIds(result.rows.map((_, index) => manualIds[index] ?? null)); setDirty(false);
    } catch (error) { if (!abort.signal.aborted && token === epoch.current) setFailure(problem(error)); }
    finally {
      const prepared = ownReplan.current;
      // A synchronous response can finish before React commits onPlanPrepared.
      // Keep that exact marker until its effect consumes it; clear no-op replans.
      if (prepared?.epoch === token && key.current === `${prepared.workflowId}:${prepared.revision}:${prepared.draftKey}:${prepared.mode}`) ownReplan.current = null;
      if (token === epoch.current) { busyRef.current = false; setBusy(null); }
    }
  };
  const canConfirm = !!preview && !dirty && preview.blockers.length === 0 && preview.rows.every(row => row.dataset_id !== null);
  const assemble = async () => {
    if (busyRef.current || disabled || !preview || !canConfirm || !confirmed) return;
    if (Date.parse(preview.expires_at) <= Date.now()) { invalidate(false); return; }
    const token = epoch.current; const abort = new AbortController(); controller.current = abort;
    busyRef.current = true; setBusy('assemble'); setFailure(null); idempotency.current ??= crypto.randomUUID();
    try {
      const result = await potcarApi.assemble({ preview_id: preview.preview_id, selection_digest: preview.selection_digest, confirmed_order_and_variants: true, idempotency_key: idempotency.current }, abort.signal);
      if (abort.signal.aborted || token !== epoch.current) return;
      if (result.artifact.workflow_binding?.workflow_id !== preview.workflow_binding?.workflow_id || result.artifact.workflow_binding?.revision !== preview.workflow_binding?.revision) throw new ApiError('POTCAR_BINDING_MISMATCH', '产物与当前计划绑定不一致，请重新预览。');
      setArtifact(result.artifact); callback.current({ mode: 'include', artifact_id: result.artifact.artifact_id });
    } catch (error) {
      if (!abort.signal.aborted && token === epoch.current) {
        const failure = problem(error);
        if (error instanceof ApiError && !['UNKNOWN', 'POTCAR_REQUEST_FAILED'].includes(error.code)) { invalidate(false); }
        setFailure(failure);
      }
    } finally { if (token === epoch.current) { busyRef.current = false; setBusy(null); } }
  };
  const status = generation?.status ?? (choice.mode === 'omit' ? 'omitted' : busy === 'assemble' ? 'generating' : failure ? 'failed' : 'pending_confirmation');
  return <Card title="工作流 POTCAR" className="potcar-workflow-card">
    <Radio.Group aria-label="工作流是否包含 POTCAR" value={choice.mode} disabled={disabled || busy === 'assemble'} onChange={event => onChoice({ mode: event.target.value })}><Space wrap><Radio value="omit">不包含，后续自行补齐</Radio><Radio value="include">从本地库包含 POTCAR</Radio></Space></Radio.Group>
    <Paragraph><Tag>{states[status]}</Tag>文件准备不会启动计算；其他上游文件和诊断条件仍需满足。</Paragraph>
    {choice.mode === 'include' && <>
      <Paragraph>最终 POSCAR 和计算上下文由服务端当前计划提供。相同产物关联所有步骤，仅核对确认一次。</Paragraph>
      <div className="potcar-assembly-library"><label htmlFor="workflow-potcar-library">当前库</label><Select id="workflow-potcar-library" aria-label="工作流赝势库" value={libraryId} disabled={disabled || busy !== null} onChange={id => { invalidate(); setLibraryId(id); }} options={libraries.map(item => ({ value: item.library_id, label: item.display_name }))} /><Button disabled={disabled || busy !== null} loading={busy === 'libraries'} onClick={() => void loadLibraries()}>刷新库信息</Button><a href="/toolbox/potcar">管理 / 重新扫描本地库</a></div>
      {loaded && libraries.length === 0 && <Empty description="尚未登记赝势库；可先选择不包含 POTCAR，或登记并扫描后刷新库信息。" />}
      {library && !ready && <Alert type="warning" showIcon title="当前库未就绪，请检查路径或完成扫描后刷新库信息。" />}
      <Button disabled={disabled || busy !== null || !ready} loading={busy === 'preview'} onClick={() => void refresh()}>读取 / 刷新工作流 POTCAR 预览</Button>
      {preview && <>
        <div className="potcar-preview-details"><span>绑定计划：{preview.workflow_binding?.workflow_id} · revision {preview.workflow_binding?.revision}</span><span>一次确认关联步骤：{preview.workflow_binding?.step_ids.join(' → ')}</span></div>
        <PotcarSelection preview={preview} datasetIds={ids} dirty={dirty} disabled={disabled || busy === 'assemble'} onChange={(index, id) => { invalidate(false); setIds(current => current.map((value, position) => position === index ? id : value)); setManualIds(current => preview.rows.map((_, position) => position === index ? id : current[position] ?? null)); }} />
        <Space wrap><Checkbox checked={confirmed} disabled={disabled || busy !== null || !canConfirm || !!artifact} onChange={event => setConfirmed(event.target.checked)}>已核对物种顺序、变体、计算用途及全部关联步骤</Checkbox><Button type="primary" disabled={disabled || busy !== null || !canConfirm || !confirmed || !!artifact} loading={busy === 'assemble'} onClick={() => void assemble()}>确认并准备工作流 POTCAR</Button></Space>
      </>}
      {artifact && <Alert type={generation?.status === 'failed' ? 'warning' : 'success'} showIcon title={generation?.status === 'generated' ? 'POTCAR 已加入工作流文件' : generation?.status === 'failed' ? 'POTCAR 已核验，本次加入工作流失败，请恢复后重新生成' : generation?.status === 'generating' ? 'POTCAR 已核验，正在加入工作流文件' : 'POTCAR 已核验，等待加入工作流文件'} description={<><div>SHA-256：{artifact.sha256} · {artifact.size_bytes} bytes</div><div>按上方关联步骤共享同一文件；正文不提供普通预览。</div></>} />}
    </>}
    {generation?.steps && <ul className="potcar-step-states">{generation.steps.map(step => <li key={step.step_id}>{step.step_id}：{states[step.status]} {step.sha256 && <Text code>{step.sha256}</Text>}</li>)}</ul>}
    {(failure || generation?.error) && <Alert role="alert" type="error" showIcon title={(failure ?? generation?.error)?.message} description={<><Text code>{(failure ?? generation?.error)?.code}</Text><div>包含失败时不会交付缺少 POTCAR 的成功包。源文件或索引变化请主动重新扫描并刷新；其余情况可刷新预览重新核对。</div></>} />}
  </Card>;
}
function problem(error: unknown) { return { code: error instanceof ApiError ? error.code : 'POTCAR_REQUEST_FAILED', message: error instanceof Error ? error.message : '请求失败，请重试' }; }
