import { useState } from 'react';
import { Alert, Button, Select } from 'antd';
import type { EnergyBasis, EnergySampleValueSource } from '../../api/energy';
import { basisOptions, energyError } from './energyDraft';

type Props = { initialBasis: EnergyBasis; dirty: boolean; disabled: boolean; onExport: (basis: EnergyBasis, source: EnergySampleValueSource) => Promise<void> };
export default function EnergySampleExport({ initialBasis, dirty, disabled, onExport }: Props) {
  const [basis, setBasis] = useState(initialBasis);
  const [valueSource, setValueSource] = useState<EnergySampleValueSource>('effective');
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState('');
  async function download() {
    if (disabled || downloading) return;
    setDownloading(true); setError('');
    try { await onExport(basis, valueSource); }
    catch (cause) { setError(energyError(cause)); }
    finally { setDownloading(false); }
  }
  return <section className="energy-sample-export" aria-label="可导入样本表导出"><div className="energy-group-grid"><label className="energy-field">样本表能量字段<Select aria-label="样本表能量字段" value={basis} options={basisOptions} disabled={disabled || downloading} onChange={setBasis} /></label><label className="energy-field">样本表取值<Select aria-label="样本表取值" value={valueSource} disabled={disabled || downloading} options={[{ value: 'effective', label: '有效值（包含人工修订）' }, { value: 'original', label: '原始值（忽略人工修订）' }]} onChange={setValueSource} /></label></div><p className="energy-note">{valueSource === 'effective' ? '有效值使用当前人工修订后的组成和所选能量，并保留原始能量与人工修订说明。' : '原始值使用导入时的组成和所选能量，忽略人工覆盖值。'} 样本表无需计算结果；所选能量字段缺失时会明确报错。回导后按新的 CSV 来源处理，状态保持未知并重新核对。</p><Button disabled={disabled || downloading} loading={downloading} onClick={() => void download()}>{dirty ? '保存草稿并导出可导入样本表' : '导出可导入样本表'}</Button>{error && <Alert type="error" title={error} />}</section>;
}
