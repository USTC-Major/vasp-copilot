import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Button, Card, Checkbox, Empty, Input, Progress, Select, Space, Spin, Tag, Typography } from 'antd';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useSearchParams } from 'react-router-dom';
import ReactECharts from 'echarts-for-react';
import { ppApi, type PPDataset, type PPCurves, type PPTaskIdentity } from '../api/postprocessing';
import { buildPlotOption, exportPostprocessingPng, fullEnergyWindow, SCIENTIFIC_PLOT_THEME } from '../components/postprocessing/plotting';
import { forgetAnalysisViewSession, useAnalysisView } from '../components/postprocessing/analysisViewStore';
import { validateView, viewKey } from '../components/postprocessing/viewState';
import ProjectionSelector from '../components/postprocessing/ProjectionSelector';
import NumericDraftInput from '../components/postprocessing/NumericDraftInput';
import PlotExportDialog from '../components/postprocessing/PlotExportDialog';
import TaskSourceImport from '../components/postprocessing/TaskSourceImport';
import TaskSourceDetails from '../components/postprocessing/TaskSourceDetails';
import { usePlotPalette } from '../hooks/usePlotPreferences';
import './postprocessing.css';

const labels = { draft: '待导入／解析', downloading: '正在取回文件', processing: '正在解析', ready: '可查看', failed: '需要处理', cancelled: '已取消' };
const allowed = new Set(['vasprun.xml', 'DOSCAR', 'EIGENVAL', 'KPOINTS', 'POSCAR', 'CONTCAR', 'INCAR']);
const errorText = (error: unknown) => error instanceof Error ? error.message : '操作失败，请重试';
function saveBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a'); link.href = url; link.download = name; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function Plot({ data, exportEnabled, stateLabel }: { data: PPCurves; exportEnabled: boolean; stateLabel: string }) {
  const [exportError, setExportError] = useState('');
  const [exportOpen, setExportOpen] = useState(false);
  const { colors, error: paletteError } = usePlotPalette();
  async function download(format: 'csv' | 'json') {
    if (!exportEnabled) return;
    setExportError('');
    try { saveBlob(await ppApi.download(data.id, format), `${data.id}.${format}`); }
    catch (error) { setExportError(errorText(error)); }
  }
  const option = useMemo(() => buildPlotOption(data, colors, SCIENTIFIC_PLOT_THEME), [data, colors]);
  return <>
    {(data.plot_warnings ?? []).map(message => <Alert key={message} type="warning" title={message} showIcon />)}
    {paletteError && <Alert type="error" title={paletteError} />}
    <div className="pp-scientific-plot" data-analysis-id={data.id} data-applied-revision={data.revision}><ReactECharts option={option} notMerge style={{ height: 430, width: '100%' }} /></div>
    <div className="pp-plot-update-status" role="status"><Typography.Text type="secondary">{stateLabel}</Typography.Text></div>
    <Space wrap>
      <Button disabled={!exportEnabled} onClick={() => void download('csv')}>导出 CSV</Button>
      <Button disabled={!exportEnabled} onClick={() => void download('json')}>导出 JSON</Button>
      <Button disabled={!exportEnabled} onClick={() => setExportOpen(true)}>导出 PNG</Button>
    </Space>
    <PlotExportDialog open={exportOpen} onCancel={() => setExportOpen(false)} preview={settings => exportPostprocessingPng(data, { width: settings.width_px, height: settings.height_px, colors: settings.colors })} onExport={async settings => {
      if (!exportEnabled) throw new Error('当前选择或图形尚未更新完成，请完成更新后再导出');
      const url = await exportPostprocessingPng(data, { width: settings.width_px, height: settings.height_px, colors: settings.colors });
      const link = document.createElement('a'); link.href = url; link.download = `${data.id}.png`; link.click();
    }} />
    {exportError && <Alert type="error" title={exportError} />}
    <Typography.Paragraph type="secondary">导出使用当前已应用图形，配置或曲线更新期间暂停导出。CSV／JSON 保留文件指纹和能量参考；向下自旋镜像仅影响图形，不改变导出 DOS 的符号。</Typography.Paragraph>
  </>;
}

