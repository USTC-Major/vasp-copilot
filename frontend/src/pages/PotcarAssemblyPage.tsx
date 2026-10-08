import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Card, Checkbox, Empty, Input, Select, Space, Spin, Tag, Typography } from 'antd';
import { Link } from 'react-router-dom';
import { ApiError } from '../api/client';
import { potcarApi } from '../api/potcar';
import type { PotcarArtifact, PotcarContext, PotcarLibrary, PotcarPreview } from '../types/potcar';
import './potcar-assembly.css';
import PotcarSelection from '../components/potcar/PotcarSelection';

const { Title, Paragraph, Text } = Typography;
const MAX_STRUCTURE_BYTES = 2 * 1024 * 1024;
const failureOf = (error: unknown) => ({ code: error instanceof ApiError ? error.code : 'POTCAR_REQUEST_FAILED', message: error instanceof Error ? error.message : '请求失败，请重试' });
const staleCodes = new Set(['POTCAR_PREVIEW_NOT_FOUND', 'POTCAR_PREVIEW_EXPIRED', 'POTCAR_SELECTION_DIGEST_MISMATCH', 'POTCAR_SELECTION_BLOCKED', 'POTCAR_INDEX_REVISION_CONFLICT', 'POTCAR_SOURCE_CHANGED', 'POTCAR_SOURCE_REPLACED', 'POTCAR_LIBRARY_NOT_FOUND', 'POTCAR_SCAN_ACTIVE', 'POTCAR_RULE_VERSION_CHANGED']);
const recovery: Record<string, string> = {
  POTCAR_RULE_VERSION_CHANGED: '推荐规则已更新，请刷新预览，再核对并确认。',
  POTCAR_PREVIEW_NOT_FOUND: '请重新读取 / 刷新预览，再核对并确认。',
  POTCAR_PREVIEW_EXPIRED: '预览已到期，请刷新预览，再核对并确认。',
  POTCAR_SELECTION_DIGEST_MISMATCH: '选择已失效，请刷新预览，再核对并确认。',
  POTCAR_SELECTION_BLOCKED: '请处理下方阻断项，选择变体后刷新预览。',
  POTCAR_INDEX_REVISION_CONFLICT: '库索引已变化，请刷新库信息并重新预览；必要时前往管理页扫描。',
  POTCAR_SOURCE_CHANGED: '源文件已变化，请前往管理页重新扫描，再刷新库信息和预览。',
  POTCAR_SOURCE_REPLACED: '库来源已替换，请刷新库信息并重新选择变体。',
  POTCAR_LIBRARY_NOT_FOUND: '库登记已不存在，请刷新库信息并明确选择已有库。',
  POTCAR_SCAN_ACTIVE: '库正在扫描，请等扫描结束后刷新库信息。',
  POTCAR_LEGACY_SPECIES_REQUIRED: '旧格式 POSCAR 缺少元素行，请在物种映射中按计数行顺序逐项填写元素。',
  POTCAR_ARTIFACT_EXPIRED: '产物已到期，请重新预览、核对并生成。',
  POTCAR_ARTIFACT_NOT_FOUND: '产物不可用，请重新预览、核对并生成。',
  POTCAR_ARTIFACT_INVALID: '产物校验失败，请重新预览、核对并生成。',
};
const displayTime = (value: string) => new Date(value).toLocaleString();
const libraryStatus = (library: PotcarLibrary) => !library.reachable ? '路径不可达' : library.scan?.status === 'running' || library.scan?.status === 'queued' ? '扫描中' : library.index_revision === null ? '未扫描' : `可达 · 索引 ${library.index_revision}`;

