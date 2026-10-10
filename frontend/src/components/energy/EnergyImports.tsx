import { useState } from 'react';
import { Alert, Button, Input, Select, Typography } from 'antd';
import type { EnergyCollection, EnergyManualInput } from '../../api/energy';
import { basisOptions, energyError, parseComposition, parseEnergy } from './energyDraft';

export type LocalEnergyFile = { id: string; file: File; relativePath: string };
type Props = {
  collection: EnergyCollection; disabled: boolean;
  onFiles: (files: LocalEnergyFile[], onImported: (id: string) => void) => Promise<void>;
  onManual: (input: EnergyManualInput) => Promise<boolean>;
  onCsv: (file: File) => Promise<boolean>;
};
export default function EnergyImports({ collection, disabled, onFiles, onManual, onCsv }: Props) {
  const [mode, setMode] = useState<'local' | 'manual' | 'csv'>('local');
  const [files, setFiles] = useState<LocalEnergyFile[]>([]);
  const [csv, setCsv] = useState<File>();
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
      <label className="energy-file-picker">导入 UTF-8 CSV<input aria-label="选择能量 CSV" type="file" accept=".csv,text/csv" disabled={disabled} onChange={event => { setCsv(event.target.files?.[0]); event.target.value = ''; }} /></label>
      <Typography.Text type="secondary">列：name、relative_path、composition、energy_basis、energy_ev、unit、reference_note。组成填写 JSON，单位明确为 eV；能量字段使用对应键名。</Typography.Text>
      <details className="energy-details"><summary>CSV 格式示例</summary><pre>{'name,relative_path,composition,energy_basis,energy_ev,unit,reference_note\n合成 Pt 表面,,"{""Pt"":4}",sigma_to_zero_ev,-100,eV,计量合成示例'}</pre><p>能量字段键名：sigma_to_zero_ev、without_entropy_ev、free_energy_toten_ev。示例只演示格式，不是科研结果。</p></details>
      {csv && <Typography.Text>{csv.name}</Typography.Text>}
      <Button type="primary" disabled={disabled || !csv} onClick={() => { if (csv) void onCsv(csv).then(succeeded => { if (succeeded) setCsv(undefined); }); }}>读取 CSV 并加入确认表</Button>
    </div>}
    {error && <Alert type="error" title={error} />}
  </div>;
}
