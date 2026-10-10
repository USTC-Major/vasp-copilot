// ============================================================
// HomePage — 首页入口：两大板块（智能模式 / 工具箱）+ 最近记录
// ============================================================

import React, { useMemo } from 'react';
import { Alert, Button, Tag, Spin } from 'antd';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  RobotOutlined, ToolOutlined, BuildOutlined, BugOutlined, CloudUploadOutlined,
  ArrowRightOutlined,
} from '@ant-design/icons';
import { useFeatureFlags } from '../hooks/useApi';
import { aiApi, diagnosisApi, toolboxApi, workflowsApi } from '../api/client';
import type { HistoryKind } from '../types/history';
import { historyRecordPath, mergeRecentRecords } from '../utils/history';
import './scientific-home.css';

const HISTORY_LIMIT = 10;

const HISTORY_LABELS: Record<HistoryKind, { label: string; color: string }> = {
  ai_project: { label: '智能项目', color: 'purple' },
  ai_task: { label: '智能任务', color: 'geekblue' },
  toolbox_task: { label: 'Toolbox 任务', color: 'cyan' },
  workflow: { label: '工作流', color: 'blue' },
  diagnosis: { label: '诊断', color: 'green' },
};

const formatTime = (value: string): string => {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '时间未知';
  return date.toLocaleString('zh-CN', { hour12: false });
};

