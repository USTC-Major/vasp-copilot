// ============================================================
// WorkflowBuilderPage — 串联上传→解析→计划→生成→下载完整流程
// ============================================================

import React, { useState, useCallback, useMemo, useRef, useEffect } from 'react';
import { Steps, Button, Space, Card, Result, Typography, Input, Alert, Select, Checkbox } from 'antd';
import { ReloadOutlined, DownloadOutlined, ArrowRightOutlined, ArrowLeftOutlined, RobotOutlined, GlobalOutlined } from '@ant-design/icons';
import WorkflowPotcarPanel from '../components/potcar/WorkflowPotcarPanel';
import { workflowsApi } from '../api/client';
import type { WorkflowPotcarChoice } from '../types/potcar';
import StructureUploadPanel from '../components/upload/StructureUploadPanel';
import MaterialsProjectPanel from '../components/upload/MaterialsProjectPanel';
import ParameterConfirmForm, { type ParameterConfirmFormData } from '../components/workflow/ParameterConfirmForm';
import WorkflowConfirmSummaryModal from '../components/workflow/WorkflowConfirmSummaryModal';
import CatalysisWorkflowContext from '../components/workflow/CatalysisWorkflowContext';
import WorkflowPlanPreview from '../components/workflow/WorkflowPlanPreview';
import RecipeCompositionPreview from '../components/recipes/RecipeCompositionPreview';
import ParameterPatchEditor from '../components/recipes/ParameterPatchEditor';
import GeneratedFilesPreview from '../components/workflow/GeneratedFilesPreview';
import CrystalViewer from '../components/structure/CrystalViewer';
import ErrorAlert from '../components/common/ErrorAlert';
import AiPlanAssistant, { type AiPlanAssistantResult } from '../components/workflow/AiPlanAssistant';
import { useWorkflowPlan, useWorkflowGenerate, useWorkflowDownload, useFeatureFlags } from '../hooks/useApi';
import type { StructureSummary, WorkflowPlan, ParameterPatch } from '../types/generated-api';
import { useWorkflowDraft, setWorkflowDraftField, resetWorkflowDraft, getWorkflowDraft, type WorkflowStep } from '../stores/workflowDraft';
import { formatMaterialId } from '../utils/materialId';
import type {
  DftuSettingsRequest,
  WorkflowConfirmSnapshot,
  WorkflowPlanRequestBody,
} from '../types/workflow-contract';
import type { CatalysisSurfacePolicy, CatalysisWorkflowBinding } from '../types/catalysis';

const { Title } = Typography;

const ALLOWED_PARAMS: { parameter: string; type: string; minimum?: number; maximum?: number; options?: string[] }[] = [
  { parameter: 'ENCUT', type: 'number', minimum: 1 },
  { parameter: 'EDIFF', type: 'number' },
  { parameter: 'EDIFFG', type: 'number' },
  { parameter: 'NSW', type: 'number', minimum: 0 },
  { parameter: 'ALGO', type: 'string', options: ['Normal', 'Fast', 'VeryFast'] },
  { parameter: 'ISMEAR', type: 'number' },
  { parameter: 'SIGMA', type: 'number' },
];

type StepKey = WorkflowStep;

const draftSetters = {
  setCurrentStep: (value: Parameters<typeof setWorkflowDraftField<'currentStep'>>[1]) => setWorkflowDraftField('currentStep', value),
  setStructureId: (value: Parameters<typeof setWorkflowDraftField<'structureId'>>[1]) => setWorkflowDraftField('structureId', value),
  setSummary: (value: Parameters<typeof setWorkflowDraftField<'summary'>>[1]) => setWorkflowDraftField('summary', value),
  setSampleName: (value: Parameters<typeof setWorkflowDraftField<'sampleName'>>[1]) => setWorkflowDraftField('sampleName', value),
  setWorkflowPlan: (value: Parameters<typeof setWorkflowDraftField<'workflowPlan'>>[1]) => setWorkflowDraftField('workflowPlan', value),
  setWorkflowId: (value: Parameters<typeof setWorkflowDraftField<'workflowId'>>[1]) => setWorkflowDraftField('workflowId', value),
  setFileTree: (value: Parameters<typeof setWorkflowDraftField<'fileTree'>>[1]) => setWorkflowDraftField('fileTree', value),
  setPotcarChoice: (value: Parameters<typeof setWorkflowDraftField<'potcarChoice'>>[1]) => setWorkflowDraftField('potcarChoice', value),
  setPotcarResult: (value: Parameters<typeof setWorkflowDraftField<'potcarResult'>>[1]) => setWorkflowDraftField('potcarResult', value),
  setPatches: (value: Parameters<typeof setWorkflowDraftField<'patches'>>[1]) => setWorkflowDraftField('patches', value),
  setPatchRows: (value: Parameters<typeof setWorkflowDraftField<'patchRows'>>[1]) => setWorkflowDraftField('patchRows', value),
  setFormValues: (value: Parameters<typeof setWorkflowDraftField<'formValues'>>[1]) => setWorkflowDraftField('formValues', value),
  setGenerationNeedsCheck: (value: Parameters<typeof setWorkflowDraftField<'generationNeedsCheck'>>[1]) => setWorkflowDraftField('generationNeedsCheck', value),
};

