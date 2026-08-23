// ============================================================
// WorkflowBuilderPage — 串联上传→解析→计划→生成→下载完整流程
// ============================================================

import React, { useState, useCallback, useMemo, useRef } from 'react';
import { Steps, Button, Space, Card, Result, Typography } from 'antd';
import { ReloadOutlined, DownloadOutlined, ArrowRightOutlined, ArrowLeftOutlined, RobotOutlined, GlobalOutlined } from '@ant-design/icons';
import StructureUploadPanel from '../components/upload/StructureUploadPanel';
import MaterialsProjectPanel from '../components/upload/MaterialsProjectPanel';
import ParameterConfirmForm, { type ParameterConfirmFormData } from '../components/workflow/ParameterConfirmForm';
import WorkflowConfirmSummaryModal from '../components/workflow/WorkflowConfirmSummaryModal';
import WorkflowPlanPreview from '../components/workflow/WorkflowPlanPreview';
import RecipeCompositionPreview from '../components/recipes/RecipeCompositionPreview';
import ParameterPatchEditor from '../components/recipes/ParameterPatchEditor';
import GeneratedFilesPreview from '../components/workflow/GeneratedFilesPreview';
import ErrorAlert from '../components/common/ErrorAlert';
import AiPlanAssistant, { type AiPlanAssistantResult } from '../components/workflow/AiPlanAssistant';
import { useWorkflowPlan, useWorkflowGenerate, useWorkflowDownload } from '../hooks/useApi';
import type { StructureSummary, WorkflowPlan, FileTreeNode, ParameterPatch } from '../types/generated-api';
import type { WorkflowStatus } from '../types/enums';
import type {
  DftuSettingsRequest,
  WorkflowConfirmSnapshot,
  WorkflowPlanRequestBody,
} from '../types/workflow-contract';

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

type StepKey = 'upload' | 'confirm' | 'plan' | 'edit' | 'generate' | 'download';

