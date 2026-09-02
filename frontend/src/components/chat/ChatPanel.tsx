// ============================================================
// ChatPanel — AI 助手浮动对话面板（独立运行，无需 Codex）
// 用法：项目自带后端 + 前端即可在浏览器里直接与配置的大模型对话。
// 增强：Markdown 渲染、清空对话、历史后端持久化（防抖保存）。
// ============================================================

import React, { useEffect, useRef, useState } from 'react';
import { Button, Input, Popconfirm, Spin, Tag, Typography } from 'antd';
import {
  CloseOutlined,
  DeleteOutlined,
  MessageOutlined,
  RobotOutlined,
  SendOutlined,
} from '@ant-design/icons';
import {
  useChatHistory,
  useChatHistoryClear,
  useChatHistorySave,
  useChatSend,
  useLlmConfig,
} from '../../hooks/useApi';
import { renderMarkdown } from '../../utils/markdown';
import type { ChatMessageItem } from '../../api/client';

const { Text, Paragraph } = Typography;
const { TextArea } = Input;

const STORAGE_KEY = 'vasp-ai-chat-history';

const FAB_POS_KEY = 'vasp-ai-assistant-fab-pos';
const PANEL_POS_KEY = 'vasp-ai-assistant-panel-pos';

interface Pos {
  left: number;
  top: number;
}

function clampPos(pos: Pos, w: number, h: number): Pos {
  const margin = 8;
  const vw = Math.max(margin, window.innerWidth);
  const vh = Math.max(margin, window.innerHeight);
  return {
    left: Math.min(Math.max(margin, pos.left), vw - w - margin),
    top: Math.min(Math.max(margin, pos.top), vh - h - margin),
  };
}

function loadPos(key: string, fallback: () => Pos): Pos {
  try {
    const raw = localStorage.getItem(key);
    if (raw) {
      const parsed: unknown = JSON.parse(raw);
      const p = parsed as Pos;
      if (typeof p.left === 'number' && typeof p.top === 'number') {
        return p;
      }
    }
  } catch {
    // ignore corrupted position
  }
  return fallback();
}

const FAB_SIZE = 52;
const PANEL_WIDTH = 380;
const PANEL_HEIGHT = 560;

function defaultFabPos(): Pos {
  return {
    left: Math.max(8, window.innerWidth - 24 - FAB_SIZE),
    top: Math.max(8, window.innerHeight - 24 - FAB_SIZE),
  };
}

function defaultPanelPos(): Pos {
  return {
    left: Math.max(8, window.innerWidth - 24 - PANEL_WIDTH),
    top: Math.max(8, window.innerHeight - 24 - FAB_SIZE - 12 - PANEL_HEIGHT),
  };
}
interface ChatPanelProps {
  onOpenSettings: () => void;
}

function loadHistory(): ChatMessageItem[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (Array.isArray(parsed)) {
      return (parsed as ChatMessageItem[]).filter(
        (m) =>
          m &&
          (m.role === 'user' || m.role === 'assistant') &&
          typeof m.content === 'string',
      );
    }
  } catch {
    // ignore corrupted history
  }
  return [];
}

const PANEL_STYLE: React.CSSProperties = {
  position: 'fixed',
  right: 24,
  bottom: 88,
  zIndex: 999,
  width: 380,
  maxWidth: 'calc(100vw - 48px)',
  height: 560,
  maxHeight: 'calc(100vh - 160px)',
  display: 'flex',
  flexDirection: 'column',
  background: '#fff',
  borderRadius: 12,
  boxShadow: '0 6px 24px rgba(0,0,0,0.18)',
  overflow: 'hidden',
};

const FAB_STYLE: React.CSSProperties = {
  position: 'fixed',
  zIndex: 999,
  width: 52,
  height: 52,
  borderRadius: '50%',
  fontSize: 20,
  userSelect: 'none',
  touchAction: 'none',
};