const HomePage: React.FC = () => {
  const fakeHpcEnabled = useFeatureFlags().data?.ENABLE_FAKE_HPC === true;
  const aiHistory = useQuery({
    queryKey: ['recent-history', 'ai'],
    queryFn: () => aiApi.recentHistory(HISTORY_LIMIT),
    retry: false,
    refetchOnMount: 'always',
  });
  const workflowHistory = useQuery({
    queryKey: ['recent-history', 'workflows'],
    queryFn: () => workflowsApi.recent(HISTORY_LIMIT),
    retry: false,
    refetchOnMount: 'always',
  });
  const toolboxHistory = useQuery({
    queryKey: ['recent-history', 'toolbox'],
    queryFn: () => toolboxApi.recentHistory(HISTORY_LIMIT),
    retry: false,
    refetchOnMount: 'always',
  });
  const diagnosisHistory = useQuery({
    queryKey: ['recent-history', 'diagnoses'],
    queryFn: () => diagnosisApi.recent(HISTORY_LIMIT),
    retry: false,
    refetchOnMount: 'always',
  });
  const recentSessions = useMemo(() => mergeRecentRecords([
    (toolboxHistory.data?.items ?? []).map((item) => ({ ...item, kind: 'toolbox_task' as const })),
    aiHistory.data?.records ?? [],
    workflowHistory.data?.records ?? [],
    diagnosisHistory.data?.records ?? [],
  ]), [toolboxHistory.data, aiHistory.data, workflowHistory.data, diagnosisHistory.data]);
  const historySources = [
    { name: 'Toolbox', query: toolboxHistory },
    { name: '智能模式', query: aiHistory },
    { name: '工作流', query: workflowHistory },
    { name: '诊断', query: diagnosisHistory },
  ];
  const failedSources = historySources.filter(({ query }) => query.isError);
  const historyLoading = historySources.some(({ query }) => query.isLoading);
  const mockHistory = aiHistory.data?.demo === true || aiHistory.data?.records.some((record) => record.demo === true)
    || workflowHistory.data?.demo === true || workflowHistory.data?.records.some((record) => record.demo === true)
    || diagnosisHistory.data?.demo === true || diagnosisHistory.data?.records.some((record) => record.demo === true);

  const toolboxEntries = [
    { key: '/toolbox/projects', icon: <ToolOutlined />, title: '计算任务', desc: '无需模型，直接准备输入、人工确认、提交、监控并查看报告' },
    { key: '/workflow', icon: <BuildOutlined />, title: '生成工作流', desc: '根据结构和计算需求，生成并下载 VASP 输入文件' },
    { key: '/toolbox/surface-builder', icon: <BuildOutlined />, title: '表面构建', desc: '切出指定晶面，选择终止面和固定层，保存草稿并导出结构' },
    { key: '/diagnosis/upload', icon: <BugOutlined />, title: '诊断计算', desc: '分析计算输出，查看问题、收敛趋势与处理建议' },
    ...(fakeHpcEnabled
      ? [{ key: '/hpc/deploy', icon: <CloudUploadOutlined />, title: '远程部署（离线演示）', desc: 'Fake HPC 工具箱演示：不连接真实集群，不代表智能任务的运行环境' }]
      : []),
  ];

  return (
    <div className="home-workspace">
      <header className="home-heading">
        <h1>材料计算工作区</h1>
        <p>准备 VASP 输入文件、管理计算任务与诊断计算结果。</p>
      </header>

      <div className="home-entry-grid">
        <section className="home-panel home-ai" aria-labelledby="home-ai-title">
          <div className="home-section-heading"><RobotOutlined aria-hidden="true" /><h2 id="home-ai-title">智能模式</h2></div>
          <p className="home-panel-intro">以项目为中心，Agent 协助规划、排程并生成输入文件。</p>
          <p className="home-action-note">文件写入和计算提交前，会请你确认。</p>
          <Link className="home-primary-link" to="/ai">进入智能模式<ArrowRightOutlined aria-hidden="true" /></Link>
        </section>
        <section className="home-panel home-toolbox" aria-labelledby="home-toolbox-title">
          <div className="home-section-heading"><ToolOutlined aria-hidden="true" /><h2 id="home-toolbox-title">工具箱</h2></div>
          <p className="home-panel-intro">按当前任务选择工具，保留人工确认与操作控制。</p>
          <div className="home-tools">
            {toolboxEntries.map(entry => (
              <Link className="home-tool-link" key={entry.key} to={entry.key}>
                <span className="home-tool-icon" aria-hidden="true">{entry.icon}</span>
                <span className="home-tool-copy"><strong>{entry.title}</strong><span>{entry.desc}</span></span>
                <ArrowRightOutlined className="home-tool-arrow" aria-hidden="true" />
              </Link>
            ))}
          </div>
        </section>
      </div>

      <section className="home-panel home-history" aria-labelledby="home-history-title" aria-busy={historyLoading}>
        <div className="home-history-heading"><h2 id="home-history-title">最近记录</h2>{historyLoading && <span className="home-loading-note" role="status"><Spin size="small" />正在加载记录</span>}</div>
        <p className="home-history-note">智能模式记录持久保存；工作流与诊断仅显示当前进程中尚未过期的 TTL 记录，不等于完整历史。</p>
        {mockHistory && <Alert type="warning" showIcon message="演示数据" description="当前最近记录来自显式 Mock 模式，不是真实任务历史。" />}
        {failedSources.map(({ name, query }) => (
          <Alert key={name} type="warning" showIcon message={`${name}历史暂不可用`}
            description="其他数据源的记录仍会正常显示。"
            action={<Button size="small" loading={query.isFetching} onClick={() => void query.refetch()} aria-label={`重试${name}历史`}>重试</Button>} />
        ))}
        {recentSessions.length > 0 ? (
          <ul className="home-records">
            {recentSessions.map(item => (
              <li key={`${item.kind}:${item.id}`}>
                <Link className="home-record-link" to={historyRecordPath(item)} aria-label={item.title}>
                  <span className="home-record-kind"><Tag color={HISTORY_LABELS[item.kind].color}>{HISTORY_LABELS[item.kind].label}</Tag></span>
                  <span className="home-record-content">
                    <strong>{item.title}</strong>
                    {item.project_name && <span className="home-record-project">{item.project_name}</span>}
                    <span className="home-record-status">
                      <Tag>{item.status}</Tag>
                      {item.execution_mode && <Tag color={item.execution_mode === 'Real' ? 'green' : item.execution_mode === 'Fake' ? 'gold' : 'default'}>{item.execution_mode}</Tag>}
                      {item.demo && <Tag color="warning">演示</Tag>}
                    </span>
                  </span>
                  <time dateTime={item.updated_at}>{formatTime(item.updated_at)}</time>
                  <ArrowRightOutlined className="home-record-arrow" aria-hidden="true" />
                </Link>
              </li>
            ))}
          </ul>
        ) : !historyLoading && (
          <p className="home-history-empty" role="status">{failedSources.length === historySources.length ? '历史服务暂不可用' : failedSources.length > 0 ? '已加载的数据源暂无记录；部分历史暂不可用' : '暂无可显示的真实记录'}</p>
        )}
      </section>
    </div>
  );
};

export default HomePage;
