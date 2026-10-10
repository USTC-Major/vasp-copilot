import { useState } from 'react';
import { Alert, Button, Input, Select, Typography } from 'antd';
import { energyApi, type EnergyBasis, type EnergyCollection, type EnergyCsvPreview, type EnergyManualInput } from '../../api/energy';
import { basisLabel, basisOptions, compositionLabel, energyError, energyNumber, parseComposition, parseEnergy, saveEnergyBlob } from './energyDraft';

export type LocalEnergyFile = { id: string; file: File; relativePath: string };
type Props = {
  collection: EnergyCollection; disabled: boolean; draftDirty: boolean;
  onFiles: (files: LocalEnergyFile[], onImported: (id: string) => void) => Promise<void>;
  onManual: (input: EnergyManualInput) => Promise<boolean>;
  onCsvPreview: (file: File, basis?: EnergyBasis) => Promise<{ preview: EnergyCsvPreview; revision: number }>;
  onCsv: (file: File, basis: EnergyBasis | undefined, previewRevision: number) => Promise<boolean>;
};
export default function EnergyImports({ collection, disabled, draftDirty, onFiles, onManual, onCsvPreview, onCsv }: Props) {
  const [mode, setMode] = useState<'local' | 'manual' | 'csv'>('local');
  const [files, setFiles] = useState<LocalEnergyFile[]>([]);
  const [csv, setCsv] = useState<File>();
  const [csvBasis, setCsvBasis] = useState<EnergyBasis | 'file'>('file');
  const [csvPreview, setCsvPreview] = useState<{ collectionId: string; file: File; basis: EnergyBasis | 'file'; revision: number; data: EnergyCsvPreview; invalidated?: boolean }>();
  const [readingPreview, setReadingPreview] = useState(false);
  const [downloadingTemplate, setDownloadingTemplate] = useState(false);
  const [name, setName] = useState('');
  const [composition, setComposition] = useState('');
  const [energy, setEnergy] = useState('');
  const [basis, setBasis] = useState<EnergyManualInput['energy_basis']>('sigma_to_zero_ev');
  const [note, setNote] = useState('');
  const [error, setError] = useState('');
  const bytes = files.reduce((sum, item) => sum + item.file.size, 0);
  const usedBytes = collection.samples.reduce((sum, sample) => sum + (sample.source.size_bytes ?? 0), 0);
  const selectionError = files.some(item => !item.file.size || item.file.size > collection.limits.max_file_bytes) ? '所选文件为空或超过单文件 64 MiB 上限。'
    : bytes + usedBytes > collection.limits.max_collection_bytes ? '所选文件与当前比较集缓存超过 128 MiB 上限。'
    : files.length + collection.samples.length > collection.limits.max_samples ? `当前比较集最多 ${collection.limits.max_samples} 个样本。` : '';
  const previewCurrent = !!csvPreview && !csvPreview.invalidated && csvPreview.collectionId === collection.id && csvPreview.file === csv && csvPreview.basis === csvBasis && csvPreview.revision === collection.revision && !draftDirty;
  const canImportCsv = previewCurrent && csvPreview.data.can_import && !csvPreview.data.issues.length && csvPreview.data.rows.every(row => !row.issues.length);
  async function downloadTemplate() {
    if (downloadingTemplate) return;
    setDownloadingTemplate(true); setError('');
    try { saveEnergyBlob(await energyApi.template(csvBasis === 'file' ? undefined : csvBasis), 'energy-samples-template.csv'); }
    catch (cause) { setError(energyError(cause)); }
    finally { setDownloadingTemplate(false); }
  }
  async function previewCsv() {
    if (!csv || disabled || readingPreview) return;
    const file = csv; const basis = csvBasis;
    setReadingPreview(true); setError('');
    try { const response = await onCsvPreview(file, basis === 'file' ? undefined : basis); setCsvPreview({ collectionId: collection.id, file, basis, revision: response.revision, data: response.preview }); }
    catch (cause) { setError(energyError(cause)); setCsvPreview(undefined); }
    finally { setReadingPreview(false); }
  }
  async function addManual() {
    setError('');
    try {
      if (!name.trim()) throw new Error('请填写样本名称。');
      if (!note.trim()) throw new Error('请注明手填数据来源及参考定义。');
      const succeeded = await onManual({ name: name.trim(), composition: parseComposition(composition), energy_fields: { [basis]: parseEnergy(energy) }, energy_basis: basis, unit: 'eV', reference_note: note.trim() });
      if (succeeded) { setName(''); setComposition(''); setEnergy(''); setNote(''); }
    } catch (cause) { setError(energyError(cause)); }
  }
  return <div>
    <div className="energy-import-tabs" aria-label="本地能量来源类型">
      {([{ key: 'local', label: '批量 OUTCAR' }, { key: 'manual', label: '手填' }, { key: 'csv', label: 'CSV' }] as const).map(item => <Button key={item.key} type={mode === item.key ? 'primary' : 'default'} disabled={disabled} aria-pressed={mode === item.key} onClick={() => { setMode(item.key); setError(''); }}>{item.label}</Button>)}
    </div>
    {mode === 'local' && <div className="energy-import-form">
      <label className="energy-file-picker">选择多份 OUTCAR
        <input aria-label="选择能量 OUTCAR" type="file" multiple disabled={disabled} onChange={event => {
          setFiles(previous => [...previous, ...Array.from(event.target.files ?? []).map(file => ({ id: crypto.randomUUID(), file, relativePath: file.webkitRelativePath ?? '' }))]); event.target.value = ''; setError('');
        }} />
      </label>
      <ul className="energy-files">{files.map(item => <li key={item.id}><strong>{item.file.name} · {(item.file.size / 1024 ** 2).toFixed(2)} MiB</strong>
        <Input aria-label={`相对目录 ${item.id}`} placeholder="相对目录／来源标签（可留空）" value={item.relativePath} disabled={disabled} onChange={event => setFiles(previous => previous.map(file => file.id === item.id ? { ...file, relativePath: event.target.value } : file))} />
        <Button size="small" type="text" disabled={disabled} onClick={() => setFiles(previous => previous.filter(file => file.id !== item.id))}>移除待导入文件</Button>
      </li>)}</ul>
      <Typography.Text type="secondary">同名 OUTCAR 独立保存；相对目录仅作来源元数据。普通文件选择无法取得原目录时，可自行填写中文或空格标签。</Typography.Text>
      {selectionError && <Alert type="error" title={selectionError} />}
      <Button type="primary" disabled={disabled || !files.length || !!selectionError} onClick={() => void onFiles(files, id => setFiles(previous => previous.filter(item => item.id !== id)))}>批量读取并加入确认表</Button>
      <p className="energy-note">64 MiB／文件 · 128 MiB／比较集 · 最多 {collection.limits.max_samples} 个样本。读取后逐项保留原始值、来源指纹和计算状态。</p>
    </div>}
    {mode === 'manual' && <div className="energy-import-form">
      <div className="energy-manual-grid">
        <label className="energy-field">名称<Input aria-label="手填样本名称" value={name} disabled={disabled} onChange={event => setName(event.target.value)} /></label>
        <label className="energy-field">元素计数<Input aria-label="手填组成" value={composition} disabled={disabled} placeholder="例如 Pt:4 C:1 O:1" onChange={event => setComposition(event.target.value)} /></label>
        <label className="energy-field">能量口径<Select aria-label="手填能量字段" value={basis} options={basisOptions} disabled={disabled} onChange={setBasis} /></label>
        <label className="energy-field">能量（eV）<Input aria-label="手填能量 eV" value={energy} disabled={disabled} placeholder="有限数值，不能缺省补零" onChange={event => setEnergy(event.target.value)} /></label>
        <label className="energy-field energy-wide">来源与参考定义<Input.TextArea aria-label="手填参考说明" value={note} rows={2} disabled={disabled} onChange={event => setNote(event.target.value)} /></label>
      </div>
      <Typography.Text type="secondary">手填来源的完成与收敛状态保持未知，计算前需要在确认表显式接受。组内所有参与项使用同一种已声明能量字段。</Typography.Text>
      <Button type="primary" disabled={disabled} onClick={() => void addManual()}>添加手填样本</Button>
    </div>}
    {mode === 'csv' && <div className="energy-import-form">
      <Button disabled={downloadingTemplate} loading={downloadingTemplate} onClick={() => void downloadTemplate()}>下载 CSV 模板</Button>
      <Typography.Text type="secondary">下载空白模板，填写名称、元素计数、能量（eV）与来源说明。元素计数例如 Pt:4 C:1 O:1。模板不含科研数值。</Typography.Text>
      <label className="energy-file-picker">导入 UTF-8 CSV<input aria-label="选择能量 CSV" type="file" accept=".csv,text/csv" disabled={disabled} onChange={event => { setCsv(event.target.files?.[0]); setCsvPreview(previous => previous ? { ...previous, invalidated: true } : previous); event.target.value = ''; }} /></label>
      <label className="energy-field">CSV 能量口径<Select aria-label="CSV 能量口径" value={csvBasis} disabled={disabled} options={[{ value: 'file', label: '按文件声明' }, ...basisOptions]} onChange={value => { setCsvBasis(value); setCsvPreview(previous => previous ? { ...previous, invalidated: true } : previous); }} /></label>
      <Typography.Text type="secondary">按文件声明会逐行保留原口径。选择某个字段只补充未声明的行；文件已声明不同口径时会明确报冲突并阻止导入。旧 JSON 元素计数仍可读取。</Typography.Text>
      {csv && <Typography.Text>{csv.name}</Typography.Text>}
      <Button disabled={disabled || !csv || readingPreview || (draftDirty && (collection.workflow === 'cards' || !!collection.card_projection))} loading={readingPreview} onClick={() => void previewCsv()}>{draftDirty && (collection.workflow === 'cards' || !!collection.card_projection) ? '先保存共享修改再预览 CSV' : draftDirty ? '保存草稿并预览 CSV' : '预览 CSV'}</Button>
      {csvPreview && !previewCurrent && <Alert type="warning" title="CSV 预览已失效，请重新预览。" description="文件、口径选择或当前分析修订发生变化后，旧预览不能用于导入。" />}
      {previewCurrent && csvPreview && <section aria-label="CSV 导入预览"><Typography.Text>预览 {csvPreview.data.row_count} 行，{csvPreview.data.valid_count} 行通过检查。尚未导入。</Typography.Text>{!!csvPreview.data.issues.length && <Alert type="error" title="CSV 文件存在错误，不能导入。" description={<ul>{csvPreview.data.issues.map((issue, index) => <li key={`${issue.code}-${index}`}>{issue.message}</li>)}</ul>} />}<div className="energy-table-scroll"><table className="energy-csv-preview"><thead><tr><th>行号</th><th>名称</th><th>元素计数</th><th>能量口径</th><th>能量（eV）</th><th>来源说明</th><th>检查结果</th></tr></thead><tbody>{csvPreview.data.rows.map(row => <tr key={row.row_number}><td>{row.row_number}</td><td>{row.name || '缺失'}</td><td>{compositionLabel(row.composition) || '缺失'}</td><td>{row.energy_basis ? basisLabel(row.energy_basis) : '缺失'}</td><td>{energyNumber(row.energy_ev)}</td><td>{row.relative_path && <div>{row.relative_path}</div>}{row.reference_note || '未提供'}</td><td>{row.issues.length ? <ul className="energy-hard-error">{row.issues.map((issue, index) => <li key={`${issue.code}-${index}`}>{issue.message}</li>)}</ul> : '通过'}</td></tr>)}</tbody></table></div></section>}
      <Button type="primary" disabled={disabled || !csv || !canImportCsv || readingPreview} onClick={() => { if (csv && csvPreview && canImportCsv) void onCsv(csv, csvBasis === 'file' ? undefined : csvBasis, csvPreview.revision).then(succeeded => { if (succeeded) { setCsv(undefined); setCsvPreview(undefined); } }); }}>确认导入预览中的 CSV</Button>
      <p className="energy-note">CSV 回导仅建立新的表格来源，运行与收敛状态为未知；不会继承原分析的锁定、核对、风险接受或 OUTCAR 来源验证。</p>
    </div>}
    {error && <Alert type="error" title={error} />}
  </div>;
}
