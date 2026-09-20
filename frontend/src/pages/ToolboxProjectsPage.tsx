import React, { useEffect, useMemo, useState } from 'react';
import { Alert, Button, Card, Col, Empty, Input, List, Modal, Row, Space, Tag, Typography, message } from 'antd';
import { CloudServerOutlined, FolderOpenOutlined, PlusOutlined, ProjectOutlined, ReloadOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import AiDirectoryPicker from '../components/ai/AiDirectoryPicker';
import { toolboxApi } from '../api/client';
import { useToolboxProjectCreate, useToolboxProjects, useToolboxTaskCreate, useToolboxTasks } from '../hooks/useApi';

const { Title, Text, Paragraph } = Typography;

const ToolboxProjectsPage: React.FC = () => {
  const navigate = useNavigate();
  const projectsQuery = useToolboxProjects();
  const createProjectMutation = useToolboxProjectCreate();
  const createTaskMutation = useToolboxTaskCreate();
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const tasksQuery = useToolboxTasks(selectedProjectId);
  const [projectOpen, setProjectOpen] = useState(false);
  const [projectName, setProjectName] = useState('');
  const [projectDescription, setProjectDescription] = useState('');
  const [taskOpen, setTaskOpen] = useState(false);
  const [taskTitle, setTaskTitle] = useState('');
  const [taskGoal, setTaskGoal] = useState('');
  const [localWorkspace, setLocalWorkspace] = useState('');
  const [hpcWorkspace, setHpcWorkspace] = useState('');
  const [pickerKind, setPickerKind] = useState<'local' | 'hpc' | null>(null);
  const [pickingLocal, setPickingLocal] = useState(false);

  const projects = useMemo(() => projectsQuery.data?.projects ?? [], [projectsQuery.data?.projects]);
  const tasks = useMemo(() => tasksQuery.data?.tasks ?? [], [tasksQuery.data?.tasks]);
  const selectedProject = projects.find((project) => project.id === selectedProjectId);

  useEffect(() => {
    if (!selectedProjectId && projects[0]) setSelectedProjectId(projects[0].id);
    if (selectedProjectId && !projects.some((project) => project.id === selectedProjectId)) {
      setSelectedProjectId(projects[0]?.id ?? null);
    }
  }, [projects, selectedProjectId]);

  const createProject = async () => {
    if (!projectName.trim()) {
      message.warning('请输入项目名称');
      return;
    }
    try {
      const response = await createProjectMutation.mutateAsync({
        name: projectName.trim(), description: projectDescription.trim() || undefined,
      });
      await projectsQuery.refetch();
      setSelectedProjectId(response.project.id);
      setProjectOpen(false);
      setProjectName('');
      setProjectDescription('');
    } catch (error) {
      message.error(error instanceof Error ? error.message : '创建项目失败');
    }
  };

  const createTask = async () => {
    if (!selectedProjectId) return;
    if (!localWorkspace.trim()) {
      message.warning('请选择或填写本地工作区');
      return;
    }
    try {
      const response = await createTaskMutation.mutateAsync({
        projectId: selectedProjectId,
        body: {
          title: taskTitle.trim() || undefined,
          goal: taskGoal.trim() || undefined,
          local_workspace: localWorkspace.trim(),
          hpc_workspace: hpcWorkspace.trim() || undefined,
        },
      });
      setTaskOpen(false);
      setTaskTitle('');
      setTaskGoal('');
      setLocalWorkspace('');
      setHpcWorkspace('');
      navigate(`/toolbox/projects/${encodeURIComponent(selectedProjectId)}/tasks/${encodeURIComponent(response.task.id)}`);
    } catch (error) {
      message.error(error instanceof Error ? error.message : '创建任务失败');
    }
  };

  const deleteProject = (projectId: string) => {
    Modal.confirm({
      title: '删除项目记录？',
      content: '只删除 Toolbox 记录，不删除用户文件，也不会取消远端作业。',
      okText: '删除记录', cancelText: '取消', okButtonProps: { danger: true },
      onOk: async () => {
        try {
          await toolboxApi.deleteProject(projectId);
          if (selectedProjectId === projectId) setSelectedProjectId(null);
          await projectsQuery.refetch();
        } catch (error) {
          message.error(error instanceof Error ? error.message : '删除项目失败');
        }
      },
    });
  };

  const deleteTask = (projectId: string, taskId: string) => {
    Modal.confirm({
      title: '删除任务记录？',
      content: '只删除任务记录，不删除计算目录，也不会停止或取消远端作业。',
      okText: '删除记录', cancelText: '取消', okButtonProps: { danger: true },
      onOk: async () => {
        try {
          await toolboxApi.deleteTask(projectId, taskId);
          await tasksQuery.refetch();
        } catch (error) {
          message.error(error instanceof Error ? error.message : '删除任务失败');
        }
      },
    });
  };

  const pickLocal = async () => {
    setPickingLocal(true);
    try {
      const response = await toolboxApi.pickLocal(localWorkspace);
      if (response.ok && response.path) setLocalWorkspace(response.path);
      else if (response.notice) message.info(response.notice);
    } catch (error) {
      message.warning(error instanceof Error ? error.message : '无法打开本地目录选择器');
    } finally {
      setPickingLocal(false);
    }
  };

  return (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <div>
        <Title level={2} style={{ marginBottom: 4 }}>Toolbox 计算任务</Title>
        <Paragraph type="secondary" style={{ marginBottom: 0 }}>
          无需配置 AI 模型，可直接准备、批准、提交和跟踪计算。
        </Paragraph>
      </div>

      {projectsQuery.isError && (
        <Alert
          type="error"
          showIcon
          message="Toolbox 服务不可用"
          description="请确认 8000 服务已启动。页面不会回退到 AI 服务或伪造演示提交。"
          action={<Button icon={<ReloadOutlined />} onClick={() => void projectsQuery.refetch()}>重试</Button>}
        />
      )}

      <Row gutter={[18, 18]}>
        <Col xs={24} md={9}>
          <Card
            title="项目"
            extra={<Button type="primary" size="small" icon={<PlusOutlined />} onClick={() => setProjectOpen(true)}>新建项目</Button>}
          >
            <List
              loading={projectsQuery.isLoading}
              dataSource={projects}
              locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有 Toolbox 项目" /> }}
              renderItem={(project) => (
                <List.Item
                  actions={[<Button key="delete" danger type="link" onClick={(event) => { event.stopPropagation(); deleteProject(project.id); }}>删除记录</Button>]}
                  onClick={() => setSelectedProjectId(project.id)}
                  style={{ cursor: 'pointer', borderRadius: 10, paddingInline: 10, background: project.id === selectedProjectId ? '#e6f4ff' : undefined }}
                >
                  <List.Item.Meta
                    avatar={<ProjectOutlined style={{ color: '#1677ff' }} />}
                    title={project.name}
                    description={project.description || '无项目说明'}
                  />
                </List.Item>
              )}
            />
          </Card>
        </Col>

        <Col xs={24} md={15}>
          <Card
            title={selectedProject ? `${selectedProject.name} · 计算任务` : '计算任务'}
            extra={<Button type="primary" size="small" icon={<PlusOutlined />} disabled={!selectedProjectId} onClick={() => setTaskOpen(true)}>新建任务</Button>}
          >
            <List
              loading={tasksQuery.isLoading}
              dataSource={tasks}
              locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={selectedProjectId ? '该项目还没有计算任务' : '请先选择或创建项目'} /> }}
              renderItem={(task) => (
                <List.Item
                  actions={[
                    <Button key="open" type="link" onClick={() => navigate(`/toolbox/projects/${encodeURIComponent(task.project_id)}/tasks/${encodeURIComponent(task.id)}`)}>打开任务</Button>,
                    <Button key="delete" danger type="link" onClick={() => deleteTask(task.project_id, task.id)}>删除记录</Button>,
                  ]}
                >
                  <List.Item.Meta
                    title={<Space wrap><Text strong>{task.title || task.id}</Text><Tag>{task.status}</Tag></Space>}
                    description={<Space direction="vertical" size={1}><Text type="secondary">{task.goal || '未填写计算目标'}</Text><Text type="secondary" style={{ fontSize: 12 }}>{task.local_workspace || '未设置本地目录'}</Text></Space>}
                  />
                </List.Item>
              )}
            />
          </Card>
        </Col>
      </Row>

      <Modal title="新建 Toolbox 项目" open={projectOpen} onCancel={() => setProjectOpen(false)} onOk={() => void createProject()} confirmLoading={createProjectMutation.isPending} okText="创建">
        <Space direction="vertical" style={{ width: '100%' }}>
          <label htmlFor="toolbox-project-name"><Text strong>项目名称</Text></label>
          <Input id="toolbox-project-name" value={projectName} onChange={(event) => setProjectName(event.target.value)} placeholder="如：Si 基础计算" />
          <label htmlFor="toolbox-project-description"><Text strong>项目说明（可选）</Text></label>
          <Input.TextArea id="toolbox-project-description" value={projectDescription} onChange={(event) => setProjectDescription(event.target.value)} rows={3} />
        </Space>
      </Modal>

      <Modal title="新建计算任务" open={taskOpen} onCancel={() => setTaskOpen(false)} onOk={() => void createTask()} confirmLoading={createTaskMutation.isPending} okText="创建任务" width={680}>
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          <div><label htmlFor="toolbox-task-title"><Text strong>任务标题</Text></label><Input id="toolbox-task-title" value={taskTitle} onChange={(event) => setTaskTitle(event.target.value)} placeholder="如：Si 静态计算" /></div>
          <div><label htmlFor="toolbox-task-goal"><Text strong>计算目标</Text></label><Input.TextArea id="toolbox-task-goal" value={taskGoal} onChange={(event) => setTaskGoal(event.target.value)} rows={2} placeholder="说明这次计算要完成什么" /></div>
          <div>
            <label htmlFor="toolbox-local-workspace"><Text strong>本地工作区（必填）</Text></label>
            <Space.Compact style={{ width: '100%' }}>
              <Input id="toolbox-local-workspace" value={localWorkspace} onChange={(event) => setLocalWorkspace(event.target.value)} placeholder="选择已有输入所在目录" />
              <Button icon={<FolderOpenOutlined />} loading={pickingLocal} onClick={() => void pickLocal()}>系统选择</Button>
              <Button onClick={() => setPickerKind('local')}>浏览</Button>
            </Space.Compact>
          </div>
          <div>
            <label htmlFor="toolbox-hpc-workspace"><Text strong>超算工作区（可选）</Text></label>
            <Space.Compact style={{ width: '100%' }}>
              <Input id="toolbox-hpc-workspace" value={hpcWorkspace} onChange={(event) => setHpcWorkspace(event.target.value)} placeholder="未配置 SSH 时可留空" prefix={<CloudServerOutlined />} />
              <Button onClick={() => setPickerKind('hpc')}>浏览</Button>
            </Space.Compact>
          </div>
        </Space>
      </Modal>

      <AiDirectoryPicker
        open={pickerKind !== null}
        kind={pickerKind ?? 'local'}
        initialPath={pickerKind === 'hpc' ? hpcWorkspace : localWorkspace}
        onCancel={() => setPickerKind(null)}
        onSelect={(path) => {
          if (pickerKind === 'hpc') setHpcWorkspace(path);
          else setLocalWorkspace(path);
          setPickerKind(null);
        }}
      />
    </Space>
  );
};

export default ToolboxProjectsPage;