/** 表单数据 → 不可变快照（Modal 展示与实际 payload 同源，避免显示与发送不一致）。 */
const buildSnapshot = (data: ParameterConfirmFormData, summary: StructureSummary): WorkflowConfirmSnapshot => {
  const dftu: DftuSettingsRequest = data.dftu.enabled
    ? {
        enabled: true,
        entries: data.dftu.entries.map((entry) => ({
          element: entry.element as string,
          l: entry.l as number,
          u_ev: entry.u_ev as number,
          j_ev: entry.j_ev as number,
          source_note: 'user_input',
          // 必须复制表单真实确认状态，禁止在此处生成/伪造用户确认。
          confirmed_by_user: entry.confirmed_by_user === true,
        })),
      }
    : { enabled: false, entries: [] };
  // 深拷贝冻结，后续表单变化不影响快照。
  return JSON.parse(JSON.stringify({
    structure: {
      formula: summary.formula,
      elements: summary.elements,
    },
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
  !data.dftu.enabled || data.dftu.entries.every((entry) => entry.confirmed_by_user === true);

/** 快照 → 后端嵌套契约请求体（confirm=true 已在最终确认 Modal 中由用户点击确认）。 */
const buildPlanBody = (structureId: string, snapshot: WorkflowConfirmSnapshot): WorkflowPlanRequestBody => ({
  structure_id: structureId,
  workflow: {
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

const WorkflowBuilderPage: React.FC = () => {
  const [currentStep, setCurrentStep] = useState<StepKey>('upload');
  const [structureId, setStructureId] = useState<string | null>(null);
  const [summary, setSummary] = useState<StructureSummary | null>(null);
  const [workflowPlan, setWorkflowPlan] = useState<WorkflowPlan | null>(null);
  const [workflowId, setWorkflowId] = useState<string | null>(null);
  const [fileTree, setFileTree] = useState<FileTreeNode | null>(null);
  const [patches, setPatches] = useState<ParameterPatch[]>([]);
  const [, setWorkflowStatus] = useState<WorkflowStatus>('draft');
  const [showAiPanel, setShowAiPanel] = useState(false);
  const [showMpPanel, setShowMpPanel] = useState(false);
  const [confirmSnapshot, setConfirmSnapshot] = useState<WorkflowConfirmSnapshot | null>(null);
  // 同步互斥锁：调用 API 前同步加锁，快速双击/重复回调只能产生一次请求。
  const submitLock = useRef(false);

  const planMutation = useWorkflowPlan();
  const generateMutation = useWorkflowGenerate();
  const downloadMutation = useWorkflowDownload();

  const handleStructureAnalyzed = useCallback((structId: string, structSummary: StructureSummary) => {
    setStructureId(structId);
    setSummary(structSummary);
  }, []);

  const handleAiAccepted = useCallback((result: AiPlanAssistantResult) => {
    setWorkflowPlan(result as unknown as WorkflowPlan);
    setWorkflowId(result.workflow_id);
    setWorkflowStatus('planned');
    setCurrentStep('plan');
    setShowAiPanel(false);
  }, []);

  const handleFormSubmit = useCallback((data: ParameterConfirmFormData) => {
    if (!structureId || !summary) return;
    // Fail-closed：DFT+U 启用且任一条目未获用户真实确认时，
    // 不构造确认快照、不打开最终确认 Modal（表单校验之外的二道防线）。
    if (!canBuildConfirmSnapshot(data)) return;
    // 不直接调 API：先冻结快照并打开最终确认 Modal。
    setConfirmSnapshot(buildSnapshot(data, summary));
  }, [structureId, summary]);

  const handleModalConfirm = useCallback(async () => {
    if (submitLock.current || !confirmSnapshot || !structureId) return;
    submitLock.current = true;
    try {
      const plan = await planMutation.mutateAsync(buildPlanBody(structureId, confirmSnapshot));
      // 成功：关闭并清空快照，进入计划步骤。
      setConfirmSnapshot(null);
      setWorkflowPlan(plan);
      setWorkflowId(plan.workflow_id);
      setWorkflowStatus(plan.workflow_id ? 'planned' : 'draft');
      setCurrentStep('plan');
    } catch {
      // 失败：保留不可变快照，允许用户安全重试（错误由 ErrorAlert 展示）。
    } finally {
      submitLock.current = false;
    }
  }, [structureId, confirmSnapshot, planMutation]);

  const handleModalCancel = useCallback(() => {
    if (submitLock.current) return;
    // 取消/关闭：清空快照且不调用 API。
    setConfirmSnapshot(null);
  }, []);

  const handleGenerate = useCallback(async () => {
    if (!workflowId) return;
    try {
      const result = await generateMutation.mutateAsync({ workflowId, patches });
      setWorkflowStatus('generated');
      setFileTree(result.file_tree);
      setCurrentStep('generate');
    } catch {
      // handled by error display
    }
  }, [workflowId, patches, generateMutation]);

  const handleDownload = useCallback(async () => {
    if (!workflowId) return;
    try {
      const blob = await downloadMutation.mutateAsync(workflowId);
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
    <div style={{ maxWidth: 1200, margin: '0 auto', padding: '24px 16px' }}>
      <Title level={3}>生成工作流</Title>

      <Steps
        current={currentStepIndex}
        items={steps.map((s) => ({ title: s.title }))}
        style={{ marginBottom: 32 }}
      />

      {/* Step 1: 上传 */}
      {currentStep === 'upload' && (
        <>
          <StructureUploadPanel onStructureAnalyzed={handleStructureAnalyzed} />
          <Card style={{ marginTop: 16 }}>
            <Space wrap>
              <Button
                size="large"
                type={showMpPanel ? 'default' : 'dashed'}
                icon={<GlobalOutlined />}
                onClick={() => { setShowMpPanel((v) => !v); setShowAiPanel(false); }}
              >
                {showMpPanel ? '收起 Materials Project 导入' : '从 Materials Project 导入（可选）'}
              </Button>
            </Space>
          </Card>
          {showMpPanel && (
            <div style={{ marginTop: 16 }}>
              <MaterialsProjectPanel onStructureImported={handleStructureAnalyzed} />
            </div>
          )}
          {summary && (
            <Card style={{ marginTop: 16 }}>
              <Space wrap>
                <Button
                  type="primary"
                  size="large"
                  icon={<ArrowRightOutlined />}
                  onClick={() => setCurrentStep('confirm')}
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

          {summary && showAiPanel && (
            <div style={{ marginTop: 16 }}>
              <AiPlanAssistant
                structureId={summary.structure_id}
                formula={summary.formula}
                elements={summary.elements}
                onAccepted={handleAiAccepted}
              />
            </div>
          )}
        </>
      )}

      {/* Step 2: 确认参数 */}
      {currentStep === 'confirm' && summary && (
        <ParameterConfirmForm
          elements={summary.elements}
          transitionMetals={summary.transition_metals}
          onSubmit={handleFormSubmit}
          isGenerating={planMutation.isPending}
          onBack={() => setCurrentStep('upload')}
        />
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
          currentValues={currentValues}
          allowedParams={ALLOWED_PARAMS}
          onPatchesChange={setPatches}
        />
      )}

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
              onClick={() => {
                setCurrentStep('upload');
                setStructureId(null);
                setSummary(null);
                setWorkflowPlan(null);
                setWorkflowId(null);
                setFileTree(null);
              }}
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
        <div style={{ marginTop: 24, display: 'flex', justifyContent: 'space-between' }}>
          <Button
            icon={<ArrowLeftOutlined />}
            onClick={() => {
              const idx = steps.findIndex((s) => s.key === currentStep);
              if (idx > 0) setCurrentStep(steps[idx - 1].key);
            }}
          >
            上一步
          </Button>
          <Space>
            {currentStep === 'plan' && (
              <Button
                onClick={() => setCurrentStep('edit')}
              >
                编辑参数 (可选)
              </Button>
            )}
            {currentStep === 'plan' && (
              <Button
                type="primary"
                icon={<ArrowRightOutlined />}
                onClick={handleGenerate}
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
                loading={generateMutation.isPending}
              >
                下一步：生成文件
              </Button>
            )}
          </Space>
        </div>
      )}

    </div>
  );
};

export default WorkflowBuilderPage;
export { buildSnapshot };
