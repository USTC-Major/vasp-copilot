import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Card, Checkbox, Empty, Input, Modal, Select, Space, Spin, Table, Tag, Typography } from 'antd';
import { FolderOpenOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import { Link } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ApiError, toolboxApi } from '../api/client';
import { potcarApi } from '../api/potcar';
import type { PotcarDataset, PotcarDatasetStatus, PotcarDiscovery, PotcarLibrary, PotcarScan } from '../types/potcar';
import './potcar-library.css';

const { Title, Text, Paragraph } = Typography;
const statusLabels: Record<PotcarDatasetStatus, string> = { ready: '格式检查通过', unsupported: '首版未支持', invalid: '无效', ambiguous: '内容有歧义' };
const statusColors: Record<PotcarDatasetStatus, string> = { ready: 'success', unsupported: 'default', invalid: 'error', ambiguous: 'warning' };
const scanLabels: Record<PotcarScan['status'], string> = { queued: '等待扫描', running: '正在扫描', succeeded: '扫描完成', failed: '扫描失败', cancelled: '扫描已取消' };
const activeScan = (scan?: PotcarScan | null) => scan?.status === 'queued' || scan?.status === 'running';
type Failure = { code: string; message: string };
type Dialog = {
  token: number;
  mode: 'register' | 'edit' | 'relink' | 'replace' | 'delete';
  library?: PotcarLibrary;
  name: string; path: string; note: string; ack: boolean;
  discovery?: PotcarDiscovery; collection?: string;
};
const dialogTitles: Record<Dialog['mode'], string> = { register: '登记现有赝势库', edit: '编辑库信息', relink: '重新关联同一来源', replace: '替换赝势库来源', delete: '删除库登记' };
const failureOf = (error: unknown): Failure => ({ code: error instanceof ApiError ? error.code : 'POTCAR_REQUEST_FAILED', message: error instanceof Error ? error.message : '请求失败，请重试' });
const unknown = (value: string | number | null) => value ?? '未知';

