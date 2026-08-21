// ============================================================
// RecipeBadge — 显示 recipe_id/version/test_status
// ============================================================

import React from 'react';
import { Tag, Tooltip } from 'antd';
import { ExperimentOutlined, CheckCircleOutlined, ExclamationCircleOutlined } from '@ant-design/icons';
import type { RecipeStatus } from '../../types/enums';
import StatusBadge from '../common/StatusBadge';

interface RecipeBadgeProps {
  recipeId: string;
  version: string;
  kind?: string;
  recipeStatus?: RecipeStatus;
  testStatus?: string;
  selectionReason?: string;
}

const RecipeBadge: React.FC<RecipeBadgeProps> = ({
  recipeId,
  version,
  kind,
  recipeStatus,
  testStatus,
  selectionReason,
}) => {
  const content = (
    <span>
      <ExperimentOutlined style={{ marginRight: 4 }} />
      <strong>{recipeId}</strong>
      <span style={{ margin: '0 4px', color: '#999' }}>v{version}</span>
      {kind && <Tag style={{ marginLeft: 4 }}>{kind}</Tag>}
      {recipeStatus && <StatusBadge status={recipeStatus} type="recipe" />}
      {testStatus === 'passed' ? (
        <CheckCircleOutlined style={{ color: '#52c41a', marginLeft: 4 }} />
      ) : testStatus ? (
        <ExclamationCircleOutlined style={{ color: '#faad14', marginLeft: 4 }} />
      ) : null}
    </span>
  );

  if (selectionReason) {
    return (
      <Tooltip title={selectionReason}>
        <div style={{ marginBottom: 4 }}>{content}</div>
      </Tooltip>
    );
  }

  return <div style={{ marginBottom: 4 }}>{content}</div>;
};

export default RecipeBadge;