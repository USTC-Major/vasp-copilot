// ============================================================
// AiProjectsPage — 科研项目列表、创建/删除确认与等待空位队列。
// ============================================================

import React, { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Card, Typography, Button, Tag, Input, Modal, List, Empty, Popconfirm, message, Segmented, Spin } from 'antd';
import {
  PlusOutlined, RobotOutlined, ArrowRightOutlined, DeleteOutlined,
  FieldTimeOutlined, FolderOpenOutlined,
} from '@ant-design/icons';
import ErrorAlert from '../components/common/ErrorAlert';
import './scientific-ai-projects.css';
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
    <div className="scientific-ai-projects">
      <header className="ai-projects-heading">
        <div className="ai-projects-heading-copy">
          <Title level={1}><RobotOutlined aria-hidden="true" /> 智能模式 · 项目</Title>
          <Paragraph type="secondary">
            每个项目可包含多个计算任务；每个计算任务是一段独立对话，需绑定本地/超算工作区。
          </Paragraph>
        </div>
        {!projectsQuery.isLoading && !projectsQuery.isError && (
          <Button type="primary" icon={<PlusOutlined aria-hidden="true" />} onClick={() => setCreateOpen(true)}>新建项目</Button>
        )}
      </header>

      {projectsQuery.error && <div className="ai-projects-error"><ErrorAlert error={projectsQuery.error} title="项目加载失败" /><Button onClick={() => void projectsQuery.refetch()}>重试项目</Button></div>}

      {/* 排序切换（创建时间 / 修改时间） */}
      {!projectsQuery.isLoading && !projectsQuery.isError && <div className="ai-projects-toolbar">
        <Text type="secondary">共 {projects.length} 个项目</Text>
        <div className="ai-projects-sort">
          <Text type="secondary" id="ai-projects-sort-label">排序</Text>
          <Segmented
            aria-labelledby="ai-projects-sort-label"
            value={sortMode}
            onChange={(v) => setSortMode(v as SortMode)}
            options={[
              { label: '按创建时间', value: 'created' },
              { label: '按修改时间', value: 'updated' },
            ]}
          />
        </div>
      </div>}

      {projectsQuery.isLoading ? <div className="ai-projects-loading"><Spin aria-label="项目加载中" /></div> : projectsQuery.isError ? null : projects.length === 0 ? (
        <Card className="ai-projects-empty"><Empty description="暂无项目 — 点击「新建项目」开始" /></Card>
      ) : (
        <List
          className="ai-projects-list"
          rowKey="id"
          dataSource={projects}
          loading={projectsQuery.isLoading}
          split={false}
          renderItem={(project) => (
            <List.Item>
              <Card
                className={`ai-project-record${selectedProjectId === project.id ? ' is-selected' : ''}`}
                onClick={() => setSelectedProjectId(project.id)}
              >
                <div className="ai-project-row">
                  <button
                    type="button"
                    className="ai-project-select"
                    aria-label={`选择项目 ${project.name}`}
                    aria-pressed={selectedProjectId === project.id}
                    onClick={() => setSelectedProjectId(project.id)}
                  >
                    <span className="ai-project-name">{project.name}</span>
                    {project.description && (
                      <span className="ai-project-description">{project.description}</span>
                    )}
                    <span className="ai-project-meta">
                      <Tag>任务 {project.job_count}</Tag>
                      <span>
                        修改于 {formatTime(project.updated_at)}
                      </span>
                    </span>
                  </button>
                  <div className="ai-project-actions" onClick={(e) => e.stopPropagation()}>
                    <Popconfirm
                      title="删除项目？"
                      description="将删除该项目及其计算任务记录；不会取消超算作业或删除工作区文件。"
                      classNames={{ root: 'scientific-ai-projects-popconfirm' }}
                      onConfirm={() => removeProject(project.id)}
                    >
                      <Button type="text" danger size="small" icon={<DeleteOutlined />} aria-label="删除项目" />
                    </Popconfirm>
                    <Button type="primary" icon={<ArrowRightOutlined aria-hidden="true" />} onClick={() => navigate(`/ai/projects/${project.id}`)}>进入</Button>
                  </div>
                </div>
              </Card>
            </List.Item>
          )}
        />
      )}

      {/* 等待空位队列：条件满足后重新预检与确认，不自动补提。 */}
      <Card
        className="ai-projects-queue"
        title={
          <div className="ai-queue-heading">
            <Title level={2}><FieldTimeOutlined aria-hidden="true" /> 等待空位队列</Title>
            {queued > 0 && <Tag>{queued}</Tag>}
          </div>
        }
      >
        <Paragraph type="secondary" className="ai-queue-note">条件满足后重新预检并确认提交，不自动补提。</Paragraph>
        {queueQuery.isLoading ? <Spin aria-label="队列加载中" /> : queueQuery.isError ? (
          <div className="ai-projects-error"><ErrorAlert error={queueQuery.error} title="等待队列加载失败" /><Button onClick={() => void queueQuery.refetch()}>重试队列</Button></div>
        ) : queue.length === 0 ? (
          <Empty description="当前无排队作业 — 前置完成或有空位后仍会重新预检并确认提交" />
        ) : (
          <List
            size="small"
            dataSource={queue}
            renderItem={(entry, i) => (
              <List.Item key={`${entry.queued_at}_${i}`}>
                <div className="ai-queue-entry">
                  <Text type="secondary" className="ai-queue-position">{i + 1}.</Text>
                  <div className="ai-queue-copy">
                    <div className="ai-queue-title">
                      <Text strong>{entry.task_title || '待定任务'}</Text>
                      <Tag>排队中</Tag>
                    </div>
                    <Paragraph type="secondary" className="ai-queue-reason">{entry.reason}</Paragraph>
                    <Text type="secondary">排队时间 {formatTime(entry.queued_at)} · 条件满足后重新预检并确认提交</Text>
                  </div>
                </div>
              </List.Item>
            )}
          />
        )}
      </Card>

      <Modal
        title={<>新建项目 <FolderOpenOutlined aria-hidden="true" /></>}
        className="scientific-ai-projects-modal"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={createProject}
        okText="创建"
        confirmLoading={createMutation.isPending}
      >
        <div className="ai-projects-create-fields">
          <div>
            <label htmlFor="ai-project-name">项目名称</label>
            <Input id="ai-project-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="如：Fe2O3 表面能研究" maxLength={80} />
          </div>
          <div>
            <label htmlFor="ai-project-description">描述（可选）</label>
            <Input id="ai-project-description" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="一句话描述目标" maxLength={200} />
          </div>
        </div>
      </Modal>
    </div>
  );
};

export default AiProjectsPage;
