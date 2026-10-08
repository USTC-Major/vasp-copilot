import { Alert, Select, Tag, Typography } from 'antd';
import type { PotcarPreview } from '../../types/potcar';
import '../../pages/potcar-assembly.css';

const { Text } = Typography;
const legacySources: Record<string, { title: string; url: string }> = {
  S1: { title: '选择赝势', url: 'https://vasp.at/wiki/index.php?title=Choosing_pseudopotentials&oldid=38073' },
  S2: { title: '可用赝势与后缀', url: 'https://vasp.at/wiki/index.php?title=Available_pseudopotentials&oldid=25069' },
  S3: { title: '准备 POTCAR', url: 'https://vasp.at/wiki/index.php?title=Preparing_a_POTCAR&oldid=25377' },
  S4: { title: 'TITEL 日期含义', url: 'https://vasp.at/wiki/index.php?title=TITEL&oldid=25191' },
  S5: { title: 'POTCAR 只读来源', url: 'https://vasp.at/wiki/index.php?title=POTCAR&oldid=33125' },
  S6: { title: 'DFT+U / LDAUL', url: 'https://vasp.at/wiki/index.php?title=LDAUL&oldid=36501' },
  S7: { title: '磁性官方教程', url: 'https://vasp.at/tutorials/latest/magnetism/part1/' },
  S8: { title: 'PAW 数据库官方讲义', url: 'https://www.vasp.at/vasp-workshop/pseudoppdatabase.pdf' },
};

/** Shared by independent assembly and workflow; selection remains owned by the caller. */
export default function PotcarSelection({ preview, datasetIds, dirty, disabled, onChange }: {
  preview: PotcarPreview; datasetIds: (string | null)[]; dirty: boolean; disabled: boolean;
  onChange: (index: number, id: string) => void;
}) {
  const context = preview.context;
  // New previews carry the reviewed package's own provenance. Only old servers
  // without this field use the compatibility list; an explicit list is authoritative.
  const sources: Record<string, { title: string; url: string }> = preview.rule_sources
    ? Object.fromEntries(preview.rule_sources.map(source => [source.source_id, source]))
    : legacySources;
  return <>
    <div className="potcar-preview-details">
      <span>推荐上下文：{context?.purpose === 'regular' ? '普通用途' : context?.purpose === 'special' ? '特殊用途，人工核对' : '用途未指定'} · {context?.functional ?? 'unknown'}{preview.context_source === 'workflow' ? '（来自服务端工作流参数）' : ''}</span>
      <span>库发布版本未知；版本备注和数据集日期不作为发布版证明。{preview.rule_version && `规则版本：${preview.rule_version}`}</span>
    </div>
    {dirty && <Alert type="warning" showIcon title="选择预览已失效，请刷新预览后重新核对并确认。" />}
    <div role="table" aria-label="物种顺序与变体" className="potcar-selection-table">
      <div role="row" className="potcar-selection-heading"><span role="columnheader">序号 / 物种 / 数量</span><span role="columnheader">变体及数据集元数据</span><span role="columnheader">选择依据与建议</span></div>
      {preview.rows.map((row, index) => {
        const chosen = row.candidates.find(candidate => candidate.dataset_id === datasetIds[index]);
        const reason = row.selection_reason ?? row.reason;
        return <div role="row" className="potcar-selection-row" key={row.position}>
          <div role="cell"><Text strong>#{row.position} · {row.element}</Text><div>数量：{row.atom_count}</div></div>
          <div role="cell"><Select aria-label={`第 ${row.position} 项 ${row.element} 变体`} value={datasetIds[index]} disabled={disabled || row.candidates.length === 0} placeholder="请选择兼容变体" onChange={id => onChange(index, id)} options={row.candidates.map(candidate => ({ value: candidate.dataset_id, label: `${candidate.variant ?? '未知变体'} · ZVAL ${candidate.zval ?? '未知'} · ENMAX ${candidate.enmax_ev ?? '未知'} eV · ${candidate.title ?? '标题未知'} · ${candidate.relative_path}` }))} />
            {chosen ? <div className="potcar-selected-metadata">{chosen.title ?? '标题未知'}<div>ZVAL：{chosen.zval ?? '未知'} · ENMAX：{chosen.enmax_ev === null ? '未知' : `${chosen.enmax_ev} eV`}</div><div>来源相对路径：{chosen.relative_path}</div></div> : <Text type="warning">尚未选择变体</Text>}
          </div>
          <div role="cell"><Tag>{dirty ? '待刷新确认' : ({ USER_SELECTED: '明确选择', RULE_RECOMMENDED: '规则推荐', UNIQUE_COMPATIBLE: '唯一兼容候选', SELECTION_REQUIRED: '需选择变体', NO_COMPATIBLE_DATASET: '无兼容候选' }[reason.code])}</Tag><div>{dirty ? '所示依据属于上次预览，请刷新取得当前选择依据。' : reason.message}</div>
            {(row.advice ?? []).map((item, adviceIndex) => <div className="potcar-advice" key={`${item.code}-${adviceIndex}`}><Text strong={item.code === 'HYBRID_SOFT_AVOID'}>{item.message}</Text>{item.target_variants.length > 0 && <div>建议目标：{item.target_variants.join('、')}</div>}<div>{item.rule_ids.join(' · ')} {item.source_ids.map(id => sources[id] ? <a key={id} href={sources[id].url} target="_blank" rel="noreferrer">{id} {sources[id].title} </a> : <span key={id}>{id} </span>)}</div></div>)}
          </div>
        </div>;
      })}
    </div>
    {preview.blockers.length > 0 && <Alert type="warning" showIcon title={dirty ? '上次预览阻断项（刷新后更新）' : '生成前需处理的阻断项'} description={<ul>{preview.blockers.map((blocker, index) => <li key={`${blocker.code}-${index}`}>{blocker.position !== null && `第 ${blocker.position} 项：`}{blocker.message} <Text code>{blocker.code}</Text></li>)}</ul>} />}
    <div className="potcar-preview-details"><span>库：{preview.library.display_name} · 索引 {preview.library.index_revision}</span><span>预览到期：{new Date(preview.expires_at).toLocaleString()}</span><span>结构 SHA-256：{preview.structure_sha256}</span></div>
  </>;
}
