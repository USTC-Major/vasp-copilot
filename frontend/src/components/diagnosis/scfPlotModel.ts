import type { EChartsOption } from 'echarts';
import type { ScfParameter, ScfPlotData, ScfSeries, ScfValueStatus } from '../../types/generated-api';

export type ScfViewMode = 'full' | 'tail';
export interface ScfZoom { start: number; end: number }
export interface ScfBlock { id: number; label: string; points: ScfSeries[] }
export interface ScfView {
  blocks: ScfBlock[];
  selectedBlock: ScfBlock | null;
  zoom: ScfZoom;
  option: EChartsOption;
  notes: string[];
  evidenceNotes: string[];
  pointDetails: { step: number; energy: string; deltaEnergy: string; deltaEpsilon: string; source: string }[];
}

const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
export const formatScfValue = (value: number | null | undefined): string => finite(value) ? String(Number(value.toPrecision(12))) : '缺失 / 无效';
const statusLabel: Record<ScfValueStatus, string> = {
  available: '', zero: '输出零值，不表示已收敛', missing: '缺失', unparseable: '无法解析',
  non_finite: '非有限值', precision_limited: '输出精度不足', precision_unknown: '输出精度未知',
};
const escapeHtml = (text: string): string => text.replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!);
const printedResolution = (raw: string | null | undefined): number | undefined => {
  const match = raw?.match(/^[+-]?((?:\d+(?:\.\d*)?|\.\d+))(?:[eEdD]([+-]?\d+))?$/);
  if (!match) return undefined;
  const digits = match[1].includes('.') ? match[1].split('.')[1].length : 0;
  const resolution = 10 ** (Number(match[2] ?? 0) - digits);
  return finite(resolution) && resolution > 0 ? resolution : undefined;
};

export function getScfBlocks(data: ScfPlotData): ScfBlock[] {
  const blocks: ScfBlock[] = [];
  let previous: ScfSeries | undefined;
  let current: ScfBlock | undefined;
  let legacyId = -1;
  for (const input of data.series) {
    const explicit = finite(input.block_id);
    const boundary = !previous || (explicit ? input.block_id !== previous.block_id :
      input.ionic_step !== previous.ionic_step || input.electronic_step <= previous.electronic_step);
    if (boundary) {
      const id = explicit ? input.block_id! : legacyId--;
      current = { id, label: `离子步 ${input.ionic_step} · 块 ${blocks.length + 1}${input.ionic_step_inferred ? '（编号推断）' : ''}`, points: [] };
      blocks.push(current);
    }
    const point: ScfSeries = { ...input, energy: finite(input.energy) ? input.energy : finite(input.energy_ev) ? input.energy_ev : null };
    // Old responses have E only. Do not overwrite explicit new nullable fields,
    // or bridge gaps / block boundaries. Unknown printed precision is disclosed.
    if (!Object.hasOwn(input, 'delta_energy_ev') && input.delta_energy_raw == null && previous && !boundary &&
        input.electronic_step === previous.electronic_step + 1 && finite(point.energy) && finite(previous.energy)) {
      const delta = point.energy - previous.energy;
      if (finite(delta)) {
        point.delta_energy_ev = delta;
        point.delta_energy_source = 'derived';
        point.delta_energy_status = 'precision_unknown';
        point.delta_energy_derivation = { previous_source_line: previous.source_line ?? null, previous_energy_ev: previous.energy,
          previous_energy_raw: previous.energy_raw ?? null, resolution_ev: null };
      }
    }
    current!.points.push(point);
    previous = point;
  }
  return blocks;
}

export function readScfZoom(event: unknown): ScfZoom | undefined {
  if (!event || typeof event !== 'object') return undefined;
  const original = event as { start?: unknown; end?: unknown; batch?: unknown[] };
  const payload = Array.isArray(original.batch) ? original.batch[0] : original;
  if (!payload || typeof payload !== 'object') return undefined;
  const { start, end } = payload as { start?: unknown; end?: unknown };
  if (!finite(start) || !finite(end)) return undefined;
  return { start: Math.max(0, Math.min(100, Math.min(start, end))), end: Math.max(0, Math.min(100, Math.max(start, end))) };
}

