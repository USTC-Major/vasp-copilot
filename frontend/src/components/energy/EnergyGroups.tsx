import { Input, InputNumber, Select } from 'antd';
import type { ReactNode } from 'react';
import type { EnergyCollection, EnergyFieldError, EnergyGroup } from '../../api/energy';
import { basisOptions, effectiveComposition, type EnergyDraft } from './energyDraft';
import { energyFieldKey } from './energyCards';

type Props = { collection: EnergyCollection; draft: EnergyDraft; disabled: boolean; nameDisabled?: boolean; errors?: EnergyFieldError[]; onValidate?: (group: EnergyGroup, field: string) => void; onGroup: (group: EnergyGroup, scientific?: boolean) => void };
export default function EnergyGroups({ collection, draft, disabled, nameDisabled = disabled, errors = [], onValidate, onGroup }: Props) {
  const options = draft.rows.map(row => ({ value: row.sample_id, label: `${row.name} · ${row.sample_id.slice(-8)}` }));
  const manual = (group: EnergyGroup, key: string) => ({ ...group, reference_origins: { ...group.reference_origins, [key]: 'manual' as const } });
  return <>{draft.groups.map(group => {
    const elements = [...new Set(group.targets.flatMap(target => {
      const sample = collection.samples.find(item => item.id === target.sample_id), row = draft.rows.find(item => item.sample_id === target.sample_id);
      return sample && row ? Object.keys(effectiveComposition(sample, row) ?? {}) : [];
    }))];
    const fieldErrors = (field: string) => errors.filter(error => error.card_id === group.id && error.field === field);
    const field = (path: string, label: string, control: ReactNode) => <label className="energy-field" data-energy-field={energyFieldKey(group.id, path)}>{label}{control}{fieldErrors(path).map((error, index) => <span key={`${error.code}-${index}`} className="energy-field-error" role="status">{error.message}</span>)}</label>;
    const invalid = (path: string) => fieldErrors(path).length ? 'error' as const : undefined;
    const select = (path: string, value: string, label: string, change: (value: string) => void) => field(path, label, <Select aria-label={`${label} ${group.id}`} aria-invalid={!!invalid(path)} status={invalid(path)} value={value || undefined} placeholder="从共享样本中选择" showSearch optionFilterProp="label" allowClear options={options} disabled={disabled} onChange={value => change(value ?? '')} />);
    return <section className="energy-card-fields" key={group.id} aria-label={`计算卡 ${group.name}`}>
      <div className="energy-group-grid">
        {field('name', '计算卡名称', <Input aria-label={`卡名称 ${group.id}`} status={invalid('name')} value={group.name} disabled={nameDisabled} onChange={event => onGroup({ ...group, name: event.target.value }, false)} />)}
        {field('energy_basis', '统一能量字段', <Select aria-label={`卡能量字段 ${group.id}`} aria-invalid={!!invalid('energy_basis')} status={invalid('energy_basis')} value={group.energy_basis} options={basisOptions} disabled={disabled} onChange={value => onGroup({ ...group, energy_basis: value })} />)}
        {group.kind === 'adsorption' && <>
          {select('clean_sample_id', group.clean_sample_id, '清洁表面参考', value => onGroup(manual({ ...group, clean_sample_id: value }, 'clean_sample_id')))}
          {select('adsorbate_sample_id', group.adsorbate_sample_id, '吸附物参考', value => onGroup(manual({ ...group, adsorbate_sample_id: value }, 'adsorbate_sample_id')))}
          {field('reference_units', '参考文件中的吸附物单元数 m', <InputNumber aria-label={`参考单元数 ${group.id}`} aria-invalid={!!invalid('reference_units')} status={invalid('reference_units')} value={group.reference_units || null} disabled={disabled} onBlur={() => onValidate?.(group, 'reference_units')} onChange={value => onGroup(manual({ ...group, reference_units: value ?? 0 }, 'reference_units'))} />)}
        </>}
      </div>
      {field('targets', group.kind === 'adsorption' ? '目标构型（可多选）' : '目标材料（可多选）', <Select mode="multiple" aria-label={`目标样本 ${group.id}`} aria-invalid={!!invalid('targets')} status={invalid('targets')} value={group.targets.map(target => target.sample_id)} placeholder="自由选择参与当前卡的样本" showSearch optionFilterProp="label" options={options} disabled={disabled} onChange={(ids: string[]) => {
        const next: EnergyGroup = group.kind === 'adsorption' ? { ...group, targets: ids.map(sampleId => ({ sample_id: sampleId, adsorbate_count: group.targets.find(target => target.sample_id === sampleId)?.adsorbate_count ?? 1 })) } : { ...group, targets: ids.map(sampleId => ({ sample_id: sampleId })) };
        onGroup(manual(next, 'targets'));
      }} />)}
      {group.kind === 'formation' && <div className="energy-group-grid">{elements.map(element => {
        const path = `element_references.${element}`;
        return select(path, group.element_references[element] ?? '', `${element} 元素参考`, value => { const references = { ...group.element_references }; if (value) references[element] = value; else delete references[element]; onGroup(manual({ ...group, element_references: references }, `element:${element}`)); });
      })}</div>}
      {field('reference_note', '参考态／单元定义', <Input.TextArea aria-label={`参考定义 ${group.id}`} status={invalid('reference_note')} rows={2} disabled={disabled} value={group.reference_note} placeholder={group.kind === 'adsorption' ? '例如每个 CO 分子；说明参考文件中 m 个相同分子的含义' : '注明用户选择的单质晶体／分子参考态及必要条件'} onChange={event => onGroup({ ...group, reference_note: event.target.value })} />)}
      {group.kind === 'adsorption' ? <>
        <div className="energy-formula">ΔE<sub>ads,j</sub> = E<sub>构型 j</sub> − E<sub>清洁表面</sub> − n<sub>j</sub> × E<sub>参考文件</sub> / m<br /><span className="energy-muted">每吸附物能差 = ΔE<sub>ads,j</sub> / n<sub>j</sub>。m、n 均为正整数；按实际分子／片段定义核对，组成比例不独自证明分子个数。</span></div>
        <div className="energy-group-grid">{group.targets.map(target => {
          const path = `targets.${target.sample_id}.adsorbate_count`;
          return <div key={target.sample_id}>{field(path, `${draft.rows.find(row => row.sample_id === target.sample_id)?.name ?? target.sample_id} · 吸附物数量 n`, <InputNumber aria-label={`吸附物数量 ${group.id} ${target.sample_id}`} aria-invalid={!!invalid(path)} status={invalid(path)} value={target.adsorbate_count || null} disabled={disabled} onBlur={() => onValidate?.(group, path)} onChange={value => onGroup(manual({ ...group, targets: group.targets.map(item => item.sample_id === target.sample_id ? { ...item, adsorbate_count: value ?? 0 } : item) }, `target:${target.sample_id}`))} />)}</div>;
        })}</div>
      </> : <div className="energy-formula">μ<sub>i</sub> = E<sub>元素参考 i</sub> / N<sub>参考原子 i</sub>；ΔE<sub>f</sub> = E<sub>目标胞</sub> − Σ N<sub>i</sub> μ<sub>i</sub><br /><span className="energy-muted">按每计算胞和每原子输出。O₂ 参考除以两个氧原子；单元素组成不证明标准参考态正确。</span></div>}
      <p className="energy-note">样本可被不同卡用于不同角色，规则仅提供可修改建议。{Object.entries(group.reference_origins ?? {}).some(([, origin]) => origin === 'manual') ? '当前配置含人工选择，追加导入或规则更新会保留。' : '请按实际参考条件核对并自由修改。'}统一口径与参考定义在当前卡确认时集中核对。TOTEN 包含电子展宽相关自由能项，未包含振动等完整 Gibbs 修正。</p>
    </section>;
  })}</>;
}
