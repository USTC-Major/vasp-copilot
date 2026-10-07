import React, { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { List, Space, Typography, Button, Tag, Popconfirm, Tooltip, Drawer } from 'antd';
import { ArrowLeftOutlined, EditOutlined, DeleteOutlined, MenuOutlined } from '@ant-design/icons';
import { AI_JOB_STATUS_MAP } from '../../types/ai';
import type { AiTask } from '../../types/ai';
import { useAiProjects, useAiTasks } from '../../hooks/useApi';

const { Text, Title } = Typography;

interface AiTaskSidebarProps {
  projectId: string;
  selectedTaskId: string | null;
  onSelectTask: (taskId: string) => void;
  contextHint?: string;
  extra?: React.ReactNode;
  onEditTask?: (task: AiTask) => void;
  onDeleteTask?: (task: AiTask) => void;
}

const SIDER_MIN = 200;
const SIDER_MAX = 480;
const SIDER_DEFAULT = 280;
const WIDTH_KEY = 'ai_sidebar_width';
const clampWidth = (w: number) => Math.min(SIDER_MAX, Math.max(SIDER_MIN, w));

// 任务栏 = 「通讯录」式扁平联系人列表：所有任务平行并列、上下文各自独立；可随时切换/编辑/删除。
const AiTaskSidebar: React.FC<AiTaskSidebarProps> = ({
  projectId,
  selectedTaskId,
  onSelectTask,
  contextHint,
  extra,
  onEditTask,
  onDeleteTask,
}) => {
  const navigate = useNavigate();
  const projectsQuery = useAiProjects();
  const tasksQuery = useAiTasks(projectId);
  const project = projectsQuery.data?.projects.find((p) => p.id === projectId);
  const tasks = tasksQuery.data?.tasks ?? [];

  const [width, setWidth] = useState<number>(() => {
    try {
      const saved = localStorage.getItem(WIDTH_KEY);
      return saved ? clampWidth(Number(saved)) : SIDER_DEFAULT;
    } catch {
      return SIDER_DEFAULT;
    }
  });
  const dragging = useRef(false);
  const dragCleanup = useRef<(() => void) | null>(null);
  const [compact, setCompact] = useState(() => window.matchMedia('(max-width: 900px)').matches);
  const [drawerOpen, setDrawerOpen] = useState(false);

  useEffect(() => {
    const media = window.matchMedia('(max-width: 900px)');
    const update = () => { setCompact(media.matches); setDrawerOpen(false); dragCleanup.current?.(); };
    media.addEventListener('change', update);
    return () => { media.removeEventListener('change', update); dragCleanup.current?.(); };
  }, []);

  useEffect(() => {
    try { localStorage.setItem(WIDTH_KEY, String(width)); } catch { /* ignore */ }
  }, [width]);

  const startDrag = (e: React.MouseEvent) => {
    e.preventDefault();
    dragCleanup.current?.();
    dragging.current = true;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    const startX = e.clientX;
    const startWidth = width;
    const onMove = (ev: MouseEvent) => {
      if (!dragging.current) return;
      setWidth(clampWidth(startWidth + (ev.clientX - startX)));
    };
    const onUp = () => {
      dragging.current = false;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      dragCleanup.current = null;
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
    dragCleanup.current = onUp;
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  };

  const content = (
      <div className="ai-task-sidebar-content">
        <div className="ai-task-sidebar-heading">
        <Space direction="vertical" style={{ width: '100%' }} size={12}>
          <Button icon={<ArrowLeftOutlined />} type="text" onClick={() => navigate('/ai')} style={{ alignSelf: 'flex-start', padding: 0 }}>
            返回项目列表
          </Button>
          <Title level={5} style={{ margin: 0 }}>{project?.name ?? projectId}</Title>
          {contextHint && <Text type="secondary" style={{ fontSize: 12 }}>{contextHint}</Text>}
          <div onClick={() => setDrawerOpen(false)}>{extra}</div>
        </Space>
        </div>
        <List
          className="ai-task-list"
          size="small"
          dataSource={tasks}
          loading={tasksQuery.isLoading}
          renderItem={(task) => {
            const st = task.job?.status ?? task.status;
            const cfg = AI_JOB_STATUS_MAP[st];
            const statusLabel = cfg?.label ?? st;
            const statusColor = cfg?.color ?? 'default';
            return (
              <List.Item
                className={task.id === selectedTaskId ? 'ai-task-item is-selected' : 'ai-task-item'}
                onClick={() => { onSelectTask(task.id); setDrawerOpen(false); }}
                actions={[
                  <Tooltip key="edit" title="编辑任务">
                    <Button size="small" type="text" icon={<EditOutlined />}
                      aria-label={`编辑任务 ${task.title}`} onClick={(e) => { e.stopPropagation(); onEditTask?.(task); }} />
                  </Tooltip>,
                  <Popconfirm key="del" title="删除该计算任务？" classNames={{ root: 'scientific-ai-chat-popconfirm' }}
                    description="将同时删除它的聊天记录与上下文，不可恢复。"
                    okText="删除" cancelText="取消"
                    onConfirm={() => onDeleteTask?.(task)}>
                    <Tooltip title="删除任务">
                      <Button size="small" type="text" danger icon={<DeleteOutlined />}
                        aria-label={`删除任务 ${task.title}`} onClick={(e) => e.stopPropagation()} />
                    </Tooltip>
                  </Popconfirm>,
                ]}
              >
                <Space direction="vertical" size={2} style={{ width: '100%', minWidth: 0 }}>
                  <button type="button" className="ai-task-select" aria-current={task.id === selectedTaskId ? 'true' : undefined}
                    onClick={(event) => { event.stopPropagation(); onSelectTask(task.id); setDrawerOpen(false); }}>
                    {task.title}
                  </button>
                  {task.last_message ? (
                    <Text type="secondary" style={{ fontSize: 12 }} ellipsis>
                      {task.last_message}
                    </Text>
                  ) : null}
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                    <Tag color={statusColor} style={{ fontSize: 11, margin: 0, lineHeight: '18px' }}>{statusLabel}</Tag>
                    {typeof task.context_ratio === 'number' ? (
                      <Text type="secondary" style={{ fontSize: 11 }}>{Math.round(task.context_ratio * 100)}% 上下文</Text>
                    ) : null}
                  </div>
                </Space>
              </List.Item>
            );
          }}
          locale={{ emptyText: <div className="ai-task-empty">暂无计算任务</div> }}
        />
      </div>
  );

  if (compact) return (
    <div className="ai-task-mobile-bar">
      <Button icon={<MenuOutlined />} aria-label="任务列表" onClick={() => setDrawerOpen(true)} aria-expanded={drawerOpen} aria-controls="ai-task-navigation">任务列表</Button>
      <span>{project?.name ?? projectId}</span>
      <Drawer title="计算任务" placement="left" width="min(340px, calc(100vw - 24px))" open={drawerOpen}
        onClose={() => setDrawerOpen(false)} getContainer={false} className="ai-task-drawer">
        <div id="ai-task-navigation">{content}</div>
      </Drawer>
    </div>
  );

  return (
    <aside className="ai-task-sidebar" aria-label="计算任务列表" style={{ width }}>
      {content}
      <div className="ai-task-resize" role="separator" aria-label="调整任务栏宽度" aria-orientation="vertical"
        aria-valuemin={SIDER_MIN} aria-valuemax={SIDER_MAX} aria-valuenow={width} tabIndex={0}
        onMouseDown={startDrag}
        onKeyDown={(event) => {
          if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
          event.preventDefault();
          setWidth((current) => event.key === 'Home' ? SIDER_MIN : event.key === 'End' ? SIDER_MAX
            : clampWidth(current + (event.key === 'ArrowRight' ? 20 : -20)));
        }} />
    </aside>
  );
};

export default AiTaskSidebar;