export default function PotcarLibraryPage() {
  const client = useQueryClient();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const dialogRef = useRef<Dialog | null>(null);
  dialogRef.current = dialog;
  const tokenRef = useRef(0);
  const mountedRef = useRef(true);
  const discoveryRef = useRef<AbortController | null>(null);
  const [pathBusy, setPathBusy] = useState(false);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [notice, setNotice] = useState('');
  const [element, setElement] = useState('');
  const [status, setStatus] = useState<PotcarDatasetStatus | undefined>();
  const [cursor, setCursor] = useState<string | null>(null);
  const [previousCursors, setPreviousCursors] = useState<(string | null)[]>([]);
  const librariesQuery = useQuery({ queryKey: ['potcar', 'libraries'], queryFn: ({ signal }) => potcarApi.libraries(signal), retry: false });
  const libraries = librariesQuery.data?.libraries ?? [];
  const detailQuery = useQuery({ queryKey: ['potcar', 'library', selectedId], queryFn: ({ signal }) => potcarApi.library(selectedId!, signal), enabled: !!selectedId, retry: false });
  const library = detailQuery.data?.library;
  const scanId = library?.scan?.scan_id;
  const scanQuery = useQuery({
    queryKey: ['potcar', 'scan', scanId], queryFn: ({ signal }) => potcarApi.scan(scanId!, signal),
    enabled: !!scanId && activeScan(library?.scan), retry: false,
    refetchInterval: query => !query.state.error && activeScan(query.state.data?.scan ?? library?.scan) ? 900 : false,
  });
  // A refreshed library can report cancellation/interruption by another client.
  // Its terminal state must override an older running scan-query cache.
  const scan = activeScan(library?.scan) ? scanQuery.data?.scan ?? library?.scan : library?.scan;
  const datasetsQuery = useQuery({
    queryKey: ['potcar', 'datasets', selectedId, library?.index_revision, element, status, cursor],
    queryFn: ({ signal }) => potcarApi.datasets(selectedId!, { element: element.trim() || undefined, status, cursor: cursor ?? undefined }, signal),
    enabled: !!library, retry: false,
  });
  const publishedRef = useRef<string | null>(null);
  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; discoveryRef.current?.abort(); };
  }, []);
  useEffect(() => {
    if (librariesQuery.data && (!selectedId || !librariesQuery.data.libraries.some(item => item.library_id === selectedId))) {
      setSelectedId(librariesQuery.data.default_library_id ?? librariesQuery.data.libraries[0]?.library_id ?? null);
    }
  }, [librariesQuery.data, selectedId]);
  useEffect(() => { setCursor(null); setPreviousCursors([]); }, [selectedId, element, status, library?.index_revision]);
  useEffect(() => {
    const result = scanQuery.data?.scan;
    if (result && !activeScan(result) && publishedRef.current !== result.scan_id) {
      publishedRef.current = result.scan_id;
      void client.invalidateQueries({ queryKey: ['potcar', 'libraries'] });
      void client.invalidateQueries({ queryKey: ['potcar', 'library', result.library_id] });
      void client.invalidateQueries({ queryKey: ['potcar', 'datasets', result.library_id] });
    }
  }, [scanQuery.data, client]);

  const refresh = async () => {
    await Promise.all([
      client.invalidateQueries({ queryKey: ['potcar', 'libraries'] }),
      client.invalidateQueries({ queryKey: ['potcar', 'library'] }),
      client.invalidateQueries({ queryKey: ['potcar', 'datasets'] }),
    ]);
  };
  const perform = async (action: () => Promise<unknown>, success: string, close = false) => {
    if (busyRef.current) return;
    busyRef.current = true; setBusy(true); setFailure(null); setNotice('');
    try {
      await action();
      if (!mountedRef.current) return;
      if (close) setDialog(null);
      setNotice(success);
      await refresh();
    } catch (error) {
      if (mountedRef.current) setFailure(failureOf(error));
    } finally {
      busyRef.current = false;
      if (mountedRef.current) setBusy(false);
    }
  };
  const openDialog = (mode: Dialog['mode'], target?: PotcarLibrary) => {
    discoveryRef.current?.abort();
    setPathBusy(false); setFailure(null); setNotice('');
    setDialog({ token: ++tokenRef.current, mode, library: target, name: target?.display_name ?? '', path: mode === 'edit' || mode === 'delete' ? target?.root_path ?? '' : '', note: target?.version_note ?? '', ack: false });
  };
  const changeDialog = (patch: Partial<Dialog>) => setDialog(current => current ? { ...current, ...patch } : current);
  const closeDialog = () => {
    if (busyRef.current) return;
    discoveryRef.current?.abort(); ++tokenRef.current; setPathBusy(false); setDialog(null); setFailure(null);
  };
  const changePath = (path: string) => {
    discoveryRef.current?.abort(); setPathBusy(false);
    changeDialog({ path, discovery: undefined, collection: undefined, ack: false });
  };
  const pickDirectory = async () => {
    if (!dialog || pathBusy) return;
    const token = dialog.token;
    const originalPath = dialog.path;
    setPathBusy(true); setFailure(null);
    try {
      const result = await toolboxApi.pickLocal(dialog.path);
      if (!mountedRef.current || dialogRef.current?.token !== token || dialogRef.current.path !== originalPath) return;
      if (result.ok && result.path) changePath(result.path);
      else if (result.notice) setFailure({ code: 'POTCAR_DIRECTORY_PICKER', message: result.notice });
    } catch (error) {
      if (mountedRef.current && dialogRef.current?.token === token) setFailure(failureOf(error));
    } finally {
      if (mountedRef.current && dialogRef.current?.token === token) setPathBusy(false);
    }
  };
  const discover = async () => {
    if (!dialog?.path.trim() || pathBusy) return;
    discoveryRef.current?.abort();
    const controller = new AbortController(); discoveryRef.current = controller;
    const token = dialog.token; const path = dialog.path.trim();
    setPathBusy(true); setFailure(null);
    try {
      const result = await potcarApi.discover(path, controller.signal);
      if (!mountedRef.current || controller.signal.aborted || dialogRef.current?.token !== token || dialogRef.current.path.trim() !== path) return;
      changeDialog({ discovery: result, collection: result.requires_selection ? undefined : result.root_path });
    } catch (error) {
      if (!controller.signal.aborted && mountedRef.current && dialogRef.current?.token === token) setFailure(failureOf(error));
    } finally {
      if (!controller.signal.aborted && mountedRef.current && dialogRef.current?.token === token) setPathBusy(false);
    }
  };
  const reloadRevision = async () => {
    if (!dialog || busy) return;
    const token = dialog.token;
    setPathBusy(true);
    try {
      const registry = await librariesQuery.refetch();
      if (registry.isError) throw registry.error;
      if (dialog.library) {
        const latest = await potcarApi.library(dialog.library.library_id);
        if (mountedRef.current && dialogRef.current?.token === token) changeDialog({ library: latest.library });
      }
      if (mountedRef.current && dialogRef.current?.token === token) setFailure(null);
    } catch (error) { if (mountedRef.current && dialogRef.current?.token === token) setFailure(failureOf(error)); }
    finally { if (mountedRef.current && dialogRef.current?.token === token) setPathBusy(false); }
  };
  const submitDialog = async () => {
    if (!dialog || pathBusy) return;
    const { mode, library: target } = dialog;
    const path = dialog.collection ?? dialog.path.trim();
    if ((mode === 'register' || mode === 'edit') && !dialog.name.trim()) { setFailure({ code: 'POTCAR_NAME_REQUIRED', message: '请填写库显示名称' }); return; }
    if ((mode === 'register' || mode === 'replace') && (!dialog.discovery || !dialog.collection || !dialog.ack)) { setFailure({ code: 'POTCAR_SOURCE_REQUIRED', message: '请先识别并选择具体集合，再确认该来源的使用权限' }); return; }
    if (mode === 'relink' && !path) { setFailure({ code: 'POTCAR_PATH_REQUIRED', message: '请填写移动后的同一来源目录' }); return; }
    if (mode === 'register' && librariesQuery.data) await perform(() => potcarApi.register({ display_name: dialog.name.trim(), root_path: path, version_note: dialog.note.trim() || null, source_ack: { confirmed: true }, expected_registry_revision: librariesQuery.data!.revision }), '已登记，请点击“扫描库”建立索引。', true);
    if (mode === 'edit' && target) await perform(() => potcarApi.edit(target.library_id, target.revision, dialog.name.trim(), dialog.note.trim() || null), '库信息已保存', true);
    if (mode === 'relink' && target) await perform(() => potcarApi.relink(target.library_id, path, target.revision), '已重新关联同一来源，来源声明继续有效', true);
    if (mode === 'replace' && target) await perform(() => potcarApi.replace(target.library_id, path, target.revision), '已替换来源，旧索引已清空；请重新扫描。', true);
    if (mode === 'delete' && target) await perform(() => potcarApi.remove(target.library_id, target.revision), '仅已删除应用登记与索引，源文件保留。', true);
  };
  const errorAlert = failure && <Alert type="error" showIcon title={failure.message} description={<><Text code>{failure.code}</Text>{failure.code === 'POTCAR_REVISION_CONFLICT' && <Paragraph>{dialog ? '后台记录已更新。本页草稿已保留，请读取最新版本后再提交。' : '后台记录已更新，请刷新库列表与详情后再操作。'}</Paragraph>}</>} />;
  const columns = [
    { title: '元素 / 变体', key: 'species', width: 140, render: (_: unknown, item: PotcarDataset) => <><Text strong>{unknown(item.element)}</Text><div>{unknown(item.variant)}</div></> },
    { title: '相对路径 / 格式', key: 'path', width: 250, render: (_: unknown, item: PotcarDataset) => <><div className="potcar-path">{item.relative_path}</div><Text type="secondary">{unknown(item.compression)}{item.duplicate_of ? ' · 同内容别名' : ''}</Text></> },
    { title: '体系 / LEXCH', key: 'family', width: 140, render: (_: unknown, item: PotcarDataset) => <>{unknown(item.family)}<div>LEXCH：{unknown(item.lexch)}</div></> },
    { title: 'ZVAL / ENMAX', key: 'values', width: 150, render: (_: unknown, item: PotcarDataset) => <>ZVAL：{unknown(item.zval)}<div>ENMAX：{item.enmax_ev === null ? '未知' : `${item.enmax_ev} eV`}</div></> },
    { title: '数据集日期', dataIndex: 'dataset_date', width: 130, render: (value: string | null) => unknown(value) },
    { title: '状态与原因', key: 'status', width: 270, render: (_: unknown, item: PotcarDataset) => <><Tag color={statusColors[item.status]}>{statusLabels[item.status]}</Tag>{item.issues.map((issue, index) => <div className="potcar-issue" key={`${issue.code}-${index}`}><Text code>{issue.code}</Text> {issue.message}</div>)}</> },
  ];

  return <div className="wf-page potcar-library-page">
    <div className="wf-page-heading"><div><Title level={1}>本地 POTCAR 赝势库</Title><Paragraph type="secondary">登记已有目录，查看元素、变体和异常。库管理不依赖智能模式。</Paragraph></div><Space wrap><Link to="/toolbox/settings">返回执行设置</Link><Link to="/toolbox/potcar/assemble">拼接 POTCAR</Link><Button type="primary" aria-label="登记现有库" icon={<PlusOutlined />} disabled={!librariesQuery.data || busy} onClick={() => openDialog('register')}>登记现有库</Button></Space></div>
    <Alert type="info" showIcon title="登记并检查本地赝势库，源文件保持不变" description="目录位于运行后端的电脑；远端或 Docker 后端需使用其可访问路径。完成扫描后，可在独立拼接页读取 POSCAR 并核对变体。" />
    {notice && <Alert type="success" showIcon title={notice} closable onClose={() => setNotice('')} />}
    {!dialog && errorAlert}
    {librariesQuery.isError && <Alert type="error" showIcon title="无法读取赝势库登记" description={librariesQuery.error.message} action={<Button onClick={() => void librariesQuery.refetch()}>重试读取库列表</Button>} />}
    {librariesQuery.isPending ? <div className="potcar-loading"><Spin /><span>正在读取库登记…</span></div> : <div className="potcar-manager-grid">
      <section aria-label="已登记的赝势库" className="potcar-libraries">
        <div className="potcar-section-heading"><Text strong>库登记（{libraries.length}）</Text><Button aria-label="刷新库列表" icon={<ReloadOutlined />} onClick={() => void refresh()} /></div>
        {libraries.length === 0 && !librariesQuery.isError && <Empty description="尚未登记赝势库" />}
        {libraries.map(item => <button type="button" disabled={busy} className={`potcar-library-choice${item.library_id === selectedId ? ' is-selected' : ''}`} aria-current={item.library_id === selectedId ? 'true' : undefined} key={item.library_id} onClick={() => { setSelectedId(item.library_id); setFailure(null); }}><strong>{item.display_name}</strong><span className="potcar-path">{item.root_path}</span><span>{item.is_default ? '默认库 · ' : ''}{item.reachable ? '路径可达' : '路径不可达'}{activeScan(item.scan) ? ' · 扫描中' : ''}</span></button>)}
      </section>
      <section aria-label="赝势库详情" className="potcar-detail">
        {detailQuery.isFetching && !library && <Spin aria-label="读取库详情" />}
        {detailQuery.isError && <Alert type="error" showIcon title="库详情读取失败" description={detailQuery.error.message} action={<Button onClick={() => void detailQuery.refetch()}>重试读取详情</Button>} />}
        {library && <>
          <Card title={<span className="potcar-path">{library.display_name}</span>}>
            {!library.reachable && <Alert type="warning" showIcon title="登记路径当前不可达" description="检查后端电脑上的路径；目录移动后可重新关联同一来源。" />}
            <dl className="potcar-metadata"><div><dt>目录</dt><dd className="potcar-path">{library.root_path}</dd></div><div><dt>用户版本备注</dt><dd>{library.version_note || '未填写（不等同于发布版本）'}</dd></div><div><dt>来源声明</dt><dd>已确认 · {library.source_ack.confirmed_at}</dd></div><div><dt>完成索引版本</dt><dd>{library.index_revision ?? '尚未完成扫描'}</dd></div></dl>
            <Space wrap className="potcar-actions">
              <Button type="primary" loading={busy} disabled={activeScan(scan) || detailQuery.isError} onClick={() => void perform(async () => { const result = await potcarApi.startScan(library.library_id, library.revision); client.setQueryData(['potcar', 'library', library.library_id], { mode: 'toolbox', library: result.library, revision: result.revision }); }, '扫描已启动，可查看进度或取消。')}>扫描库</Button>
              <Button disabled={busy || activeScan(scan) || detailQuery.isError} onClick={() => openDialog('edit', library)}>编辑信息</Button>
              <Button disabled={busy || activeScan(scan) || detailQuery.isError} onClick={() => openDialog('relink', library)}>重新关联路径</Button>
              <Button disabled={busy || activeScan(scan) || detailQuery.isError} onClick={() => openDialog('replace', library)}>替换来源</Button>
              <Button disabled={busy || activeScan(scan) || !librariesQuery.data || detailQuery.isError} onClick={() => void perform(() => potcarApi.setDefault(library.is_default ? null : library.library_id, librariesQuery.data!.revision), library.is_default ? '已清空默认库' : '已设为默认库')}>{library.is_default ? '清空默认库' : '设为默认库'}</Button>
              <Button danger disabled={busy || detailQuery.isError} onClick={() => openDialog('delete', library)}>删除登记</Button>
            </Space>
          </Card>
          {scan && <Card size="small" title={scanLabels[scan.status]}><div role="status">已扫描 {scan.scanned_count} / 候选 {scan.candidate_count}，失败 {scan.failed_count}</div>{scan.error && <Alert type="error" title={scan.error.message} description={scan.error.code} />}{(scan.status === 'failed' || scan.status === 'cancelled') && <Paragraph type="secondary">本次未发布完整索引，之前完成的索引保持。可重新扫描。</Paragraph>}{activeScan(scan) && <Button loading={busy} onClick={() => void perform(async () => { const result = await potcarApi.cancelScan(scan.scan_id); client.setQueryData(['potcar', 'scan', scan.scan_id], result); }, '已收到扫描取消回执，请以当前终态为准。')}>取消扫描</Button>}{scanQuery.isError && <Alert type="error" title="扫描进度读取失败" description={scanQuery.error.message} action={<Button onClick={() => void scanQuery.refetch()}>重试进度查询</Button>} />}</Card>}
          <Card title="候选数据集与异常" className="potcar-candidates">
            {library.summary && <div className="potcar-summary">共 {library.summary.total} 项 · 格式检查通过 {library.summary.ready} · 未支持 {library.summary.unsupported} · 无效 {library.summary.invalid} · 歧义 {library.summary.ambiguous}</div>}
            <Paragraph type="secondary">首版支持可识别的 PAW-PBE。格式检查通过不代表计算参数已验证；数据集日期不等同于发布版本。</Paragraph>
            <Space wrap className="potcar-filters"><Input aria-label="按元素筛选" placeholder="元素，如 Fe" value={element} onChange={event => setElement(event.target.value)} allowClear /><Select aria-label="按候选状态筛选" placeholder="全部状态" value={status} onChange={setStatus} allowClear options={Object.entries(statusLabels).map(([value, label]) => ({ value, label }))} /></Space>
            {datasetsQuery.isError && <Alert type="error" showIcon title="候选列表读取失败" description={datasetsQuery.error.message} action={<Button onClick={() => { setPreviousCursors([]); if (cursor !== null) setCursor(null); else void datasetsQuery.refetch(); }}>返回第一页并刷新</Button>} />}
            {library.index_revision === null && <Paragraph type="secondary">尚无完整索引，请先扫描。登记与来源替换不会自动扫描。</Paragraph>}
            <Table<PotcarDataset> size="small" columns={columns} dataSource={datasetsQuery.data?.datasets ?? []} loading={datasetsQuery.isFetching} rowKey="dataset_id" pagination={false} scroll={{ x: 1080 }} expandable={{ expandedRowRender: item => <dl className="potcar-metadata"><div><dt>标题元数据</dt><dd>{unknown(item.title)}</dd></div><div><dt>解码后 SHA-256</dt><dd className="potcar-path">{unknown(item.decoded_sha256)}</dd></div><div><dt>源文件 SHA-256</dt><dd className="potcar-path">{unknown(item.source_sha256)}</dd></div>{item.duplicate_of && <div><dt>同内容引用</dt><dd>{item.duplicate_of}（别名仍单独展示，未自动选用）</dd></div>}</dl> }} locale={{ emptyText: library.index_revision === null ? '尚未建立索引' : '没有符合条件的候选' }} />
            <div className="potcar-pagination"><Text type="secondary">当前筛选共 {datasetsQuery.data?.total ?? 0} 项 · 每页最多 50 项</Text><Space wrap><Button disabled={!previousCursors.length || datasetsQuery.isFetching} onClick={() => { setCursor(previousCursors[previousCursors.length - 1]); setPreviousCursors(values => values.slice(0, -1)); }}>上一页</Button><Button disabled={!datasetsQuery.data?.next_cursor || datasetsQuery.isFetching} onClick={() => { setPreviousCursors(values => [...values, cursor]); setCursor(datasetsQuery.data!.next_cursor); }}>下一页</Button></Space></div>
          </Card>
        </>}
      </section>
    </div>}
    <Modal className="potcar-library-modal" title={dialog ? dialogTitles[dialog.mode] : ''} open={!!dialog} onCancel={closeDialog} onOk={() => void submitDialog()} confirmLoading={busy} okButtonProps={{ disabled: pathBusy || (dialog?.mode === 'register' && !librariesQuery.data), danger: dialog?.mode === 'delete' }} cancelButtonProps={{ disabled: busy }} okText={dialog?.mode === 'delete' ? '仅删除登记' : dialog?.mode === 'register' ? '登记' : dialog?.mode === 'replace' ? '确认替换来源' : '保存'} cancelText="取消" destroyOnHidden>
      {dialog && <div className="potcar-dialog-form">
        {errorAlert}
        {failure?.code === 'POTCAR_REVISION_CONFLICT' && <Button loading={pathBusy} onClick={() => void reloadRevision()}>读取最新版本，保留草稿</Button>}
        {dialog.library && <Text strong className="potcar-path">当前库：{dialog.library.display_name}</Text>}
        {dialog.mode === 'delete' ? <Alert type="warning" showIcon title="仅删除应用的登记和索引" description="源目录与源文件不会删除。正在进行的扫描将安全取消；删除默认库后不会自动指定其他库。" /> : <>
          {(dialog.mode === 'register' || dialog.mode === 'edit') && <><label htmlFor="potcar-library-name">显示名称</label><Input id="potcar-library-name" value={dialog.name} onChange={event => changeDialog({ name: event.target.value })} maxLength={120} /><label htmlFor="potcar-version-note">用户版本备注（可选）</label><Input id="potcar-version-note" value={dialog.note} onChange={event => changeDialog({ note: event.target.value })} maxLength={500} /><Text type="secondary">备注由你填写；不会被当作可证实的发布版本或数据集日期。</Text></>}
          {dialog.mode !== 'edit' && <><label htmlFor="potcar-root-path">{dialog.mode === 'relink' ? '同一来源移动后的目录' : '后端电脑上的现有目录'}</label><div className="potcar-path-input"><Input id="potcar-root-path" value={dialog.path} onChange={event => changePath(event.target.value)} placeholder="如 D:\赝势库\paw_pbe" /><Button icon={<FolderOpenOutlined />} loading={pathBusy} disabled={busy} onClick={() => void pickDirectory()}>选择目录</Button></div></>}
          {(dialog.mode === 'register' || dialog.mode === 'replace') && <><Button loading={pathBusy} disabled={!dialog.path.trim() || busy} onClick={() => void discover()}>识别候选集合</Button>{dialog.discovery && <><Text>发现 {dialog.discovery.candidate_count} 个候选文件{dialog.discovery.requires_selection ? '，请明确选择一个具体集合。' : '，将登记此具体集合。'}</Text><Select aria-label="选择具体赝势集合" placeholder="请选择具体集合" value={dialog.collection} onChange={value => changeDialog({ collection: value, ack: false })} options={dialog.discovery.collections.map(item => ({ value: item.root_path, label: `${item.display_name} · ${item.candidate_count} 项 · ${item.root_path}` }))} /><div className="potcar-path">实际来源：{dialog.collection || '尚未选择'}</div></>}<Checkbox checked={dialog.ack} disabled={!dialog.collection || busy} onChange={event => changeDialog({ ack: event.target.checked })}>我确认有权使用所选来源的赝势数据。此声明不代表软件验证了许可或科学适用性。</Checkbox>{dialog.mode === 'replace' && <Alert type="warning" showIcon title="这是新的来源确认" description="明确替换将清空旧索引，必须重新扫描；不会删除旧源目录。" />}</>}
          {dialog.mode === 'relink' && <><Paragraph>后端将使用已完成索引的完整指纹核验同一来源。仅移动目录不重复确认使用权限。</Paragraph>{(failure?.code === 'POTCAR_SOURCE_MISMATCH' || failure?.code === 'POTCAR_SOURCE_UNVERIFIED') && <Button onClick={() => { changeDialog({ mode: 'replace', ack: false, discovery: undefined, collection: undefined }); setFailure(null); }}>改为显式替换来源</Button>}</>}
        </>}
      </div>}
    </Modal>
  </div>;
}
