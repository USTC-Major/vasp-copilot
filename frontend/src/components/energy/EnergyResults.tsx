import { useState } from 'react';
import { Alert, Button, Space, Tag, Typography } from 'antd';
import { energyApi, type EnergyCollection } from '../../api/energy';
import { basisLabel, energyError, energyNumber, saveEnergyBlob } from './energyDraft';

export default function EnergyResults({ collection, valid }: { collection: EnergyCollection; valid: boolean }) {
  const [error, setError] = useState('');
  const [downloading, setDownloading] = useState(false);
  const warnings = collection.result ? [...new Set([...collection.result.warnings, ...collection.result.groups.flatMap(group => [...group.warnings, ...group.rows.flatMap(row => row.warnings)])])] : [];
  async function download(format: 'json' | 'csv') {
    if (!valid || downloading) return;
    setDownloading(true); setError('');
    try { saveEnergyBlob(await energyApi.download(collection.id, format), `${collection.id}.${format}`); }
    catch (cause) { setError(energyError(cause)); }
    finally { setDownloading(false); }
  }
  return <>
    <Space wrap><Button disabled={!valid || downloading} onClick={() => void download('csv')}>导出能量 CSV</Button><Button disabled={!valid || downloading} onClick={() => void download('json')}>导出能量 JSON</Button></Space>
    {valid && collection.result ? <>
      <Typography.Paragraph type="secondary">计算时间：{collection.result.calculated_at}。负值表示相对于所选参考能量降低；不证明全局最优、热力学稳定或相对于所有竞争相稳定。</Typography.Paragraph>
      {!!warnings.length && <Alert type="warning" title={`包含 ${warnings.length} 项状态与可比性提示`} description={<details className="energy-details"><summary>展开完整提示（导出保留全部状态标记）</summary><ul>{warnings.map(warning => <li key={warning}>{warning}</li>)}</ul></details>} />}
      <div className="energy-result-grid">{collection.result.groups.map(group => <section key={group.id} aria-label={`结果组 ${group.name}`}>
        <Typography.Title level={5}>{group.name} · {basisLabel(group.energy_basis)}</Typography.Title>
        {group.rows.map(row => <article className="energy-result-card" key={`${group.id}/${row.sample_id}`}>
          <div className="energy-result-title"><h4>{row.name}</h4><Space wrap>{!!row.warnings.length && <Tag color="gold">含状态／可比性提示</Tag>}<Tag>{group.kind === 'adsorption' ? '吸附能' : '材料形成能'}</Tag></Space></div>
          <div className="energy-result-values"><div><span>每目标计算胞</span><strong>{energyNumber(row.delta_ev)} eV</strong></div><div><span>{row.normalization === 'per_atom' ? '每原子' : '每吸附物'}</span><strong>{energyNumber(row.normalized_ev)} {row.normalization === 'per_atom' ? 'eV/原子' : 'eV/吸附物'}</strong></div></div>
          <details className="energy-details"><summary>公式、原始取值与逐项明细</summary><p>{row.formula}</p><ul className="energy-result-terms">{row.terms.map((term, index) => <li key={`${term.sample_id}/${index}`}>{collection.samples.find(sample => sample.id === term.sample_id)?.name ?? term.sample_id}{term.element ? `（${term.element}）` : ''}：{energyNumber(term.coefficient)} × {energyNumber(term.energy_ev)} eV = {energyNumber(term.contribution_ev)} eV</li>)}</ul></details>
        </article>)}
      </section>)}</div>
    </> : <p className="energy-note">{collection.result ? '输入已修改，旧结果已失效。保存并重新确认计算后可查看及导出。' : '尚未计算。完成确认表与参考绑定后，保存并计算以生成可追溯结果。'}</p>}
    {error && <Alert type="error" title={error} />}
  </>;
}
