import React, { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Layout, List, Space, Typography, Button, Tag, Popconfirm, Tooltip } from 'antd';
import { ArrowLeftOutlined, EditOutlined, DeleteOutlined } from '@ant-design/icons';
import { AI_JOB_STATUS_MAP } from '../../types/ai';
import type { AiTask } from '../../types/ai';
import { useAiProjects, useAiTasks } from '../../hooks/useApi';

const { Sider } = Layout;
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

  useEffect(() => {
    try { localStorage.setItem(WIDTH_KEY, String(width)); } catch { /* ignore */ }
  }, [width]);

  const startDrag = (e: React.MouseEvent) => {
    e.preventDefault();
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
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  };

  return (
    <div style={{ position: 'relative', height: '100%', minWidth: 0 }}>
      <Sider width={width} style={{ background: '#f5f5f7', borderRight: '1px solid rgba(0,0,0,0.08)', padding: '16px 14px', height: '100%' }}>
        <Space direction="vertical" style={{ width: '100%' }} size={12}>
          <Button icon={<ArrowLeftOutlined />} type="text" onClick={() => navigate('/ai')} style={{ alignSelf: 'flex-start', padding: 0 }}>
            返回项目列表
          </Button>
          <Title level={5} style={{ margin: 0 }}>{project?.name ?? projectId}</Title>
          {contextHint && <Text type="secondary" style={{ fontSize: 12 }}>{contextHint}</Text>}
          {extra}
        </Space>
        <List
          style={{ marginTop: 20 }}
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
                onClick={() => onSelectTask(task.id)}
                style={{
                  cursor: 'pointer', borderRadius: 12, padding: '8px 6px 8px 12px',
                  background: task.id === selectedTaskId ? '#fff' : 'transparent',
                  boxShadow: task.id === selectedTaskId ? '0 1px 4px rgba(0,0,0,0.08)' : undefined,
                  alignItems: 'flex-start',
                }}
                actions={[
                  <Tooltip key="edit" title="编辑任务">
                    <Button size="small" type="text" icon={<EditOutlined />}
                      onClick={(e) => { e.stopPropagation(); onEditTask?.(task); }} />
                  </Tooltip>,
                  <Popconfirm key="del" title="删除该计算任务？"
                    description="将同时删除它的聊天记录与上下文，不可恢复。"
                    okText="删除" cancelText="取消"
                    onConfirm={() => onDeleteTask?.(task)}>
                    <Tooltip title="删除任务">
                      <Button size="small" type="text" danger icon={<DeleteOutlined />}
                        onClick={(e) => e.stopPropagation()} />
                    </Tooltip>
                  </Popconfirm>,
                ]}
              >
                <Space direction="vertical" size={2} style={{ width: '100%', minWidth: 0 }}>
                  <Text strong style={{ fontSize: 13 }} ellipsis>{task.title}</Text>
                  {task.last_message ? (
                    <Text type="secondary" style={{ fontSize: 12, color: '#86868b' }} ellipsis>
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
          locale={{ emptyText: <div style={{ padding: 8, color: '#999' }}>暂无计算任务</div> }}
        />
      </Sider>
      <div
        onMouseDown={startDrag}
        style={{ position: 'absolute', top: 0, bottom: 0, right: -3, width: 7, cursor: 'col-resize', zIndex: 5 }}
      />
    </div>
  );
};

export default AiTaskSidebar;