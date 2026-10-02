// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { init } from 'echarts';
import type { ScfParameter, ScfPlotData, ScfSeries } from '../../types/generated-api';
import { buildScfView, formatScfValue, getScfBlocks, readScfZoom } from './scfPlotModel';

const point = (step: number, fields: Partial<ScfSeries> = {}): ScfSeries => ({ ionic_step: 1, electronic_step: step, energy: -100,
  block_id: 1, source_file: 'OSZICAR', source_line: step, delta_energy_ev: -1e-8, delta_energy_source: 'oszicar',
  delta_energy_raw: '-1e-8', delta_energy_status: 'available', delta_epsilon_ev: 2e-9, ...fields });
const data = (series: ScfSeries[]): ScfPlotData => ({ x_label: '电子步', y_label: 'E (eV)', series });
const parameter = (value: number | null, fields: Partial<ScfParameter> = {}): ScfParameter => ({ value, source: 'INCAR', source_line: 1,
  raw: String(value), status: value === null ? 'missing' : 'available', input_conflict: false, candidates: [], scope: 'input_unverified', ...fields });
type TestSeries = { data: { value: [number, number | null]; rowIndex: number }[]; markLine?: { data: { xAxis?: number; yAxis?: number }[] }; connectNulls: boolean };
const chartSeries = (view: ReturnType<typeof buildScfView>) => view.option.series as TestSeries[];

