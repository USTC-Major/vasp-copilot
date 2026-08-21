// ============================================================
// EmptyState — 空状态占位组件
// ============================================================

import React from 'react';
import { Empty, Button } from 'antd';
import { useNavigate } from 'react-router-dom';

interface EmptyStateProps {
  title?: string;
  description?: string;
  actionText?: string;
  actionLink?: string;
  actionOnClick?: () => void;
  icon?: React.ReactNode;
  image?: string;
}

const EmptyState: React.FC<EmptyStateProps> = ({
  title = '暂无数据',
  description,
  actionText,
  actionLink,
  actionOnClick,
  icon,
  image,
}) => {
  const navigate = useNavigate();

  const handleAction = () => {
    if (actionOnClick) actionOnClick();
    else if (actionLink) navigate(actionLink);
  };

  return (
    <Empty
      image={image || Empty.PRESENTED_IMAGE_SIMPLE}
      description={
        <div>
          <div style={{ fontSize: 16, fontWeight: 500, marginBottom: 4 }}>{title}</div>
          {description && (
            <div style={{ color: '#999', fontSize: 14 }}>{description}</div>
          )}
        </div>
      }
    >
      {actionText && (
        <Button type="primary" onClick={handleAction}>
          {icon}
          {actionText}
        </Button>
      )}
    </Empty>
  );
};

export default EmptyState;