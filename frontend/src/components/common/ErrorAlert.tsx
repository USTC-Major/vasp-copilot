// ============================================================
// ErrorAlert — 统一错误展示，解析 error.code/message/retryable/field_errors
// ============================================================

import React from 'react';
import { Alert, Button, Space, List } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';

interface ErrorAlertProps {
  error: Error | { code: string; message: string; retryable?: boolean; field_errors?: { field: string; code: string; message: string }[] };
  onRetry?: () => void;
  title?: string;
}

const ErrorAlert: React.FC<ErrorAlertProps> = ({ error, onRetry, title }) => {
  const message = error instanceof Error ? error.message : error.message;
  const isRetryable = 'retryable' in error ? error.retryable : true;
  const fieldErrors = 'field_errors' in error ? (error.field_errors || []) : [];

  return (
    <Alert
      type="error"
      showIcon
      message={title || '操作失败'}
      description={
        <Space direction="vertical" style={{ width: '100%' }}>
          <div>
            {('code' in error) && (
              <span style={{ fontSize: 12, color: '#999', marginRight: 8 }}>
                [{error.code}]
              </span>
            )}
            {message}
          </div>
          {fieldErrors.length > 0 && (
            <List
              size="small"
              dataSource={fieldErrors}
              renderItem={(fe) => (
                <List.Item style={{ padding: '2px 0' }}>
                  <span style={{ color: '#ff4d4f' }}>{fe.field}: </span>
                  <span>{fe.message}</span>
                  {fe.code && <span style={{ color: '#999', marginLeft: 4 }}>({fe.code})</span>}
                </List.Item>
              )}
            />
          )}
        </Space>
      }
      action={
        onRetry && isRetryable ? (
          <Button size="small" icon={<ReloadOutlined />} onClick={onRetry}>
            重试
          </Button>
        ) : undefined
      }
    />
  );
};

export default ErrorAlert;