const ChatPanel: React.FC<ChatPanelProps> = ({ onOpenSettings }) => {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<ChatMessageItem[]>(loadHistory);
  const [input, setInput] = useState('');

  const [fabPos, setFabPos] = useState<Pos>(() => loadPos(FAB_POS_KEY, defaultFabPos));
  const [panelPos, setPanelPos] = useState<Pos>(() => loadPos(PANEL_POS_KEY, defaultPanelPos));
  const dragRef = useRef<{
    key: 'fab' | 'panel';
    startX: number;
    startY: number;
    orig: Pos;
    w: number;
    h: number;
  } | null>(null);
  const suppressClickRef = useRef(false);

  useEffect(() => {
    try {
      localStorage.setItem(FAB_POS_KEY, JSON.stringify(fabPos));
    } catch { /* ignore */ }
  }, [fabPos]);

  useEffect(() => {
    try {
      localStorage.setItem(PANEL_POS_KEY, JSON.stringify(panelPos));
    } catch { /* ignore */ }
  }, [panelPos]);

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      const drag = dragRef.current;
      if (!drag) return;
      const next = clampPos(
        {
          left: drag.orig.left + e.clientX - drag.startX,
          top: drag.orig.top + e.clientY - drag.startY,
        },
        drag.w,
        drag.h,
      );
      if (drag.key === 'fab') setFabPos(next);
      else setPanelPos(next);
    };
    const onUp = (e: MouseEvent) => {
      const drag = dragRef.current;
      if (!drag) return;
      const dx = e.clientX - drag.startX;
      const dy = e.clientY - drag.startY;
      if (dx * dx + dy * dy > 36) suppressClickRef.current = true;
      dragRef.current = null;
    };
    const onResize = () => {
      setFabPos((p) => clampPos(p, FAB_SIZE, FAB_SIZE));
      setPanelPos((p) => clampPos(p, PANEL_WIDTH, PANEL_HEIGHT));
    };
    window.addEventListener('mousemove', onMove as EventListener);
    window.addEventListener('mouseup', onUp);
    window.addEventListener('resize', onResize);
    return () => {
      window.removeEventListener('mousemove', onMove as EventListener);
      window.removeEventListener('mouseup', onUp);
      window.removeEventListener('resize', onResize);
    };
  }, []);

  const startDrag = (key: 'fab' | 'panel', e: React.MouseEvent) => {
    if (e.button !== 0) return;
    e.preventDefault();
    suppressClickRef.current = false;
    const pos = key === 'fab' ? fabPos : panelPos;
    dragRef.current = {
      key,
      startX: e.clientX,
      startY: e.clientY,
      orig: pos,
      w: key === 'fab' ? FAB_SIZE : PANEL_WIDTH,
      h: key === 'fab' ? FAB_SIZE : PANEL_HEIGHT,
    };
  };
  const listRef = useRef<HTMLDivElement>(null);
  const saveTimer = useRef<number | undefined>(undefined);
  const { data: config, isLoading: configLoading } = useLlmConfig(open);
  const chatSend = useChatSend();
  const historyQuery = useChatHistory(open);
  const historySave = useChatHistorySave();
  const historyClear = useChatHistoryClear();

  const usable = Boolean(config?.usable);

  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, open, chatSend.isPending]);

  // 打开面板时拉取后端持久化历史；获取失败时保留 localStorage 兜底
  useEffect(() => {
    if (!open || !historyQuery.data) return;
    const fetched = Array.isArray(historyQuery.data.messages)
      ? historyQuery.data.messages.filter(
          (m) =>
            m &&
            (m.role === 'user' || m.role === 'assistant') &&
            typeof m.content === 'string',
        )
      : [];
    if (fetched.length > 0) setMessages(fetched);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, historyQuery.data]);

  // 本地缓存（跨网络兜底）
  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(messages));
    } catch {
      // ignore quota errors
    }
  }, [messages]);

  // 对话变化防抖 800ms 保存到后端
  useEffect(() => {
    if (messages.length === 0) return;
    if (saveTimer.current != null) window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(() => {
      saveTimer.current = undefined;
      const toSave = messages
        .filter((m) => !(m.role === 'assistant' && m.content === ''))
        .slice(-200);
      if (toSave.length > 0) historySave.mutate(toSave);
    }, 800);
    return () => {
      if (saveTimer.current != null) window.clearTimeout(saveTimer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages]);

  const handleClear = () => {
    setInput('');
    setMessages([]);
    try {
      localStorage.removeItem(STORAGE_KEY);
    } catch {
      // ignore storage errors
    }
    historyClear.mutate();
  };

  const handleSend = async () => {
    const text = input.trim();
    if (!text || chatSend.isPending) return;
    const history = messages.slice(-10);
    setMessages((prev) => [
      ...prev,
      { role: 'user', content: text },
      { role: 'assistant', content: '' },
    ]);
    setInput('');
    try {
      const res = await chatSend.mutateAsync({ message: text, history });
      setMessages((prev) => {
        const copy = [...prev];
        const last = copy[copy.length - 1];
        if (last && last.role === 'assistant') {
          copy[copy.length - 1] = { role: 'assistant', content: res.answer };
        }
        return copy;
      });
    } catch {
      setMessages((prev) => {
        const copy = [...prev];
        const last = copy[copy.length - 1];
        if (last && last.role === 'assistant') {
          copy[copy.length - 1] = {
            role: 'assistant',
            content: '请求失败：请检查后端服务是否已启动，或模型配置是否正确。',
          };
        }
        return copy;
      });
    }
  };

  return (
    <>
      <Button
        type="primary"
        icon={open ? <CloseOutlined /> : <MessageOutlined />}
        style={{ ...FAB_STYLE, left: fabPos.left, top: fabPos.top, cursor: 'grab' }}
        onMouseDown={(e) => startDrag('fab', e)}
        onClick={() => {
          if (suppressClickRef.current) return;
          setOpen((v) => !v);
        }}
        aria-label={open ? '关闭 AI 助手' : '打开 AI 助手'}
      />

      {open && (
        <div style={{ ...PANEL_STYLE, left: panelPos.left, top: panelPos.top }}>
          <div
            onMouseDown={(e) => startDrag('panel', e)}
            style={{
              padding: '12px 16px',
              background: '#001529',
              color: '#fff',
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              cursor: 'move',
            }}
          >
            <RobotOutlined style={{ fontSize: 18 }} />
            <Text strong style={{ color: '#fff', flex: 1 }}>
              AI 助手
            </Text>
            {configLoading ? (
              <Spin size="small" />
            ) : usable ? (
              <Tag color="green" style={{ marginInlineEnd: 0 }}>
                {config?.model || '已启用'}
              </Tag>
            ) : (
              <Tag color="orange" style={{ marginInlineEnd: 0 }}>未启用</Tag>
            )}
            <Popconfirm
              title="清空对话记录？"
              description="将同时清除本地与后端保存的历史。"
              okText="清空"
              cancelText="取消"
              okButtonProps={{ danger: true }}
              onConfirm={handleClear}
            >
              <Button
                type="text"
                size="small"
                icon={<DeleteOutlined />}
                style={{ color: '#fff' }}
                aria-label="清空对话"
              />
            </Popconfirm>
          </div>

          <div
            ref={listRef}
            style={{
              flex: 1,
              overflowY: 'auto',
              padding: 12,
              background: '#f5f5f5',
            }}
          >
            {messages.length === 0 && (
              <div style={{ textAlign: 'center', marginTop: 40 }}>
                <Text type="secondary">
                  在这里输入你的问题（VASP 排错 / 参数建议 / 概念讲解等）。
                </Text>
              </div>
            )}
            {messages.map((m, idx) => {
              const isUser = m.role === 'user';
              const loading = !isUser && m.content === '' && chatSend.isPending;
              return (
                <div
                  key={idx}
                  style={{
                    display: 'flex',
                    justifyContent: isUser ? 'flex-end' : 'flex-start',
                    marginBottom: 10,
                  }}
                >
                  <div
                    style={{
                      maxWidth: '78%',
                      padding: '8px 12px',
                      borderRadius: 10,
                      background: isUser ? '#1677ff' : '#fff',
                      color: isUser ? '#fff' : 'rgba(0,0,0,0.88)',
                      whiteSpace: 'pre-wrap',
                      wordBreak: 'break-word',
                    }}
                  >
                    {loading ? (
                      <Spin size="small" />
                    ) : isUser ? (
                      <Paragraph style={{ margin: 0 }}>{m.content}</Paragraph>
                    ) : (
                      renderMarkdown(m.content)
                    )}
                  </div>
                </div>
              );
            })}
          </div>

          {!configLoading && !usable && (
            <div
              style={{
                padding: '8px 12px',
                background: '#fff7e6',
                borderTop: '1px solid #ffd591',
                display: 'flex',
                alignItems: 'center',
                gap: 8,
              }}
            >
              <Text type="warning" style={{ flex: 1, fontSize: 12 }}>
                未配置可用模型，发送消息将返回提示。
              </Text>
              <Button size="small" onClick={onOpenSettings}>
                去配置
              </Button>
            </div>
          )}

          <div
            style={{
              padding: 10,
              borderTop: '1px solid #f0f0f0',
              display: 'flex',
              alignItems: 'flex-end',
              gap: 8,
              background: '#fff',
            }}
          >
            <TextArea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  void handleSend();
                }
              }}
              placeholder={'输入你的需求，Enter 发送 / Shift+Enter 换行'}
              autoSize={{ minRows: 1, maxRows: 4 }}
              style={{ flex: 1 }}
            />
            <Button
              type="primary"
              icon={<SendOutlined />}
              onClick={() => void handleSend()}
              loading={chatSend.isPending}
              disabled={!input.trim()}
            />
          </div>
        </div>
      )}
    </>
  );
};

export default ChatPanel;