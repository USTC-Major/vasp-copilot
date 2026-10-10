import { Button, Checkbox, Input, Select, Tag } from 'antd';
import type { EnergyCollection, EnergyGroup, EnergySample } from '../../api/energy';
import { analysisKind, basisLabel, basisOptions, compositionLabel, effectiveComposition, effectiveEnergy, energyNumber, riskRequired, roleLabels, type EnergyDraft, type EnergyRowDraft } from './energyDraft';

const convergence = (value: boolean | null) => value === true ? '已收敛' : value === false ? '未收敛' : '未知';
const completion = (value: string) => ({ completed: '已结束', incomplete: '未完成', truncated: '截断', unknown: '未知' }[value] ?? '未完成／未知');
const sourceLabels = { local_upload: '本地 OUTCAR', manual: '手填', csv: 'CSV', task_result: '任务快照' };
type Props = {
  collection: EnergyCollection; draft: EnergyDraft; disabled: boolean;
  selectedIds: string[]; onSelection: (ids: string[]) => void; onDelete: () => void; onClear: () => void;
  onRow: (id: string, patch: Partial<EnergyRowDraft>, scientific?: boolean) => void;
  onIncludeAll: (included: boolean) => void;
  onAcceptRisks: (accepted: boolean) => void;
};
function sampleGroups(sampleId: string, groups: EnergyGroup[]) {
  return groups.filter(group => group.targets.some(target => target.sample_id === sampleId) || (group.kind === 'adsorption' ? group.clean_sample_id === sampleId || group.adsorbate_sample_id === sampleId : Object.values(group.element_references).includes(sampleId)));
}
function SourceDetails({ sample }: { sample: EnergySample }) {
  const csvMetadata = sample.source.csv_metadata;
  return <details className="energy-details"><summary>原值与来源明细</summary><dl>
    <dt>样本 ID</dt><dd>{sample.id}</dd><dt>原文件／名称</dt><dd>{sample.source.original_name ?? sample.name}</dd>
    <dt>相对目录</dt><dd>{sample.source.relative_path || '未知／未提供'}</dd><dt>SHA-256</dt><dd>{sample.source.sha256 ?? '人工记录'}</dd>
    <dt>导入时间</dt><dd>{sample.source.imported_at ?? '未知'}</dd>
    <dt>原始参考与来源说明</dt><dd className="energy-source-note">{sample.source.reference_note || '未提供'}</dd>
    <dt>运行段／离子步</dt><dd>{sample.parsed.provenance.run_segment ?? '未知'} / {sample.parsed.provenance.selected_ionic_step ?? '未知'}</dd>
    {basisOptions.map(option => <div key={option.value} style={{ display: 'contents' }}><dt>{option.label}</dt><dd>{energyNumber(sample.parsed.energy_fields[option.value])} eV</dd></div>)}
    <dt>解析组成</dt><dd>{compositionLabel(sample.parsed.composition) || '缺失'}</dd>
    {sample.source.kind === 'csv' && <><dt>CSV 声明取值方式</dt><dd>{csvMetadata?.value_source === 'original' ? '原始值（CSV 声明）' : csvMetadata?.value_source === 'effective' ? '有效值（CSV 声明，可能包含人工修订）' : '未声明／未知'}</dd><dt>CSV 附注核查状态</dt><dd>以下附带说明由 CSV 文件提供，未经来源验证；不代表已核对或已接受风险。</dd>
      {!!csvMetadata?.source_notes?.length && <><dt>CSV 附带来源说明</dt><dd><ul>{csvMetadata.source_notes.map((note, index) => <li className="energy-source-note" key={index}>{note}</li>)}</ul></dd></>}
      {!!csvMetadata?.override_notes?.length && <><dt>CSV 附带人工修订说明</dt><dd><ul>{csvMetadata.override_notes.map((note, index) => <li className="energy-source-note" key={index}>{note}</li>)}</ul></dd></>}
      {!!csvMetadata?.original_energies?.length && <><dt>CSV 附带原始能量</dt><dd><ul>{csvMetadata.original_energies.map((original, index) => <li key={index}>{basisLabel(original.energy_basis)}：{energyNumber(original.energy_ev)} eV</li>)}</ul></dd></>}
    </>}
    {sample.source.kind === 'task_result' && <><dt>项目／任务</dt><dd>{sample.source.project_id} / {sample.source.task_id}</dd><dt>作业／尝试</dt><dd>{sample.source.job_key} / {sample.source.attempt_id}</dd><dt>作业号</dt><dd>{sample.source.slurm_id ?? '未知'}</dd><dt>快照指纹</dt><dd>{sample.source.snapshot_sha256 ?? sample.source.sha256 ?? '未知'}</dd></>}
  </dl><details><summary>方法参数与取值源行</summary><pre>{JSON.stringify({ metadata: sample.parsed.metadata, provenance: sample.parsed.provenance }, null, 2)}</pre></details></details>;
}
export default function EnergyConfirmationTable({ collection, draft, disabled, selectedIds, onSelection, onDelete, onClear, onRow, onIncludeAll, onAcceptRisks }: Props) {
  const kind = analysisKind(collection);
  const availableRoles = Object.entries(roleLabels).filter(([value]) => !kind || (kind === 'adsorption' ? ['clean_slab', 'adsorbate', 'adsorbed'] : ['material', 'element_reference']).includes(value));
  const included = draft.rows.filter(row => row.included);
  const risky = included.filter(row => { const sample = collection.samples.find(item => item.id === row.sample_id); return !!sample && riskRequired(sample); });
  const risksAccepted = risky.length > 0 && risky.every(row => row.accepted_warnings);
  return <>
    <div className="energy-summary-strip"><span><strong>{draft.rows.length}</strong>来源样本</span><span><strong>{included.length}</strong>已纳入</span><span><strong>{included.filter(row => row.confirmed).length}</strong>已人工确认</span><span><strong>{risky.length}</strong>需接受风险状态</span></div>
    <div className="energy-table-actions">
      <Checkbox aria-label="管理全选" disabled={disabled} checked={selectedIds.length === draft.rows.length && !!draft.rows.length} indeterminate={!!selectedIds.length && selectedIds.length < draft.rows.length} onChange={event => onSelection(event.target.checked ? draft.rows.map(row => row.sample_id) : [])}>选择全部用于管理</Checkbox>
      <span aria-live="polite">已选 {selectedIds.length} 个样本</span>
      <Button danger disabled={disabled || !selectedIds.length} onClick={onDelete}>删除所选</Button>
      <Button danger disabled={disabled || !draft.rows.length} onClick={onClear}>清空当前分析样本</Button>
    </div>
    <div className="energy-table-actions">
      <Checkbox disabled={disabled} checked={included.length === draft.rows.length && included.length > 0} indeterminate={included.length > 0 && included.length < draft.rows.length} onChange={event => onIncludeAll(event.target.checked)}>纳入全部样本</Checkbox>
      <Checkbox disabled={disabled || !risky.length} checked={risksAccepted} onChange={event => onAcceptRisks(event.target.checked)}>明确接受已纳入样本的未完成／未知状态及警告</Checkbox>
    </div>
    <div className="energy-table-scroll" tabIndex={0} role="region" aria-label="能量样本集中确认表，可横向滚动">
      <table className="energy-table"><thead><tr><th scope="col">管理选择</th><th scope="col">名称与来源</th><th scope="col">组成与所选能量</th><th scope="col">独立计算状态</th><th scope="col">角色与推荐依据</th><th scope="col">目标／参考用途</th><th scope="col">纳入与确认</th></tr></thead><tbody>
        {collection.samples.map(sample => {
          const row = draft.rows.find(item => item.sample_id === sample.id)!;
          const memberships = sampleGroups(sample.id, draft.groups);
          const bases = [...new Set(memberships.map(group => group.energy_basis))];
          const risk = riskRequired(sample);
          return <tr key={sample.id} data-sample-id={sample.id}>
            <td><Checkbox aria-label={`选择样本 ${sample.id}`} disabled={disabled} checked={selectedIds.includes(sample.id)} onChange={event => onSelection(event.target.checked ? [...selectedIds, sample.id] : selectedIds.filter(id => id !== sample.id))} /></td>
            <td><div className="energy-cell"><Input aria-label={`样本名称 ${sample.id}`} value={row.name} disabled={disabled} onChange={event => onRow(sample.id, { name: event.target.value }, false)} /><small>{sourceLabels[sample.source.kind]} · {sample.id.slice(-8)}</small><small>{sample.source.relative_path || '目录未知／未提供'}</small><SourceDetails sample={sample} /></div></td>
            <td><div className="energy-cell"><p>{compositionLabel(effectiveComposition(sample, row)) || <span className="energy-hard-error">组成缺失／无效</span>}{row.override?.composition && <Tag>人工组成</Tag>}</p>
              {bases.length ? bases.map(basis => <div key={basis}><small>{basisLabel(basis)}</small><strong>{energyNumber(effectiveEnergy(sample, row, basis))} eV</strong>{row.override?.energy && row.override.energy_basis === basis && <Tag>人工能量</Tag>}</div>) : <small>指定角色与参考用途后显示所选字段；原始三字段见来源明细。</small>}
              <details className="energy-details"><summary>人工补充／覆盖</summary>
                <Checkbox disabled={disabled} checked={!!row.override} onChange={event => onRow(sample.id, { override: event.target.checked ? { composition: '', energy: '', energy_basis: bases[0] ?? 'sigma_to_zero_ev', note: '' } : null })}>使用单独人工值</Checkbox>
                {row.override && <div className="energy-cell">
                  <Input aria-label={`人工组成 ${sample.id}`} disabled={disabled} value={row.override.composition} placeholder="留空保留原组成；例如 Pt:4 C:1 O:1" onChange={event => onRow(sample.id, { override: { ...row.override!, composition: event.target.value } })} />
                  <Select aria-label={`人工能量字段 ${sample.id}`} disabled={disabled} value={row.override.energy_basis} options={basisOptions} onChange={value => onRow(sample.id, { override: { ...row.override!, energy_basis: value } })} />
                  <Input aria-label={`人工能量 ${sample.id}`} disabled={disabled} value={row.override.energy} placeholder="eV；留空保留原能量" onChange={event => onRow(sample.id, { override: { ...row.override!, energy: event.target.value } })} />
                  <Input.TextArea aria-label={`人工值依据 ${sample.id}`} disabled={disabled} rows={2} value={row.override.note} placeholder="说明人工值来源／依据（必填）" onChange={event => onRow(sample.id, { override: { ...row.override!, note: event.target.value } })} />
                </div>}
              </details>
            </div></td>
            <td><div className="energy-cell"><div className="energy-status-lines"><span>运行：<strong>{completion(sample.parsed.status.completion)}</strong></span><span>电子：<strong>{convergence(sample.parsed.status.electronic_converged)}</strong></span><span>离子：<strong>{sample.parsed.status.ionic_applicability === 'not_applicable' ? '静态，不适用' : convergence(sample.parsed.status.ionic_converged)}</strong></span></div>
              {risk && <Tag color="gold">状态／警告需确认</Tag>}
              {(sample.parsed.errors.length > 0 || sample.parsed.warnings.length > 0 || sample.parsed.metadata.support?.reasons?.length) && <details className="energy-details"><summary>解析与适用性提示</summary><ul>{[...sample.parsed.errors, ...sample.parsed.warnings, ...(sample.parsed.metadata.support?.reasons ?? [])].map((warning, index) => <li key={`${index}-${warning}`}>{warning}</li>)}</ul></details>}
            </div></td>
            <td><div className="energy-cell"><Select aria-label={`确认角色 ${sample.id}`} value={row.role ?? undefined} placeholder="待人工指定" allowClear disabled={disabled} options={availableRoles.map(([value, label]) => ({ value, label }))} onSelect={value => onRow(sample.id, { role: value, role_origin: 'manual' })} onClear={() => onRow(sample.id, { role: null, role_origin: 'manual' })} />
              <Tag>{row.role_origin === 'manual' ? '人工指定' : row.role_origin === 'auto' ? '规则填入 · 待核对' : row.role ? '已有角色 · 待核对' : '待处理'}</Tag>
              {sample.assignment_reasons?.length ? <small>{sample.assignment_reasons[0]}</small> : <small>线索：{sample.role_suggestion.role ? roleLabels[sample.role_suggestion.role] : '证据不足'}</small>}
              <details className="energy-details"><summary>分配依据与线索</summary><ul>{(sample.assignment_reasons?.length ? sample.assignment_reasons : sample.role_suggestion.reasons).length ? (sample.assignment_reasons?.length ? sample.assignment_reasons : sample.role_suggestion.reasons).map((reason, index) => <li key={index}>{reason}</li>) : <li>证据不足，请依据组成及参考定义人工指定。</li>}</ul><small>文件名仅提供线索，不证明参考态正确。规则填入不会代替科学核对或风险接受。</small></details>
            </div></td>
            <td><div className="energy-cell">{row.role === 'adsorbed' || row.role === 'material' ? <small>{row.included ? '已按目标角色纳入当前分析' : '纳入后自动加入目标列表'}</small> : <small>在下方参考配置中选择用途。</small>}
              {memberships.map(group => <small key={group.id}>{group.name} · {group.targets.some(target => target.sample_id === sample.id) ? '目标' : '共用参考'}</small>)}
            </div></td>
            <td><div className="energy-cell"><Checkbox aria-label={`纳入 ${sample.id}`} disabled={disabled} checked={row.included} onChange={event => onRow(sample.id, { included: event.target.checked, included_origin: 'manual' })}>纳入计算</Checkbox>
              <Checkbox aria-label={`人工确认 ${sample.id}`} disabled={disabled || !row.included || !row.role} checked={row.confirmed} onChange={event => onRow(sample.id, { confirmed: event.target.checked }, false)}>已核对本行</Checkbox>
              {risk && <Checkbox aria-label={`接受风险 ${sample.id}`} disabled={disabled || !row.included} checked={row.accepted_warnings} onChange={event => onRow(sample.id, { accepted_warnings: event.target.checked }, false)}>接受本行状态</Checkbox>}
            </div></td>
          </tr>;
        })}
      </tbody></table>
    </div>
    <p className="energy-note">推荐不等于确认。缺能量、组成、必要参考或存在硬冲突时，风险接受不能放行；缺所选字段不会自动改用其他能量。科学输入修改只使受影响的核对失效，风险接受独立保留。统一确认不会替你接受风险。</p>
  </>;
}
