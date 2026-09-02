// ============================================================
// AiJobTimeline — AI 全流程六阶段可视化（含当前状态与失败分支）
// ============================================================

import React from 'react';
import { Steps } from 'antd';
import type { AiJobStatus } from '../../types/ai';
import { AI_JOB_STATUS_MAP, AI_JOB_FLOW } from '../../types/ai';

const FLOW_LABELS: Record<string, string> = {
  planned: '计划',
  generated: '生成包',
  submitted: '已提交',
  collected: '已回收',
  inspecting: '质检中',
  done: '完成',
};

const AiJobTimeline: React.FC<{ status: AiJobStatus }> = ({ status }) => {
  const isFailed = status === 'failed';
  const currentIndex = isFailed ? -1 : AI_JOB_FLOW.indexOf(status);

  const items = AI_JOB_FLOW.map((s, i) => {
    let stepStatus: 'wait' | 'process' | 'finish' | 'error' = 'wait';
    if (!isFailed) {
      if (currentIndex > i) stepStatus = 'finish';
      else if (currentIndex === i) stepStatus = 'process';
      else stepStatus = 'wait';
    }
    return {
      title: FLOW_LABELS[s],
      description: AI_JOB_STATUS_MAP[s].label,
      status: stepStatus,
    };
  });

  return (
    <div style={{ padding: '8px 0 12px' }}>
      <Steps size="small" current={isFailed ? -1 : currentIndex} items={items} />
    </div>
  );
};

export default AiJobTimeline;
