// ============================================================
// AiProgressPage — 进度页（聊天区消失，左任务栏保留）
// 数据源：GET /ai/v1/projects/{pid}/tasks/{tid}/detail（真实 flow 概要）
// 顶部：计算目标 + 流程阶段 / 中间：作业链（依赖嵌套 + 状态 + Slurm 号）
// 下方：等待队列 / 预检问题 / 计算报告
// ============================================================

import React from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Layout, Card, Typography, Space, Tag, Empty, Button, Alert } from 'antd';
import { BarChartOutlined, ReloadOutlined } from '@ant-design/icons';
import AiTaskSidebar from '../components/ai/AiTaskSidebar';
import ErrorAlert from '../components/common/ErrorAlert';
import { useAiTaskDetail } from '../hooks/useApi';
import { AI_FLOW_PHASE_MAP, AI_FLOW_JOB_STATUS_MAP } from '../types/ai';
import type { AiFlowJob } from '../types/ai';

const { Content } = Layout;
const { Title, Text, Paragraph } = Typography;

// 作业链条目：按 key 的路径深度缩进，直观呈现 relax → relax/static → relax/static/dos 嵌套依赖
const JobChainList: React.FC<{ jobs: AiFlowJob[] }> = ({ jobs }) => (
  <Space direction="vertical" size={10} style={{ width: '100%' }}>
    {jobs.map((job) => {
      const st = AI_FLOW_JOB_STATUS_MAP[job.status]
        || { color: 'default', label: job.status };
      const depth = job.key.split('/').length - 1;
      const deps = job.requires.length > 0 ? job.requires.join(' → ') : '';
      return (
        <Card
          key={job.key}
          size="small"
          style={{ marginLeft: depth * 28, borderLeft: '4px solid #1677ff' }}
        >
          <Space size={10} wrap>
            <Text strong style={{ fontSize: 14 }}>{job.label}</Text>
            <Text type="secondary" style={{ fontSize: 12 }}>{job.key}</Text>
            <Tag color={st.color}>{st.label}</Tag>
            {job.slurm_id != null && job.slurm_id !== '' && (
              <Text type="secondary" style={{ fontSize: 12 }}>Slurm 作业号 {job.slurm_id}</Text>
            )}
          </Space>
          {deps && (
            <div style={{ marginTop: 6 }}>
              <Text type="secondary" style={{ fontSize: 12 }}>依赖：{deps}{job.status === 'waiting' ? '（前序完成后自动补提）' : ''}</Text>
            </div>
          )}
          {job.description && (
            <div style={{ marginTop: 4 }}>
              <Text type="secondary" style={{ fontSize: 12 }}>{job.description}</Text>
            </div>
          )}
        </Card>
      );
    })}
  </Space>
);

const AiProgressPage: React.FC = () => {
  const { projectId = '', taskId = '' } = useParams();
  const navigate = useNavigate();
  const detailQuery = useAiTaskDetail(projectId || null, taskId || null);
  const flow = detailQuery.data?.flow;

  const openTaskProgress = (id: string) => navigate(`/ai/projects/${projectId}/progress/${id}`);

  if (detailQuery.error) {
    return (
      <ErrorAlert
        error={detailQuery.error}
        onRetry={detailQuery.refetch}
        title="进度加载失败"
      />
    );
  }

  if (!detailQuery.isLoading && detailQuery.data == null) {
    return (
      <Card style={{ maxWidth: 720, margin: '0 auto', textAlign: 'center' }}>
        <Empty description="未找到该任务或任务已删除" />
        <Button onClick={() => navigate(`/ai/projects/${projectId}`)}>返回项目</Button>
      </Card>
    );
  }

  const phase = flow?.phase || '';
  const phaseInfo = AI_FLOW_PHASE_MAP[phase];
  const jobs = flow?.jobs ?? [];
  const waiting = flow?.waiting ?? [];
  const precheckIssues = (flow?.precheck?.issues ?? []).filter((i) => i.level !== 'ok');
  const report = (flow?.report || '').trim();

  return (
    <Layout style={{ minHeight: 'calc(100vh - 200px)', background: 'transparent', gap: 20 }}>
      <AiTaskSidebar
        projectId={projectId}
        selectedTaskId={taskId}
        onSelectTask={openTaskProgress}
        contextHint="聊天区已收起 —— 查看计算流程与作业进度"
      />

      <Content style={{ background: '#fff', borderRadius: 20, padding: 24, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
        <Space style={{ marginBottom: 12 }}>
          <Button type="text" icon={<BarChartOutlined />} onClick={() => navigate(`/ai/projects/${projectId}`)}>
            返回聊天
          </Button>
          <Button type="text" icon={<ReloadOutlined />} onClick={() => detailQuery.refetch()}>
            刷新
          </Button>
        </Space>

        {/* 最上方：计算目标 + 当前阶段 */}
        <Space size={12} wrap style={{ marginBottom: 4 }}>
          <Title level={4} style={{ margin: 0 }}>计算目标</Title>
          {phaseInfo && <Tag color={phaseInfo.color}>{phaseInfo.label}</Tag>}
        </Space>
        <Text style={{ fontSize: 14, display: 'block', marginBottom: 20 }}>
          {flow?.goal || '（未开始计算流程）'}
        </Text>

        {/* 中间：作业链（真实 flow 数据） */}
        <Card
          size="small"
          title="作业链与进度"
          style={{ marginBottom: 20 }}
          extra={flow?.strategy ? <Text type="secondary" style={{ fontSize: 12 }}>策略：{flow.strategy}</Text> : undefined}
        >
          {jobs.length > 0 ? (
            <JobChainList jobs={jobs} />
          ) : (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description="尚未规划作业。回聊天区发送计算需求（如「做 relax、static、dos」），AI 规划后这里会显示作业链与实时进度。"
            />
          )}
        </Card>

        {/* 等待队列：依赖闸门未放行的作业 */}
        {waiting.length > 0 && (
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 20 }}
            message="等待队列"
            description={(
              <Space direction="vertical" size={2}>
                {waiting.map((k) => (
                  <Text key={k} style={{ fontSize: 12 }}>{k}：等待前置作业完成后自动补提</Text>
                ))}
              </Space>
            )}
          />
        )}

        {/* 预检问题摘要 */}
        {precheckIssues.length > 0 && (
          <Alert
            type={flow?.precheck?.ok ? 'warning' : 'error'}
            showIcon
            style={{ marginBottom: 20 }}
            message="提交前检查问题"
            description={(
              <Space direction="vertical" size={2}>
                {precheckIssues.map((i, idx) => (
                  <Text key={idx} style={{ fontSize: 12 }}>
                    [{i.level}] {i.job} · {i.file}：{i.message}
                  </Text>
                ))}
              </Space>
            )}
          />
        )}

        {/* 计算报告（done 后由系统生成） */}
        {report && (
          <Card size="small" title="计算报告" style={{ marginBottom: 20 }}>
            <Paragraph style={{ whiteSpace: 'pre-wrap', marginBottom: 0, maxHeight: 360, overflow: 'auto' }}>
              {report}
            </Paragraph>
          </Card>
        )}
      </Content>
    </Layout>
  );
};

export default AiProgressPage;
