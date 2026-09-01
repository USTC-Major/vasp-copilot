// ============================================================
// AiChatBubble — Codex 式对话气泡容器（user / assistant）
// 纯可视化容器：用户消息靠右蓝气泡，AI 消息靠左灰容器承载面板。
// ============================================================

import React from 'react';
import { Avatar, Typography } from 'antd';
import { UserOutlined, RobotOutlined } from '@ant-design/icons';

const { Text } = Typography;

interface AiChatBubbleProps {
  role: 'user' | 'assistant';
  name?: string;
  children: React.ReactNode;
}

const AiChatBubble: React.FC<AiChatBubbleProps> = ({ role, name, children }) => {
  const isUser = role === 'user';
  return (
    <div
      style={{
        display: 'flex',
        marginBottom: 16,
        justifyContent: isUser ? 'flex-end' : 'flex-start',
        alignItems: 'flex-start',
      }}
    >
      {!isUser && (
        <Avatar
          size={34}
          icon={<RobotOutlined />}
          style={{ backgroundColor: '#0071e3', marginRight: 10, flexShrink: 0 }}
        />
      )}
      <div style={{ maxWidth: '84%' }}>
        {!isUser && name && (
          <Text strong style={{ display: 'block', fontSize: 12, marginBottom: 4, color: '#6e6e73' }}>
            {name}
          </Text>
        )}
        {isUser ? (
          <div
            style={{
              background: 'linear-gradient(135deg, #0071e3, #0a84ff)',
              color: '#fff',
              padding: '10px 14px',
              borderRadius: 16,
              borderTopRightRadius: 4,
              fontSize: 14,
              lineHeight: 1.6,
              boxShadow: '0 2px 8px rgba(10,132,255,0.25)',
              whiteSpace: 'pre-wrap',
            }}
          >
            {children}
          </div>
        ) : (
          <div
            style={{
              background: '#f5f5f7',
              borderRadius: 16,
              padding: 14,
              border: '1px solid rgba(0,0,0,0.05)',
            }}
          >
            {children}
          </div>
        )}
      </div>
      {isUser && (
        <Avatar
          size={34}
          icon={<UserOutlined />}
          style={{ backgroundColor: '#c7c7cc', marginLeft: 10, flexShrink: 0 }}
        />
      )}
    </div>
  );
};

export default AiChatBubble;
