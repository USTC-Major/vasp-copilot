import React, { useMemo, useState } from 'react';
import { Alert, Button, Card, Collapse, Descriptions, Empty, List, Space, Tag, Typography, message } from 'antd';
import { Link } from 'react-router-dom';
import { ReloadOutlined, SafetyCertificateOutlined, StopOutlined } from '@ant-design/icons';
import { useToolboxResolveConsent, useToolboxTaskDetail } from '../../hooks/useApi';
import type { ToolboxConsentCard, ToolboxJob } from '../../types/toolbox';
import { renderMarkdown } from '../../utils/markdown';

const { Text, Paragraph } = Typography;

const colorForMode = (mode?: string) => mode === 'Real' ? 'green' : mode === 'Fake' ? 'gold' : 'default';
const colorForStatus = (status?: string) => {
  if (!status) return 'default';
  if (/fail|error|reject|unknown/i.test(status)) return 'red';
  if (/complete|success|executed/i.test(status)) return 'green';
  if (/run|monitor|submit|execut/i.test(status)) return 'blue';
  if (/wait|pending|recover/i.test(status)) return 'gold';
  return 'default';
};

const STATUS_LABELS: Record<string, string> = {
  idle: '空闲', planning: '规划中', planned: '已规划', preparing: '准备中', prepared: '已准备', draft: '待认领脚本', await_submit: '待提交确认',
  pending: '等待中', waiting: '等待中', submitted: '已提交', queued: '排队中', running: '运行中', monitoring: '监控中',
  recovering: '恢复核对中', stopped: '已停止跟踪', completed: '已完成', done: '流程结束', failed: '失败',
  not_converged: '未收敛', unknown: '状态待核实', error: '采集错误', executed: '已执行', rejected: '已拒绝',
};
const statusLabel = (status?: string) => status ? (STATUS_LABELS[status] ?? status) : '未知';
const modeLabel = (mode?: string) => mode === 'Real' ? '真实' : mode === 'Fake' ? '模拟' : mode === 'None' ? '未配置' : (mode || '不可用');

const jobSubmissionText = (job: ToolboxJob) => {
  const states: Record<string, string> = { confirmed: '提交已确认', submitted: '提交已确认', unknown: '提交结果待核实', pending: '待提交确认', not_submitted: '尚未提交', failed: '提交失败' };
  const state = states[job.submission_state ?? ''] ?? job.submission_state ?? '尚未提交';
  const id = job.slurm_id ? ` · 作业号 ${job.slurm_id}` : '';
  return `${state}${id}`;
};

const readableValue = (value: unknown) => {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
};

const recordDetails = (record: Record<string, unknown>, key: string) => (
  <Descriptions key={key} size="small" column={1} bordered style={{ marginTop: 6 }}>
    {Object.entries(record).map(([name, value]) => (
      <Descriptions.Item key={name} label={name}>{readableValue(value)}</Descriptions.Item>
    ))}
  </Descriptions>
);

interface Props {
  projectId: string;
  taskId: string;
  title?: string;
  showTaskLink?: boolean;
  onStopMonitor?: () => Promise<void> | void;
  stopPending?: boolean;
}

