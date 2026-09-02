// ============================================================
// AiProjectsPage — 初始页（项目列表：通讯录式排列 + 创建/修改时间切换排序
// + 列表最上方「＋」新建入口 + 等待空位队列界面）
// ============================================================

import React, { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Card, Typography, Button, Space, Tag, Input, Modal, List, Empty, Popconfirm, message, Segmented } from 'antd';
import {
  PlusOutlined, RobotOutlined, ArrowRightOutlined, DeleteOutlined,
  FieldTimeOutlined, FolderOpenOutlined,
} from '@ant-design/icons';
import ErrorAlert from '../components/common/ErrorAlert';
import {
  useAiProjects, useAiProjectCreate, useAiProjectDelete,
  useAiWaitQueue,
} from '../hooks/useApi';

const { Title, Text, Paragraph } = Typography;
type SortMode = 'created' | 'updated';

const formatTime = (iso?: string): string => {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleString('zh-CN', {
    hour12: false, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  });
};

const AiProjectsPage: React.FC = () => {
  const navigate = useNavigate();
  const projectsQuery = useAiProjects();
  const createMutation = useAiProjectCreate();
  const deleteMutation = useAiProjectDelete();
  const queueQuery = useAiWaitQueue();

  const [sortMode, setSortMode] = useState<SortMode>('created');
  const [createOpen, setCreateOpen] = useState(false);
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');

  const projects = useMemo(() => {
    const list = projectsQuery.data?.projects ?? [];
    const copy = [...list];
    copy.sort((a, b) => {
      const key = (p: { created_at: string; updated_at?: string }) =>
        sortMode === 'updated' ? (p.updated_at ?? p.created_at) : p.created_at;
      return key(b).localeCompare(key(a));
    });
    return copy;
  }, [projectsQuery.data, sortMode]);

  const queue = queueQuery.data?.waiting ?? [];
  const queued = queueQuery.data?.count ?? queue.length;
  const error = projectsQuery.error || queueQuery.error;

  const createProject = async () => {
    if (!name.trim()) {
      message.warning('请输入项目名称');
      return;
    }
    try {
      const { project } = await createMutation.mutateAsync({ name, description });
      setCreateOpen(false);
      setName('');
      setDescription('');
      projectsQuery.refetch();
      navigate(`/ai/projects/${project.id}`);
    } catch (err) {
      message.error(err instanceof Error ? err.message : '创建失败');
    }
  };

  const removeProject = async (id: string) => {
    try {
      await deleteMutation.mutateAsync(id);
      projectsQuery.refetch();
      message.success('项目已删除');
    } catch (err) {
      message.error(err instanceof Error ? err.message : '删除失败');
    }
  };

  return (
    <div style={{ maxWidth: 960, margin: '0 auto', padding: '8px 0' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20, gap: 16, flexWrap: 'wrap' }}>
        <div>
          <Title level={3} style={{ marginBottom: 4 }}><RobotOutlined /> 智能模式 · 项目</Title>
          <Paragraph type="secondary" style={{ marginBottom: 0 }}>
            每个项目可包含多个计算任务；每个计算任务是一段独立对话，需绑定本地/超算工作区。
          </Paragraph>
        </div>
      </div>

      {error && <ErrorAlert error={error} onRetry={projectsQuery.refetch} title="项目加载失败" />}

      {/* 排序切换（创建时间 / 修改时间） */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12, gap: 12 }}>
        <Text type="secondary">排序：</Text>
        <Segmented
          value={sortMode}
          onChange={(v) => setSortMode(v as SortMode)}
          options={[
            { label: '按创建时间', value: 'created' },
            { label: '按修改时间', value: 'updated' },
          ]}
        />
        <Text type="secondary">共 {projects.length} 个项目</Text>
      </div>

      {projects.length === 0 && !projectsQuery.isLoading ? (
        <Card><Empty description="暂无项目 — 点击下方「＋ 新建项目」开始" /></Card>
      ) : (
        <List
          dataSource={projects}
          loading={projectsQuery.isLoading}
          split={false}
          renderItem={(project) => (
            <List.Item style={{ padding: '8px 0' }}>
              <Card
                hoverable
                styles={{ body: { padding: '14px 18px' } }}
                style={selectedProjectId === project.id ? { borderColor: '#0071e3', boxShadow: '0 0 0 1px #0071e3' } : undefined}
                onClick={() => setSelectedProjectId(project.id)}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 16 }}>
                  <div style={{ minWidth: 0 }}>
                    <Title level={5} style={{ margin: 0 }}>{project.name}</Title>
                    {project.description && (
                      <Text type="secondary" style={{ fontSize: 13 }}>{project.description}</Text>
                    )}
                    <div style={{ marginTop: 8 }}>
                      <Tag color="blue">任务 {project.job_count}</Tag>
                      <Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>
                        修改于 {formatTime(project.updated_at)}
                      </Text>
                    </div>
                  </div>
                  <Space onClick={(e) => e.stopPropagation()}>
                    <Popconfirm
                      title="删除项目？"
                      description="将删除该项目下的全部计算任务（仅演示数据）。"
                      onConfirm={() => removeProject(project.id)}
                    >
                      <Button type="text" danger size="small" icon={<DeleteOutlined />} aria-label="删除项目" />
                    </Popconfirm>
                    <Button type="primary" icon={<ArrowRightOutlined />} onClick={() => navigate(`/ai/projects/${project.id}`)}>进入</Button>
                  </Space>
                </div>
              </Card>
            </List.Item>
          )}
        />
      )}

      {/* 列表最上方「＋」新建入口（已确认） */}
      <Card
        hoverable
        onClick={() => setCreateOpen(true)}
        styles={{ body: { padding: '14px 18px', display: 'flex', alignItems: 'center', gap: 12 } }}
        style={{ marginTop: 12 }}
      >
        <Button type="primary" shape="circle" icon={<PlusOutlined />} />
        <Text strong>新建项目</Text>
      </Card>

      {/* 等待空位队列（对应工作流 §9：无空位时按确认先后排队，补提自动提交） */}
      <Card
        style={{ marginTop: 20 }}
        title={
          <Space>
            <FieldTimeOutlined />
            等待空位队列
            {queued > 0 && <Tag color="orange">{queued}</Tag>}
          </Space>
        }
      >
        {queue.length === 0 ? (
          <Empty description="当前无排队作业 — 有空位时自动提交" />
        ) : (
          <List
            size="small"
            dataSource={queue}
            renderItem={(entry, i) => (
              <List.Item key={`${entry.queued_at}_${i}`}>
                <Space align="start" style={{ width: '100%' }}>
                  <Text strong type="secondary">{i + 1}.</Text>
                  <div>
                    <div>
                      {entry.task_title || '待定任务'}
                      <Tag color="default" style={{ marginLeft: 8, fontSize: 12 }}>排队中</Tag>
                    </div>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {entry.reason} · 排队时间 {formatTime(entry.queued_at)} · 有空位后自动提交
                    </Text>
                  </div>
                </Space>
              </List.Item>
            )}
          />
        )}
      </Card>

      <Modal
        title={<>新建项目 <FolderOpenOutlined /></>}
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={createProject}
        okText="创建"
        confirmLoading={createMutation.isPending}
      >
        <Space direction="vertical" style={{ width: '100%' }}>
          <div>
            <Text strong>项目名称</Text>
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="如：Fe2O3 表面能研究" maxLength={80} />
          </div>
          <div>
            <Text strong>描述（可选）</Text>
            <Input value={description} onChange={(e) => setDescription(e.target.value)} placeholder="一句话描述目标" maxLength={200} />
          </div>
        </Space>
      </Modal>
    </div>
  );
};

export default AiProjectsPage;