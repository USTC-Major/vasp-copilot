import type { EChartsOption } from 'echarts';
import type { CalculationMode, MagneticAtom, MagneticGroup, MagnetizationPlotData } from '../../types/generated-api';

export interface MagneticFilters {
  element?: string; group?: MagneticGroup;
  change?: 'reversed' | 'attenuated' | 'emerged' | 'near_zero' | 'similar' | 'unavailable';
  compact?: boolean;
}
export interface MagneticDisplayRow {
  key: string; atom_index: number; element: string; position: string;
  input: string; output: string; delta: string; referenceGroup: string;
  orientation: string; magnitude: string; changes: string[]; evidence: string;
}
export interface MagneticView {
  statusLabel: string; summary: string[]; notes: string[]; thresholdNotes: string[];
  totals: { key: string; label: string; value: string; evidence: string }[];
  rows: MagneticDisplayRow[]; allRows: MagneticDisplayRow[];
  rawRows: { key: string; axis: string; atom: string; values: string; evidence: string }[];
  filterOptions: { elements: string[]; groups: { value: MagneticGroup; label: string }[]; changes: { value: NonNullable<MagneticFilters['change']>; label: string }[] };
  option: EChartsOption; chartAvailable: boolean;
}
const known = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
export function formatMagneticValue(value: number | null | undefined): string {
  if (!known(value)) return '缺失/未解析';
  if (value === 0) return '0';
  return Number(value.toPrecision(8)).toString();
}
const groupLabels: Record<MagneticGroup, string> = { positive: '正参考组', negative: '负参考组', near_zero: '近零参考组', unknown: '参考未知' };
const orientationLabels = { retained: '相对符号保持', reversed: '相对参考反向', undefined: '近零不比较方向', unavailable: '未分类' };
const magnitudeLabels = { similar: '幅值变化未超过显示阈值', decreased: '幅值减小', increased: '幅值增大', emerged: '出现/诱导投影', near_zero: '输出近零', unavailable: '未分类' };
const changeLabels: Record<NonNullable<MagneticFilters['change']>, string> = {
  reversed: '相对参考反向', attenuated: '幅值衰减', emerged: '出现/诱导投影', near_zero: '输出近零', similar: '幅值相近', unavailable: '比较证据不足',
};
const escape = (value: string) => value.replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!);
const sourceLine = (file: string | null, line: number | null | undefined) => `${file ?? '来源未知'}${known(line) ? `:${line}` : '（行号未提供）'}`;
const changes = (atom: MagneticAtom): NonNullable<MagneticFilters['change']>[] => {
  if (!atom.comparison_available) return ['unavailable'];
  const list: NonNullable<MagneticFilters['change']>[] = [];
  if (atom.orientation === 'reversed') list.push('reversed');
  if (atom.attenuated) list.push('attenuated');
  if (atom.magnitude === 'emerged' || atom.magnitude === 'near_zero' || atom.magnitude === 'similar') list.push(atom.magnitude);
  return list;
};
const displayRow = (atom: MagneticAtom, data: MagnetizationPlotData): MagneticDisplayRow => ({
  key: String(atom.atom_index), atom_index: atom.atom_index, element: atom.element || '未知',
  position: atom.position ? `${atom.position.mode} ${atom.position.raw.join(' ')} · ${sourceLine(data.analysis?.structure.source_file ?? null, atom.position.source_line)}` : '坐标缺失',
  input: formatMagneticValue(atom.input_reference), output: `${formatMagneticValue(atom.output_moment)}${atom.output_raw != null ? `（原值 ${atom.output_raw}）` : ''}`,
  delta: formatMagneticValue(atom.delta_moment), referenceGroup: groupLabels[atom.reference_group],
  orientation: orientationLabels[atom.orientation], magnitude: magnitudeLabels[atom.magnitude],
  changes: changes(atom).map(c => changeLabels[c]),
  evidence: `${sourceLine('INCAR', data.analysis?.reference.source_line)}；${sourceLine('OUTCAR', atom.output_source_line)}${atom.reasons.length ? `；${atom.reasons.join('；')}` : ''}`,
});