export default function PotcarAssemblyPage() {
  const [libraries, setLibraries] = useState<PotcarLibrary[]>([]);
  const [libraryId, setLibraryId] = useState<string | null>(null);
  const [libraryBusy, setLibraryBusy] = useState(true);
  const [libraryLoaded, setLibraryLoaded] = useState(false);
  const [context, setContext] = useState<PotcarContext>({ purpose: 'regular', functional: 'PBE' });
  const [poscar, setPoscar] = useState('');
  const [legacySpecies, setLegacySpecies] = useState('');
  const [preview, setPreview] = useState<PotcarPreview | null>(null);
  const [manualIds, setManualIds] = useState<(string | null)[]>([]);
  const [datasetIds, setDatasetIds] = useState<(string | null)[]>([]);
  const [dirty, setDirty] = useState(true);
  const [confirmed, setConfirmed] = useState(false);
  const [artifact, setArtifact] = useState<PotcarArtifact | null>(null);
  const [artifactUnavailable, setArtifactUnavailable] = useState(false);
  const [busy, setBusy] = useState<'preview' | 'generate' | 'download' | 'file' | null>(null);
  const [failure, setFailure] = useState<ReturnType<typeof failureOf> | null>(null);
  const mounted = useRef(true);
  const epoch = useRef(0);
  const busyRef = useRef<typeof busy>(null);
  const libraryRequest = useRef(0);
  const selectedLibrary = useRef<string | null>(null);
  const operation = useRef<AbortController | null>(null);
  const libraryOperation = useRef<AbortController | null>(null);
  const idempotency = useRef<string | null>(null);
  const library = libraries.find(item => item.library_id === libraryId);
  const generating = busy === 'generate';
  const libraryReady = !!library && library.reachable && library.index_revision !== null && library.scan?.status !== 'running' && library.scan?.status !== 'queued';
  const canConfirm = !!preview && !dirty && preview.blockers.length === 0 && preview.rows.every(row => row.dataset_id !== null);

  // Every draft edit revokes the confirmation and aborts reads; epoch also protects
  // against APIs / File.text implementations that finish after cancellation.
  const invalidate = (clearRows = true) => {
    ++epoch.current; operation.current?.abort();
    busyRef.current = null; setBusy(null); setDirty(true); setConfirmed(false);
    setArtifact(null); setArtifactUnavailable(false); setFailure(null); idempotency.current = null;
    if (clearRows) { setPreview(null); setDatasetIds([]); setManualIds([]); }
  };
  const loadLibraries = async (initial = false) => {
    if (busyRef.current === 'generate') return;
    if (!initial) invalidate();
    libraryOperation.current?.abort();
    const controller = new AbortController(); libraryOperation.current = controller;
    const request = ++libraryRequest.current;
    setLibraryBusy(true); setFailure(null);
    try {
      const result = await potcarApi.libraries(controller.signal);
      if (!mounted.current || controller.signal.aborted || libraryRequest.current !== request) return;
      setLibraries(result.libraries); setLibraryLoaded(true);
      if (initial) {
        const id = result.default_library_id ?? result.libraries[0]?.library_id ?? null;
        selectedLibrary.current = id; setLibraryId(id);
      } else if (!result.libraries.some(item => item.library_id === selectedLibrary.current)) {
        selectedLibrary.current = null; setLibraryId(null);
      }
    } catch (error) {
      if (mounted.current && !controller.signal.aborted && request === libraryRequest.current) setFailure(failureOf(error));
    } finally {
      if (mounted.current && request === libraryRequest.current) setLibraryBusy(false);
    }
  };
  useEffect(() => {
    mounted.current = true;
    void loadLibraries(true);
    // Cleanup must cancel the latest operation, including requests started after mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    return () => { mounted.current = false; ++epoch.current; ++libraryRequest.current; operation.current?.abort(); libraryOperation.current?.abort(); };
    // This initial load deliberately does not follow draft changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    if (!preview || dirty || artifact || busy === 'generate') return;
    const timeout = window.setTimeout(() => {
      invalidate(false);
      setFailure({ code: 'POTCAR_PREVIEW_EXPIRED', message: '预览已到期' });
    }, Math.max(0, Date.parse(preview.expires_at) - Date.now()));
    return () => window.clearTimeout(timeout);
  }, [preview, dirty, artifact, busy]);
  useEffect(() => {
    if (!artifact) return;
    const timeout = window.setTimeout(() => setArtifactUnavailable(true), Math.max(0, Date.parse(artifact.expires_at) - Date.now()));
    return () => window.clearTimeout(timeout);
  }, [artifact]);

  const readFile = async (file?: File) => {
    if (!file || generating) return;
    invalidate(); setPoscar('');
    if (file.size > MAX_STRUCTURE_BYTES) { setFailure({ code: 'POTCAR_STRUCTURE_TOO_LARGE', message: '结构文件超过 2 MiB，请使用较小的 UTF-8 POSCAR 文件。' }); return; }
    const token = epoch.current;
    busyRef.current = 'file'; setBusy('file');
    try {
      const text = await file.text();
      if (!mounted.current || epoch.current !== token) return;
      if (text.includes('\uFFFD') || text.includes('\u0000')) { setFailure({ code: 'POTCAR_STRUCTURE_ENCODING', message: '文件不是有效的 UTF-8 文本，请转为 UTF-8 后重新读取。' }); return; }
      setPoscar(text);
    } catch (error) { if (mounted.current && epoch.current === token) setFailure(failureOf(error)); }
    finally { if (mounted.current && epoch.current === token) { busyRef.current = null; setBusy(null); } }
  };
  const refreshPreview = async () => {
    if (busyRef.current || libraryBusy || !libraryReady || !poscar.trim()) return;
    invalidate(false);
    const token = epoch.current;
    const controller = new AbortController(); operation.current = controller;
    busyRef.current = 'preview'; setBusy('preview');
    try {
      const species = legacySpecies.trim();
      const result = await potcarApi.preview({ library_id: library!.library_id, index_revision: library!.index_revision!, poscar_text: poscar, context, ...(preview ? { dataset_ids: manualIds } : {}), ...(species ? { legacy_species: species.split(/\s+/) } : {}) }, controller.signal);
      if (!mounted.current || controller.signal.aborted || epoch.current !== token) return;
      setPreview(result); setDatasetIds(result.rows.map(row => row.dataset_id)); setManualIds(result.rows.map((_, index) => manualIds[index] ?? null)); setDirty(false);
    } catch (error) { if (mounted.current && !controller.signal.aborted && epoch.current === token) setFailure(failureOf(error)); }
    finally { if (mounted.current && epoch.current === token) { busyRef.current = null; setBusy(null); } }
  };
  const generate = async () => {
    if (busyRef.current || !canConfirm || !confirmed || !preview) return;
    if (Date.parse(preview.expires_at) <= Date.now()) { invalidate(false); setFailure({ code: 'POTCAR_PREVIEW_EXPIRED', message: '预览已到期' }); return; }
    const token = epoch.current;
    const controller = new AbortController(); operation.current = controller;
    idempotency.current ??= crypto.randomUUID();
    busyRef.current = 'generate'; setBusy('generate'); setFailure(null);
    try {
      const result = await potcarApi.assemble({ preview_id: preview.preview_id, selection_digest: preview.selection_digest, confirmed_order_and_variants: true, idempotency_key: idempotency.current }, controller.signal);
      if (!mounted.current || controller.signal.aborted || epoch.current !== token) return;
      setArtifact(result.artifact); setArtifactUnavailable(false);
    } catch (error) {
      if (mounted.current && !controller.signal.aborted && epoch.current === token) {
        const problem = failureOf(error);
        if (staleCodes.has(problem.code)) { setDirty(true); setConfirmed(false); idempotency.current = null; }
        setFailure(problem);
      }
    } finally { if (mounted.current && epoch.current === token) { busyRef.current = null; setBusy(null); } }
  };
  const download = async () => {
    if (busyRef.current || !artifact || artifactUnavailable) return;
    const token = epoch.current;
    const controller = new AbortController(); operation.current = controller;
    busyRef.current = 'download'; setBusy('download'); setFailure(null);
    try {
      const blob = await potcarApi.download(artifact.artifact_id, controller.signal);
      if (!mounted.current || controller.signal.aborted || epoch.current !== token) return;
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a'); anchor.href = url; anchor.download = 'POTCAR';
      document.body.appendChild(anchor);
      try { anchor.click(); } finally { anchor.remove(); URL.revokeObjectURL(url); }
    } catch (error) {
      if (mounted.current && !controller.signal.aborted && epoch.current === token) {
        const problem = failureOf(error); setFailure(problem);
        if (['POTCAR_ARTIFACT_EXPIRED', 'POTCAR_ARTIFACT_NOT_FOUND', 'POTCAR_ARTIFACT_INVALID'].includes(problem.code)) setArtifactUnavailable(true);
      }
    } finally { if (mounted.current && epoch.current === token) { busyRef.current = null; setBusy(null); } }
  };

  return <div className="wf-page potcar-assembly-page">
    <div className="wf-page-heading"><div><Title level={1}>拼接 POTCAR</Title><Paragraph type="secondary">读取 POSCAR 的物种顺序，核对本地 PAW-PBE 变体后生成文件。</Paragraph></div><Link to="/toolbox/potcar">管理赝势库</Link></div>
    <div className="potcar-assembly-library">
      <label htmlFor="assembly-library">当前库</label>
      <Select id="assembly-library" aria-label="选择已有赝势库" value={libraryId} placeholder="选择已有库" disabled={generating || libraryBusy} onChange={id => { invalidate(); selectedLibrary.current = id; setLibraryId(id); }} options={libraries.map(item => ({ value: item.library_id, label: item.display_name }))} />
      {libraryBusy ? <Spin size="small" /> : library && <><span>版本备注：{library.version_note ?? '未填写'}</span><Tag color={libraryReady ? 'success' : 'warning'}>{libraryStatus(library)}</Tag></>}
      <Button disabled={generating || libraryBusy} onClick={() => void loadLibraries()}>刷新库信息</Button><Link to="/toolbox/potcar">管理 / 扫描库</Link>
    </div>
    {libraryLoaded && libraries.length === 0 && <Empty description="尚未登记赝势库"><Link to="/toolbox/potcar">前往登记并扫描本地库</Link></Empty>}
    {library && !libraryReady && <Alert type="warning" showIcon title={libraryStatus(library)} description="请在管理页检查路径或完成扫描，然后刷新库信息。" />}
    {failure && <Alert role="alert" type="error" showIcon title={failure.message} description={<><Text code>{failure.code}</Text><div>{recovery[failure.code] ?? '请求未完成，当前输入保留，可以重试。'}</div></>} />}
    <Card title="计算用途与方法" className="potcar-context-card">
      <Paragraph>当前默认普通用途 / PBE；核对后随物种和变体一起确认。特殊研究目标请明确声明，未知条件保持未知。</Paragraph>
      <label htmlFor="assembly-purpose">计算用途</label><Select id="assembly-purpose" aria-label="计算用途" value={context.purpose} disabled={generating} onChange={purpose => { invalidate(false); setContext(current => ({ ...current, purpose })); }} options={[{ value: 'regular', label: '普通用途（一般基态准备）' }, { value: 'special', label: '特殊用途（需人工核对）' }, { value: 'unknown', label: '用途未指定' }]} />
      <label htmlFor="assembly-functional">计算方法</label><Select id="assembly-functional" aria-label="计算方法" value={context.functional} disabled={generating} onChange={functional => { invalidate(false); setContext(current => ({ ...current, functional })); }} options={['PBE', 'PBE+U', 'HSE06', 'unknown'].map(value => ({ value, label: value === 'unknown' ? '方法未指定' : value }))} />
      <div className="potcar-context-conditions">{([['short_bonds', '短键目标'], ['high_pressure', '高压目标'], ['high_unoccupied', '高能未占据态'], ['spin_polarized', '自旋极化'], ['magnetic_energy', '磁性能量差']] as const).map(([key, label]) => <label key={key}>{label}<Select aria-label={label} value={context[key] == null ? 'unknown' : context[key] ? 'yes' : 'no'} disabled={generating} onChange={value => { invalidate(false); setContext(current => ({ ...current, [key]: value === 'unknown' ? null : value === 'yes' })); }} options={[{ value: 'unknown', label: '未知 / 未声明' }, { value: 'yes', label: '是' }, { value: 'no', label: '否' }]} /></label>)}</div>
    </Card>
    <Card title="1. 读取结构" className="potcar-structure-card">
      <div className="potcar-file-input"><label htmlFor="assembly-file">读取本地 POSCAR 文件</label><input id="assembly-file" type="file" disabled={generating} onChange={event => { const file = event.target.files?.[0]; event.target.value = ''; void readFile(file); }} /><Text type="secondary">仅在浏览器读取 UTF-8 文本，最大 2 MiB。</Text>{busy === 'file' && <Spin size="small" />}</div>
      <label htmlFor="assembly-poscar">POSCAR 文本</label>
      <Input.TextArea id="assembly-poscar" value={poscar} disabled={generating} rows={9} placeholder="粘贴完整 POSCAR；物种顺序以实际结构为准" onChange={event => { invalidate(); setPoscar(event.target.value); }} />
      <label htmlFor="assembly-legacy">旧格式物种映射（VASP4）</label>
      <Input id="assembly-legacy" value={legacySpecies} disabled={generating} placeholder="仅旧格式填写，例如 Fe O Fe；按计数行顺序，用空格分隔" onChange={event => { invalidate(); setLegacySpecies(event.target.value); }} />
      <Paragraph type="secondary">旧格式缺少元素行时，必须逐物种明确映射；有元素行的 POSCAR 请留空。不会按化学式推算或合并物种块。</Paragraph>
      <Button type="primary" loading={busy === 'preview'} disabled={!libraryReady || libraryBusy || !poscar.trim() || busy !== null} onClick={() => void refreshPreview()}>读取 / 刷新预览</Button>
    </Card>
    {preview && <Card title="2. 核对物种顺序和变体" className="potcar-confirmation-card">
      <Paragraph type="secondary">按 POSCAR 原始物种块顺序展示，保留重复物种与零数量块。唯一兼容候选仅表示可用，不代表科学最优；变体选择仍需结合研究对象验证。</Paragraph>
      <PotcarSelection preview={preview} datasetIds={datasetIds} dirty={dirty} disabled={generating} onChange={(index, id) => { invalidate(false); setManualIds(current => preview.rows.map((_, position) => position === index ? id : current[position] ?? null)); setDatasetIds(current => current.map((value, position) => position === index ? id : value)); }} />
      <Space wrap className="potcar-confirm-actions"><Checkbox checked={confirmed} disabled={!canConfirm || busy !== null || !!artifact} onChange={event => setConfirmed(event.target.checked)}>已核对物种顺序和变体（含当前用途与建议）</Checkbox><Button type="primary" loading={generating} disabled={!canConfirm || !confirmed || busy !== null || !!artifact} onClick={() => void generate()}>生成 POTCAR</Button></Space>
    </Card>}
    {artifact && <Card title="3. 已生成 POTCAR" className="potcar-artifact-card">
      <Paragraph>文件已生成。下方仅展示元数据和有序清单，下载保留原始字节。</Paragraph>
      <dl className="potcar-artifact-metadata"><dt>文件大小</dt><dd>{artifact.size_bytes} bytes</dd><dt>SHA-256</dt><dd>{artifact.sha256}</dd><dt>结构 SHA-256</dt><dd>{artifact.structure_sha256}</dd><dt>库索引</dt><dd>{artifact.library_id} · {artifact.index_revision}</dd><dt>生成时间</dt><dd>{displayTime(artifact.created_at)}</dd><dt>到期时间</dt><dd>{displayTime(artifact.expires_at)}</dd></dl>
      <ol className="potcar-artifact-order">{artifact.rows.map(row => <li key={row.position}>#{row.position} · {row.element} × {row.atom_count} · {row.variant ?? '未知变体'} · {row.title ?? '标题未知'}<div>数据集 SHA-256：{row.decoded_sha256}</div></li>)}</ol>
      {artifactUnavailable && <Alert type="warning" showIcon title="产物已到期或不可用，请重新预览并生成。" />}
      <Space wrap><Button type="primary" loading={busy === 'download'} disabled={busy !== null || artifactUnavailable} onClick={() => void download()}>下载 POTCAR</Button><Button disabled={busy !== null || libraryBusy || !libraryReady} onClick={() => void refreshPreview()}>重新预览并生成</Button></Space>
    </Card>}
  </div>;
}
