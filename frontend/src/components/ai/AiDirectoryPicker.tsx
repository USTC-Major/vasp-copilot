// ============================================================
// AiDirectoryPicker — 工作区目录图形化浏览点选（M032 初版 / M33 增强）
// 本地/超算通用：起点视图 -> 逐层进入文件夹 -> 「选择当前文件夹」回填。
// 增强：隐藏/系统/无权目录已由后端过滤；支持「新建文件夹」后即时刷新；
// 交互与视觉对齐工具箱「上传结构文件」弹窗（大图标 + 说明 + 干净列表）。
// ============================================================

import React, { useCallback, useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { Modal, List, Button, Spin, Alert, Typography, Empty, Input, Tag } from 'antd';
import {
  FolderFilled,
  FolderOpenFilled,
  ArrowLeftOutlined,
  CheckCircleOutlined,
  ReloadOutlined,
  CloudServerOutlined,
  HddOutlined,
  PlusOutlined,
} from '@ant-design/icons';
import { aiApi } from '../../api/client';
import type { AiBrowseEntry } from '../../types/ai';

const { Text } = Typography;

interface Props {
  open: boolean;
  kind: 'local' | 'hpc';
  initialPath: string;
  onSelect: (path: string) => void;
  onCancel: () => void;
}

const KIND_TITLE: Record<'local' | 'hpc', string> = {
  local: '选择本地工作区目录',
  hpc: '选择超算工作区目录',
};

const KIND_HINT: Record<'local' | 'hpc', string> = {
  local: '单击文件夹选中，双击进入；选定后点击「选择此文件夹」。',
  hpc: '经 SSH 列出超算目录，单击选中、双击进入；选定后点击「选择此文件夹」。',
};

const joinPath = (kind: 'local' | 'hpc', base: string, name: string) => {
  const sep = kind === 'local' ? '\\' : '/';
  return base.endsWith(sep) ? `${base}${name}` : `${base}${sep}${name}`;
};

const AiDirectoryPicker: React.FC<Props> = ({ open, kind, initialPath, onSelect, onCancel }) => {
  const [path, setPath] = useState<string | null>(null); // null = 起点视图
  const [parent, setParent] = useState<string | null>(null);
  const [entries, setEntries] = useState<AiBrowseEntry[]>([]);
  const [roots, setRoots] = useState<AiBrowseEntry[]>([]);
  const [notice, setNotice] = useState('');
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState('');
  const [selectedName, setSelectedName] = useState<string | null>(null);

  const browse = useCallback(
    async (p: string | undefined) => {
      if (kind === 'local') return p ? aiApi.browseLocal(p) : aiApi.browseLocal('');
      return p ? aiApi.browseHpc(p) : aiApi.browseHpc('');
    },
    [kind],
  );

  const loadPath = useCallback(
    async (p: string | undefined) => {
      setLoading(true);
      setNotice('');
      setSelectedName(null);
      try {
        const r = await browse(p);
        if (p) {
          setPath(r.path ?? p);
          setParent(r.parent ?? null);
          setEntries((r.entries ?? []).filter((e) => e.is_dir && !e.name.startsWith('.')));
          setRoots([]);
          if (r.notice) setNotice(r.notice);
        } else {
          setPath(null);
          setParent(null);
          setEntries([]);
          setRoots(r.roots ?? []);
          if (r.notice) setNotice(r.notice);
        }
      } catch (err) {
        setPath(null);
        setParent(null);
        setEntries([]);
        setRoots([]);
        setNotice(err instanceof Error ? err.message : '无法浏览该目录');
      } finally {
        setLoading(false);
      }
    },
    [browse],
  );

  useEffect(() => {
    if (open) {
      const start = initialPath && initialPath.trim() ? initialPath.trim() : undefined;
      setCreating(false);
      setNewName('');
      void loadPath(start);
    }
  }, [open, initialPath, loadPath]);

  const createFolder = useCallback(async () => {
    const name = newName.trim();
    if (!name || path === null) return;
    setCreating(true);
    setNotice('');
    try {
      const r = kind === 'local'
        ? await aiApi.mkdirLocal(path, name)
        : await aiApi.mkdirHpc(path, name);
      if (r.ok) {
        setNewName('');
        setCreating(false);
        await loadPath(path);
      } else {
        setCreating(false);
        setNotice(r.notice || '新建文件夹失败');
      }
    } catch (err) {
      setCreating(false);
      setNotice(err instanceof Error ? err.message : '新建文件夹失败');
    }
  }, [newName, path, kind, loadPath]);

  const dirs = entries; // 后端已过滤隐藏/无权目录；此处仅展示目录
  const inFolder = path !== null;
  const rootName = (name: string) => {
    if (kind === 'hpc' && name === '/') return '超算根目录 /';
    if (kind === 'local' && /^[A-Za-z]:\\?$/.test(name)) return `本机磁盘 ${name}`;
    return name;
  };
  const actionBtn = (label: string, icon: ReactNode, onClick: () => void, disabled = false) => (
    <Button size="small" icon={icon} onClick={onClick} disabled={disabled}>
      {label}
    </Button>
  );

  return (
    <Modal
      title={KIND_TITLE[kind]}
      open={open}
      onCancel={onCancel}
      width={600}
      footer={[
        <Button key="cancel" onClick={onCancel}>取消</Button>,
        <Button
          key="ok"
          type="primary"
          disabled={!inFolder}
          icon={<CheckCircleOutlined />}
          onClick={() => {
            if (!inFolder) return;
            if (selectedName) {
              onSelect(joinPath(kind, path, selectedName));
            } else {
              onSelect(path);
            }
          }}
        >
          {selectedName ? '选择此文件夹' : '选择当前文件夹'}
        </Button>,
      ]}
    >
      {/* 顶部说明区——风格对齐「上传结构文件」弹窗 */}
      <div style={{
        border: '1px dashed #d0d7de',
        borderRadius: 8,
        background: '#fafbfc',
        padding: '18px 14px',
        textAlign: 'center',
        marginBottom: 12,
      }}>
        <FolderOpenFilled style={{ fontSize: 40, color: '#0071e3' }} />
        <div style={{ marginTop: 6 }}>
          <Text strong style={{ fontSize: 15 }}>
            {inFolder ? path : kind === 'local' ? '本机磁盘 / 用户主目录' : '超算根目录 / 主目录'}
          </Text>
        </div>
        <Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 4 }}>
          {inFolder ? KIND_HINT[kind] : '从下方起始位置进入文件夹'}
        </Text>
      </div>

      {/* 工具栏 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10, flexWrap: 'wrap' }}>
        <Tag icon={kind === 'hpc' ? <CloudServerOutlined /> : <HddOutlined />} color={kind === 'hpc' ? 'purple' : 'geekblue'} style={{ margin: 0 }}>
          {kind === 'hpc' ? '超算（SSH）' : '本机'}
        </Tag>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
          <Button
            size="small"
            type="primary"
            ghost
            icon={<PlusOutlined />}
            disabled={!inFolder || creating || loading}
            onClick={() => { setNotice(''); setCreating(true); }}
          >
            新建文件夹
          </Button>
          {actionBtn('上一级', <ArrowLeftOutlined />, () => void loadPath(parent ?? undefined), !parent || loading)}
          {actionBtn('刷新', <ReloadOutlined />, () => void loadPath(path ?? undefined), loading)}
        </div>
      </div>

      {/* 新建文件夹输入行 */}
      {creating && (
        <div style={{ display: 'flex', gap: 8, marginBottom: 10 }}>
          <Input
            placeholder="输入新文件夹名称"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            onPressEnter={() => void createFolder()}
            allowClear
            autoFocus
            maxLength={120}
            style={{ flex: 1 }}
          />
          <Button type="primary" onClick={() => void createFolder()}>确定</Button>
          <Button onClick={() => { setCreating(false); setNewName(''); }}>取消</Button>
        </div>
      )}

      {notice && <Alert type="warning" showIcon closable message={notice} style={{ marginBottom: 10 }} />}

      <div style={{ maxHeight: 320, overflowY: 'auto', border: '1px solid rgba(0,0,0,0.08)', borderRadius: 8 }}>
        {loading ? (
          <div style={{ padding: 32, textAlign: 'center' }}><Spin /></div>
        ) : !inFolder ? (
          roots.length === 0 ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="无法获取起始位置" style={{ padding: 24 }} />
          ) : (
            <List
              size="small"
              dataSource={roots}
              renderItem={(r) => (
                <List.Item
                  style={{ cursor: 'pointer', background: selectedName === r.name ? '#e6f4ff' : undefined }}
                  onClick={() => setSelectedName(r.name)}
                  onDoubleClick={() => void loadPath(r.name)}
                >
                  <FolderFilled style={{ color: '#f0a020', marginRight: 8 }} />
                  <span>{rootName(r.name)}</span>
                </List.Item>
              )}
            />
          )
        ) : dirs.length === 0 ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="此目录下没有可用的子文件夹" style={{ padding: 24 }} />
        ) : (
          <List
            size="small"
            dataSource={dirs}
            renderItem={(d) => (
              <List.Item
                style={{ cursor: 'pointer', background: selectedName === d.name ? '#e6f4ff' : undefined }}
                onClick={() => setSelectedName(d.name)}
                onDoubleClick={() => void loadPath(joinPath(kind, path, d.name))}
              >
                <FolderFilled style={{ color: '#f0a020', marginRight: 8 }} />
                <span>{d.name}</span>
              </List.Item>
            )}
          />
        )}
      </div>

      <Text type="secondary" style={{ display: 'block', marginTop: 8, fontSize: 12 }}>
        单击文件夹选中（可在「选择此文件夹」直接确认路径），双击进入。可先「新建文件夹」再使用。
      </Text>
    </Modal>
  );
};

export default AiDirectoryPicker;