export function buildMagneticView(data: MagnetizationPlotData, calculationMode: CalculationMode, filters: MagneticFilters = {}): MagneticView {
  const analysis = data.analysis;
  const unsupported = calculationMode.is_soc || calculationMode.is_noncollinear || calculationMode.magnetization_analysis_mode === 'unsupported_noncollinear_or_soc' || analysis?.status === 'unsupported';
  const atoms = analysis?.atoms ?? [];
  const selected = atoms.filter(a => (!filters.element || a.element === filters.element)
    && (!filters.group || a.reference_group === filters.group) && (!filters.change || changes(a).includes(filters.change)));
  const legacy = data.series.map((row, i): MagneticDisplayRow => ({
    key: `legacy:${i}`, atom_index: row.atom_index ?? i + 1, element: row.element || '未知', position: '坐标未提供', input: '参考未验证',
    output: formatMagneticValue(row.final_moment ?? row.tot), delta: '未比较', referenceGroup: '未分类', orientation: '未分类', magnitude: '未分类', changes: [],
    evidence: row.atom_index == null ? '旧响应原子编号缺失；行序号仅用于列出原值' : '旧响应输出；原子对应/来源行号未验证',
  }));
  const allRows = unsupported ? [] : analysis ? atoms.map(a => displayRow(a, data)) : legacy;
  const rows = unsupported ? [] : analysis ? selected.map(a => displayRow(a, data)) : legacy.filter(r => !filters.element || r.element === filters.element);
  const notes = analysis ? [...analysis.notes, ...analysis.structure.notes, ...analysis.output.notes] : ['旧响应只显示输出原值，未从 initial_moment 推断输入参考或磁性排列。'];
  if (unsupported && !notes.some(n => n.includes('SOC/非共线'))) notes.push('SOC/非共线：保留输出证据，标量符号图与分类不适用。');
  const rawRows = analysis ? analysis.raw_rows.map(r => ({ key: r.row_id, axis: r.axis, atom: r.atom_index == null ? '编号未解析' : String(r.atom_index),
    values: Object.entries(r.values).map(([k, v]) => `${k}=${r.raw[k] ?? formatMagneticValue(v)}`).join('；'), evidence: `${sourceLine('OUTCAR', r.source_line)} · 表头 ${r.table_header_line ?? '未知'}` })) : legacy.map(r => ({ key: r.key, axis: unsupported ? '旧响应分量/含义未核实' : '标量原值（模式未验证）', atom: String(r.atom_index), values: r.output, evidence: r.evidence }));
  if (analysis && (unsupported || !analysis.reference.valid) && analysis.reference.raw != null) {
    rawRows.unshift({ key: 'input:MAGMOM', axis: 'MAGMOM 输入参考原文', atom: '完整赋值（不作标量配对）',
      values: analysis.reference.raw, evidence: sourceLine('INCAR', analysis.reference.source_line) });
    notes.push(`MAGMOM 完整输入参考见原始证据（${sourceLine('INCAR', analysis.reference.source_line)}）；${unsupported ? '不将 3N 分量截为 N 个标量' : '完整展开/数量未通过验证，不作逐原子参考配对'}。`);
  }
  const legacySelected = data.series.map((row, i) => ({ row, display: legacy[i] })).filter(({ row }) => known(row.atom_index) && (!filters.element || row.element === filters.element));
  const chartAvailable = !unsupported && (analysis ? selected.some(a => known(a.input_reference) || known(a.output_moment)) : legacySelected.some(({ row }) => known(row.final_moment ?? row.tot)));
  const chartRows = analysis ? selected : legacySelected.map(r => r.row);
  const tooltipRows = analysis ? rows : legacySelected.map(r => r.display);
  const categories = chartRows.map(a => String(a.atom_index));
  const inputName = 'INCAR MAGMOM 输入参考';
  const outputName = '最近观测局域投影';
  const series: NonNullable<EChartsOption['series']> = [];
  if (analysis) series.push({ name: inputName, type: 'bar', data: selected.map(a => known(a.input_reference) ? a.input_reference : null), itemStyle: { color: '#1677ff' } });
  series.push({ name: outputName, type: 'bar', itemStyle: { color: '#8b5cf6' }, data: analysis ? selected.map(a => known(a.output_moment) ? a.output_moment : null)
    : legacySelected.map(({ row }) => known(row.final_moment ?? row.tot) ? (row.final_moment ?? row.tot)! : null) });
  const option: EChartsOption = {
    animation: false, legend: { top: 0, left: 'center', textStyle: { fontSize: 11 }, data: analysis ? [inputName, outputName] : [outputName] },
    grid: { top: filters.compact ? 88 : 52, left: filters.compact ? 42 : 60, right: 18, bottom: categories.length > 30 ? 82 : 44, containLabel: true },
    xAxis: { type: 'category', data: categories, name: '原子索引', nameLocation: 'middle', nameGap: 28, axisLabel: { hideOverlap: true } },
    yAxis: { type: 'value', name: '磁矩 (μB)', nameGap: 12, scale: true, axisLabel: { formatter: (v: number) => formatMagneticValue(v) } },
    dataZoom: categories.length > 30 ? [{ type: 'inside', xAxisIndex: 0, filterMode: 'filter' }, { type: 'slider', xAxisIndex: 0, bottom: 18, height: 18, filterMode: 'filter' }] : [],
    tooltip: { trigger: 'axis', confine: true, formatter: (params: unknown) => {
      const list = Array.isArray(params) ? params : [params];
      const first = list[0] as { dataIndex?: number } | undefined;
      const row = tooltipRows[first?.dataIndex ?? -1];
      if (!row) return '';
      return [`原子 ${row.atom_index} · ${row.element}`, ...(analysis ? [`${inputName}：${row.input} μB`] : []), `${outputName}：${row.output} μB`, ...(analysis ? [`输出−参考：${row.delta} μB`, `${row.orientation}；${row.magnitude}`, row.changes.join('；')] : []), row.evidence].filter(Boolean).map(escape).join('<br/>');
    } }, series,
  };
  const t = unsupported ? undefined : analysis?.thresholds;
  return {
    statusLabel: unsupported ? '标量对照不适用' : analysis ? ({ ready: '可比较（不代表已收敛）', partial: '部分/暂态证据', unavailable: '比较证据不足', unsupported: '标量对照不适用' })[analysis.status] : '旧响应：输出原值',
    summary: unsupported ? [] : analysis?.summary ?? [], notes,
    thresholdNotes: t ? [`以下为显示整理启发式，不是收敛标准、磁性相边界或基态判据（${t.source}）。`, `近零 |m|≤${t.near_zero} μB；幅值变化 ≥max(${t.absolute_change} μB, ${t.relative_change * 100}% × |输入参考|)。`, `幅值衰减：输出幅值≤${t.attenuation_ratio * 100}%参考，且减小≥${t.absolute_change} μB；方向与幅值标签独立。`] : [],
    totals: (analysis?.totals ?? []).map((t, i) => ({ key: `${t.source_file}:${t.kind}:${i}`, label: t.label,
      value: t.values?.length && t.values.every(known) ? `${t.values.map(formatMagneticValue).join(', ')} μB${t.values.length === 3 ? '（矢量分量）' : ''}` : `未解析${t.raw.length ? `（原值 ${t.raw.join(' ')}）` : ''}`,
      evidence: `${sourceLine(t.source_file, t.source_line)}；${t.complete ? '该证据已读取' : '不完整/未解析'}；时间归属未验证${t.raw.length ? `；原值 ${t.raw.join(' ')}` : ''}` })),
    rows, allRows, rawRows, filterOptions: { elements: [...new Set((analysis ? atoms : data.series).map(a => a.element).filter(Boolean))],
      groups: Object.entries(groupLabels).map(([value, label]) => ({ value: value as MagneticGroup, label })),
      changes: Object.entries(changeLabels).map(([value, label]) => ({ value: value as NonNullable<MagneticFilters['change']>, label })) }, option, chartAvailable,
  };
}
