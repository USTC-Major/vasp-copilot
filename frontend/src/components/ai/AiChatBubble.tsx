import React from 'react';
import { Avatar } from 'antd';
import { UserOutlined, RobotOutlined } from '@ant-design/icons';

interface AiChatBubbleProps {
  role: 'user' | 'assistant';
  name?: string;
  children: React.ReactNode;
}

const AiChatBubble: React.FC<AiChatBubbleProps> = ({ role, name, children }) => (
  <div className="ai-chat-message" data-message-role={role}>
    <Avatar size={30} icon={role === 'user' ? <UserOutlined /> : <RobotOutlined />} className="ai-chat-avatar" />
    <div className="ai-chat-message-body">
      {name && <div className="ai-chat-message-name">{name}</div>}
      <div className="ai-chat-bubble">{children}</div>
    </div>
  </div>
);

export default AiChatBubble;
