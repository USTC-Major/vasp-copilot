import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Button, Card, Descriptions, Input, InputNumber, List, Select, Space, Tag, Typography, message } from 'antd';
import { useLocation } from 'react-router-dom';
import { toolboxApi } from '../../api/client';
import { useToolboxFileAction, useToolboxTaskDetail } from '../../hooks/useApi';
import type { ToolboxFileAction, ToolboxFileItemInput, ToolboxFileOperation, ToolboxFileScope, ToolboxFileRoot } from '../../types/toolbox';
import AiDirectoryPicker from '../ai/AiDirectoryPicker';

const { Text, Paragraph } = Typography;
const operations: { value: ToolboxFileOperation; label: string }[] = [
  { value: 'copy', label: '远端复制' }, { value: 'symlink', label: '建立软链接' },
  { value: 'write_text', label: '写入文本' }, { value: 'mkdir', label: '创建目录' },
];
const opLabel = (op: string) => operations.find((item) => item.value === op)?.label ?? op;
const stateLabels: Record<string, string> = {
  pending: '待人工确认', approved: '已批准，等待执行', executing: '执行中', executed: '文件准备完成',
  rejected: '已拒绝', expired: '已过期', failed: '部分或全部失败', unknown: '结果待核实',
  proposed: '待首次确认', active: '已激活', revoked: '已撤销',
  queued: '排队中', connecting: '连接中', preparing: '准备文件中', prepared: '已准备，等待发布',
  committing: '发布中', finished: '执行已结束', committed: '已发布', not_executed: '已证明未执行',
};
const stateLabel = (value: string | undefined) => value ? stateLabels[value] ?? value : '尚无回执';
const errorText = (error: unknown) => {
  if (error && typeof error === 'object' && 'code' in error && 'message' in error) {
    return `[${String(error.code)}] ${String(error.message)}`;
  }
  return error instanceof Error ? error.message : '请求失败';
};
const field = (record: Record<string, unknown> | null | undefined, key: string) => {
  const value = record?.[key];
  return value === null || value === undefined ? '' : String(value);
};
const newKey = () => `ui-${Date.now()}-${Math.random().toString(36).slice(2)}`;
const expiresDefault = () => {
  const date = new Date(Date.now() + 60 * 60_000);
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
};

export interface ToolboxFilesPanelProps { projectId: string; taskId: string }