const ToolboxTaskStatus: React.FC<Props> = ({
  projectId, taskId, title = 'Toolbox 执行状态', showTaskLink = false, onStopMonitor, stopPending = false,
}) => {
  const detailQuery = useToolboxTaskDetail(projectId, taskId);
  const resolveMutation = useToolboxResolveConsent();
  const [resolvingId, setResolvingId] = useState<string | null>(null);
  const detail = detailQuery.data;
  const pendingCards = useMemo(
    () => (detail?.consents ?? []).filter((card) => card.state === 'pending'),
    [detail?.consents],
  );

  const resolveCard = async (card: ToolboxConsentCard, approved: boolean) => {
    if (resolvingId) return;
    setResolvingId(card.card_id);
    try {
      const result = await resolveMutation.mutateAsync({ projectId, taskId, cardId: card.card_id, approved });
      message[approved ? 'success' : 'info'](result.result || (approved ? '已处理本次授权' : '已拒绝，本次操作未执行'));
    } catch (error) {
      // 确认请求超时或断线时只读核对卡与任务；绝不自动重发写操作。
      message.warning(`${error instanceof Error ? error.message : '授权结果未知'}；正在重新读取任务状态，请勿重复确认。`);
    } finally {
      await detailQuery.refetch();
      setResolvingId(null);
    }
  };

  if (detailQuery.isLoading) {
    return <Card size="small" title={title} loading />;
  }

  if (detailQuery.isError || !detail) {
    const error = detailQuery.error;
    const missing = typeof error === 'object' && error !== null && 'status' in error && error.status === 404;
    return (
      <Alert
        type={missing ? 'warning' : 'error'}
        showIcon
        message={missing ? '计算任务不存在或已被删除' : 'Toolbox 状态暂时不可用'}
        description={missing
          ? <Link to="/toolbox/projects">返回 Toolbox 项目列表</Link>
          : '8000 服务不可达时不会回退到 AI 本地执行，也不会自动重试提交。'}
        action={<Button size="small" icon={<ReloadOutlined />} onClick={() => void detailQuery.refetch()}>重试读取</Button>}
      />
    );
  }

  const { task, flow, monitor } = detail;
  const stopped = monitor?.state === 'stopped';
  const lastSuccessMs = monitor?.last_success_at ? Date.parse(monitor.last_success_at) : Number.NaN;
  const staleThresholdSeconds = Math.max(2 * (monitor?.interval_seconds ?? 60), 30);
  const collectionStale = monitor?.state === 'monitoring'
    && Number.isFinite(lastSuccessMs)
    && Date.now() - lastSuccessMs > staleThresholdSeconds * 1000;
  return (
    <Card
      size="small"
      title={title}
      extra={showTaskLink ? <Link to={`/toolbox/projects/${encodeURIComponent(projectId)}/tasks/${encodeURIComponent(taskId)}`}>打开任务页</Link> : undefined}
      style={{ borderColor: '#d6e4ff' }}
    >
      <Space wrap style={{ marginBottom: 12 }}>
        <Tag title={flow.phase} color={colorForStatus(flow.phase)}>阶段：{statusLabel(flow.phase)}</Tag>
        <Tag title={task.status} color={colorForStatus(task.status)}>任务：{statusLabel(task.status)}</Tag>
        <Tag title={flow.execution_mode} color={colorForMode(flow.execution_mode)}>历史执行：{modeLabel(flow.execution_mode)}</Tag>
        <Tag title={String(detail.backend_mode)} color={colorForMode(detail.backend_mode)}>当前后端：{modeLabel(detail.backend_mode)}</Tag>
        <Tag title={monitor?.state} color={colorForStatus(monitor?.state)}>监控：{statusLabel(monitor?.state)}</Tag>
      </Space>

      {stopped && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message="已停止自动跟踪"
          description="这不会取消远端作业；远端计算可能仍在继续。本版本没有真实远端取消接口。"
        />
      )}
      {monitor?.state === 'recovering' && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message="服务恢复后等待重新采集"
          description="当前展示的是已保存状态；首次成功采集前不会把历史状态改写为计算失败。"
        />
      )}
      {collectionStale && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message="最近采集已过期，请检查连接或 Toolbox 服务"
          description={`超过 ${staleThresholdSeconds} 秒未成功采集。页面仍保留最后已知状态，不据此判断远端计算失败。`}
        />
      )}
      {monitor?.last_error && (
        <Alert type="warning" showIcon style={{ marginBottom: 12 }} message="最近一次状态采集失败" description={monitor.last_error} />
      )}

      {pendingCards.map((card) => (
        <Card key={card.card_id} size="small" style={{ marginBottom: 10, background: '#fffbe6', borderColor: '#ffe58f' }}>
          <Space direction="vertical" size={6} style={{ width: '100%' }}>
            <Space wrap align="start"><Tag color="gold">人工确认</Tag><Text strong style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{card.summary}</Text></Space>
            {card.reason && <Text type="secondary" style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{card.reason}</Text>}
            <Space>
              <Button
                type="primary"
                size="small"
                icon={<SafetyCertificateOutlined />}
                loading={resolvingId === card.card_id}
                onClick={() => void resolveCard(card, true)}
              >确认本次操作</Button>
              <Button
                danger
                size="small"
                disabled={!!resolvingId}
                onClick={() => void resolveCard(card, false)}
              >拒绝</Button>
            </Space>
          </Space>
        </Card>
      ))}

      <Descriptions size="small" column={{ xs: 1, sm: 2, md: 3 }} style={{ marginBottom: 10 }}>
        <Descriptions.Item label="本地目录">{flow.local_dir || task.local_workspace || '未设置'}</Descriptions.Item>
        <Descriptions.Item label="超算目录">{flow.hpc_dir || task.hpc_workspace || '未设置'}</Descriptions.Item>
        <Descriptions.Item label="轮询间隔">{monitor?.interval_seconds ?? 60} 秒</Descriptions.Item>
        <Descriptions.Item label="最近成功采集">{monitor?.last_success_at || '尚无'}</Descriptions.Item>
        <Descriptions.Item label="等待条件">{flow.waiting?.length ? flow.waiting.join('；') : '无'}</Descriptions.Item>
        <Descriptions.Item label="预检">{flow.precheck?.ok ? '通过' : '未通过或尚未执行'}</Descriptions.Item>
      </Descriptions>

      {flow.jobs?.length ? (
        <List
          size="small"
          header={<Text strong>作业</Text>}
          dataSource={flow.jobs}
          renderItem={(job) => (
            <List.Item>
              <List.Item.Meta
                title={<Space wrap><Text strong>{job.label || job.key}</Text><Tag title={job.status} color={colorForStatus(job.status)}>{statusLabel(job.status)}</Tag></Space>}
                description={<>
                  <div>{job.description || job.kind}</div>
                  <Text type="secondary">{jobSubmissionText(job)}</Text>
                  {((job.attempt_history?.length ?? 0) > 0 || (job.attempts?.length ?? 0) > 0) && (
                    <details style={{ marginTop: 6 }}>
                      <summary>查看提交尝试记录（{job.attempt_history?.length ?? job.attempts?.length ?? 0}）</summary>
                      {(job.attempt_history ?? job.attempts ?? []).map((attempt, index) => recordDetails(attempt, `${job.key}-attempt-${index}`))}
                    </details>
                  )}
                  {job.diagnosis && Object.keys(job.diagnosis).length > 0 && (
                    <details style={{ marginTop: 6 }}>
                      <summary>查看确定性诊断</summary>
                      {recordDetails(job.diagnosis, `${job.key}-diagnosis`)}
                    </details>
                  )}
                </>}
              />
            </List.Item>
          )}
        />
      ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未规划作业" />}

      <Collapse
        ghost
        items={[
          {
            key: 'events',
            label: `执行事件（${detail.events?.length ?? 0}）`,
            children: detail.events?.length ? (
              <List size="small" dataSource={[...detail.events].reverse()} renderItem={(event) => (
                <List.Item><Space direction="vertical" size={0}><Text>{event.message}</Text><Text type="secondary" style={{ fontSize: 12 }}>{event.at} · {event.kind}</Text></Space></List.Item>
              )} />
            ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无执行事件" />,
          },
          {
            key: 'report',
            label: '确定性报告',
            children: flow.report ? <div>{renderMarkdown(flow.report)}</div> : <Paragraph type="secondary">报告尚未生成。</Paragraph>,
          },
        ]}
      />

      {onStopMonitor && monitor?.state !== 'stopped' && (
        <Button danger icon={<StopOutlined />} loading={stopPending} onClick={() => void onStopMonitor()}>
          停止跟踪
        </Button>
      )}
    </Card>
  );
};

export default ToolboxTaskStatus;
