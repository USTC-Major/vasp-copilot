// ============================================================
// AiProjectPage — 项目聊天主界面（任务栏 + 对话 + 额外设置）
// 布局：左侧任务栏贴左，单分隔线；右侧聊天栏占满其余全部，无空白。
// 聊天：发送后立即显示用户消息，LLM 思考与正文流式实时展示。
// M032：新建任务的工作区支持「浏览」按钮图形化点选目录。
// ============================================================

import React, { useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { Alert, Layout, Button, Input, Space, Typography, Tag, Modal, message } from 'antd';
import { PlusOutlined, SendOutlined, SettingOutlined, RobotOutlined, FolderOutlined, FolderOpenOutlined, CloudServerOutlined, LoadingOutlined, StopOutlined, DeleteOutlined } from '@ant-design/icons';
import AiChatBubble from '../components/ai/AiChatBubble';
import AiTaskSidebar from '../components/ai/AiTaskSidebar';
import AiProjectExtraSettings from '../components/ai/AiProjectExtraSettings';
import AiContextBar from '../components/ai/AiContextBar';
import AiDirectoryPicker from '../components/ai/AiDirectoryPicker';
import ToolboxTaskStatus, { ToolboxEnvironmentTags } from '../components/toolbox/ToolboxTaskStatus';
import { aiApi, toolboxApi } from '../api/client';
import { AI_JOB_STATUS_MAP } from '../types/ai';
import { useAiTasks, useAiTaskCreate, useAiMessages, useAiTaskContext, useAiTaskUpdate, useAiTaskDelete, useToolboxTaskDetail, useAiSettings } from '../hooks/useApi';
import type { AiMessage as AiMsg, AiTask, AiConsentCard } from '../types/ai';

const { Content } = Layout;
const { Text, Title } = Typography;

// 可批量处理的卡片：只限“机械文件准备”。科学输入（INCAR/KPOINTS/结构导入）、
// 脚本认领、提交与重试一律逐项确认，不做批量。
// 文件准备阶段的所有机械动作（复制输入 / 上传 / 生成 POTCAR / 部署脚本）在用户眼里
// 就是"同一件事"：并成**一组**，一次点击全部批准；科学参数、结构导入、提交仍逐项确认。
const BATCHABLE_KINDS = new Set(['copy_inputs', 'hpc_upload', 'potcar_generate', 'script_deploy']);
const PREPARE_GROUP_KEY = 'prepare';

const CARD_LABELS: Record<string, string> = {
  workspace: '操作授权',        // 旧版/演示后端使用的泛化类型
  submit: '提交确认',
  copy_inputs: '输入复制',
  hpc_upload: '文件上传',
  incar_write: 'INCAR 参数写入',
  kpoints_write: 'KPOINTS 生成',
  mp_poscar_write: '结构导入',
  script_attestation: '提交脚本认领',
  retry_job: '失败重试',
  remote_file: '远端文件计划',
};

const cardLabel = (kind: string) => CARD_LABELS[kind] ?? kind;

/** 折叠时只显示第一行指纹（文件名/目标/SHA 前几位都在里面）。 */
const cardFingerprint = (summary: string) => {
  const first = (summary || '').split('\n').map((line) => line.trim()).find(Boolean) ?? '（无摘要）';
  return first.length > 96 ? `${first.slice(0, 96)}…` : first;
};

/** 折叠时也显示一句理由，避免用户看不到“为什么需要确认”。 */
const reasonBrief = (reason: string) => {
  const text = (reason || '').trim();
  return text.length > 90 ? `${text.slice(0, 90)}…` : text;
};

interface LiveMsg {
  role: 'user' | 'assistant';
  content: string;
  thinking: string;
  stopped?: boolean;
}

interface StreamIssue {
  kind: 'generation' | 'connection';
  message: string;
}

/** 单张待批卡：默认只显示指纹，完整预览按需展开并限高滚动。 */
const PendingCardRow: React.FC<{
  card: AiConsentCard;
  resolving: boolean;
  onResolve: (card: AiConsentCard, approved: boolean) => void;
  toolboxLink?: string;
}> = ({ card, resolving, onResolve, toolboxLink }) => {
  const [open, setOpen] = useState(false);
  return (
    <div style={{ borderTop: '1px dashed rgba(0,0,0,0.10)', paddingTop: 8, marginTop: 8 }}>
      <Space style={{ width: '100%', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div style={{ paddingRight: 8 }}>
          <div style={{ fontSize: 13 }}>{cardFingerprint(card.summary)}</div>
          {reasonBrief(card.reason) && (
            <div style={{ fontSize: 12, color: '#8c6d1f', marginTop: 2 }}>{reasonBrief(card.reason)}</div>
          )}
        </div>
        <Button size="small" type="link" onClick={() => setOpen((v) => !v)}>
          {open ? '收起' : '展开完整预览'}
        </Button>
      </Space>
      {open && (
        <div style={{ maxHeight: 240, overflowY: 'auto', background: '#fafafa', borderRadius: 6, padding: '8px 10px', marginTop: 6 }}>
          <div style={{ whiteSpace: 'pre-wrap', fontSize: 12 }}>{card.summary}</div>
          <div style={{ fontSize: 12, color: '#8c6d1f', marginTop: 8, whiteSpace: 'pre-wrap' }}>{card.reason}</div>
        </div>
      )}
      {card.kind === 'remote_file' ? (
        <Link to={toolboxLink ?? '#'} style={{ fontSize: 13 }}>审阅完整文件计划与授权范围</Link>
      ) : (
        <Space style={{ marginTop: 8 }}>
          {(card.options && card.options.length
            ? card.options.filter((opt) => opt !== '同意本批' && opt !== 'allow_batch')
            : ['同意本次', '拒绝']).map((opt) => (
            <Button
              key={opt}
              size="small"
              type={opt === '拒绝' ? 'default' : 'primary'}
              danger={opt === '拒绝'}
              loading={resolving}
              onClick={() => onResolve(card, opt !== '拒绝')}
            >
              {opt}
            </Button>
          ))}
        </Space>
      )}
    </div>
  );
};

const AiProjectPage: React.FC = () => {
  const { projectId = '' } = useParams();
  const tasksQuery = useAiTasks(projectId);
  const createTaskMutation = useAiTaskCreate();
  const updateTaskMutation = useAiTaskUpdate();
  const deleteTaskMutation = useAiTaskDelete();

  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [newTaskOpen, setNewTaskOpen] = useState(false);
  const [newTitle, setNewTitle] = useState('');
  const [newLocalWorkspace, setNewLocalWorkspace] = useState('');
  const [newHpcWorkspace, setNewHpcWorkspace] = useState('');
  const [pickerKind, setPickerKind] = useState<'local' | 'hpc' | null>(null);

  const [pickingLocal, setPickingLocal] = useState(false);

  const handlePickLocalWorkspace = async () => {
    setPickingLocal(true);
    try {
      const r = await toolboxApi.pickLocal(newLocalWorkspace);
      if (r.ok && r.path) {
        setNewLocalWorkspace(r.path);
      } else if (r.notice) {
        message.info(r.notice);
      }
    } catch {
      message.warning('无法打开系统目录选择窗口');
    } finally {
      setPickingLocal(false);
    }
  };
  const [input, setInput] = useState('');
  const [extraOpen, setExtraOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [editingTask, setEditingTask] = useState<AiTask | null>(null);
  const [editTitle, setEditTitle] = useState('');
  const [liveMsgs, setLiveMsgs] = useState<LiveMsg[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [streamIssue, setStreamIssue] = useState<StreamIssue | null>(null);
  const [pendingCards, setPendingCards] = useState<AiConsentCard[]>([]);
  const [resolvingCardId, setResolvingCardId] = useState<string | null>(null);
  const [batchBusy, setBatchBusy] = useState(false);
  const threadRef = useRef<HTMLDivElement>(null);
  const selectedTaskIdRef = useRef<string | null>(selectedTaskId);
  const streamSequenceRef = useRef(0);
  const activeStreamRef = useRef<{
    requestId: number;
    taskId: string;
    controller: AbortController;
  } | null>(null);

  const tasks = tasksQuery.data?.tasks ?? [];
  const selectedTask = tasks.find((t) => t.id === selectedTaskId) ?? null;
  // 单条消息进入模型上下文的上限由后端下发，避免前后端各写一份常量。
  const settingsQuery = useAiSettings(true);
  const messageLimit = settingsQuery.data?.settings.message_char_limit ?? 2000;
  const draftLength = input.trim().length;
  const draftOverLimit = draftLength > messageLimit;
  const executionQuery = useToolboxTaskDetail(projectId, selectedTaskId, { observeOnly: true });
  const messagesQuery = useAiMessages(projectId, selectedTaskId);
  const taskContextQuery = useAiTaskContext(projectId, selectedTaskId);
  const messages: AiMsg[] = messagesQuery.data?.messages ?? [];
  const allMsgs = [...messages, ...liveMsgs];
  const generationRunning = messagesQuery.data?.generation?.running ?? false;
  const conversationBusy = streaming || generationRunning;

  const selectTask = (taskId: string | null) => {
    const active = activeStreamRef.current;
    if (active && active.taskId !== taskId) {
      active.controller.abort();
      activeStreamRef.current = null;
    }
    // 先同步 ref，确保已经在途的异步回调不会污染刚切换到的任务。
    selectedTaskIdRef.current = taskId;
    setSelectedTaskId(taskId);
  };

  useEffect(() => {
    const t = tasksQuery.data?.tasks;
    if (t && t.length > 0 && !selectedTaskId) {
      selectTask(t[0].id);
    }
    // selectTask 只依赖 ref/setter；无需让函数身份触发任务重选。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tasksQuery.data, selectedTaskId]);

  useEffect(() => {
    const active = activeStreamRef.current;
    if (active && active.taskId !== selectedTaskId) {
      active.controller.abort();
      activeStreamRef.current = null;
    }
    setLiveMsgs([]);
    setStreaming(false);
    setStreamIssue(null);
    setPendingCards([]);
    setResolvingCardId(null);
  }, [selectedTaskId]);

  useEffect(() => () => {
    activeStreamRef.current?.controller.abort();
    activeStreamRef.current = null;
  }, []);

  useEffect(() => {
    const restored = messagesQuery.data?.pending_actions;
    if (restored !== undefined) setPendingCards(restored);
  }, [messagesQuery.data?.pending_actions, selectedTaskId]);

  useEffect(() => {
    const el = threadRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [allMsgs.length, streaming]);

  const createTask = async () => {
    if (!newLocalWorkspace.trim()) {
      message.warning('请填写本地工作区（必填，可复用：存放初始文件与报告），多个任务可共用同一文件夹');
      return;
    }
    try {
      const { task } = await createTaskMutation.mutateAsync({
        projectId,
        title: newTitle,
        local_workspace: newLocalWorkspace.trim(),
        hpc_workspace: newHpcWorkspace.trim() || undefined,
      });
      void tasksQuery.refetch();
      setNewTaskOpen(false);
      setNewTitle('');
      setNewLocalWorkspace('');
      setNewHpcWorkspace('');
      selectTask(task.id);
    } catch (err) {
      message.error(err instanceof Error ? err.message : '创建失败');
    }
  };

  const openEditTask = (task: AiTask) => {
    setEditingTask(task);
    setEditOpen(true);
    setEditTitle(task.title);
  };

  const saveEditTask = async () => {
    if (!editingTask) return;
    if (!editTitle.trim()) {
      message.warning('任务名称不能为空');
      return;
    }
    const patch: import("../types/ai").AiTaskPatch = { title: editTitle.trim() };
    try {
      await updateTaskMutation.mutateAsync({
        projectId, taskId: editingTask.id, patch,
      });
      setEditOpen(false);
      void tasksQuery.refetch();
    } catch (err) {
      message.error(err instanceof Error ? err.message : "保存失败");
    }
  };

  const deleteTask = async (task: AiTask) => {
    try {
      await deleteTaskMutation.mutateAsync({ projectId, taskId: task.id });
      const remain = (tasksQuery.data?.tasks ?? []).filter((t) => t.id !== task.id);
      if (selectedTaskId === task.id) {
        selectTask(remain.length ? (remain[0]?.id ?? null) : null);
      }
      void tasksQuery.refetch();
    } catch (err) {
      message.error(err instanceof Error ? err.message : "删除失败");
    }
  };

  const patchAssistant = (
    requestId: number,
    patch: Partial<Pick<LiveMsg, 'content' | 'thinking' | 'stopped'>>,
  ) => {
    if (activeStreamRef.current?.requestId !== requestId) return;
    setLiveMsgs((prev) => {
      if (prev.length === 0) return prev;
      const copy = [...prev];
      const last = copy[copy.length - 1];
      copy[copy.length - 1] = { ...last, ...patch };
      return copy;
    });
  };

  const stoppingRef = useRef(false);

  const handleStop = async () => {
    if (!selectedTask || stoppingRef.current) return;
    stoppingRef.current = true;
    try {
      const r = await aiApi.stopMessage(projectId, selectedTask.id);
      if (!r.stopped) {
        message.info('当前没有进行中的回复生成，可直接发送下一条。');
      }
      void messagesQuery.refetch();
    } catch (err) {
      message.warning(err instanceof Error ? err.message : '停止失败');
    } finally {
      stoppingRef.current = false;
    }
  };

  const handleResolveCard = async (card: AiConsentCard, approved: boolean) => {
    const taskId = selectedTask?.id;
    if (!taskId || resolvingCardId) return;
    setResolvingCardId(card.card_id);
    try {
      const r = await aiApi.resolveConsent(projectId, taskId, card.card_id, approved);
      if (selectedTaskIdRef.current === taskId) {
        if (approved) {
          message.success(r.result || '已批准本次操作；后续操作仍需单独确认');
        } else {
          message.info(r.result || '已拒绝，本次不执行');
        }
        setPendingCards((prev) => prev.filter((c) => c.card_id !== card.card_id));
        // 提交/授权结果已由后端落库为 assistant 消息，立即刷出，不能只靠 toast。
        await messagesQuery.refetch();
        void tasksQuery.refetch();
        void taskContextQuery.refetch();
      }
    } catch (err) {
      if (selectedTaskIdRef.current === taskId) {
        const detail = err instanceof Error ? err.message : '授权处理失败';
        message.error(detail);
        // 这张卡可能已经作废（例如作业已提交、或准备过程被重新做过）：
        // 失败后也刷新一次，让过期卡从页面上消失，避免用户反复点同一张废卡。
        setPendingCards((prev) => prev.filter((c) => c.card_id !== card.card_id));
        void messagesQuery.refetch();
        void taskContextQuery.refetch();
      }
    } finally {
      if (selectedTaskIdRef.current === taskId) setResolvingCardId(null);
    }
  };

  /** 顶栏「停止」：一个按钮停到底（后端负责同时停对话、监控与自动唤醒），并刷出 AI 的收尾提醒。 */
  const handleStopAll = async () => {
    if (!selectedTask) return;
    try {
      const r = await aiApi.stopMessage(projectId, selectedTask.id);
      if (r.message) message.info('已停止：对话与计算流程都停下了（已在超算运行的作业见对话里的提示）');
      await messagesQuery.refetch();
      void tasksQuery.refetch();
      void taskContextQuery.refetch();
    } catch (err) {
      message.warning(err instanceof Error ? err.message : '停止失败');
    }
  };

  /** 批量处理同类卡片：一次点击，但逐张提交、各自留决议记录。 */
  const handleResolveCards = async (cards: AiConsentCard[], approved: boolean) => {
    const taskId = selectedTask?.id;
    if (!taskId || resolvingCardId || batchBusy || cards.length === 0) return;
    const confirmed = await new Promise<boolean>((resolve) => {
      Modal.confirm({
        title: approved ? `批准本批 ${cards.length} 项` : `拒绝本批 ${cards.length} 项`,
        content: (
          <div style={{ maxHeight: 220, overflowY: 'auto' }}>
            <div style={{ marginBottom: 6 }}>将逐项处理以下操作（每项仍单独记录决议）：</div>
            <div style={{ whiteSpace: 'pre-wrap', fontSize: 12 }}>
              {cards.map((card) => `· ${cardFingerprint(card.summary)}`).join('\n')}
            </div>
          </div>
        ),
        okText: approved ? '全部批准' : '全部拒绝',
        cancelText: '取消',
        onOk: () => resolve(true),
        onCancel: () => resolve(false),
      });
    });
    if (!confirmed) return;
    setBatchBusy(true);
    let done = 0;
    const failed: string[] = [];
    for (const card of cards) {
      try {
        await aiApi.resolveConsent(projectId, taskId, card.card_id, approved);
        done += 1;
        setPendingCards((prev) => prev.filter((c) => c.card_id !== card.card_id));
      } catch (err) {
        failed.push(`${cardFingerprint(card.summary)}：${err instanceof Error ? err.message : '处理失败'}`);
      }
    }
    setBatchBusy(false);
    if (failed.length) {
      message.warning(`本批完成 ${done} 项，${failed.length} 项未成功：\n${failed.join('\n')}`);
    } else {
      message.success(approved ? `本批已批准 ${done} 项（每项单独留决议记录）` : `本批已拒绝 ${done} 项`);
    }
    await messagesQuery.refetch();
    void tasksQuery.refetch();
    void taskContextQuery.refetch();
  };

  const send = async () => {
    const content = input.trim();
    if (!content || !selectedTask || conversationBusy) return;
    if (content.length > messageLimit) {
      // 后端按上限截断后才会进模型上下文；这里如实告知，避免用户以为全文都被读到。
      message.warning(`本条消息 ${content.length} 字，超过 ${messageLimit} 字上限；超出部分不会进入模型上下文，建议分段发送。`);
    }
    const taskId = selectedTask.id;
    const requestId = ++streamSequenceRef.current;
    const controller = new AbortController();
    activeStreamRef.current = { requestId, taskId, controller };
    setInput('');
    setStreaming(true);
    setStreamIssue(null);
    setLiveMsgs((prev) => [
      ...prev,
      { role: 'user', content, thinking: '' },
      { role: 'assistant', content: '', thinking: '' },
    ]);
    try {
      let answer = '';
      let thinking = '';
      for await (const ev of aiApi.sendMessageStream(projectId, taskId, content, controller.signal)) {
        if (activeStreamRef.current?.requestId !== requestId) break;
        if (ev.type === 'thinking') {
          thinking += ev.text;
          patchAssistant(requestId, { thinking });
        } else if (ev.type === 'answer') {
          answer += ev.text;
          patchAssistant(requestId, { content: answer });
        } else if (ev.type === 'card') {
          setPendingCards((prev) => (prev.some((c) => c.card_id === ev.card.card_id) ? prev : [...prev, ev.card]));
        } else if (ev.type === 'done') {
          answer = ev.answer;
          patchAssistant(requestId, { content: answer });
        } else if (ev.type === 'stopped') {
          answer = ev.answer;
          patchAssistant(requestId, { content: answer, stopped: true });
          break;
        } else if (ev.type === 'error') {
          // 业务/模型错误后服务端仍会发送 done 并落库；继续消费终止事件。
          setStreamIssue({ kind: 'generation', message: ev.message });
        }
      }
    } catch (err) {
      if (activeStreamRef.current?.requestId === requestId && !controller.signal.aborted) {
        setStreamIssue({
          kind: 'connection',
          message: err instanceof Error ? err.message : '发送失败',
        });
      }
    } finally {
      await messagesQuery.refetch();
      void taskContextQuery.refetch();
      void tasksQuery.refetch();
      if (activeStreamRef.current?.requestId === requestId) {
        activeStreamRef.current = null;
        setStreaming(false);
        setLiveMsgs([]);
      }
    }
  };

  const currentStatus = selectedTask?.job ? selectedTask.job.status : selectedTask?.status;
  const statusLabel = currentStatus ? AI_JOB_STATUS_MAP[currentStatus]?.label : '';
  const currentColor = currentStatus ? AI_JOB_STATUS_MAP[currentStatus]?.color : undefined;

  const sidebarExtra = (
    <>
      <Button block icon={<PlusOutlined />} onClick={() => setNewTaskOpen(true)}>新建计算任务</Button>
      <Button block icon={<SettingOutlined />} onClick={() => setExtraOpen(true)}>额外设置 · 计算精度</Button>
    </>
  );

  return (
    <Layout style={{ height: 'calc(100vh - 64px)', minHeight: 520, minWidth: 0, background: '#fff', margin: '-32px -24px' }}>
      <AiTaskSidebar
        projectId={projectId}
        selectedTaskId={selectedTaskId}
        onSelectTask={selectTask}
        contextHint="每个计算任务是一段独立对话"
        extra={sidebarExtra}
        onEditTask={openEditTask}
        onDeleteTask={deleteTask}
      />

      <Content style={{ display: 'flex', flexDirection: 'column', minWidth: 0, minHeight: 0, overflow: 'hidden', background: '#fff' }}>
        {!selectedTask ? (
          <Space direction="vertical" align="center" style={{ margin: 'auto', textAlign: 'center' }}>
            <RobotOutlined style={{ fontSize: 48, color: '#c7c7cc' }} />
            <Title level={5}>选择或新建一个计算任务开始对话</Title>
          </Space>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', height: '100%', padding: '20px 28px 18px' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, marginBottom: 10 }}>
              <Space size={10} wrap style={{ minWidth: 0 }}>
                <Title level={5} style={{ margin: 0 }}>{selectedTask.title}</Title>
                {statusLabel && <Tag color={currentColor || 'default'} style={{ margin: 0 }}>{statusLabel}</Tag>}
                <ToolboxEnvironmentTags detail={executionQuery.data} unavailable={executionQuery.isError} />
                <Text type="secondary" style={{ fontSize: 12, maxWidth: 'min(60vw, 460px)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{selectedTask.goal}</Text>
              </Space>
              <Space size={8} wrap>
                {selectedTask.local_workspace && <Tag icon={<FolderOutlined />} color="geekblue" style={{ margin: 0 }}>{selectedTask.local_workspace}</Tag>}
                {selectedTask.hpc_workspace && <Tag icon={<CloudServerOutlined />} color="purple" style={{ margin: 0 }}>{selectedTask.hpc_workspace}</Tag>}
                <AiContextBar context={taskContextQuery.data} />
                {/* 一个按钮停到底：对话生成 + 计算流程 + 后台自动唤醒全停；再说一句「继续」即可接上 */}
                <Button size="small" danger onClick={() => {
                  Modal.confirm({
                    title: '停止这个任务的对话与计算流程？',
                    content: '会停止 AI 输出、停止后台监控与自动准备、不再自动弹卡。'
                      + '已经在超算上运行的作业不会被取消（我会把作业号告诉你，需要时用 scancel）。'
                      + '想继续时说一句「继续」即可，已算完的结果会保留。',
                    okText: '停止', cancelText: '取消',
                    onOk: () => void handleStopAll(),
                  });
                }}>停止</Button>
                <Button size="small" danger icon={<DeleteOutlined />} onClick={() => {
                  Modal.confirm({
                    title: "删除该计算任务？",
                    content: "将同时删除它的聊天记录与上下文，不可恢复。",
                    okText: "删除", cancelText: "取消", okButtonProps: { danger: true },
                    onOk: () => deleteTask(selectedTask),
                  });
                }}>删除</Button>
              </Space>
            </div>

            <div ref={threadRef} style={{ flex: 1, minHeight: 0, overflowY: 'auto', overscrollBehavior: 'contain', padding: '4px 4px 16px', marginBottom: 10 }}>
              <div style={{ marginBottom: 16 }}>
                <ToolboxTaskStatus
                  projectId={projectId}
                  taskId={selectedTask.id}
                  title="共享执行状态（每 5 秒更新）"
                  showTaskLink
                />
              </div>
              {allMsgs.length === 0 && <div style={{ color: '#999', textAlign: 'center', marginTop: 40 }}>还没有消息，说点什么吧。</div>}
              {messages.map((m, i) => (
                <AiChatBubble key={i} role={m.role} name={m.role === 'assistant' ? 'VASP 计算助手' : undefined}>
                  <>
                    {m.role === 'assistant' && m.thinking ? (
                      <details style={{ marginBottom: 10, padding: '8px 12px', background: 'rgba(0,0,0,0.035)', borderRadius: 8, borderLeft: '3px solid #0071e3', cursor: 'pointer' }}>
                        <summary style={{ fontSize: 12, fontWeight: 600, color: '#6e6e73', cursor: 'pointer', userSelect: 'none' }}>
                          思考过程 <Text type="secondary" style={{ fontSize: 11 }}>（点击展开/收起）</Text>
                        </summary>
                        <div style={{ whiteSpace: 'pre-wrap', color: '#6e6e73', fontSize: 13, marginTop: 8 }}>{m.thinking}</div>
                      </details>
                    ) : null}
                    <div style={{ whiteSpace: 'pre-wrap' }}>{m.content}</div>
                  </>
                </AiChatBubble>
              ))}
              {liveMsgs.map((m, idx) => (
                <AiChatBubble key={`live-${idx}`} role={m.role} name={m.role === 'assistant' ? 'VASP 计算助手' : undefined}>
                  <>
                    {m.role === 'assistant' && m.thinking ? (
                      // 思考过程默认收起：模型的自述/中间推理（常含英文）不占聊天版面，
                      // 想看过程点一下即可展开。
                      <details style={{ marginBottom: 10, padding: '8px 12px', background: 'rgba(0,0,0,0.035)', borderRadius: 8, borderLeft: '3px solid #0071e3' }}>
                        <summary style={{ fontSize: 12, fontWeight: 600, color: '#6e6e73', cursor: 'pointer', userSelect: 'none' }}>
                          思考过程 <Text type="secondary" style={{ fontSize: 11 }}>（点击展开）</Text>
                        </summary>
                        <div style={{ whiteSpace: 'pre-wrap', color: '#6e6e73', fontSize: 13, marginTop: 8 }}>{m.thinking}</div>
                      </details>
                    ) : null}
                    {m.role === 'assistant' && !m.thinking && !m.content ? (
                      <div style={{ color: '#8a8a8e', fontSize: 13 }}>
                        <LoadingOutlined spin style={{ marginRight: 8 }} />正在理解并思考，工作区可随时让我查看…
                      </div>
                    ) : null}
                    {m.content ? <div style={{ whiteSpace: 'pre-wrap' }}>{m.content}</div> : null}
                    {m.stopped ? (
                      <div style={{ color: '#b25000', fontSize: 12, marginTop: 6 }}>
                        ⏹ 已停止生成，以上为已生成的部分内容。
                      </div>
                    ) : null}
                  </>
                </AiChatBubble>
              ))}
            </div>

            {pendingCards.length > 0 && (
              <div style={{ marginBottom: 10 }}>
                {(() => {
                  // 同类卡（仅限机械文件准备）折成一组，可一次批准/拒绝；科学输入、
                  // 脚本认领、提交与重试保持逐项确认。
                  const groups: { key: string; label: string; cards: AiConsentCard[]; batchable: boolean }[] = [];
                  for (const card of pendingCards) {
                    const batchable = BATCHABLE_KINDS.has(card.kind);
                    const group = batchable ? groups.find((g) => g.batchable) : undefined;
                    if (group) group.cards.push(card);
                    else groups.push({ key: batchable ? PREPARE_GROUP_KEY : card.card_id,
                                       label: batchable ? '文件准备' : cardLabel(card.kind),
                                       cards: [card], batchable });
                  }
                  return groups.map((group) => (
                    <div key={group.key} style={{ border: '1px solid #f0c36d', background: '#fffbe6', borderRadius: 10, padding: '10px 14px', marginBottom: 8 }}>
                      <Space style={{ width: '100%', justifyContent: 'space-between' }}>
                        <Space size={8}>
                          <Tag color="gold">{group.label}</Tag>
                          <Text type="secondary" style={{ fontSize: 12 }}>
                            {group.cards.length} 项待批准{group.batchable ? '（可批量）' : '（逐项确认）'}
                          </Text>
                        </Space>
                        {group.batchable && group.cards.length > 1 && (
                          <Space>
                            <Button size="small" type="primary" loading={batchBusy}
                                    onClick={() => void handleResolveCards(group.cards, true)}>
                              全部批准本批（{group.cards.length} 项）
                            </Button>
                            <Button size="small" danger loading={batchBusy}
                                    onClick={() => void handleResolveCards(group.cards, false)}>
                              全部拒绝
                            </Button>
                          </Space>
                        )}
                      </Space>
                      {group.batchable ? (
                        // 准备阶段只显示"一组 + 一个全部批准"，明细默认收起，不再糊满屏幕
                        <details style={{ marginTop: 6 }}>
                          <summary style={{ fontSize: 12, color: '#6e6e73', cursor: 'pointer' }}>
                            查看这 {group.cards.length} 项明细（默认收起）
                          </summary>
                          {group.cards.map((card) => (
                            <PendingCardRow
                              key={card.card_id}
                              card={card}
                              resolving={resolvingCardId === card.card_id || batchBusy}
                              onResolve={(target, approved) => void handleResolveCard(target, approved)}
                              toolboxLink={`/toolbox/projects/${encodeURIComponent(projectId)}/tasks/${encodeURIComponent(selectedTaskId || '')}?fileAction=${encodeURIComponent(card.card_id)}#toolbox-files`}
                            />
                          ))}
                        </details>
                      ) : group.cards.map((card) => (
                        <PendingCardRow
                          key={card.card_id}
                          card={card}
                          resolving={resolvingCardId === card.card_id || batchBusy}
                          onResolve={(target, approved) => void handleResolveCard(target, approved)}
                          toolboxLink={`/toolbox/projects/${encodeURIComponent(projectId)}/tasks/${encodeURIComponent(selectedTaskId || '')}?fileAction=${encodeURIComponent(card.card_id)}#toolbox-files`}
                        />
                      ))}
                    </div>
                  ));
                })()}
              </div>
            )}
            {streamIssue && (
              <Alert
                type={streamIssue.kind === 'connection' ? 'error' : 'warning'}
                showIcon
                closable
                onClose={() => setStreamIssue(null)}
                style={{ marginBottom: 10 }}
                message={streamIssue.kind === 'connection' ? '回复连接中断' : '回复生成提示'}
                description={streamIssue.kind === 'connection'
                  ? `${streamIssue.message}。已重新同步已保存消息；${generationRunning ? '后端显示仍在生成，页面会继续轮询结果。' : '后端当前未报告进行中的生成。'}`
                  : streamIssue.message}
              />
            )}
            {generationRunning && !streaming && (
              <Alert
                type="info"
                showIcon
                style={{ marginBottom: 10 }}
                message="后台仍在生成回复"
                description="页面正在自动同步已持久化的消息。你可以等待完成，或点击“停止”。"
              />
            )}
            <div style={{ display: 'flex', gap: 10, borderTop: '1px solid rgba(0,0,0,0.06)', paddingTop: 14 }}>
              <Input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onPressEnter={send}
                placeholder="描述计算需求…（如：对 Fe2O3 结构做 relax → static → dos）"
                size="large"
                disabled={conversationBusy}
              />
              {conversationBusy ? (
                <Button danger size="large" icon={<StopOutlined />} onClick={() => void handleStop()}>
                  停止
                </Button>
              ) : (
                <Button type="primary" size="large" icon={<SendOutlined />} onClick={send}>
                  发送
                </Button>
              )}
            </div>
            {draftOverLimit && (
              <div style={{ marginTop: 6, fontSize: 12, color: '#d46b08' }}>
                本条消息 {draftLength} 字，超过 {messageLimit} 字上限；超出部分不会进入模型上下文，建议分段发送。
              </div>
            )}
          </div>
        )}
      </Content>

      <AiProjectExtraSettings projectId={projectId} open={extraOpen} onClose={() => setExtraOpen(false)} />

      <Modal title="新建计算任务" open={newTaskOpen} onCancel={() => setNewTaskOpen(false)} onOk={createTask} okText="创建" confirmLoading={createTaskMutation.isPending}>
        <Space direction="vertical" style={{ width: '100%' }}>
          <Input placeholder="任务标题（可选）" value={newTitle} onChange={(e) => setNewTitle(e.target.value)} maxLength={80} />
          <div>
            <Text strong><FolderOutlined /> 本地工作区（必填 · 可复用）</Text>
            <br />
            <Text type="secondary" style={{ fontSize: 12 }}>存放初始计算文件与导出报告；多个计算任务可共用同一本地文件夹。</Text>

            <Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 2 }}>点击「浏览」会在本机弹出系统目录选择窗口，选取后自动填入路径；超算工作区仍走 SSH 浏览。</Text>
            <div style={{ display: 'flex', gap: 8, marginTop: 6 }}>
              <Input placeholder="如 D:\calc\fe2o3_relax" value={newLocalWorkspace} onChange={(e) => setNewLocalWorkspace(e.target.value)} style={{ flex: 1 }} />
              <Button icon={<FolderOpenOutlined />} loading={pickingLocal} onClick={() => void handlePickLocalWorkspace()}>浏览</Button>
            </div>
          </div>
          <div>
            <Text strong><CloudServerOutlined /> 超算工作区（可留空）</Text>
            <br />
            <Text type="secondary" style={{ fontSize: 12 }}>划定计算操作区域；若不在超算正式计算可留空。</Text>
            <div style={{ display: 'flex', gap: 8, marginTop: 6 }}>
              <Input placeholder="如 /lustre/hpc_home/u01/fe2o3_relax（可留空）" value={newHpcWorkspace} onChange={(e) => setNewHpcWorkspace(e.target.value)} style={{ flex: 1 }} />
              <Button icon={<FolderOpenOutlined />} onClick={() => setPickerKind('hpc')}>浏览</Button>
            </div>
          </div>
          <Alert
            type="info"
            showIcon
            message="选定这两个工作区，就是这次任务的文件授权"
            description="AI 可以在本地工作区与超算工作区之间互传文件（复制输入、上传、写文本、建目录），每一步都先弹确认卡给你；不需要再额外配置研究根或文件范围。想收回授权时，到同一任务的 Toolbox 页面撤销对应文件根即可；清空超算工作区同样会收回授权。"
          />
        </Space>
      </Modal>

      <Modal
        title="编辑计算任务"
        open={editOpen}
        onCancel={() => setEditOpen(false)}
        onOk={() => void saveEditTask()}
        okText="保存"
        confirmLoading={updateTaskMutation.isPending}
      >
        <Space direction="vertical" style={{ width: "100%" }}>
          <Input placeholder="任务名称" value={editTitle} onChange={(e) => setEditTitle(e.target.value)} maxLength={80} />
          <Text type="secondary" style={{ fontSize: 12 }}>仅可重命名任务；工作区与聊天记录保持不变。</Text>
        </Space>
      </Modal>

      <AiDirectoryPicker
        open={pickerKind !== null}
        kind="hpc"
        initialPath={newHpcWorkspace}
        onSelect={(p) => {
          setNewHpcWorkspace(p);
          setPickerKind(null);
        }}
        onCancel={() => setPickerKind(null)}
      />
    </Layout>
  );
};

export default AiProjectPage;