function metricText(point: ScfSeries, metric: 'energy' | 'delta_energy' | 'delta_epsilon'): string {
  const value = metric === 'energy' ? point.energy : point[`${metric}_ev`];
  const raw = point[`${metric}_raw`];
  const state = point[`${metric}_status`] ?? (finite(value) ? value === 0 ? 'zero' : 'available' : 'missing');
  const origin = metric === 'delta_energy' && point.delta_energy_source === 'derived' ? '相邻 E 差分（派生）' : 'OSZICAR 原始列';
  const detail = point.delta_energy_derivation;
  const derivation = metric === 'delta_energy' && point.delta_energy_source === 'derived' && detail ?
    `；前步 E=${detail.previous_energy_raw ?? formatScfValue(detail.previous_energy_ev)}，源行 ${detail.previous_source_line ?? '未知'}；差分显示分辨率=${formatScfValue(detail.resolution_ev)} eV` : '';
  return `${formatScfValue(value)} eV；${origin}${raw != null ? `；原值 ${raw}` : ''}${statusLabel[state] ? `；${statusLabel[state]}` : ''}${derivation}`;
}

function parameterNotes(name: string, parameter: ScfParameter | undefined): string[] {
  if (!parameter || parameter.status === 'missing') return [`${name} 未知：没有可用输入或输出证据。`];
  const candidates = parameter.candidates.map(c => `${c.source}:${c.source_line ?? '?'}=${c.raw ?? formatScfValue(c.value)}`).join('，');
  if (parameter.status === 'conflict') return [`${name} 输出回显冲突，不绘制参考线（${candidates}）。`];
  if (parameter.status === 'invalid' || !finite(parameter.value)) return [`${name} 无效，不绘制参考线（${candidates}）。`];
  const note = `${name}=${formatScfValue(parameter.value)}${name === 'EDIFF' ? ' eV' : ''}，来源 ${parameter.source}:${parameter.source_line ?? '?'}；${parameter.scope === 'input_unverified' ? '输入参考，未核实所选块实际采用' : '输出参考，无法归属所选块'}。`;
  return [note, ...(parameter.input_conflict ? [`${name} 输入与输出冲突（${candidates}），优先显示输出参考。`] : [])];
}

