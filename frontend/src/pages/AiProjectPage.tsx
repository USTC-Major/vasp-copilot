// ============================================================
// AiProjectPage — 项目聊天主界面（任务栏 + 对话 + 额外设置）
// 布局：左侧任务栏贴左，单分隔线；右侧聊天栏占满其余全部，无空白。
// 聊天：发送后立即显示用户消息，LLM 思考与正文流式实时展示。
// M032：新建任务的工作区支持「浏览」按钮图形化点选目录。
// ============================================================

import React, { useEffect, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { Alert, Layout, Button, Input, Space, Typography, Tag, Modal, message } from 'antd';
import { PlusOutlined, SendOutlined, SettingOutlined, RobotOutlined, FolderOutlined, FolderOpenOutlined, CloudServerOutlined, LoadingOutlined, StopOutlined, DeleteOutlined } from '@ant-design/icons';
import AiChatBubble from '../components/ai/AiChatBubble';
import AiTaskSidebar from '../components/ai/AiTaskSidebar';
import AiProjectExtraSettings from '../components/ai/AiProjectExtraSettings';
import AiContextBar from '../components/ai/AiContextBar';
import AiDirectoryPicker from '../components/ai/AiDirectoryPicker';
import { aiApi } from '../api/client';
import { AI_JOB_STATUS_MAP } from '../types/ai';
import { useAiTasks, useAiTaskCreate, useAiMessages, useAiTaskContext, useAiTaskUpdate, useAiTaskDelete } from '../hooks/useApi';
import type { AiMessage as AiMsg, AiTask, AiConsentCard } from '../types/ai';

const { Content } = Layout;
const { Text, Title } = Typography;

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
      const r = await aiApi.pickLocal(newLocalWorkspace);
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
        message.error(err instanceof Error ? err.message : '授权处理失败');
      }
    } finally {
      if (selectedTaskIdRef.current === taskId) setResolvingCardId(null);
    }
  };

  const send = async () => {
    const content = input.trim();
    if (!content || !selectedTask || conversationBusy) return;
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
                <Tag color={selectedTask.execution_mode === 'Real' ? 'green' : selectedTask.execution_mode === 'Fake' ? 'gold' : 'default'}>
                  运行环境: {selectedTask.execution_mode || 'None'}
                </Tag>
                <Text type="secondary" style={{ fontSize: 12, maxWidth: 'min(60vw, 460px)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{selectedTask.goal}</Text>
              </Space>
              <Space size={8} wrap>
                {selectedTask.local_workspace && <Tag icon={<FolderOutlined />} color="geekblue" style={{ margin: 0 }}>{selectedTask.local_workspace}</Tag>}
                {selectedTask.hpc_workspace && <Tag icon={<CloudServerOutlined />} color="purple" style={{ margin: 0 }}>{selectedTask.hpc_workspace}</Tag>}
                <AiContextBar context={taskContextQuery.data} />
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
                      <div style={{ marginBottom: 10, padding: '8px 12px', background: 'rgba(0,0,0,0.035)', borderRadius: 8, borderLeft: '3px solid #0071e3' }}>
                        <Text strong style={{ fontSize: 12, display: 'block', marginBottom: 4, color: '#6e6e73' }}>
                          <LoadingOutlined spin style={{ marginRight: 6 }} />思考过程
                        </Text>
                        <div style={{ whiteSpace: 'pre-wrap', color: '#6e6e73', fontSize: 13 }}>{m.thinking}</div>
                      </div>
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
                {pendingCards.map((card) => (
                  <div key={card.card_id} style={{ border: '1px solid #f0c36d', background: '#fffbe6', borderRadius: 10, padding: '12px 14px', marginBottom: 8 }}>
                    <Space style={{ marginBottom: 6, width: '100%', justifyContent: 'space-between' }}>
                      <Space size={8}>
                        <Tag color="gold">{card.kind === 'submit' ? '提交确认' : '操作授权'}</Tag>
                        <Text strong style={{ whiteSpace: 'pre-wrap' }}>{card.summary}</Text>
                      </Space>
                    </Space>
                    <div style={{ fontSize: 13, color: '#8c6d1f', marginBottom: 10, whiteSpace: 'pre-wrap' }}>{card.reason}</div>
                    <Space>
                      {(card.options && card.options.length
                        ? card.options.filter((opt) => opt !== '同意本批' && opt !== 'allow_batch')
                        : ['同意本次', '拒绝']).map((opt) => (
                        <Button
                          key={opt}
                          size="small"
                          type={opt === '拒绝' ? 'default' : 'primary'}
                          danger={opt === '拒绝'}
                          loading={resolvingCardId === card.card_id}
                          onClick={() => void handleResolveCard(card, opt !== '拒绝')}
                        >
                          {opt}
                        </Button>
                      ))}
                    </Space>
                  </div>
                ))}
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
