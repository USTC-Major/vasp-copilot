import { useState } from 'react';
import { Checkbox, Input, Typography } from 'antd';
import type { PPView } from '../../api/postprocessing';
import { atomSelectionPatch, orbitalGroups, selectedAtoms, selectedOrbitals } from './viewState';

export default function ProjectionSelector({ view, atoms, orbitals, onChange }: {
  view: PPView; atoms: [number, string][]; orbitals: string[]; onChange: (patch: Partial<PPView>) => void;
}) {
  const [search, setSearch] = useState('');
  const elements = [...new Set(atoms.map(([, element]) => element))];
  const selected = new Set(selectedAtoms(view, atoms));
  const orbitalSelection = new Set(selectedOrbitals(view, orbitals));
  const searchText = search.trim().toLowerCase();
  function selectAtoms(ids: number[], checked: boolean) {
    const next = new Set(selected);
    ids.forEach(id => checked ? next.add(id) : next.delete(id));
    onChange(atomSelectionPatch([...next], atoms));
  }
  function selectOrbitals(components: string[], checked: boolean) {
    const next = new Set(orbitalSelection);
    components.forEach(component => checked ? next.add(component) : next.delete(component));
    onChange({ orbitals: orbitals.filter(component => next.has(component)), projection_grouping: 'element' });
  }
  return <fieldset className="pp-projection-settings"><legend>投影 DOS</legend>
    {!elements.length || !orbitals.length ? <Typography.Paragraph type="secondary">该文件没有可用的原子／轨道投影。</Typography.Paragraph> : <>
      <Typography.Paragraph type="secondary">勾选元素后，分别显示该元素所有原子的投影之和；展开可精确选原子。</Typography.Paragraph>
      {atoms.length > 12 && <Input aria-label="搜索原子" placeholder="搜索元素或原子编号" value={search} allowClear onChange={event => setSearch(event.target.value)} />}
      <div className="pp-projection-columns">
        <div><Typography.Text strong>元素与原子</Typography.Text><div className="pp-selection-list">
          {elements.map(element => {
            const elementAtoms = atoms.filter(([, symbol]) => symbol === element);
            const count = elementAtoms.filter(([id]) => selected.has(id)).length;
            const visible = elementAtoms.filter(([id, symbol]) => !searchText || `${symbol} #${id}`.toLowerCase().includes(searchText));
            if (searchText && !visible.length) return null;
            return <div key={element} className="pp-selection-group">
              <Checkbox aria-label={`${element} 全部原子`} checked={count === elementAtoms.length} indeterminate={count > 0 && count < elementAtoms.length} onChange={event => selectAtoms(elementAtoms.map(([id]) => id), event.target.checked)}>{element} <span className="pp-selection-count">{count}/{elementAtoms.length}</span></Checkbox>
              <details open={searchText ? true : undefined}><summary>展开 {element} 原子</summary><div className="pp-selection-children">
                {visible.map(([id]) => <Checkbox key={id} aria-label={`${element} #${id}`} checked={selected.has(id)} onChange={event => selectAtoms([id], event.target.checked)}>{element} #{id}</Checkbox>)}
              </div></details>
            </div>;
          })}
          {searchText && !atoms.some(([id, element]) => `${element} #${id}`.toLowerCase().includes(searchText)) && <Typography.Text type="secondary">没有匹配的原子</Typography.Text>}
        </div></div>
        <div><Typography.Text strong>轨道</Typography.Text><div className="pp-selection-list">
          {orbitalGroups(orbitals).map(group => {
            const count = group.components.filter(component => orbitalSelection.has(component)).length;
            return <div key={group.name} className="pp-selection-group">
              <Checkbox aria-label={`${group.name} 全部轨道`} checked={count === group.components.length} indeterminate={count > 0 && count < group.components.length} onChange={event => selectOrbitals(group.components, event.target.checked)}>{group.name}</Checkbox>
              {group.components.some(component => component !== group.name) && <details><summary>展开 {group.name} 分量</summary><div className="pp-selection-children">
                {group.components.map(component => <Checkbox key={component} aria-label={`${component} 轨道分量`} checked={orbitalSelection.has(component)} onChange={event => selectOrbitals([component], event.target.checked)}>{component}</Checkbox>)}
              </div></details>}
            </div>;
          })}
        </div></div>
      </div>
      <Typography.Paragraph type="secondary">未选择元素／原子或轨道时，仅显示总 DOS。投影按所选原子求和，单位为 states/eV。</Typography.Paragraph>
      {view.projection_grouping === 'combined' && selected.size > 0 && <Typography.Paragraph type="secondary">当前恢复的是旧版投影汇总；修改选择后按元素分别对比。</Typography.Paragraph>}
    </>}
  </fieldset>;
}