const { setCurrentStep, setStructureId, setSummary, setSampleName, setWorkflowPlan, setWorkflowId,
    setFileTree, setPotcarChoice, setPotcarResult, setPatches, setPatchRows, setFormValues, setGenerationNeedsCheck } = draftSetters;

const defaultSampleName = (summary: StructureSummary): string => {
  const divisor = summary.counts.reduce((a, b) => {
    let x = a; let y = b;
    while (y) { [x, y] = [y, x % y]; }
    return x;
  }, 0) || 1;
  return summary.elements.map((element, index) => {
    const count = summary.counts[index] / divisor;
    return `${element}${count === 1 ? '' : count}`;
  }).join('');
};

const effectiveSampleName = (summary: StructureSummary, value: string): string =>
  value.trim() || defaultSampleName(summary);

const sampleNameValid = (value: string): boolean => {
  if (/[\p{C}\u2028\u2029]/u.test(value)) return false;
  const trimmed = value.trim();
  return Array.from(trimmed).length <= 256;
};

const poscarComment = (summary: StructureSummary, name: string): string => {
  const full = `${summary.source_material_id ? `${formatMaterialId(summary.source_material_id)} ` : ''}${effectiveSampleName(summary, name)}`;
  let result = '';
  const encoder = new TextEncoder();
  for (const character of full) {
    if (encoder.encode(result + character).length > 40) break;
    result += character;
  }
  return result;
};

const SampleNameEditor: React.FC<{
  summary: StructureSummary;
  value: string;
  onChange: (value: string) => void;
}> = ({ summary, value, onChange }) => {
  const materialId = summary.source_material_id;
  const preview = poscarComment(summary, value);
  return (
    <Card size="small" title="样品名称 / POSCAR 首行" style={{ marginBottom: 16 }}>
      <Input aria-label="样品名称" value={value}
        onChange={(event) => onChange(event.target.value)} placeholder="留空则使用化学式" />
      {!sampleNameValid(value) && <Alert style={{ marginTop: 8 }} type="error" showIcon
        message="名称最多 256 个 Unicode 字符，不允许换行或控制字符；留空则使用化学式。" />}
      <Typography.Text type="secondary" style={{ display: 'block', marginTop: 8 }}>
        POSCAR 首行预览：{preview}。为兼容 UTF-8，首行保守限制为 40 字节，完整名称保存在工作流元数据中。
      </Typography.Text>
      {materialId ? (
        <Typography.Text type="secondary" style={{ display: 'block' }}>
          来源：Materials Project {materialId}
          {formatMaterialId(materialId) !== materialId
            ? `；可信旧数字编号 ${formatMaterialId(materialId)}`
            : /^mp-[a-z]{1,8}$/.test(materialId)
              ? '；此 AlphaID 无可信旧数字编号，保留原值'
              : ''}
        </Typography.Text>
      ) : (
        <Typography.Text type="secondary" style={{ display: 'block' }}>
          无经验证的 MP 编号；旧文件标题中的编号不会自动转换。如需确认来源，请重新从 Materials Project 导入。
        </Typography.Text>
      )}
    </Card>
  );
};

/** 表单数据 → 不可变快照（Modal 展示与实际 payload 同源，避免显示与发送不一致）。 */
const buildSnapshot = (data: ParameterConfirmFormData, summary: StructureSummary,
                       sampleName: string = defaultSampleName(summary),
                       context?: { binding: CatalysisWorkflowBinding; policy: CatalysisSurfacePolicy; patches?: ParameterPatch[] }): WorkflowConfirmSnapshot => {
  const entries = data.dftu?.entries ?? [];
  const isLegacyDftu = data.dftu.enabled && !data.dftu.form &&
    entries.some((entry) => entry.u_ev != null || entry.j_ev != null);
  const dftu: DftuSettingsRequest = data.dftu.enabled
    ? {
        enabled: true,
        ...(data.dftu.form === 'legacy' || isLegacyDftu ? {} : {
          form: data.dftu.form === 'liechtenstein' ? 'liechtenstein' as const : 'dudarev' as const,
          input_mode: data.dftu.form === 'liechtenstein' ? 'u_j' as const : 'u_eff' as const,
        }),
        entries: entries.map((entry) => ({
          element: entry.element as string,
          l: entry.l as number,
          ...(data.dftu.form === 'legacy' || isLegacyDftu
            ? { u_ev: entry.u_ev, j_ev: entry.j_ev }
            : data.dftu.form === 'liechtenstein'
              ? { u_ev: entry.u_ev, j_ev: entry.j_ev }
              : { u_eff_ev: entry.u_eff_ev }),
          ...(entry.source_note !== undefined ? { source_note: entry.source_note } :
            data.dftu.form === 'legacy' || isLegacyDftu ? {} : { source_note: 'user_input' }),
          // 必须复制表单真实确认状态，禁止在此处生成/伪造用户确认。
          confirmed_by_user: entry.confirmed_by_user === true,
        })),
      }
    : { enabled: false, entries: [] };
  // 深拷贝冻结，后续表单变化不影响快照。
  return JSON.parse(JSON.stringify({
    ...(context ? { catalysis_binding: context.binding, surface_policy: context.policy, ...(context.patches?.length ? { patches: context.patches } : {}) } : {}),
    structure: {
      formula: summary.formula,
      elements: summary.elements,
    },
    sample_name: effectiveSampleName(summary, sampleName),
    poscar_comment: poscarComment(summary, sampleName),
    requested_tasks: data.tasks,
    electronic_type: data.electronic_type,
    magnetic: data.magnetic,
    soc: data.soc,
    precision: data.precision,
    dftu,
    scheduler: {
      type: data.scheduler.type,
      nodes: data.scheduler.nodes,
      tasks_per_node: data.scheduler.tasks_per_node,
      walltime: data.scheduler.walltime,
      vasp_binary_hint: data.scheduler.vasp_binary_hint,
    },
  })) as WorkflowConfirmSnapshot;
};

