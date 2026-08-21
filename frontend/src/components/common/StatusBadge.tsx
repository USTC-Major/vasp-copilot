// ============================================================
// StatusBadge — 统一状态标签，支持所有分层状态枚举
// ============================================================

import React from 'react';
import { Tag } from 'antd';
import type {
  WorkflowStatus, DiagnosisStatus, HpcJobStatus, DeploymentStatus,
  CheckStatus, ConfirmationStatus, Severity, RecipeStatus,
} from '../../types/enums';
import {
  WORKFLOW_STATUS_MAP, DIAGNOSIS_STATUS_MAP, HPC_JOB_STATUS_MAP,
  DEPLOYMENT_STATUS_MAP, SEVERITY_MAP, RECIPE_STATUS_MAP,
} from '../../types/enums';

type StatusType = WorkflowStatus | DiagnosisStatus | HpcJobStatus | DeploymentStatus | CheckStatus | ConfirmationStatus;

interface StatusBadgeProps {
  status: StatusType | Severity | RecipeStatus;
  type?: 'workflow' | 'diagnosis' | 'hpc_job' | 'deployment' | 'severity' | 'recipe';
}

const STATUS_MAPS: Record<string, Record<string, { color: string; label: string }>> = {
  workflow: WORKFLOW_STATUS_MAP,
  diagnosis: DIAGNOSIS_STATUS_MAP,
  hpc_job: HPC_JOB_STATUS_MAP,
  deployment: DEPLOYMENT_STATUS_MAP,
  severity: SEVERITY_MAP,
  recipe: RECIPE_STATUS_MAP,
};

const StatusBadge: React.FC<StatusBadgeProps> = ({ status, type = 'workflow' }) => {
  const map = STATUS_MAPS[type];
  const config = map?.[status] || { color: 'default', label: status };

  // unknown 状态使用警示样式
  const isUnknown = status === 'unknown';
  const tagColor = isUnknown ? 'warning' : config.color;

  return (
    <Tag color={tagColor} style={isUnknown ? { borderStyle: 'dashed' } : undefined}>
      {isUnknown ? `⚠ ${config.label}` : config.label}
    </Tag>
  );
};

export default StatusBadge;