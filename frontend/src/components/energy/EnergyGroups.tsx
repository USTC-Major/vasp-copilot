import { Button, Checkbox, Input, InputNumber, Select, Space, Typography } from 'antd';
import type { EnergyCollection, EnergyGroup } from '../../api/energy';
import { basisOptions, effectiveComposition, type EnergyDraft } from './energyDraft';

type Props = { collection: EnergyCollection; draft: EnergyDraft; disabled: boolean; onGroup: (group: EnergyGroup, scientific?: boolean) => void; onAdd: (kind: EnergyGroup['kind']) => void; onRemove: (id: string) => void };
export default function EnergyGroups({ collection, draft, disabled, onGroup, onAdd, onRemove }: Props) {
  const optionsFor = (role: string) => draft.rows.filter(row => row.role === role).map(row => ({ value: row.sample_id, label: `${row.name} · ${row.sample_id.slice(-8)}` }));
  return <>
    <Space wrap><Button disabled={disabled} onClick={() => onAdd('adsorption')}>添加吸附能比较组</Button><Button disabled={disabled} onClick={() => onAdd('formation')}>添加形成能比较组</Button></Space>
    {!draft.groups.length && <p className="energy-note">先添加比较组，再在确认表为目标样本选择所属组。每个吸附组共用一份清洁表面和吸附物参考。</p>}
    {draft.groups.map(group => {
      const elements = [...new Set(group.targets.flatMap(target => {
        const sample = collection.samples.find(item => item.id === target.sample_id);
        const row = draft.rows.find(item => item.sample_id === target.sample_id);
        return sample && row ? Object.keys(effectiveComposition(sample, row) ?? {}) : [];
      }))];
      return <section className="energy-reference-box" key={group.id} style={{ marginTop: 14 }} aria-label={`比较组 ${group.name}`}>
        <div className="energy-group-heading"><h4>{group.kind === 'adsorption' ? '吸附能 · 共用参考' : '材料形成能 · 元素参考'}</h4><Button size="small" type="text" disabled={disabled} onClick={() => onRemove(group.id)}>移除比较组</Button></div>
        <div className="energy-group-grid">
          <label className="energy-field">组名称<Input aria-label={`组名称 ${group.id}`} value={group.name} disabled={disabled} onChange={event => onGroup({ ...group, name: event.target.value }, false)} /></label>
          <label className="energy-field">组内统一能量字段<Select aria-label={`组能量字段 ${group.id}`} value={group.energy_basis} options={basisOptions} disabled={disabled} onChange={value => onGroup({ ...group, energy_basis: value })} /></label>
          {group.kind === 'adsorption' && <>
            <label className="energy-field">清洁表面参考<Select aria-label={`清洁表面参考 ${group.id}`} value={group.clean_sample_id || undefined} placeholder="选择已指定清洁表面角色的样本" allowClear options={optionsFor('clean_slab')} disabled={disabled} onChange={value => onGroup({ ...group, clean_sample_id: value ?? '' })} /></label>
            <label className="energy-field">吸附物参考<Select aria-label={`吸附物参考 ${group.id}`} value={group.adsorbate_sample_id || undefined} placeholder="选择已指定吸附物参考角色的样本" allowClear options={optionsFor('adsorbate')} disabled={disabled} onChange={value => onGroup({ ...group, adsorbate_sample_id: value ?? '' })} /></label>
            <label className="energy-field">参考文件中的吸附物单元数 m<InputNumber aria-label={`参考单元数 ${group.id}`} value={group.reference_units} disabled={disabled} onChange={value => onGroup({ ...group, reference_units: value ?? 0 })} /></label>
          </>}
          {group.kind === 'formation' && elements.map(element => {
            const references = draft.rows.filter(row => {
              if (row.role !== 'element_reference') return false;
              const sample = collection.samples.find(item => item.id === row.sample_id)!;
              const composition = effectiveComposition(sample, row);
              return composition && Object.keys(composition).length === 1 && !!composition[element];
            }).map(row => ({ value: row.sample_id, label: row.name }));
            return <label key={element} className="energy-field">{element} 元素参考<Select aria-label={`${element} 元素参考 ${group.id}`} value={group.element_references[element] || undefined} allowClear placeholder={`选择单元素 ${element} 来源`} options={references} disabled={disabled} onChange={value => {
              const next = { ...group.element_references }; if (value) next[element] = value; else delete next[element]; onGroup({ ...group, element_references: next });
            }} /></label>;
          })}
        </div>
        <label className="energy-field">参考态／单元定义<Input.TextArea aria-label={`参考定义 ${group.id}`} rows={2} disabled={disabled} value={group.reference_note} placeholder={group.kind === 'adsorption' ? '例如每个 CO 分子；说明参考文件中 m 个相同分子的含义' : '注明用户选择的单质晶体／分子参考态及必要条件'} onChange={event => onGroup({ ...group, reference_note: event.target.value })} /></label>
        {group.kind === 'adsorption' ? <>
          <div className="energy-formula">ΔE<sub>ads,j</sub> = E<sub>构型 j</sub> − E<sub>清洁表面</sub> − n<sub>j</sub> × E<sub>参考文件</sub> / m<br /><span className="energy-muted">每吸附物能差 = ΔE<sub>ads,j</sub> / n<sub>j</sub>；m 与 n 均为正整数，分子个数不能以原子数代替。</span></div>
          <div className="energy-group-grid">{group.targets.map(target => <label className="energy-field" key={target.sample_id}>{draft.rows.find(row => row.sample_id === target.sample_id)?.name ?? target.sample_id} · 吸附物数量 n<InputNumber aria-label={`吸附物数量 ${group.id} ${target.sample_id}`} value={target.adsorbate_count} disabled={disabled} onChange={value => onGroup({ ...group, targets: group.targets.map(item => item.sample_id === target.sample_id ? { ...item, adsorbate_count: value ?? 0 } : item) })} /></label>)}</div>
        </> : <>
          <div className="energy-formula">μ<sub>i</sub> = E<sub>元素参考 i</sub> / N<sub>参考原子 i</sub>；ΔE<sub>f</sub> = E<sub>目标胞</sub> − Σ N<sub>i</sub> μ<sub>i</sub><br /><span className="energy-muted">按每计算胞与每原子输出。O₂ 参考除以两个氧原子；单元素组成不证明标准参考态正确。</span></div>
          <Typography.Text type="secondary">已绑定 {group.targets.length} 个目标；所需元素：{elements.join('、') || '待目标组成明确后显示'}。</Typography.Text>
        </>}
        <Checkbox disabled={disabled} checked={group.basis_confirmed} onChange={event => onGroup({ ...group, basis_confirmed: event.target.checked }, false)}>已核对本组统一能量口径与参考定义</Checkbox>
        <p className="energy-note">默认 energy(sigma→0) 是零温比较的候选政策，原子／分子与固定占据等情况仍需核对。TOTEN 含电子展宽相关自由能项，未包含振动等完整 Gibbs 修正。</p>
      </section>;
    })}
  </>;
}