/** Fail-closed 守卫：DFT+U 启用时，任一条目未获用户真实确认即不允许构造确认快照。 */
export const canBuildConfirmSnapshot = (data: Pick<ParameterConfirmFormData, 'dftu'>): boolean =>
  !data.dftu.enabled || (() => {
    const { form, input_mode, entries = [] } = data.dftu;
    const explicit = (form === 'dudarev' && input_mode === 'u_eff') ||
      (form === 'liechtenstein' && input_mode === 'u_j');
    const legacy = (form === 'legacy' || form == null) && input_mode == null &&
      entries.every((entry) => entry.u_ev != null && entry.u_eff_ev == null);
    return entries.length > 0 && (explicit || legacy) &&
      entries.every((entry) => entry.confirmed_by_user === true);
  })();

/** 快照 → 后端嵌套契约请求体（confirm=true 已在最终确认 Modal 中由用户点击确认）。 */
const buildPlanBody = (structureId: string, snapshot: WorkflowConfirmSnapshot): WorkflowPlanRequestBody => ({
  structure_id: structureId,
  ...(snapshot.patches?.length ? { patches: snapshot.patches } : {}),
  workflow: {
    sample_name: snapshot.sample_name,
    requested_tasks: snapshot.requested_tasks,
    goal_text: snapshot.requested_tasks.join('、'),
    material_assumptions: {
      electronic_type: snapshot.electronic_type,
      magnetic: snapshot.magnetic,
      soc: snapshot.soc,
      precision: snapshot.precision,
    },
    precision: snapshot.precision,
    dftu: snapshot.dftu,
    scheduler: snapshot.scheduler,
    confirm: true,
  },
});

const invalidateGeneration = () => {
  setWorkflowDraftField('generationNeedsCheck', false);
  setWorkflowDraftField('generationRequest', null);
  setWorkflowDraftField('lastGenerationKey', null);
};