function Analysis({ doc, update }: { doc: PPDataset; update: (doc: PPDataset) => void }) {
  const { view, savedDoc, status, error, patch, retry, loadLatest } = useAnalysisView(doc, update);
  const curves = useQuery({ queryKey: ['pp-curves', savedDoc.id, savedDoc.revision], queryFn: ({ signal }) => ppApi.curves(savedDoc.id, signal), retry: false });
  // Retain only this mounted analysis's last successful graph while its next revision loads.
  const [lastCurves, setLastCurves] = useState<PPCurves>();
  const readyCurves = curves.data?.id === doc.id ? curves.data : undefined;
  useEffect(() => {
    if (readyCurves) setLastCurves(previous => previous?.id === doc.id && previous.revision > readyCurves.revision ? previous : readyCurves);
  }, [readyCurves, doc.id]);
  const previousCurves = lastCurves?.id === doc.id ? lastCurves : undefined;
  const appliedCurves = readyCurves && (!previousCurves || readyCurves.revision >= previousCurves.revision) ? readyCurves : previousCurves;
  const appliedMatchesSaved = appliedCurves?.revision === savedDoc.revision;
  const appliedMatchesDraft = !!appliedCurves && viewKey(appliedCurves.view) === viewKey(view);
  const updatingGraph = status === 'saving' || status === 'scheduled' || curves.isFetching;
  const exportEnabled = status === 'saved' && !curves.isFetching && !curves.error && appliedMatchesSaved && appliedMatchesDraft;
  const graphStateLabel = curves.error ? '图形更新失败，当前仍显示上次已应用配置。'
    : updatingGraph ? '正在更新，当前显示上次已应用配置。'
      : !appliedMatchesSaved || !appliedMatchesDraft ? '当前显示上次已应用配置，当前选择尚未应用。' : '图形已更新，导出使用当前已应用配置。';
  const axisError = validateView(view, doc);
  const missingFermi = doc.summary!.efermi_ev === null;
  const referenceOptions = [
    { value: 'raw', label: '原始能量 E' },
    { value: 'fermi', label: `费米能级${missingFermi ? '（缺失）' : ` ${doc.summary!.efermi_ev!.toFixed(4)} eV`}`, disabled: missingFermi },
    { value: 'custom', label: '相对费米能级偏移', disabled: missingFermi },
    ...(view.reference === 'legacy_absolute' ? [{ value: 'legacy_absolute', label: '旧版绝对参考' }] : []),
  ];
  const bandRangePending = doc.kind === 'band' && appliedCurves && (view.band_start !== appliedCurves.view.band_start || view.band_end !== appliedCurves.view.band_end);
  const fullWindow = !bandRangePending && appliedCurves ? fullEnergyWindow(appliedCurves, view, doc.summary!.efermi_ev) : undefined;
  return <Card title={doc.kind === 'dos' ? '态密度分析' : '路径能带分析'}>
    <Space wrap className="pp-status"><Tag>{doc.summary!.spin_mode === 'collinear' ? '共线双自旋' : '非自旋极化'}</Tag><Tag>收敛：{doc.summary!.convergence === 'converged' ? '已收敛' : doc.summary!.convergence === 'not_converged' ? '未收敛' : '未确认'}</Tag></Space>
    {doc.summary!.warnings.map(message => <Alert key={message} type="warning" title={message} showIcon />)}
    <div className="pp-controls">
      <label>能量参考<Select aria-label="能量参考" value={view.reference} onChange={reference => patch({ reference, reference_ev: Number.isFinite(view.reference_ev) ? view.reference_ev : 0 })} options={referenceOptions} /></label>
      {view.reference === 'custom' && <label>相对费米能级的零点偏移 (eV)<NumericDraftInput label="相对费米能级的零点偏移" value={view.reference_ev} onChange={reference_ev => patch({ reference_ev }, true)} /></label>}
      {doc.kind === 'band' && <>
        <label>起始能带<NumericDraftInput label="起始能带" value={view.band_start} onChange={band_start => patch({ band_start }, true)} /></label>
        <label>结束能带<NumericDraftInput label="结束能带" value={view.band_end} onChange={band_end => patch({ band_end }, true)} /></label>
      </>}
    </div>
    {view.reference === 'custom' && <Typography.Paragraph type="secondary">零点偏移为 0 时以费米能级为零点；设为 +1 时整图下移 1 eV。</Typography.Paragraph>}
    {view.reference === 'legacy_absolute' && <Alert type="warning" title="旧版绝对参考" description={`此旧记录缺少费米能级，继续使用绝对参考 ${view.reference_ev} eV，以保持原图。可主动切换到原始能量。`} />}
    {missingFermi && <Typography.Paragraph type="secondary">文件缺少费米能级，无法选择费米能级或相对偏移参考。</Typography.Paragraph>}
    <fieldset className="pp-energy-settings"><legend>能量范围 (eV)</legend>
      <div className="pp-energy-grid">
        <label>能量起点<NumericDraftInput label="能量起点" value={view.energy_min_ev} onChange={energy_min_ev => patch({ energy_min_ev }, true)} /></label>
        <label>能量终点<NumericDraftInput label="能量终点" value={view.energy_max_ev} onChange={energy_max_ev => patch({ energy_max_ev }, true)} /></label>
        <Button disabled={!fullWindow} onClick={() => { if (fullWindow) patch(fullWindow); }}>完整能量范围</Button>
      </div>
      <Typography.Paragraph type="secondary">刻度自动设置；{doc.kind === 'dos' ? 'DOS 纵轴按当前能量范围内的曲线自动适配。' : '横轴严格保留计算的 k 路径端点。'}完整源数据始终保留。</Typography.Paragraph>
      {bandRangePending && <Typography.Paragraph type="secondary">能带选择更新后，即可显示该选择的完整能量范围。</Typography.Paragraph>}
    </fieldset>
    {doc.kind === 'dos' && <>
      <ProjectionSelector view={view} atoms={doc.summary!.atoms} orbitals={doc.summary!.orbitals} onChange={patch} />
      <Checkbox checked={view.mirror_down} disabled={doc.summary!.spin_mode !== 'collinear'} onChange={event => patch({ mirror_down: event.target.checked })}>向下自旋镜像显示</Checkbox>
    </>}
    <div className="pp-save-status" role="status">
      {axisError ? <Typography.Text type="warning">{axisError}；补全后自动更新。</Typography.Text>
        : status === 'saving' || status === 'scheduled' ? <Space><Spin size="small" /><Typography.Text type="secondary">正在自动保存并更新图形…</Typography.Text></Space>
          : status === 'saved' ? <Typography.Text type="secondary">已自动保存</Typography.Text> : null}
    </div>
    {appliedCurves ? <Plot data={appliedCurves} exportEnabled={exportEnabled} stateLabel={graphStateLabel} /> : curves.isPending && <div className="pp-scientific-plot pp-plot-loading"><Spin /></div>}
    {error && (status === 'conflict' ? <Alert type="warning" title="分析配置发生冲突，当前选择已保留" description={<>{error}<br />“载入最新配置”恢复外部设置；“重新应用当前选择”会以本页完整选择覆盖最新设置。</>} action={<Space direction="vertical"><Button size="small" onClick={() => void loadLatest()}>载入最新配置</Button><Button size="small" disabled={!!axisError} onClick={retry}>重新应用当前选择</Button></Space>} /> : <Alert type="error" title="自动保存失败，当前选择已保留" description={error} action={<Button size="small" disabled={!!axisError} onClick={retry}>重试自动保存</Button>} />)}
    {curves.error && <Alert type="error" title={errorText(curves.error)} action={<Button size="small" onClick={() => void curves.refetch()}>重试图形</Button>} />}
  </Card>;
}
export default function PostprocessingPage() {
  const client = useQueryClient();
  const [params, setParams] = useSearchParams();
  const id = params.get('analysis') || '';
  const [sourceMode, setSourceMode] = useState<'local' | 'task'>(params.has('task') ? 'task' : 'local');
  const [taskSource, setTaskSource] = useState<Partial<PPTaskIdentity>>(() => ({ project_id: params.get('project') ?? '', task_id: params.get('task') ?? '', job_key: params.get('job') ?? '', attempt_id: params.get('attempt') ?? '' }));
  const [refreshRequest, setRefreshRequest] = useState(0);
  const [kind, setKind] = useState<'dos' | 'band'>('dos');
  const [title, setTitle] = useState('');
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState('');
  const [error, setError] = useState('');
  const abort = useRef<AbortController | null>(null);
  useEffect(() => () => abort.current?.abort(), []);
  const listing = useQuery({ queryKey: ['pp-datasets'], queryFn: ({ signal }) => ppApi.list(signal) });
  const current = useQuery({ queryKey: ['pp-dataset', id], queryFn: async ({ signal }) => {
    const response = await ppApi.get(id, signal);
    const cached = client.getQueryData<{ dataset: PPDataset }>(['pp-dataset', id]);
    // A read started before cancel/retry must not restore its old terminal
    // status after a newer action response, which would stop download polling.
    return cached && cached.dataset.revision > response.dataset.revision ? cached : response;
  }, enabled: !!id,
    refetchInterval: query => ['processing', 'downloading'].includes(query.state.data?.dataset.status ?? '') ? 1000 : false });
  const doc = current.data?.dataset;
  const downloading = doc?.download?.status === 'downloading';
  const cachedTaskSource = doc?.source?.kind === 'task_result' && doc.download?.status === 'cached';
  const update = useCallback((next: PPDataset) => {
    const existing = client.getQueryData<{ dataset: PPDataset }>(['pp-dataset', next.id]);
    if (existing && existing.dataset.revision >= next.revision) return;
    client.setQueryData(['pp-dataset', next.id], { dataset: next });
    void client.invalidateQueries({ queryKey: ['pp-datasets'] });
  }, [client]);
  async function importFiles() {
    setError('');
    if (!files.length || files.some(f => !allowed.has(f.name)) || new Set(files.map(f => f.name)).size !== files.length) { setError('请选择不重名的原始文件：vasprun.xml、DOSCAR、EIGENVAL、INCAR、KPOINTS、POSCAR／CONTCAR。'); return; }
    if (files.some(f => !f.size || f.size > 64 * 1024 ** 2) || files.reduce((s, f) => s + f.size, 0) > 128 * 1024 ** 2) { setError('文件不能为空，单文件最多 64 MiB，一批最多 128 MiB。'); return; }
    setBusy(true); abort.current = new AbortController();
    try {
      const created = (await ppApi.create(kind, title)).dataset; update(created); setParams({ analysis: created.id });
      for (let i = 0; i < files.length; i++) { setProgress(`正在保存 ${i + 1}/${files.length}：${files[i].name}`); update((await ppApi.upload(created.id, files[i], abort.current.signal)).dataset); }
      update((await ppApi.start(created.id)).dataset); setFiles([]); setProgress('文件已保存，正在解析');
    } catch (e) { setError(errorText(e)); } finally { setBusy(false); setProgress(''); void client.invalidateQueries({ queryKey: ['pp-datasets'] }); }
  }
  async function action(work: () => Promise<unknown>) { setError(''); try { await work(); } catch (e) { setError(errorText(e)); } }
  return <div className="wf-page pp-page">
    <div className="wf-page-heading"><div><Typography.Title level={3}>结果后处理</Typography.Title><p>从任务结果或本地文件建立 DOS／能带分析；缓存后的解析与绘图无需连接超算。</p></div><Space wrap><Link to="/toolbox/postprocessing/energy">吸附能与形成能</Link><Tag>任务取回 · 本地分析</Tag></Space></div>
    <div className="pp-grid">
      <aside>
        <Card title="导入新的分析">
          <div className="pp-controls pp-single">
            <label>结果来源<Select aria-label="结果来源" value={sourceMode} disabled={busy} onChange={setSourceMode} options={[{ value: 'local', label: '本地文件' }, { value: 'task', label: 'Toolbox 任务结果' }]} /></label>
            <label>分析类型<Select aria-label="分析类型" value={kind} disabled={busy} onChange={setKind} options={[{ value: 'dos', label: 'DOS／投影 DOS' }, { value: 'band', label: '普通能带' }]} /></label>
            <label>分析名称<Input aria-label="分析名称" value={title} maxLength={120} disabled={busy} onChange={e => setTitle(e.target.value)} placeholder="例如 Si 静态计算" /></label>
          </div>
          <Typography.Paragraph>{kind === 'dos' ? '优先选择 vasprun.xml（含投影时可选原子／轨道）；也可选择 DOSCAR＋INCAR 查看总 DOS。' : '选择 vasprun.xml＋原始 line-mode KPOINTS；或 EIGENVAL＋INCAR＋POSCAR／CONTCAR＋KPOINTS。普通 k 网格不能作为路径能带。'}</Typography.Paragraph>
          {sourceMode === 'task' ? <TaskSourceImport kind={kind} title={title} initial={taskSource} refreshRequest={refreshRequest} datasets={listing.data?.datasets ?? []} disabled={busy} onBusy={setBusy} onDataset={next => { update(next); setParams({ analysis: next.id }); }} /> : <>
          <label className="pp-file-picker">选择本地文件<input aria-label="选择本地文件" type="file" multiple disabled={busy} onChange={event => { setFiles(Array.from(event.target.files || [])); event.target.value = ''; }} /></label>
          <ul className="pp-file-list">{files.map(file => <li key={file.name}>{file.name} · {(file.size / 1024 ** 2).toFixed(2)} MiB</li>)}</ul>
          <Space wrap><Button type="primary" disabled={!files.length || busy} loading={busy} onClick={() => void importFiles()}>导入并解析</Button>{busy && <Button onClick={() => abort.current?.abort()}>取消上传</Button>}</Space>
          {progress && <Typography.Paragraph role="status">{progress}</Typography.Paragraph>}
          <Typography.Paragraph type="secondary">单文件 ≤64 MiB，总计 ≤128 MiB。仅保存所选文件的副本，不修改原文件。暂不支持压缩文件、NCL／SOC。</Typography.Paragraph>
          </>}
        </Card>
        <Card title="已保存的分析" extra={<Button size="small" onClick={() => void listing.refetch()}>刷新</Button>}>
          {listing.error && <Alert type="error" title={errorText(listing.error)} />}
          {listing.data?.datasets.length === 0 && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="导入后会保存在本机" />}
          <ul className="pp-history">{listing.data?.datasets.map(item => <li key={item.id}><button disabled={busy} aria-current={item.id === id ? 'true' : undefined} onClick={() => setParams({ analysis: item.id })}>{item.title}<small>{item.kind.toUpperCase()} · {labels[item.id === doc?.id ? doc.status : item.status]}</small></button></li>)}</ul>
        </Card>
      </aside>
      <section className="pp-detail">
        {error && <Alert type="error" showIcon title={error} closable onClose={() => setError('')} />}
        {current.error && <Alert type="error" title={errorText(current.error)} />}
        {!id && <Card><Empty description="选择任务结果或本地文件建立分析，或打开已保存的分析" /></Card>}
        {doc && <Card title={doc.title} extra={<Button danger size="small" disabled={busy || doc.status === 'processing' || downloading} onClick={() => void action(async () => { await ppApi.remove(doc.id); forgetAnalysisViewSession(doc.id); setParams({}); await client.invalidateQueries({ queryKey: ['pp-datasets'] }); })}>删除本地快照</Button>}>
          <Space wrap><Tag>{labels[doc.status]}</Tag>{doc.source?.kind !== 'task_result' && <Typography.Text type="secondary">本地导入 · 独立副本</Typography.Text>}</Space>
          {doc.source?.kind === 'task_result' && <TaskSourceDetails source={doc.source} />}
          <details><summary>来源文件与指纹</summary><ul className="pp-file-list">{doc.files.map(file => <li key={file.name}><strong>{file.name}</strong> · {file.size_bytes.toLocaleString()} bytes<code>{file.sha256}</code></li>)}</ul></details>
          {doc.error && <Alert type="error" title={doc.error.message} description={doc.error.code} />}
          {doc.download && <div className="pp-download-status" role="status">
            {downloading ? <>
              <Typography.Paragraph>正在取回 {doc.download.current_file ?? '所选文件'}：{doc.download.completed_files}/{doc.download.total_files} 个文件，{doc.download.completed_bytes.toLocaleString()}/{doc.download.total_bytes.toLocaleString()} bytes</Typography.Paragraph>
              <Progress aria-label="文件取回进度" percent={doc.download.total_bytes ? Math.min(100, Math.floor(doc.download.completed_bytes / doc.download.total_bytes * 100)) : 0} />
              <Button disabled={busy} onClick={() => void action(async () => update((await ppApi.cancel(doc.id)).dataset))}>取消取回</Button>
            </> : doc.download.status === 'cached' ? <Typography.Paragraph type="secondary">文件已缓存，后续解析与重新绘图均使用本地副本。CSV／JSON 导出保留任务来源与文件指纹。</Typography.Paragraph> : <>
              <Alert type={doc.download.status === 'cancelled' ? 'info' : 'error'} title={doc.download.status === 'cancelled' ? '文件取回已取消，尚未形成完整缓存。' : '文件取回失败，尚未形成完整缓存。'} description={doc.download.error?.message} />
              <Button disabled={busy} onClick={() => void action(async () => update((await ppApi.retryDownload(doc.id)).dataset))}>重试下载已确认文件</Button>
            </>}
          </div>}
          {doc.status === 'processing' && <Space wrap><Spin size="small" /><span>独立进程解析中，切换页面后仍可返回查看。</span><Button onClick={() => void action(async () => update((await ppApi.cancel(doc.id)).dataset))}>取消解析</Button></Space>}
          {['draft', 'failed', 'cancelled'].includes(doc.status) && (!doc.download || cachedTaskSource) && <Button type={cachedTaskSource ? 'primary' : 'default'} disabled={busy || !doc.files.length} onClick={() => void action(async () => update((await ppApi.start(doc.id)).dataset))}>{cachedTaskSource ? (doc.status === 'draft' ? '解析缓存文件并查看图形' : '重试解析缓存文件') : '解析已保存文件'}</Button>}
          {doc.source?.kind === 'task_result' && <Button className="pp-refresh-source" disabled={busy || downloading} onClick={() => { if (doc.source?.kind !== 'task_result') return; setTaskSource(doc.source); setKind(doc.kind); setTitle(`${doc.title}（新快照）`.slice(0, 120)); setSourceMode('task'); setRefreshRequest(value => value + 1); }}>刷新远端并新建分析</Button>}
        </Card>}
        {doc?.status === 'ready' && <Analysis key={doc.id} doc={doc} update={update} />}
      </section>
    </div>
  </div>;
}