describe('SCF evidence model', () => {
  it('keeps signed positive/negative energy linear and changes logarithmic without losing raw evidence', () => {
    const view = buildScfView(data([point(1, { energy: 100, delta_energy_ev: -80, delta_energy_raw: '-8.00D+1' }),
      point(2, { energy: -3, delta_energy_ev: 2.55e-11, delta_energy_raw: '+2.55e-11' })]));
    expect(chartSeries(view)[0].data.map(d => d.value)).toEqual([[1, 100], [2, -3]]);
    expect(chartSeries(view)[1].data.map(d => d.value)).toEqual([[1, 80], [2, 2.55e-11]]);
    expect(view.option.yAxis).toEqual(expect.arrayContaining([expect.objectContaining({ type: 'value' }), expect.objectContaining({ type: 'log' })]));
    expect(view.pointDetails[0].deltaEnergy).toContain('-8.00D+1');
    expect(view.pointDetails[1].deltaEnergy).toContain('+2.55e-11');
    expect(view.pointDetails[1].deltaEpsilon).toContain('2e-9');
  });

  it('keeps zero, missing, invalid and precision-limited rows accessible without inventing log points', () => {
    const rows = [point(1, { energy: 0, delta_energy_ev: 0, delta_energy_status: 'zero', delta_epsilon_ev: 0 }),
      point(2, { energy: null, delta_energy_ev: null, delta_energy_status: 'non_finite', delta_energy_raw: 'NaN', delta_epsilon_ev: null }),
      point(3, { delta_energy_ev: 1e-8, delta_energy_status: 'precision_limited', delta_energy_source: 'derived' })];
    const view = buildScfView(data(rows));
    expect(chartSeries(view)[0].data[0].value).toEqual([1, 0]);
    expect(chartSeries(view)[1].data.every(d => d.value[1] === null)).toBe(true);
    expect(view.pointDetails).toHaveLength(3);
    expect(view.pointDetails[1].deltaEnergy).toContain('非有限值');
    expect(view.pointDetails[2].deltaEnergy).toContain('输出精度不足');
    expect(view.notes.join(' ')).toContain('不替换为小正值');
  });

  it('separates repeated ionic numbers and resets, and never connects a missing numbered step', () => {
    const view = buildScfView(data([point(1), point(3), point(1, { block_id: 2 }), point(2, { block_id: 2 })]));
    expect(view.blocks.map(b => b.id)).toEqual([1, 2]);
    expect(view.blocks[0].label).not.toEqual(view.blocks[1].label);
    expect(chartSeries(view)[0].data.map(d => d.value[0])).toEqual([1, 2, 3]);
    expect(chartSeries(view)[0].data[1]).toEqual({ value: [2, null], rowIndex: -1 });
    expect(chartSeries(view).every(s => !s.connectNulls)).toBe(true);
    expect(buildScfView(data([point(1), point(1, { block_id: 2 })]), { blockId: 2 }).selectedBlock?.id).toBe(2);
  });

  it('derives legacy E-only values only across finite consecutive steps in one block', () => {
    const legacy = (step: number, energy: number | null, ionic_step = 1): ScfSeries => ({ ionic_step, electronic_step: step, energy });
    const blocks = getScfBlocks(data([legacy(1, -10), legacy(2, -11), legacy(4, -12), legacy(5, null), legacy(6, -13), legacy(1, -20), legacy(2, -21, 2)]));
    expect(blocks).toHaveLength(3);
    expect(blocks[0].points[1]).toMatchObject({ delta_energy_ev: -1, delta_energy_source: 'derived', delta_energy_status: 'precision_unknown' });
    expect(blocks[0].points.slice(2).every(p => p.delta_energy_ev == null)).toBe(true);
    expect(blocks[1].points[0].delta_energy_ev).toBeUndefined();
    expect(blocks[2].points[0].delta_energy_ev).toBeUndefined();
    expect(blocks[0].points.every(p => p.delta_epsilon_ev == null)).toBe(true);
  });

  it('does not replace original zero or explicitly null changes with E differences', () => {
    const blocks = getScfBlocks(data([point(1, { energy: -10 }), point(2, { energy: -11, delta_energy_ev: 0 }),
      point(3, { energy: -12, delta_energy_ev: null, delta_energy_status: 'unparseable', delta_energy_raw: '****' })]));
    expect(blocks[0].points.map(p => p.delta_energy_ev)).toEqual([-1e-8, 0, null]);
  });

  it('uses observed horizontal bounds and only displays NELM inside the visible viewport', () => {
    const source = { ...data(Array.from({ length: 40 }, (_, i) => point(i + 1))), parameters: { ediff: parameter(1e-6), nelm: parameter(60) } };
    let view = buildScfView(source);
    expect(view.option.xAxis).toEqual([expect.objectContaining({ min: 1, max: 40 }), expect.objectContaining({ min: 1, max: 40 })]);
    expect(chartSeries(view)[0].markLine?.data).toEqual([]);
    expect(view.notes.join(' ')).toContain('NELM=60 在当前观测视区之外');
    view = buildScfView({ ...source, parameters: { ...source.parameters, nelm: parameter(30) } });
    expect(chartSeries(view)[0].markLine?.data).toEqual([expect.objectContaining({ xAxis: 30 })]);
    expect(chartSeries(buildScfView({ ...source, parameters: { ...source.parameters, nelm: parameter(30) } }, { zoom: { start: 0, end: 20 } }))[0].markLine?.data).toEqual([]);
    expect(chartSeries(view)[1].markLine?.data).toEqual(expect.arrayContaining([expect.objectContaining({ yAxis: 1e-6 })]));
  });

  it('uses last twenty observed records in tail mode and links both chart axes through dataZoom', () => {
    const view = buildScfView(data(Array.from({ length: 40 }, (_, i) => point(i + 1))), { mode: 'tail' });
    expect(view.zoom.start).toBeCloseTo(20 / 39 * 100);
    expect(view.option.dataZoom).toEqual([expect.objectContaining({ xAxisIndex: [0, 1], start: view.zoom.start, filterMode: 'filter' }),
      expect.objectContaining({ xAxisIndex: [0, 1], start: view.zoom.start, filterMode: 'filter' })]);
    expect(readScfZoom({ batch: [{ start: 70, end: 90 }] })).toEqual({ start: 70, end: 90 });
    expect(readScfZoom({ start: 101, end: -1 })).toEqual({ start: 0, end: 100 });
    expect(readScfZoom({ start: NaN, end: 100 })).toBeUndefined();
  });

  it('treats EDIFF zero as fixed steps and omits unknown or conflicting thresholds', () => {
    const source = { ...data([point(1)]), parameters: { ediff: parameter(0), nelm: parameter(null) } };
    const view = buildScfView(source);
    expect(view.notes.join(' ')).toContain('EDIFF=0：固定执行 NELM');
    expect(chartSeries(view)[1].markLine?.data).toEqual([]);
    const conflict = buildScfView({ ...source, parameters: { ediff: parameter(null, { status: 'conflict' }), nelm: parameter(null) } });
    expect(conflict.notes.join(' ')).toContain('输出回显冲突');
    expect(chartSeries(conflict)[1].markLine?.data).toEqual([]);
  });

  it('keeps output stop evidence separate from the selected block and never infers it from falling E', () => {
    const view = buildScfView({ ...data([point(1)]), convergence_evidence: [{ kind: 'electronic_ediff_stop', source: 'OUTCAR', source_line: 42, scope: 'unassigned', block_id: null }] });
    expect(view.evidenceNotes.join(' ')).toContain('OUTCAR:42');
    expect(view.evidenceNotes.join(' ')).toContain('无法归属所选块');
    expect(buildScfView(data([point(1)])).evidenceNotes.join(' ')).toContain('证据不足');
  });

  it('escapes original tokens in scientific tooltip and retains all signed values and sources', () => {
    const view = buildScfView(data([point(1, { delta_energy_raw: '<script>x</script>' })]));
    const tooltip = view.option.tooltip as { formatter: (payload: unknown) => string };
    const html = tooltip.formatter([{ data: { rowIndex: 0 } }]);
    expect(html).toContain('OSZICAR:1');
    expect(html).toContain('-1e-8');
    expect(html).toContain('&lt;script&gt;');
    expect(html).not.toContain('<script>');
    expect(formatScfValue(0)).toBe('0');
    expect(formatScfValue(Infinity)).toBe('缺失 / 无效');
    expect(formatScfValue(-0.10000000000000142)).toBe('-0.1');
  });

  it('renders an actual log EDIFF markLine without ECharts rounding it to zero and rescales tail energy', () => {
    const rows = Array.from({ length: 30 }, (_, i) => point(i + 1, { energy: i === 0 ? 17000 : -5200.90909091,
      energy_raw: i === 0 ? '17000.00000000' : '-5200.90909091', delta_energy_ev: i === 0 ? 2e4 : 1e-10,
      delta_epsilon_ev: i === 0 ? 8e3 : 4e-11 }));
    const source = { ...data(rows), parameters: { ediff: parameter(1e-6), nelm: parameter(80) } };
    const view = buildScfView(source, { mode: 'tail' });
    const chart = init(null, undefined, { renderer: 'svg', ssr: true, width: 1100, height: 390 });
    try {
      chart.setOption(view.option);
      const svg = chart.renderToSVGString();
      expect(svg).toContain('EDIFF=0.000001');
      expect(svg).toContain('-5200.9090909');
      expect(svg).not.toContain('>17000<');
      expect(svg).not.toContain('>20000<');
      expect(svg).not.toContain('NELM=80');
      expect(svg).not.toContain('NaN');
      expect(svg).not.toContain('Infinity');
    } finally { chart.dispose(); }
    expect(view.option.yAxis).toEqual([expect.objectContaining({ min: expect.any(Number), max: expect.any(Number) }),
      expect.objectContaining({ min: 1e-11, max: 1e-6 })]);
  });

  it('keeps empty and single-point views honest and supports vertically stacked grids', () => {
    expect(buildScfView(data([])).selectedBlock).toBeNull();
    const single = buildScfView(data([point(7)]), { compact: true, mode: 'tail' });
    expect(chartSeries(single)[0].data).toHaveLength(1);
    expect(single.zoom).toEqual({ start: 0, end: 100 });
    expect(single.option.grid).toEqual([expect.objectContaining({ bottom: '60%' }), expect.objectContaining({ top: '59%' })]);
    expect(single.notes.join(' ')).not.toContain('水平线');
    expect(single.notes.join(' ')).toContain('仅一个有效 E 数据点');
  });
});