const WorkflowBuilderPage: React.FC = () => {
  const { currentStep, structureId, summary, sampleName, workflowPlan, workflowId, fileTree,
    potcarChoice, potcarResult, patches, patchRows, formValues, generationNeedsCheck, catalysisBinding, surfacePolicy, surfaceDosSmearing } = useWorkflowDraft();
  const draftEpoch = useRef(0);
  const mounted = useRef(true);
  const inputVersion = useRef(0);
  const [inputEpoch, setInputEpoch] = useState(0);
  const [uploadKey, setUploadKey] = useState(0);
  const invalidateStructureInputs = useCallback(() => setInputEpoch(++inputVersion.current), []);
  const generateLock = useRef(false);
  const [checkingGeneration, setCheckingGeneration] = useState(false);
  const [checkMessage, setCheckMessage] = useState('');
  const [showAiPanel, setShowAiPanel] = useState(false);
  const [showMpPanel, setShowMpPanel] = useState(false);
  const [confirmSnapshot, setConfirmSnapshot] = useState<WorkflowConfirmSnapshot | null>(null);
  // 同步互斥锁：调用 API 前同步加锁，快速双击/重复回调只能产生一次请求。
  const submitLock = useRef(false);

  const planMutation = useWorkflowPlan();
  const generateMutation = useWorkflowGenerate();
  const downloadMutation = useWorkflowDownload();
  const featureFlags = useFeatureFlags();
  const bandWorkflowEnabled = featureFlags.data?.ENABLE_BAND_WORKFLOW === true && !featureFlags.isError;
  const bandWorkflowStatus = featureFlags.isError
    ? 'error'
    : featureFlags.isLoading
      ? 'loading'
      : bandWorkflowEnabled
        ? 'enabled'
        : 'disabled';

  const retirePage = useCallback(() => {
    mounted.current = false;
    ++draftEpoch.current;
    const current = getWorkflowDraft();
    // An unfinished transport is not evidence that generation completed.
    if (generateLock.current) setGenerationNeedsCheck(true);
    setPotcarChoice({ mode: current.potcarChoice.mode });
    if (!current.fileTree) setPotcarResult(undefined);
  }, []);
  useEffect(() => {
    mounted.current = true;
    return retirePage;
  }, [retirePage]);

  const handleNewWorkflow = useCallback(() => {
    ++draftEpoch.current;
    resetWorkflowDraft();
    invalidateStructureInputs(); setUploadKey(key => key + 1);
    setConfirmSnapshot(null); setShowAiPanel(false); setShowMpPanel(false);
    setCheckMessage(''); planMutation.reset(); generateMutation.reset(); downloadMutation.reset();
  }, [planMutation, generateMutation, downloadMutation, invalidateStructureInputs]);

  const handleCheckGeneration = useCallback(async () => {
    if (!workflowId || checkingGeneration) return;
    const token = draftEpoch.current;
    setCheckingGeneration(true);
    try {
      const current = await workflowsApi.get(workflowId);
      if (!mounted.current || token !== draftEpoch.current) return;
      const expected = getWorkflowDraft().generationRequest;
      if (expected?.canRecoverArtifact && current.workflow_id === expected.workflowId &&
          current.revision === expected.revision && current.file_tree &&
          ['generated', 'ready_to_download'].includes(current.workflow_status)) {
        setPotcarResult(current.potcar); setFileTree(current.file_tree);
        setGenerationNeedsCheck(false); setCurrentStep('generate'); setCheckMessage('');
      } else {
        setCheckMessage('尚未核实本次计划的生成结果；已有旧产物不能证明本次生成完成。请稍后检查，或保留参数重新确认新计划。');
      }
    } catch {
      if (mounted.current && token === draftEpoch.current) setCheckMessage('无法确认服务端结果，请恢复连接后再检查；草稿仍保留。');
    } finally { if (mounted.current) setCheckingGeneration(false); }
  }, [workflowId, checkingGeneration]);

  const aiEpoch = draftEpoch.current;

  const handleStructureAnalyzed = useCallback((structId: string, structSummary: StructureSummary, source: 'upload' | 'mp') => {
    if (!mounted.current || inputEpoch !== inputVersion.current) return;
    invalidateStructureInputs();
    setShowMpPanel(false);
    if (source === 'mp') setUploadKey(key => key + 1);
    ++draftEpoch.current; setPotcarChoice({ mode: 'omit' }); setPotcarResult(undefined);
    setFormValues(undefined); setConfirmSnapshot(null); invalidateGeneration();
    setWorkflowDraftField('catalysisBinding', null); setWorkflowDraftField('surfacePolicy', null);
    setWorkflowDraftField('surfaceDosSmearing', { value: null, confirmed: false });
    setStructureId(structId);
    setSummary(structSummary);
    setSampleName(defaultSampleName(structSummary));
    setWorkflowPlan(null);
    setWorkflowId(null);
    setFileTree(null);
    setPatches([]); setPatchRows(undefined);
  }, [inputEpoch, invalidateStructureInputs]);

  const handleSampleNameChange = useCallback((value: string) => {
    ++draftEpoch.current; setPotcarChoice({ mode: 'omit' }); setPotcarResult(undefined);
    invalidateGeneration();
    setSampleName(value);
    setConfirmSnapshot(null);
    setWorkflowPlan(null);
    setWorkflowId(null);
    setFileTree(null);
    setPatches([]); setPatchRows(undefined);
  }, []);

  const handleAiAccepted = useCallback((result: AiPlanAssistantResult) => {
    if (!mounted.current) return;
    ++draftEpoch.current; setPotcarChoice({ mode: 'omit' }); setPotcarResult(undefined); setPatches([]); setPatchRows(undefined);
    invalidateGeneration();
    setWorkflowPlan(result as unknown as WorkflowPlan);
    setWorkflowId(result.workflow_id);
    setCurrentStep('plan');
    setShowAiPanel(false);
  }, []);

  const handleFormSubmit = useCallback((data: ParameterConfirmFormData) => {
    if (!structureId || !summary || !sampleNameValid(sampleName)) return;
    // Fail-closed：DFT+U 启用且任一条目未获用户真实确认时，
    // 不构造确认快照、不打开最终确认 Modal（表单校验之外的二道防线）。
    if (!canBuildConfirmSnapshot(data)) return;
    if (data.tasks.includes('band') && (
      catalysisBinding ||
      !bandWorkflowEnabled ||
      data.tasks.includes('relax') ||
      data.tasks.includes('static') === false ||
      data.soc
    )) return;
    const needsDosPatch = surfacePolicy?.kpoint_mode === 'explicit_gamma_2d' && data.tasks.includes('dos');
    if (needsDosPatch && (!surfaceDosSmearing.confirmed || ![-1, 0, 1, 2].includes(surfaceDosSmearing.value as number))) return;
    const dosPatches: ParameterPatch[] = needsDosPatch ? [{
      patch_id: `cat_dos_${Date.now().toString(36)}`, composition_id: '', expected_revision: 1, step_id: '03_dos',
      parameter: 'ISMEAR', operation: 'replace', value: surfaceDosSmearing.value, source: 'user_confirmed',
      reason: '用户确认倾斜 c 表面的 DOS 占据设置', confirmed_by_user: surfaceDosSmearing.confirmed,
      validation: { allowed: true, rule_ids: [], warnings: [] },
    }] : [];
    // 不直接调 API：先冻结快照并打开最终确认 Modal。
    setConfirmSnapshot(buildSnapshot(data, summary, sampleName,
      catalysisBinding && surfacePolicy ? { binding: catalysisBinding, policy: surfacePolicy, patches: dosPatches } : undefined));
  }, [structureId, summary, sampleName, bandWorkflowEnabled, catalysisBinding, surfacePolicy, surfaceDosSmearing]);

  const handleModalConfirm = useCallback(async () => {
    if (submitLock.current || !confirmSnapshot || !structureId) return;
    // 能力读取失败或服务端关闭后，旧 band 确认快照立即失效；保留表单选择供用户处理。
    if (confirmSnapshot.requested_tasks.includes('band') && !bandWorkflowEnabled) {
      setConfirmSnapshot(null);
      return;
    }
    submitLock.current = true;
    const token = draftEpoch.current;
    try {
      const plan = await planMutation.mutateAsync(buildPlanBody(structureId, confirmSnapshot));
      if (!mounted.current || token !== draftEpoch.current) return;
      // 成功：关闭并清空快照，进入计划步骤。
      setConfirmSnapshot(null);
      ++draftEpoch.current; setPotcarChoice({ mode: 'omit' }); setPotcarResult(plan.potcar); setPatches(confirmSnapshot.patches ?? []); setPatchRows(undefined);
      invalidateGeneration();
      setWorkflowPlan(plan);
      setWorkflowId(plan.workflow_id);
      setCurrentStep('plan');
    } catch {
      // 失败：保留不可变快照，允许用户安全重试（错误由 ErrorAlert 展示）。
    } finally {
      submitLock.current = false;
    }
  }, [structureId, confirmSnapshot, planMutation, bandWorkflowEnabled]);

  const handleModalCancel = useCallback(() => {
    if (submitLock.current) return;
    // 取消/关闭：清空快照且不调用 API。
    setConfirmSnapshot(null);
  }, []);

  const handlePotcarChoice = useCallback((value: WorkflowPotcarChoice) => {
    ++draftEpoch.current; setPotcarChoice(value); setPotcarResult(undefined); setFileTree(null);
    setCurrentStep(current => current === 'generate' || current === 'download' ? 'edit' : current);
  }, []);
  const handlePatchesChange = useCallback((value: ParameterPatch[]) => {
    ++draftEpoch.current; setPatches(value); setPotcarChoice(current => ({ mode: current.mode })); setPotcarResult(undefined); setFileTree(null);
  }, []);
  const preparePotcarPlan = useCallback(async () => {
    if (!workflowId) throw new Error('当前计划不存在，请重新创建工作流。');
    return workflowsApi.replan(workflowId, patches, { mode: 'include' });
  }, [workflowId, patches]);
  const handleGenerate = useCallback(async () => {
    if (!workflowId || !workflowPlan || generationNeedsCheck || generateLock.current || (potcarChoice.mode === 'include' && !potcarChoice.artifact_id)) return;
    generateLock.current = true;
    const token = draftEpoch.current;
    const attemptKey = `${workflowId}:${workflowPlan.revision}`;
    setWorkflowDraftField('generationRequest', {
      workflowId, revision: workflowPlan.revision,
      // Any retry at the same revision may expose the previous bundle before the
      // new request reaches the server, including include. GET cannot prove which attempt won.
      canRecoverArtifact: getWorkflowDraft().lastGenerationKey !== attemptKey,
    });
    setWorkflowDraftField('lastGenerationKey', attemptKey);
    if (potcarChoice.mode === 'include') setPotcarResult({ ...potcarChoice, status: 'generating', steps: workflowPlan?.steps.map(step => ({ step_id: step.step_id, status: 'generating' })) ?? [] });
    try {
      // Explicit omit also overrides a replan that finished server-side after navigation.
      const result = await generateMutation.mutateAsync({ workflowId, patches, potcar: potcarChoice });
      if (!mounted.current || token !== draftEpoch.current) return;
      setPotcarResult(result.potcar);
      setFileTree(result.file_tree);
      setCurrentStep('generate');
    } catch {
      if (!mounted.current || token !== draftEpoch.current) return;
      setFileTree(null);
      if (potcarChoice.mode === 'include') {
        // Retrieve the canonical failure and per-step state from the same server record.
        try {
          const failed = await workflowsApi.get(workflowId);
          if (mounted.current && token === draftEpoch.current) setPotcarResult(failed.potcar ?? { ...potcarChoice, status: 'failed', steps: [] });
        } catch { if (mounted.current && token === draftEpoch.current) setPotcarResult({ ...potcarChoice, status: 'failed', steps: [] }); }
      }
    } finally { generateLock.current = false; }
  }, [workflowId, patches, potcarChoice, workflowPlan, generateMutation, generationNeedsCheck]);

  const handleDownload = useCallback(async () => {
    if (!workflowId) return;
    const token = draftEpoch.current;
    try {
      const blob = await downloadMutation.mutateAsync(workflowId);
      if (!mounted.current || token !== draftEpoch.current) return;
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `vasp_workflow_${workflowId}.zip`;
      a.click();
      URL.revokeObjectURL(url);
      setCurrentStep('download');
    } catch {
      // handled by error display
    }
  }, [workflowId, downloadMutation]);

  const steps = [
    { title: '上传结构', key: 'upload' as StepKey },
    { title: '确认参数', key: 'confirm' as StepKey },
    { title: '工作流计划', key: 'plan' as StepKey },
    { title: '参数编辑', key: 'edit' as StepKey },
    { title: '生成文件', key: 'generate' as StepKey },
    { title: '下载', key: 'download' as StepKey },
  ];

  const currentValues = useMemo(() => {
    const merged: Record<string, string | number | boolean> = {};
    if (workflowPlan) {
      for (const step of workflowPlan.steps) {
        for (const [k, v] of Object.entries(step.parameters)) {
          if (typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean') {
            merged[k] = v;
          } else {
            merged[k] = JSON.stringify(v);
          }
        }
      }
    }
    return merged;
  }, [workflowPlan]);

  const currentStepIndex = steps.findIndex((s) => s.key === currentStep);

  return (
    <div className="wf-page">
      <div className="wf-page-heading">
        <div><Title level={3}>生成工作流</Title><p>上传结构，确认计算设置，检查计划与输入文件。</p></div>
        <Space>{structureId && <Button onClick={handleNewWorkflow} disabled={planMutation.isPending || generateMutation.isPending}>新建工作流</Button>}<span className="wf-phase"><strong>{String(currentStepIndex + 1).padStart(2, '0')} / 06</strong>{steps[currentStepIndex].title}</span></Space>
      </div>
      <div className="wf-stage-rail">
      <Steps
        current={currentStepIndex}
        items={steps.map((s) => ({ title: s.title }))}
      />
      </div>
      <div className="wf-body-grid">
      <section className="wf-stage-content" aria-label={steps[currentStepIndex].title}>
      {catalysisBinding && surfacePolicy && <CatalysisWorkflowContext binding={catalysisBinding} policy={surfacePolicy} />}

      {/* Step 1: 上传 */}
      {currentStep === 'upload' && (
        <>
          <StructureUploadPanel key={uploadKey} onStructureAnalyzed={(id, value) => handleStructureAnalyzed(id, value, 'upload')} />
          <Card style={{ marginTop: 16 }}>
            <Space wrap>
              <Button
                size="large"
                type={showMpPanel ? 'default' : 'dashed'}
                icon={<GlobalOutlined />}
                onClick={() => { invalidateStructureInputs(); setUploadKey(key => key + 1); setShowMpPanel((v) => !v); setShowAiPanel(false); }}
              >
                {showMpPanel ? '收起 Materials Project 导入' : '从 Materials Project 导入（可选）'}
              </Button>
            </Space>
          </Card>
          {showMpPanel && (
            <div style={{ marginTop: 16 }}>
              <MaterialsProjectPanel key={uploadKey} onStructureImported={(id, value) => handleStructureAnalyzed(id, value, 'mp')} />
            </div>
          )}
          {summary && (
            <Card style={{ marginTop: 16 }}>
              <Typography.Paragraph>当前草稿结构：{summary.formula} · {summary.atom_count} 原子</Typography.Paragraph>
              <SampleNameEditor summary={summary} value={sampleName} onChange={handleSampleNameChange} />
              <Space wrap>
                <Button
                  type="primary"
                  size="large"
                  icon={<ArrowRightOutlined />}
                  onClick={() => { invalidateStructureInputs(); setCurrentStep('confirm'); }}
                  disabled={!sampleNameValid(sampleName)}
                >
                  下一步：确认参数
                </Button>
                <Button
                  size="large"
                  type={showAiPanel ? 'default' : 'dashed'}
                  icon={<RobotOutlined />}
                  onClick={() => setShowAiPanel((v) => !v)}
                >
                  {showAiPanel ? '收起 AI 规划' : 'AI 规划（可选）'}
                </Button>
              </Space>
            </Card>
          )}

          {summary && showAiPanel && sampleNameValid(sampleName) && (
            <div className="wf-ai-assistant" style={{ marginTop: 16 }}>
              <AiPlanAssistant
                key={`${summary.structure_id}:${effectiveSampleName(summary, sampleName)}`}
                structureId={summary.structure_id}
                sampleName={effectiveSampleName(summary, sampleName)}
                formula={summary.formula}
                elements={summary.elements}
                onAccepted={result => { if (aiEpoch === draftEpoch.current) handleAiAccepted(result); }}
              />
            </div>
          )}
        </>
      )}

      {/* Step 2: 确认参数 */}
      {currentStep === 'confirm' && summary && (
        <>
        <SampleNameEditor summary={summary} value={sampleName} onChange={handleSampleNameChange} />
        {surfacePolicy?.kpoint_mode === 'explicit_gamma_2d' && formValues?.tasks?.includes('dos') && <Card size="small" title="倾斜 c 表面 · DOS 占据设置" style={{ marginBottom: 16 }}>
          <Typography.Paragraph>显式二维网格不能使用默认四面体法。请自行选择 DOS 的 ISMEAR；系统不推荐或自动选择科研取值，SIGMA 等相关设置仍须在参数编辑中审阅。</Typography.Paragraph>
          <Select aria-label="表面 DOS ISMEAR" style={{ width: 280 }} placeholder="选择 DOS 占据方法" value={surfaceDosSmearing.value}
            options={[{ value: 0, label: '0 · Gaussian' }, { value: -1, label: '-1 · Fermi–Dirac' }, { value: 1, label: '1 · Methfessel–Paxton 1' }, { value: 2, label: '2 · Methfessel–Paxton 2' }]}
            onChange={value => { setWorkflowDraftField('surfaceDosSmearing', { value, confirmed: false }); setConfirmSnapshot(null); }} />
          <Checkbox style={{ display: 'block', marginTop: 12 }} disabled={surfaceDosSmearing.value === null} checked={surfaceDosSmearing.confirmed}
            onChange={event => setWorkflowDraftField('surfaceDosSmearing', { ...surfaceDosSmearing, confirmed: event.target.checked })}>已确认此 DOS 占据设置及其适用性</Checkbox>
          {!surfaceDosSmearing.confirmed && <Typography.Paragraph type="secondary" style={{ marginTop: 8 }}>选择并确认后才能打开最终参数摘要。</Typography.Paragraph>}
        </Card>}
        <ParameterConfirmForm
          initialValues={formValues}
          onDraftChange={setFormValues}
          elements={summary.elements}
          transitionMetals={summary.transition_metals}
          surfaceContext={!!catalysisBinding}
          bandWorkflowStatus={bandWorkflowStatus}
          onRetryBandCapability={() => { void featureFlags.refetch(); }}
          onSubmit={handleFormSubmit}
          isGenerating={planMutation.isPending}
          onBack={() => setCurrentStep('upload')}
        />
        </>
      )}

      {/* 最终确认摘要：展示内容与发送 payload 同源于 confirmSnapshot */}
      <WorkflowConfirmSummaryModal
        open={confirmSnapshot !== null}
        snapshot={confirmSnapshot}
        isPending={planMutation.isPending}
        onConfirm={handleModalConfirm}
        onCancel={handleModalCancel}
      />

      {/* Step 3: 工作流计划 */}
      {currentStep === 'plan' && workflowPlan && (
        <>
          <WorkflowPlanPreview
            steps={workflowPlan.steps}
            dependencies={workflowPlan.file_inheritance_plan.dependencies}
          />
          <RecipeCompositionPreview
            compositions={workflowPlan.recipe_compositions}
            workflowSteps={workflowPlan.steps}
          />
        </>
      )}

      {/* Step 4: 参数编辑 */}
      {currentStep === 'edit' && workflowPlan && (
        <ParameterPatchEditor
          patches={patches}
          draftRows={patchRows}
          onDraftRowsChange={setPatchRows}
          currentValues={currentValues}
          allowedParams={ALLOWED_PARAMS}
          onPatchesChange={handlePatchesChange}
        />
      )}

      {workflowPlan && ['plan', 'edit'].includes(currentStep) && <WorkflowPotcarPanel
        plan={workflowPlan} draftKey={JSON.stringify(patches)} choice={potcarChoice} onChoice={handlePotcarChoice}
        preparePlan={preparePotcarPlan} onPlanPrepared={setWorkflowPlan} generation={potcarResult}
        disabled={generateMutation.isPending || currentStep === 'download'}
      />}

      {fileTree && (currentStep === 'generate' || currentStep === 'download') && (
        <Alert type="info" showIcon style={{ marginTop: 16 }} message={potcarResult?.mode === 'include' ? 'POTCAR 已加入工作流文件' : '已生成的工作流不包含 POTCAR，运行前需自行补齐。'}
        description={potcarResult?.mode === 'include' ? '修改参数或重新生成时须重新预览确认。文件准备不会启动计算。' : '文件准备不会启动计算。'} />
      )}
      {generationNeedsCheck && <Alert type="warning" showIcon style={{ marginTop: 16 }}
        message="离开页面时生成结果尚未确认，未自动重新生成。"
        description={<Space direction="vertical">
          <span>{checkMessage || '请先检查服务端结果；未完成时可稍后再次检查。'}</span>
          <Button loading={checkingGeneration} onClick={() => void handleCheckGeneration()}>检查服务端生成结果</Button>
          <span>重新规划会清除手工参数补丁和赝势确认，需重新核对。</span>
          <Button disabled={checkingGeneration} onClick={() => {
            ++draftEpoch.current; invalidateGeneration(); setWorkflowPlan(null); setWorkflowId(null);
            setFileTree(null); setPotcarChoice({ mode: 'omit' }); setPotcarResult(undefined);
            setPatches([]); setPatchRows(undefined); setCurrentStep('confirm');
          }}>{patches.length || patchRows?.some(row => row.selected) ? '保留表单，清除手工补丁并重新规划' : '保留表单，重新规划'}</Button>
        </Space>} />}

      {/* Step 5: 生成文件 */}
      {currentStep === 'generate' && fileTree && (
        <>
          <GeneratedFilesPreview fileTree={fileTree} />
          <Card style={{ marginTop: 16 }}>
            <Space>
              <Button
                type="primary"
                size="large"
                icon={<DownloadOutlined />}
                onClick={handleDownload}
                loading={downloadMutation.isPending}
              >
                下载工作流 (ZIP)
              </Button>
              <Button
                icon={<ReloadOutlined />}
                onClick={() => {
                  ++draftEpoch.current; setPotcarChoice(current => ({ mode: current.mode })); setPotcarResult(undefined);
                  setCurrentStep('edit');
                  setFileTree(null);
                }}
              >
                修改参数后重新生成
              </Button>
            </Space>
          </Card>
        </>
      )}

      {/* Step 6: 下载完成 */}
      {currentStep === 'download' && (
        <Result
          status="success"
          title="工作流已准备就绪"
          subTitle={`工作流 ID: ${workflowId}`}
          extra={[
            <Button
              key="download"
              type="primary"
              icon={<DownloadOutlined />}
              onClick={handleDownload}
            >
              再次下载
            </Button>,
            <Button
              key="new"
              onClick={handleNewWorkflow}
            >
              开始新的工作流
            </Button>,
          ]}
        />
      )}

      {/* 错误展示 */}
      {(planMutation.error || generateMutation.error) && (
        <div style={{ marginTop: 16 }}>
          <ErrorAlert
            error={planMutation.error || generateMutation.error!}
            onRetry={() => {
              planMutation.reset();
              generateMutation.reset();
            }}
          />
        </div>
      )}

      {/* 导航按钮 */}
      {currentStep !== 'download' && currentStep !== 'upload' && currentStep !== 'confirm' && (
        <div style={{ marginTop: 24, display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', rowGap: 8 }}>
          <Button
            icon={<ArrowLeftOutlined />}
            disabled={generateMutation.isPending}
            onClick={() => {
              ++draftEpoch.current; setPotcarChoice(current => ({ mode: current.mode })); setPotcarResult(undefined); setFileTree(null);
              const idx = steps.findIndex((s) => s.key === currentStep);
              if (idx > 0) setCurrentStep(steps[idx - 1].key);
            }}
          >
            上一步
          </Button>
          <Space wrap>
            {currentStep === 'plan' && (
              <Button
                onClick={() => setCurrentStep('edit')}
                disabled={generateMutation.isPending}
              >
                编辑参数 (可选)
              </Button>
            )}
            {currentStep === 'plan' && (
              <Button
                type="primary"
                icon={<ArrowRightOutlined />}
                onClick={handleGenerate}
                disabled={generationNeedsCheck || (potcarChoice.mode === 'include' && !potcarChoice.artifact_id)}
                loading={generateMutation.isPending}
              >
                下一步：生成文件
              </Button>
            )}
            {currentStep === 'edit' && (
              <Button
                type="primary"
                icon={<ArrowRightOutlined />}
                onClick={handleGenerate}
                disabled={generationNeedsCheck || (potcarChoice.mode === 'include' && !potcarChoice.artifact_id)}
                loading={generateMutation.isPending}
              >
                下一步：生成文件
              </Button>
            )}
          </Space>
        </div>
      )}
      </section>
      <aside className="wf-inspector" aria-label="当前结构与工作流摘要">
        <h4>当前工作区</h4>
        {summary && structureId && <CrystalViewer key={structureId} structureId={structureId} />}
        {summary ? <dl>
          <div><dt>结构</dt><dd>{summary.formula}</dd></div>
          <div><dt>原子 / 元素</dt><dd>{summary.atom_count} 原子 · {summary.elements.join('、')}</dd></div>
          <div><dt>样品名称</dt><dd>{effectiveSampleName(summary, sampleName)}</dd></div>
          <div><dt>晶格长度 / Å</dt><dd>{[summary.lattice?.a, summary.lattice?.b, summary.lattice?.c].map(v => typeof v === 'number' && Number.isFinite(v) ? v.toFixed(3) : '—').join(' / ')}</dd></div>
          <div><dt>晶格角 / °</dt><dd>{[summary.lattice?.alpha, summary.lattice?.beta, summary.lattice?.gamma].map(v => typeof v === 'number' && Number.isFinite(v) ? v.toFixed(2) : '—').join(' / ')}</dd></div>
          {workflowPlan && <div><dt>计划步骤</dt><dd>{workflowPlan.steps.map(step => step.label).join(' → ')}</dd></div>}
        </dl> : <p className="wf-empty">尚未载入结构。上传 POSCAR / CIF，或从 Materials Project 导入。</p>}
        <div className="wf-inspector-note">{currentStep === 'confirm' ? '参数设置后仍需检查最终摘要并确认，才会请求工作流计划。' : currentStep === 'plan' ? '检查步骤、Recipe 覆盖来源和文件继承，再生成输入文件。' : '结构摘要来自实际解析结果。生成输入文件不会启动科学计算。'}</div>
      </aside>
      </div>
    </div>
  );
};

export default WorkflowBuilderPage;
export { buildSnapshot };
