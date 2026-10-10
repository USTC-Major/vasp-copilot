import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Descriptions, Modal, Typography } from 'antd';
import { useNavigate } from 'react-router-dom';
import { catalysisApi } from '../../api/catalysis';
import { ApiError } from '../../api/client';
import type { CatalysisDraft, CatalysisSnapshot, CatalysisWorkflowTarget } from '../../types/catalysis';
import { adoptCatalysisWorkflow, getWorkflowDraft, hasWorkflowDraft, useWorkflowDraft, type WorkflowDraft } from '../../stores/workflowDraft';

type Confirmation = { identity: string; draft: CatalysisDraft; target: CatalysisWorkflowTarget; workflow: WorkflowDraft };

export default function CatalysisWorkflowHandoff({ doc, target, snapshot, identity, disabled, label, onConflict }: {
  doc: CatalysisDraft; target: CatalysisWorkflowTarget; snapshot: CatalysisSnapshot;
  identity: string; disabled: boolean; label: string;
  onConflict?: () => void;
}) {
  const navigate = useNavigate();
  useWorkflowDraft();
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const [pending, setPending] = useState(false), [error, setError] = useState('');
  const live = useRef(true), lock = useRef(false), current = useRef({ identity, disabled });
  current.current = { identity, disabled };
  useEffect(() => { live.current = true; return () => { live.current = false; }; }, []);
  useEffect(() => {
    if (confirmation && (identity !== confirmation.identity || disabled)) {
      setConfirmation(null);
      setError('建模输入或候选已变化，请重新预览并确认。');
    }
  }, [identity, disabled, confirmation]);

  async function confirm() {
    if (!confirmation || lock.current || current.current.disabled || current.current.identity !== confirmation.identity) return;
    if (getWorkflowDraft() !== confirmation.workflow) {
      setConfirmation(null); setError('Workflow 草稿在确认期间已变化，请重新审阅替换。'); return;
    }
    lock.current = true; setPending(true); setError('');
    try {
      const response = await catalysisApi.workflowBinding(confirmation.draft, confirmation.target);
      if (!live.current) return;
      if (current.current.disabled || current.current.identity !== confirmation.identity) {
        setConfirmation(null); setError('传入期间建模输入已变化，旧快照未替换 Workflow 草稿。'); return;
      }
      const binding = response.binding;
      if (binding.draft_id !== confirmation.draft.draft_id || binding.revision !== confirmation.draft.revision ||
          (confirmation.target.candidate_id ? binding.candidate_id !== confirmation.target.candidate_id :
            binding.model_kind !== 'clean_surface' || binding.surface_id !== confirmation.target.surface_id) ||
          response.summary.structure_id !== response.structure_id) {
        setConfirmation(null); setError('交接响应与确认的结构身份不一致，请重新载入草稿后重试。'); return;
      }
      if (!adoptCatalysisWorkflow(response, confirmation.workflow)) {
        setConfirmation(null); setError('Workflow 草稿在传入期间已变化，已保留新输入；请重新审阅替换。'); return;
      }
      navigate('/workflow');
    } catch (e) {
      if (live.current) {
        // A conflict cannot reuse the old confirmation even if local inputs still look unchanged.
        setConfirmation(null);
        setError(e instanceof Error ? e.message : '传入失败，请重新载入草稿并确认。');
        if (e instanceof ApiError && e.status === 409) onConflict?.();
      }
    } finally { lock.current = false; if (live.current) setPending(false); }
  }

  const replace = confirmation && hasWorkflowDraft(confirmation.workflow);
  const fixed = snapshot.atoms.filter(atom => atom.selective_dynamics.every(flag => !flag)).length;
  const partial = snapshot.atoms.filter(atom => atom.selective_dynamics.some(Boolean) && !atom.selective_dynamics.every(Boolean)).length;
  return <>
    <Button disabled={disabled || pending} loading={pending} onClick={() => {
      if (disabled || lock.current) return;
      setError('');
      setConfirmation({ identity, draft: structuredClone(doc), target: { ...target }, workflow: getWorkflowDraft() });
    }}>{label}</Button>
    {error && <Alert type="warning" showIcon title="Workflow 传入未完成" description={error} style={{ marginTop: 12 }} />}
    <Modal open={!!confirmation} title="确认单个模型传入 Workflow" destroyOnHidden width={640}
      okText={replace ? '确认替换草稿并进入 Workflow' : '确认并进入 Workflow'} cancelText="取消，保留当前草稿"
      confirmLoading={pending} okButtonProps={{ disabled: pending }} cancelButtonProps={{ disabled: pending }}
      mask={{ closable: !pending }} keyboard={!pending} onOk={() => void confirm()}
      onCancel={() => { if (!lock.current) setConfirmation(null); }}>
      {confirmation && <>
        <Descriptions column={1} size="small" bordered>
          <Descriptions.Item label="建模草稿">{doc.name} · revision {doc.revision}</Descriptions.Item>
          <Descriptions.Item label="单个模型">{target.candidate_id ? `吸附候选 ${target.candidate_id}` : `清洁表面 ${target.surface_id}`}</Descriptions.Item>
          <Descriptions.Item label="结构快照">{snapshot.snapshot_id} · {snapshot.atoms.length} 原子</Descriptions.Item>
          <Descriptions.Item label="逐原子约束">全固定 {fixed} · 部分方向固定 {partial} · 其余自由；T/F 沿直接晶格 a/b/c。</Descriptions.Item>
        </Descriptions>
        {replace && <Alert type="warning" showIcon style={{ marginTop: 16 }} title="已有 Workflow 草稿将被替换"
          description={`当前：${confirmation.workflow.sampleName || confirmation.workflow.summary?.formula || '已编辑草稿'}。替换后需重新确认计算参数、手工补丁和 POTCAR；取消会保留全部当前输入。`} />}
        <Typography.Paragraph style={{ marginTop: 16 }}>进入后请审阅表面计算策略及计算参数，再生成计划。来源、清洁表面关联与原子约束随不可变快照传递。多选候选继续通过 ZIP 导出。</Typography.Paragraph>
      </>}
    </Modal>
  </>;
}