export function buildScfView(data: ScfPlotData, settings: { blockId?: number; mode?: ScfViewMode; zoom?: ScfZoom; compact?: boolean } = {}): ScfView {
  const blocks = getScfBlocks(data);
  const block = blocks.find(b => b.id === settings.blockId) ?? blocks[0] ?? null;
  const points = block?.points ?? [];
  const min = points.length ? Math.min(...points.map(p => p.electronic_step)) : 0;
  const max = points.length ? Math.max(...points.map(p => p.electronic_step)) : 1;
  const tailMin = points[Math.max(0, points.length - 20)]?.electronic_step ?? min;
  const defaultStart = settings.mode === 'tail' && max > min ? 100 * (tailMin - min) / (max - min) : 0;
  const zoom = readScfZoom(settings.zoom) ?? { start: defaultStart, end: 100 };
  const visibleMin = min + (max - min) * zoom.start / 100;
  const visibleMax = min + (max - min) * zoom.end / 100;
  const notes = [
    '总能量趋势为电子自由能 E（线性坐标）；变化量显示 |dE| 与独立的 |d epsilon|（对数坐标），正负原值见提示和明细。',
    '能量下降或单条变化曲线不足以判定电子收敛；EDIFF 停止条件同时涉及两种变化量。',
    ...parameterNotes('EDIFF', data.parameters?.ediff), ...parameterNotes('NELM', data.parameters?.nelm),
  ];
  if (points.some(p => p.delta_energy_source === 'derived')) notes.push('包含相邻电子步 E 差分（派生）；不跨块或缺失步计算，d epsilon 不由 E 差分替代。');
  const zeroSteps = points.filter(p => p.delta_energy_ev === 0 || p.delta_epsilon_ev === 0).map(p => p.electronic_step);
  if (zeroSteps.length) notes.push(`电子步 ${zeroSteps.join('、')} 存在输出零值：对数轴不绘制，不替换为小正值；输出零值不表示已收敛。`);
  const limited = points.filter(p => p.delta_energy_status === 'precision_limited').map(p => p.electronic_step);
  if (limited.length) notes.push(`电子步 ${limited.join('、')} 的派生变化未超出 E 的输出分辨率，精度不足；对数轴不绘制，原值保留在明细。`);
  if (points.some(p => p.delta_energy_status === 'precision_unknown')) notes.push('派生值的输出精度未知，不能据此核实阈值或收敛。');
  if (points.some(p => !finite(p.energy) || !finite(p.delta_energy_ev) || !finite(p.delta_epsilon_ev))) notes.push('缺失、无法解析和非有限数值保留为空，曲线不连接断档；各步原因见明细。');
  const nelm = data.parameters?.nelm;
  const nelmValue = nelm?.status === 'available' && finite(nelm.value) && nelm.value > 0 && Number.isInteger(nelm.value) ? nelm.value : null;
  const nelmVisible = nelmValue != null && points.length > 0 && nelmValue >= visibleMin && nelmValue <= visibleMax;
  if (nelmValue != null && !nelmVisible) notes.push(`NELM=${nelmValue} 在当前观测视区之外，以文字说明，不扩展横轴。`);
  const ediff = data.parameters?.ediff;
  const ediffValue = ediff?.status === 'available' && finite(ediff.value) && ediff.value >= 0 ? ediff.value : null;
  if (ediffValue === 0) notes.push('EDIFF=0：固定执行 NELM 个电子自洽步；不是正的收敛阈值，对数轴不画 EDIFF 线。');
  const visiblePoints = points.filter(p => p.electronic_step >= visibleMin - 1e-8 && p.electronic_step <= visibleMax + 1e-8);
  const energies = visiblePoints.map(p => p.energy).filter(finite);
  const resolutions = visiblePoints.map(p => printedResolution(p.energy_raw)).filter(finite);
  const smallestResolution = resolutions.length ? Math.min(...resolutions) : undefined;
  let energyBounds: { min: number; max: number; minInterval?: number } | undefined;
  if (energies.length) {
    const lo = Math.min(...energies), hi = Math.max(...energies);
    const machinePadding = Math.max(Math.abs(lo), Math.abs(hi), 1) * Number.EPSILON * 8;
    const padding = hi > lo ? Math.max((hi - lo) * 0.08, machinePadding) :
      Math.max(smallestResolution ?? Math.max(Math.abs(lo), 1) * 1e-6, machinePadding);
    energyBounds = { min: lo - padding, max: hi + padding, ...(smallestResolution ? { minInterval: smallestResolution } : {}) };
    if (energies.length > 1 && hi === lo) notes.push('当前视区的已输出 E 相同：图中为水平线；原始 dE 仍可独立非零，不能用 E 的显示精度替代原始变化量。');
    if (energies.length === 1) notes.push('当前视区仅一个有效 E 数据点，步数不足以观察能量变化趋势。');
  }
  const changes = visiblePoints.flatMap(p => [p.delta_energy_status === 'precision_limited' ? null : p.delta_energy_ev, p.delta_epsilon_ev])
    .filter(finite).filter(value => value !== 0).map(Math.abs);
  if (ediffValue != null && ediffValue > 0) changes.push(ediffValue);
  let logBounds: { min: number; max: number } | undefined;
  if (changes.length) {
    const lo = Math.min(...changes), hi = Math.max(...changes);
    const minLog = Math.floor(Math.log10(lo)), maxLog = Math.ceil(Math.log10(hi));
    const lower = 10 ** minLog, upper = 10 ** (maxLog === minLog ? maxLog + 1 : maxLog);
    logBounds = { min: lower > 0 ? lower : lo, max: finite(upper) ? upper : hi };
  }
  const pointDetails = points.map(point => ({ step: point.electronic_step, energy: metricText(point, 'energy'),
    deltaEnergy: metricText(point, 'delta_energy'), deltaEpsilon: metricText(point, 'delta_epsilon'),
    source: `${point.source_file ?? 'OSZICAR'}:${point.source_line ?? '未知'}${point.algorithm ? ` · ${point.algorithm}` : ''}` }));
  const evidenceNotes = data.convergence_evidence?.length ? data.convergence_evidence.map(e =>
    `${e.source}:${e.source_line} 报告因达到 EDIFF 而停止电子循环；无法归属所选块，不能据此判定此块或整个计算已收敛。`) : ['证据不足：未解析到明确的电子循环 EDIFF 停止文本。'];
  const makeData = (metric: 'energy' | 'delta_energy' | 'delta_epsilon') => {
    const result: { value: [number, number | null]; rowIndex: number }[] = [];
    points.forEach((point, index) => {
      if (index && point.electronic_step > points[index - 1].electronic_step + 1)
        result.push({ value: [points[index - 1].electronic_step + 1, null], rowIndex: -1 });
      const value = metric === 'energy' ? point.energy : point[`${metric}_ev`];
      const usable = finite(value) && (metric === 'energy' || value !== 0 && point[`${metric}_status`] !== 'precision_limited');
      result.push({ value: [point.electronic_step, usable ? metric === 'energy' ? value : Math.abs(value) : null], rowIndex: index });
    });
    return result;
  };
  const nelmLine = nelmVisible ? [{ xAxis: nelmValue!, name: 'NELM 参考', label: { formatter: `NELM=${nelmValue} 参考` } }] : [];
  const ediffLine = ediffValue != null && ediffValue > 0 ? [{ yAxis: ediffValue, name: 'EDIFF 参考', label: { formatter: `EDIFF=${formatScfValue(ediffValue)} 参考` } }] : [];
  const compact = settings.compact ?? false;
  const option: EChartsOption = {
    animation: false,
    title: [{ text: compact ? '总能量趋势 · 电子自由能 E' : '总能量趋势（电子自由能 E）', left: compact ? '50%' : '24%', top: 8, textAlign: 'center', textStyle: { fontSize: compact ? 11 : 13 } },
      { text: '相邻电子步变化', left: compact ? '50%' : '76%', top: compact ? '48%' : 8, textAlign: 'center', textStyle: { fontSize: compact ? 11 : 13 } }],
    legend: { data: ['|dE|', '|d epsilon|'], top: compact ? '52%' : 32, left: compact ? 'center' : '64%' },
    grid: compact ? [{ left: 75, right: 24, top: 48, bottom: '60%' }, { left: 75, right: 24, top: '59%', bottom: 65 }] :
      [{ left: 75, right: '54%', top: 64, bottom: 65 }, { left: '59%', right: 28, top: 64, bottom: 65 }],
    xAxis: [0, 1].map(gridIndex => ({ type: 'value' as const, gridIndex, name: '电子步', nameLocation: 'middle' as const, nameGap: 25,
      min: min === max ? min - 0.5 : min, max: min === max ? max + 0.5 : max, minInterval: 1 })),
    yAxis: [{ type: 'value', gridIndex: 0, scale: true, name: 'E (eV)', ...(compact ? { nameGap: 7 } : {}), ...energyBounds, axisLabel: { formatter: (value: number) => {
      const digits = smallestResolution && value !== 0 ? Math.min(17, Math.max(1, Math.floor(Math.log10(Math.abs(value))) + 1 - Math.floor(Math.log10(smallestResolution)))) : 12;
      return String(Number(value.toPrecision(digits)));
    }, fontSize: compact ? 10 : 12 } },
      { type: 'log', gridIndex: 1, logBase: 10, name: compact ? '|变化| (eV)' : '|变化量| (eV)', ...logBounds,
        ...(compact ? { nameGap: 7 } : {}),
        nameTextStyle: { fontSize: compact ? 10 : 12 }, axisLabel: { formatter: (value: number) => value.toExponential(1), fontSize: compact ? 10 : 12 } }],
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    tooltip: { trigger: 'axis', confine: true, formatter: (payload: unknown) => {
      const params = Array.isArray(payload) ? payload : [payload];
      const item = params.find(p => p?.data && typeof p.data.rowIndex === 'number' && p.data.rowIndex >= 0);
      const row = item ? pointDetails[item.data.rowIndex] : undefined;
      return row ? [ `电子步 ${row.step} · ${row.source}`, `E：${row.energy}`, `dE：${row.deltaEnergy}`, `d epsilon：${row.deltaEpsilon}` ].map(escapeHtml).join('<br/>') : '';
    } },
    dataZoom: [{ id: 'scf-inside', type: 'inside', xAxisIndex: [0, 1], filterMode: 'filter', ...zoom },
      { id: 'scf-slider', type: 'slider', xAxisIndex: [0, 1], filterMode: 'filter', bottom: 8, height: 18, ...zoom }],
    series: [{ name: 'E', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: makeData('energy'), connectNulls: false,
      showSymbol: true, symbolSize: 4, lineStyle: { width: 1.5 }, color: '#1677ff',
      markLine: { symbol: 'none', silent: true, precision: -1, label: { position: 'insideEndTop' }, data: nelmLine } },
    { name: '|dE|', type: 'line', xAxisIndex: 1, yAxisIndex: 1, data: makeData('delta_energy'), connectNulls: false,
      showSymbol: true, symbolSize: 4, color: '#722ed1', markLine: { symbol: 'none', silent: true, precision: -1,
        label: { position: 'insideEndBottom', fontSize: compact ? 9 : 11 }, data: [...nelmLine, ...ediffLine] } },
    { name: '|d epsilon|', type: 'line', xAxisIndex: 1, yAxisIndex: 1, data: makeData('delta_epsilon'), connectNulls: false,
      showSymbol: true, symbolSize: 4, color: '#08979c' }],
  };
  return { blocks, selectedBlock: block, zoom, option, notes, evidenceNotes, pointDetails };
}