const ToolboxFilesPanel: React.FC<ToolboxFilesPanelProps> = ({ projectId, taskId }) => {
  const location = useLocation();
  const detailQuery = useToolboxTaskDetail(projectId, taskId);
  const detail = detailQuery.data;
  const jobs = useMemo(() => detail?.flow.jobs ?? [], [detail?.flow.jobs]);
  const roots = useMemo(() => detail?.file_roots ?? [], [detail?.file_roots]);
  const scopes = useMemo(() => detail?.file_scopes ?? [], [detail?.file_scopes]);
  const actions = useMemo(() => detail?.file_actions ?? [], [detail?.file_actions]);
  const [jobKey, setJobKey] = useState('');
  const job = jobs.find((item) => item.key === jobKey);
  const [rootPath, setRootPath] = useState('');
  const [pickerOpen, setPickerOpen] = useState(false);
  const [scopeRootIds, setScopeRootIds] = useState<string[]>([]);
  const [prefix, setPrefix] = useState('');
  const [scopeOps, setScopeOps] = useState<ToolboxFileOperation[]>(['copy']);
  const [sources, setSources] = useState('');
  const [maxOps, setMaxOps] = useState(1);
  const [maxBytes, setMaxBytes] = useState(4096);
  const [expiresAt, setExpiresAt] = useState(expiresDefault);
  const [scopeId, setScopeId] = useState('');
  const scope = scopes.find((item) => item.scope_id === scopeId && item.job_key === jobKey && item.attempt_id === job?.attempt_id);
  const [itemOp, setItemOp] = useState<ToolboxFileOperation>('copy');
  const [itemRootId, setItemRootId] = useState('');
  const [destination, setDestination] = useState('');
  const [source, setSource] = useState('');
  const [text, setText] = useState('');
  const [items, setItems] = useState<ToolboxFileItemInput[]>([]);
  const [key, setKey] = useState(newKey);
  const [selectedActionId, setSelectedActionId] = useState<string | null>(null);
  const selectedActionQuery = useToolboxFileAction(projectId, taskId, selectedActionId);
  const selectedSummary = actions.find((item) => item.action_id === selectedActionId);
  const fullAction = selectedActionQuery.data?.card;
  const linkedScope = scopes.find((item) => item.scope_id === fullAction?.binding.scope_id);
  const action: ToolboxFileAction | undefined = fullAction
    ? { ...fullAction, state: selectedSummary?.state ?? fullAction.state, receipt: selectedSummary?.receipt ?? fullAction.receipt, result: selectedSummary?.result ?? fullAction.result }
    : selectedSummary;
  const [history, setHistory] = useState<ToolboxFileAction[]>([]);
  const [cursor, setCursor] = useState<string | null | undefined>(undefined);
  const [notice, setNotice] = useState('');
  const context = `${projectId}\u0000${taskId}\u0000${jobKey}\u0000${job?.attempt_id ?? ''}\u0000${scopeId}\u0000${selectedActionId ?? ''}`;
  const contextRef = useRef({ key: context, generation: 0 });
  if (contextRef.current.key !== context) contextRef.current = { key: context, generation: contextRef.current.generation + 1 };
  const requestSerialRef = useRef(0);
  const [busyToken, setBusyToken] = useState<{ generation: number; id: number } | null>(null);
  const busy = busyToken?.generation === contextRef.current.generation;
  const itemsRef = useRef(items);
  itemsRef.current = items;
  const previousIdentityRef = useRef<{ route: string; identity: string } | null>(null);

  const identity = jobs.map((item) => `${item.key}:${item.attempt_id ?? ''}`).join('|');
  useEffect(() => {
    const route = `${projectId}\u0000${taskId}`;
    const previous = previousIdentityRef.current;
    const firstForRoute = !previous || previous.route !== route;
    const firstLoadedIdentity = previous?.route === route && previous.identity === '' && identity !== '';
    const deepLinkId = new URLSearchParams(location.search).get('fileAction');
    setJobKey(jobs.length === 1 ? jobs[0].key : ''); setScopeId(''); setItems([]);
    setSelectedActionId(firstForRoute || firstLoadedIdentity ? deepLinkId : null);
    setHistory([]); setCursor(undefined); setNotice('');
    previousIdentityRef.current = { route, identity };
  // Job identities, rather than task timestamps, govern local authorization drafts.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, taskId, identity]);
  useEffect(() => {
    const id = new URLSearchParams(location.search).get('fileAction');
    if (id) setSelectedActionId(id);
  }, [location.search, projectId, taskId]);
  useEffect(() => {
    if (scope && !scope.allowed_operations.includes(itemOp)) setItemOp(scope.allowed_operations[0]);
    if (scope && !scope.root_bindings.some((binding) => binding.root_id === itemRootId)) setItemRootId(scope.root_bindings[0]?.root_id ?? '');
  }, [scope, itemOp, itemRootId]);
  useEffect(() => {
    setScopeRootIds((current) => {
      const retained = current.filter((id) => roots.some((root) => root.root_id === id));
      return retained.length === current.length ? current : retained;
    });
  }, [roots]);

  const run = async <T,>(operation: () => Promise<T>, success: string, uncertain = false): Promise<T | null> => {
    const startedIn = contextRef.current.generation;
    const token = { generation: startedIn, id: ++requestSerialRef.current };
    setBusyToken(token); setNotice('');
    try {
      const result = await operation();
      if (contextRef.current.generation !== startedIn) return null;
      await detailQuery.refetch();
      if (contextRef.current.generation !== startedIn) return null;
      setNotice(success);
      message.success(success);
      return result;
    } catch (error) {
      if (contextRef.current.generation !== startedIn) return null;
      const reason = errorText(error);
      await detailQuery.refetch();
      if (contextRef.current.generation !== startedIn) return null;
      if (selectedActionId) await selectedActionQuery.refetch();
      if (contextRef.current.generation !== startedIn) return null;
      setNotice(uncertain ? `${reason}；请先读取原卡和任务状态，勿自动重发文件请求。` : reason);
      message.error(reason);
      return null;
    } finally { setBusyToken((current) => current?.id === token.id ? null : current); }
  };

  const registerRoot = async () => {
    const path = rootPath.trim();
    if (!path.startsWith('/')) { setNotice('研究根须为远端绝对路径。'); return; }
    await run(() => toolboxApi.setFileRoots(projectId, taskId, detail?.file_roots_version ?? 0,
      [...roots.map((root) => ({ root_id: root.root_id, path: root.requested_path })), { path }]), '研究根已登记', true);
  };
  const removeRoot = async (rootId: string) => {
    await run(() => toolboxApi.setFileRoots(projectId, taskId, detail?.file_roots_version ?? 0,
      roots.filter((root) => root.root_id !== rootId).map((root) => ({ root_id: root.root_id, path: root.requested_path }))), '研究根已更新；旧范围可能失效', true);
  };
  const createScope = async () => {
    if (!job?.attempt_id || !scopeRootIds.length || !scopeRootIds.every((id) => roots.some((root) => root.root_id === id)) || !scopeOps.length || !expiresAt) { setNotice('请选择当前计算、研究根、操作和有效期。'); return; }
    const expiry = new Date(expiresAt);
    if (!Number.isFinite(expiry.getTime()) || expiry.getTime() <= Date.now() || expiry.getTime() > Date.now() + 24 * 60 * 60_000) {
      setNotice('请选择未来 24 小时内的有效期。'); return;
    }
    const sourcePaths = sources.split(/\r?\n/).map((part) => part.trim()).filter(Boolean);
    if (scopeOps.some((op) => op === 'copy' || op === 'symlink') && sourcePaths.length === 0) {
      setNotice('复制或软链接范围需要列出精确来源路径。'); return;
    }
    const startedIn = contextRef.current.generation;
    const result = await run(() => toolboxApi.createFileScope(projectId, taskId, {
      job_key: job.key, attempt_id: job.attempt_id!,
      root_bindings: scopeRootIds.map((rootId) => ({ root_id: rootId, version: roots.find((root) => root.root_id === rootId)!.version, destination_prefixes: [prefix.trim()] })),
      allowed_operations: scopeOps, source_paths: sourcePaths,
      max_operations: maxOps, max_total_bytes: maxBytes, expires_at: expiry.toISOString(), approval_mode: 'human',
    }), '文件范围已提出；首次批准精确文件卡时才激活', true);
    if (result && contextRef.current.generation === startedIn && 'scope_id' in result) setScopeId(String(result.scope_id));
  };
  const addItem = () => {
    if (!scope || !itemRootId || !destination.trim() || (['copy', 'symlink'].includes(itemOp) && !source.trim()) || (itemOp === 'write_text' && !text)) {
      setNotice('请填写当前操作的来源、目标或文本。'); return;
    }
    if (itemOp === 'write_text' && destination.trim().split('/').pop()?.toUpperCase() === 'POTCAR') {
      setNotice('不能生成 POTCAR 文本。'); return;
    }
    if (itemOp === 'write_text' && new TextEncoder().encode(text).length > 12_000) {
      setNotice('单项文本超过 12000 字节上限。'); return;
    }
    if (items.length >= 32 || items.length >= scope.max_operations) { setNotice('文件项已达到范围上限。'); return; }
    const next: ToolboxFileItemInput = {
      item_id: `item-${items.length + 1}-${Math.random().toString(36).slice(2, 7)}`,
      op: itemOp, destination: { root_id: itemRootId, relative_path: destination.trim() }, on_conflict: 'fail',
      ...(['copy', 'symlink'].includes(itemOp) ? { source: { absolute_path: source.trim() } } : {}),
      ...(itemOp === 'write_text' ? { text } : {}),
    };
    setItems((current) => [...current, next]); setKey(newKey()); setDestination(''); setSource(''); setText(''); setNotice('');
  };
  const plan = async () => {
    if (!scope || !job?.attempt_id || !items.length) { setNotice('请先选择文件范围并加入文件项。'); return; }
    const submittedItems = items;
    const startedIn = contextRef.current.generation;
    const result = await run(() => toolboxApi.planRemoteFile(projectId, taskId, {
      scope_id: scope.scope_id, scope_version: scope.version, job_key: job.key, attempt_id: job.attempt_id!,
      idempotency_key: key, items,
    }), '已生成精确文件确认卡；尚未执行', true);
    if (result && contextRef.current.generation === startedIn && 'pending' in result && result.pending) {
      setSelectedActionId(result.pending.action_id);
      if (itemsRef.current === submittedItems) { setItems([]); setKey(newKey()); }
    }
  };
  const resolve = async (approved: boolean) => {
    if (!fullAction || fullAction.kind !== 'remote_file' || fullAction.state !== 'pending') return;
    if (approved && !canApprove) { setNotice('当前计算、尝试或范围已变化，请重新读取并建立文件计划。'); return; }
    const confirmation = approved && linkedScope?.state === 'proposed'
      ? { scope_id: linkedScope.scope_id, version: linkedScope.version } : undefined;
    const startedIn = contextRef.current.generation;
    const result = await run(() => toolboxApi.resolveConsent(projectId, taskId, fullAction.card_id, approved, undefined, confirmation),
      approved ? '批准请求已受理；请继续查看逐项回执' : '已拒绝该文件计划', true);
    if (result && contextRef.current.generation === startedIn) await selectedActionQuery.refetch();
  };
  const revoke = async (selected: ToolboxFileScope) => {
    await run(() => toolboxApi.revokeFileScope(projectId, taskId, selected.scope_id, selected.version, '用户在任务页撤销'),
      '撤销请求已受理；已在途发布仍须查看回执', true);
  };
  const reconcile = async () => {
    if (!selectedActionId) return;
    const startedIn = contextRef.current.generation;
    const result = await run(() => toolboxApi.reconcileFileAction(projectId, taskId, selectedActionId), '只读核对已完成；请查看逐项结果', true);
    if (result && contextRef.current.generation === startedIn) await selectedActionQuery.refetch();
  };
  const loadHistory = async () => {
    const startedIn = contextRef.current.generation;
    const token = { generation: startedIn, id: ++requestSerialRef.current };
    setBusyToken(token);
    try {
      const response = await toolboxApi.listFileActions(projectId, taskId, 20, cursor ?? undefined);
      if (contextRef.current.generation !== startedIn) return;
      setHistory((previous) => [...previous, ...response.actions.filter((row) => !previous.some((old) => old.action_id === row.action_id))]);
      setCursor(response.next_cursor);
    } catch (error) { if (contextRef.current.generation === startedIn) setNotice(errorText(error)); }
    finally { setBusyToken((current) => current?.id === token.id ? null : current); }
  };

  const rootOptions = roots.map((root) => ({ value: root.root_id, label: `${root.requested_path}（版本 ${root.version}）` }));
  const availableScopes = scopes.filter((item) => item.job_key === jobKey && item.attempt_id === job?.attempt_id && ['proposed', 'active'].includes(item.state));
  const seen = new Set(actions.map((item) => item.action_id));
  const actionRows = [...actions, ...history.filter((item) => !seen.has(item.action_id))];
  const cardRoots = fullAction?.binding.manifest.roots ?? [];
  const canApprove = !!fullAction && action?.state === 'pending' && !!job?.attempt_id
    && fullAction.action_id === selectedActionId
    && field(fullAction.binding, 'project_id') === projectId && field(fullAction.binding, 'task_id') === taskId
    && fullAction.binding.job_key === job.key && fullAction.binding.attempt_id === job.attempt_id
    && !!linkedScope && ['proposed', 'active'].includes(linkedScope.state)
    && linkedScope.version === fullAction.binding.scope_version
    && linkedScope.job_key === job.key && linkedScope.attempt_id === job.attempt_id
    && new Date(linkedScope.expires_at).getTime() > Date.now()
    && new Date(fullAction.expires_at ?? '').getTime() > Date.now()
    && linkedScope.root_bindings.every((binding) => roots.some((root) => root.root_id === binding.root_id && root.version === binding.version))
    && fullAction.binding.manifest.roots.every((boundRoot) => roots.some((root) => root.root_id === boundRoot.root_id && root.version === boundRoot.version))
    && fullAction.binding.manifest.items.every((entry) => entry.op !== 'write_text' || typeof entry.text === 'string');
  const itemStatus = (id: string) => action?.receipt.items.find((item) => item.item_id === id)?.state
    ?? action?.receipt.item_outcomes?.[id]?.state ?? (action?.state === 'pending' ? '等待批准' : '尚无回执');
  return <Card id="toolbox-files" title="远端文件准备与授权" extra={<Tag>人工逐次确认</Tag>}>
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <Alert type="info" showIcon message="文件准备完成不代表科学适用" description="从当前计算和尝试选择研究根与精确来源；目标已存在时失败，软链接可能被计算写回根外来源。" />
      {notice && <Alert type={/失败|错误|不可|勿|缺|变化|冲突/.test(notice) ? 'warning' : 'info'} showIcon message={notice} closable onClose={() => setNotice('')} />}
      <div>
        <Text strong>1. 当前计算与研究根</Text>
        <div style={{ marginTop: 8 }}><Select aria-label="文件授权计算" style={{ width: '100%', maxWidth: 480 }} value={jobKey || undefined}
          onChange={(value) => { setJobKey(value); setScopeId(''); setItems([]); setSelectedActionId(null); }}
          options={jobs.map((entry) => ({ value: entry.key, label: `${entry.label || entry.key}${entry.attempt_id ? ` · 当前尝试 ${entry.attempt_id}` : ' · 缺少当前尝试身份'}` }))}
          placeholder="选择一个计算" /></div>
        {jobKey && !job?.attempt_id && <Text type="danger">此计算缺少当前尝试身份，无法建立文件范围。</Text>}
        <Space.Compact style={{ width: '100%', maxWidth: 750, marginTop: 10 }}>
          <Input aria-label="研究根绝对路径" value={rootPath} onChange={(event) => setRootPath(event.target.value)} placeholder="远端已存在目录，如 /scratch/me/project" />
          <Button onClick={() => setPickerOpen(true)}>浏览远端目录</Button>
          <Button disabled={busy || !rootPath.trim()} onClick={() => void registerRoot()}>登记研究根</Button>
        </Space.Compact>
        <List size="small" dataSource={roots} locale={{ emptyText: '尚未登记研究根' }} renderItem={(root: ToolboxFileRoot) =>
          <List.Item actions={[<Button key="remove" size="small" danger disabled={busy} onClick={() => void removeRoot(root.root_id)}>移除</Button>]}>
            <List.Item.Meta title={root.requested_path} description={`解析位置：${root.canonical_path} · 版本 ${root.version}`} />
          </List.Item>} />
      </div>
      <div>
        <Text strong>2. 提出人工文件范围</Text>
        <Space direction="vertical" style={{ width: '100%', marginTop: 8 }}>
          <Select aria-label="范围研究根" mode="multiple" value={scopeRootIds} onChange={setScopeRootIds} options={rootOptions} placeholder="选择一个或多个已登记研究根" style={{ width: '100%', maxWidth: 600 }} />
          <Input aria-label="根内目标前缀" value={prefix} onChange={(event) => setPrefix(event.target.value)} placeholder="如 relax/static；留空表示所选研究根内" />
          <Select aria-label="允许的文件操作" mode="multiple" value={scopeOps} onChange={setScopeOps} options={operations} style={{ width: '100%' }} />
          <Input.TextArea aria-label="来源绝对路径（每行一个）" value={sources} onChange={(event) => setSources(event.target.value)} rows={2} placeholder="复制或链接的精确远端来源；每行一个" />
          <Space wrap><label>最多操作数 <InputNumber aria-label="最多操作数" min={1} max={32} value={maxOps} onChange={(value) => setMaxOps(Number(value ?? 1))} /></label>
            <label>总字节预算 <InputNumber aria-label="总字节预算" min={0} value={maxBytes} onChange={(value) => setMaxBytes(Number(value ?? 0))} /></label>
            <label>有效期 <Input aria-label="文件范围有效期" type="datetime-local" value={expiresAt} onChange={(event) => setExpiresAt(event.target.value)} /></label></Space>
          <Button disabled={busy || !job?.attempt_id || !scopeRootIds.length} onClick={() => void createScope()}>创建文件范围</Button>
          <Select aria-label="文件范围" value={scopeId || undefined} onChange={(value) => { setScopeId(value); setItems([]); }}
            options={availableScopes.map((item) => ({ value: item.scope_id, label: `${item.state === 'proposed' ? '待首次确认' : '已激活'} · ${item.allowed_operations.map(opLabel).join('、')} · 到期 ${item.expires_at}` }))}
            placeholder="选择已建立的当前计算范围（刷新后可恢复）" style={{ width: '100%' }} />
          {scope && <Space wrap><Tag>计算：{job?.label || jobKey}</Tag><Tag>操作上限：{scope.max_operations}</Tag><Tag>字节预算：{scope.max_total_bytes}</Tag><Tag>状态：{scope.state}</Tag>
            <Button danger disabled={busy} onClick={() => void revoke(scope)}>撤销文件范围</Button></Space>}
        </Space>
      </div>
      <div>
        <Text strong>3. 精确文件计划</Text>
        <Space direction="vertical" style={{ width: '100%', marginTop: 8 }}>
          <Select aria-label="文件操作" value={itemOp} onChange={setItemOp} options={operations.filter((entry) => !scope || scope.allowed_operations.includes(entry.value))} style={{ width: '100%', maxWidth: 300 }} />
          <Select aria-label="文件目标研究根" value={itemRootId || undefined} onChange={setItemRootId}
            options={rootOptions.filter((entry) => scope?.root_bindings.some((binding) => binding.root_id === entry.value))} style={{ width: '100%', maxWidth: 600 }} placeholder="选择本项目标根" />
          <Input aria-label="文件目标相对路径" value={destination} onChange={(event) => setDestination(event.target.value)} placeholder="根内相对路径，如 relax/static/CHGCAR" />
          {['copy', 'symlink'].includes(itemOp) && <Input aria-label="文件来源绝对路径" value={source} onChange={(event) => setSource(event.target.value)} placeholder="需与范围中的精确来源一致" />}
          {itemOp === 'write_text' && <Input.TextArea aria-label="写入的完整文本" value={text} onChange={(event) => setText(event.target.value)} rows={5} />}
          {itemOp === 'symlink' && <Alert type="warning" showIcon message="链接的来源可能被计算回写" description="请确认来源是否允许写入；文件工具不会对根外来源执行 chmod。" />}
          <Button disabled={busy || !scope} onClick={addItem}>加入文件项</Button>
          <List size="small" dataSource={items} locale={{ emptyText: '尚未添加文件项' }} renderItem={(item) =>
            <List.Item actions={[<Button key="remove" size="small" onClick={() => { setItems((current) => current.filter((row) => row.item_id !== item.item_id)); setKey(newKey()); }}>删除</Button>]}>
              <List.Item.Meta title={`${opLabel(item.op)} → ${item.destination.relative_path}`} description={`${item.source?.absolute_path ?? (item.text !== undefined ? `文本 ${new TextEncoder().encode(item.text).length} 字节` : '不需要来源')} · 目标已存在时失败`} />
            </List.Item>} />
          <Button type="primary" disabled={busy || !scope || !items.length} onClick={() => void plan()}>生成文件确认卡</Button>
        </Space>
      </div>
      <div>
        <Text strong>4. 文件动作与逐项回执</Text>
        <List size="small" dataSource={actionRows} locale={{ emptyText: '暂无文件动作' }} renderItem={(row) =>
          <List.Item actions={[<Button key="view" size="small" onClick={() => setSelectedActionId(row.action_id)}>查看完整卡与回执</Button>]}>
            <List.Item.Meta title={<Space wrap><Tag title={row.state}>{stateLabel(row.state)}</Tag><Text>{jobs.find((entry) => entry.key === row.binding?.job_key)?.label || row.binding?.job_key || '文件动作'}</Text></Space>}
              description={`尝试 ${row.binding?.attempt_id || '—'} · ${row.binding?.manifest?.items?.length ?? 0} 项 · ${stateLabel(row.receipt?.phase)}`} />
          </List.Item>} />
        {(cursor === undefined || cursor) && <Button disabled={busy} onClick={() => void loadHistory()}>查看更多历史动作</Button>}
        {selectedActionId && <Card size="small" title="完整文件确认卡与回执" style={{ marginTop: 12 }}>
          {selectedActionQuery.isLoading && <Text>正在读取完整文件卡…</Text>}
          {selectedActionQuery.isError && <Alert type="warning" message={`完整卡暂不可读：${errorText(selectedActionQuery.error)}`} action={<Button size="small" onClick={() => void selectedActionQuery.refetch()}>重新读取</Button>} />}
          {action && <Space direction="vertical" style={{ width: '100%' }}>
            <Descriptions size="small" column={1} bordered>
              <Descriptions.Item label="状态"><Tag title={action.state}>{stateLabel(action.state)}</Tag> · {stateLabel(action.receipt?.phase)}</Descriptions.Item>
              <Descriptions.Item label="计算与尝试">{action.binding.job_key} · {action.binding.attempt_id}</Descriptions.Item>
              <Descriptions.Item label="研究根">{cardRoots.map((root) => `${root.requested_path} → ${root.canonical_path}`).join('；') || '读取完整卡后显示'}</Descriptions.Item>
              <Descriptions.Item label="SSH 端点">{field(fullAction?.binding.manifest.endpoint, 'host')}:{field(fullAction?.binding.manifest.endpoint, 'port')} · {field(fullAction?.binding.manifest.endpoint, 'username')}</Descriptions.Item>
              <Descriptions.Item label="当前文件范围">{linkedScope ? `${linkedScope.state === 'proposed' ? '待首次确认' : linkedScope.state === 'active' ? '已激活' : '已失效'} · 版本 ${linkedScope.version}` : '当前范围不存在或已失效'}；编号 {action.binding.scope_id}</Descriptions.Item>
              <Descriptions.Item label="范围到期">{linkedScope?.expires_at || '不可用'}</Descriptions.Item>
              <Descriptions.Item label="本卡批准期限">{action.expires_at || '不可用'}</Descriptions.Item>
              <Descriptions.Item label="范围允许操作">{linkedScope?.allowed_operations.map(opLabel).join('、') || '不可用'}</Descriptions.Item>
              <Descriptions.Item label="范围根与目标前缀">{linkedScope?.root_bindings.map((binding) => `${roots.find((root) => root.root_id === binding.root_id)?.requested_path || binding.root_id}：${binding.destination_prefixes.map((part) => part || '该根内全部').join('、')}`).join('；') || '不可用'}</Descriptions.Item>
              <Descriptions.Item label="范围精确来源">{linkedScope?.source_bindings.length ? linkedScope.source_bindings.map((sourceBinding) => `${sourceBinding.requested_path} → ${sourceBinding.canonical_path}`).join('；') : '无外部来源'}</Descriptions.Item>
              <Descriptions.Item label="范围上限">{linkedScope ? `${linkedScope.max_operations} 项 · ${linkedScope.max_total_bytes} 字节` : '不可用'}</Descriptions.Item>
              <Descriptions.Item label="本次计划用量">{fullAction?.binding.manifest.items.length ?? 0} 项 · {fullAction?.binding.manifest.items.reduce((sum, item) => sum + item.bytes, 0) ?? 0} 字节</Descriptions.Item>
            </Descriptions>
            <details><summary>核对身份与摘要证据</summary>
              <Paragraph>动作编号：{action.action_id}；清单摘要：{fullAction?.binding.manifest.manifest_digest || '不可用'}</Paragraph>
              <Paragraph>端点摘要：{field(fullAction?.binding.manifest.endpoint, 'endpoint_digest') || '不可用'}</Paragraph>
              {cardRoots.map((root) => <Paragraph key={root.root_id}>根 {root.requested_path}：版本 {root.version}；设备 {field(root.identity, 'device') || '未知'}；inode {field(root.identity, 'inode') || '未知'}</Paragraph>)}
            </details>
            {(fullAction?.binding.manifest.items ?? []).map((entry) => <Card key={entry.item_id} size="small" title={`${opLabel(entry.op)} · ${entry.item_id}`} extra={<Tag title={itemStatus(entry.item_id)}>{stateLabel(itemStatus(entry.item_id))}</Tag>}>
              <Paragraph>目标：{cardRoots.find((root) => root.root_id === field(entry.destination, 'root_id'))?.canonical_path || field(entry.destination, 'root_id')}/{field(entry.destination, 'relative_path')}</Paragraph>
              {entry.source && <Paragraph>来源：{field(entry.source, 'requested_path')}；解析位置：{field(entry.source, 'canonical_path')}</Paragraph>}
              <Paragraph>预计字节：{entry.bytes}；目标存在时：失败；内容类别：{entry.content_class || '未分类'}</Paragraph>
              {entry.op === 'write_text' && <><Text strong>批准的完整文本</Text><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{entry.text ?? '文本未载入，禁止批准'}</pre></>}
              {entry.op === 'symlink' && <Alert type="warning" showIcon message="计算可能沿软链接写回来源" description={`链接实际指向 ${field(entry.source, 'canonical_path')}；请确认根外来源的写入风险。`} />}
              {action.receipt.items.filter((receipt) => receipt.item_id === entry.item_id).map((receipt, index) => <Paragraph key={index}>回执：{stateLabel(receipt.state)}{receipt.error ? ` · ${typeof receipt.error === 'object' ? field(receipt.error as Record<string, unknown>, 'message') : String(receipt.error)}` : ''}</Paragraph>)}
            </Card>)}
            {action.receipt.error && <Alert type="warning" message={`${action.receipt.error.code}: ${action.receipt.error.message}`} />}
            {(action.receipt.leftovers?.length ?? 0) > 0 && <Alert type="warning" message={`远端遗留需人工核对：${action.receipt.leftovers?.join('；')}`} />}
            <Paragraph>已完成：{action.receipt.spent?.operations ?? 0} 项；结果未知保留：{action.receipt.held_unknown?.operations ?? 0} 项；已证明未执行：{action.receipt.released?.operations ?? 0} 项。</Paragraph>
            {action.receipt.cancel_requested_at && <Alert type="warning" message="已请求撤销；在途发布仍可能生效，请以逐项回执为准" />}
            <Text type="secondary">文件完成仅表示准备结果，科学适用性尚未判断。</Text>
            {action.state === 'pending' && fullAction && <Space>
              <Button type="primary" disabled={busy || !canApprove} onClick={() => void resolve(true)}>批准文件计划</Button>
              <Button danger disabled={busy} onClick={() => void resolve(false)}>拒绝文件计划</Button>
            </Space>}
            {action.state === 'pending' && fullAction && !canApprove && <Text type="warning">当前计算、尝试、范围或卡期限已变化；请核对后重新建立计划。</Text>}
            {action.state === 'unknown' && fullAction?.action_id === selectedActionId && <Button disabled={busy} onClick={() => void reconcile()}>核对未知结果</Button>}
            <Button size="small" onClick={() => void selectedActionQuery.refetch()}>刷新完整回执</Button>
          </Space>}
        </Card>}
      </div>
    </Space>
    <AiDirectoryPicker open={pickerOpen} kind="hpc" initialPath={rootPath} onCancel={() => setPickerOpen(false)}
      onSelect={(path) => { setRootPath(path); setPickerOpen(false); }} />
  </Card>;
};

export default ToolboxFilesPanel;
