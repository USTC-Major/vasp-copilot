// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { init } from 'echarts';
import type { CalculationMode, MagneticAnalysis, MagneticAtom, MagnetizationPlotData } from '../../types/generated-api';
import { buildMagneticView, formatMagneticValue } from './magneticPlotModel';

const mode: CalculationMode = { is_spin_polarized: true, is_dftu: false, is_soc: false, is_noncollinear: false, magnetization_analysis_mode: 'collinear' };
const atom = (i: number, fields: Partial<MagneticAtom> = {}): MagneticAtom => ({
  atom_index: i, element: i === 2 ? 'O' : 'Fe', position: { mode: 'Direct', values: [0, 0, 0], raw: ['0.000000', '0.000000', '0.000000'], source_line: i + 8 },
  input_reference: 2, output_moment: 1.8, output_raw: '1.800', output_source_line: i + 50, delta_moment: -.2,
  reference_group: 'positive', output_group: 'positive', orientation: 'retained', magnitude: 'similar', attenuated: false, comparison_available: true, reasons: [], ...fields,
});
const analysis = (atoms: MagneticAtom[]): MagneticAnalysis => ({
  version: 'u5-display-v1', status: 'ready', pattern: 'retained', summary: ['参考排列保持；不判断基态。'], notes: ['MAGMOM 输入参考，非实测初始磁矩。'],
  thresholds: { near_zero: .05, absolute_change: .1, relative_change: .2, attenuation_ratio: .5, source: 'display_heuristic' },
  structure: { source_file: 'POSCAR', atom_count: atoms.length, mapping: 'index_aligned', notes: ['原子索引对应假设，同次来源未验证。'] },
  reference: { source_file: 'INCAR', source_line: 3, raw: '2 -2 0', values: [2, -2, 0], valid: true, restart_notes: [] },
  output: { source_file: 'OUTCAR', header_line: 48, table_complete: true, provisional: false, notes: ['正常结束不代表已收敛。'] },
  atoms, raw_rows: [{ row_id: '48:0', axis: 'x', atom_index: 1, values: { f: .1, tot: 1.8 }, raw: { f: '0.100', tot: '1.800' }, source_line: 51, table_header_line: 48 }],
  totals: [{ kind: 'cell_direct', label: '全胞总磁矩（直接输出）', values: [.12], raw: ['0.120'], source_file: 'OSZICAR', source_line: 6, complete: true, scope: 'latest_observation_unassigned' },
    { kind: 'projection_sum', label: '局域投影之和（非全胞）', values: [1.8], raw: [], source_file: 'OUTCAR', source_line: 48, complete: true, scope: 'latest_observation_unassigned' }],
});
const data = (atoms: MagneticAtom[]): MagnetizationPlotData => ({ x_label: '原子', y_label: '磁矩', series: [], analysis: analysis(atoms) });
type Series = { name: string; data: (number | null)[]; itemStyle: { color: string } };
const series = (v: ReturnType<typeof buildMagneticView>) => v.option.series as Series[];
const tooltip = (v: ReturnType<typeof buildMagneticView>, index: number) => (v.option.tooltip as { formatter: (p: unknown) => string }).formatter([{ dataIndex: index }]);

describe('magnetic evidence display model', () => {
  it('uses authoritative orientation and independent attenuation without recomputing classifications', () => {
    const d = data([atom(1, { output_moment: -.8, output_raw: '-0.800', orientation: 'reversed', magnitude: 'decreased', attenuated: true })]);
    const v = buildMagneticView(d, mode);
    expect(v.rows[0].changes).toEqual(['相对参考反向', '幅值衰减']);
    expect(v.statusLabel).toContain('不代表已收敛');
    expect(v.thresholdNotes.join(' ')).toContain('显示整理启发式');
    expect(v.notes.join(' ')).toContain('同次来源未验证');
    expect(tooltip(v, 0)).toContain('INCAR MAGMOM 输入参考');
    expect(tooltip(v, 0)).toContain('-0.800');
    expect(tooltip(v, 0)).toContain('OUTCAR:51');
    d.analysis!.atoms[0].orientation = 'retained';
    expect(buildMagneticView(d, mode).rows[0].changes).not.toContain('相对参考反向');
  });

  it('preserves zero, absent values, raw tokens, unknown elements and partial evidence', () => {
    const d = data([atom(1, { element: '', output_moment: null, output_raw: 'NaN', comparison_available: false, orientation: 'unavailable', magnitude: 'unavailable', reasons: ['缺值不填零'] }), atom(2, { input_reference: 0, output_moment: 0, output_raw: '0.000' })]);
    d.analysis!.status = 'partial';
    const v = buildMagneticView(d, mode);
    expect(series(v)[1].data).toEqual([null, 0]);
    expect(v.rows[0].element).toBe('未知');
    expect(v.rows[0].output).toContain('NaN');
    expect(v.rows[0].changes).toEqual(['比较证据不足']);
    expect(v.rawRows[0].values).toContain('f=0.100');
    expect(formatMagneticValue(Number.NaN)).toBe('缺失/未解析');
    expect(formatMagneticValue(0)).toBe('0');
    expect(formatMagneticValue(-.10000000000000142)).toBe('-0.1');
  });

  it('filters by original indices, reference groups and independent change tags', () => {
    const d = data([atom(1), atom(2, { orientation: 'reversed', attenuated: true }), atom(3)]);
    const v = buildMagneticView(d, mode, { element: 'O', group: 'positive', change: 'attenuated' });
    expect(v.rows.map(r => r.atom_index)).toEqual([2]);
    expect((v.option.xAxis as { data: string[] }).data).toEqual(['2']);
    expect(v.allRows).toHaveLength(3);
    expect(tooltip(v, 0)).toContain('原子 2');
  });

  it('retains independent direct and projected totals including raw vector/unparsed evidence', () => {
    const d = data([atom(1)]);
    d.analysis!.totals.push({ kind: 'cell_direct', label: '矢量直接输出', values: [.1, .2, .3], raw: ['.1', '.2', '.3'], source_file: 'OUTCAR', source_line: 30, complete: true, scope: 'latest_observation_unassigned' });
    d.analysis!.totals.push({ kind: 'cell_direct', label: '未解析直接输出', values: null, raw: ['0.1'], source_file: 'OSZICAR', source_line: 4, complete: false, scope: 'latest_observation_unassigned' });
    const v = buildMagneticView(d, mode);
    expect(v.totals[0].value).toBe('0.12 μB');
    expect(v.totals[1].label).toContain('非全胞');
    expect(v.totals[2].value).toContain('矢量分量');
    expect(v.totals[3].value).toContain('未解析（原值 0.1）');
    expect(v.totals.every(t => t.evidence.includes('时间归属未验证'))).toBe(true);
  });

  it('disables scalar chart in SOC/noncollinear but retains raw evidence', () => {
    const d = data([atom(1)]);
    d.analysis!.reference.raw = '0 0 2 0 0 -2 0 0 0';
    d.analysis!.reference.values = [0, 0, 2, 0, 0, -2, 0, 0, 0];
    const v = buildMagneticView(d, { ...mode, is_soc: true, magnetization_analysis_mode: 'unsupported_noncollinear_or_soc' });
    expect(v.chartAvailable).toBe(false);
    expect(v.rawRows).toHaveLength(2);
    expect(v.rows).toEqual([]);
    expect(v.summary).toEqual([]);
    expect(v.allRows).toEqual([]);
    expect(v.thresholdNotes).toEqual([]);
    expect(v.rawRows[0].values).toBe('0 0 2 0 0 -2 0 0 0');
    expect(v.rawRows[0].evidence).toBe('INCAR:3');
    expect(v.rawRows[1].axis).toBe('x');
    expect(v.notes.join(' ')).toContain('标量符号图与分类不适用');
    const legacy = buildMagneticView({ x_label: '', y_label: '', series: [{ atom_index: 1, element: 'Fe', initial_moment: 2, final_moment: 1 }] }, { ...mode, is_soc: true });
    expect(legacy.rows).toEqual([]);
    expect(legacy.rawRows[0].axis).toBe('旧响应分量/含义未核实');
  });

  it('shows legacy outputs conservatively and never treats initial alias as reference', () => {
    const d: MagnetizationPlotData = { x_label: '', y_label: '', series: [{ atom_index: 1, element: 'Fe', initial_moment: 99, final_moment: -.5 }] };
    const v = buildMagneticView(d, mode);
    expect(series(v)).toHaveLength(1);
    expect(v.rows[0].input).toBe('参考未验证');
    expect(v.rows[0].changes).toEqual([]);
    expect(tooltip(v, 0)).not.toContain('99');
    expect(v.filterOptions.elements).toEqual(['Fe']);
  });

  it('keeps indexed legacy chart and tooltip aligned when an earlier raw row has no ion index', () => {
    const d: MagnetizationPlotData = { x_label: '', y_label: '', series: [
      { atom_index: null, element: 'Fe', initial_moment: 0, final_moment: 9 },
      { atom_index: 2, element: 'O', initial_moment: 0, final_moment: 2 },
    ] };
    const v = buildMagneticView(d, mode);
    expect(v.allRows).toHaveLength(2);
    expect(series(v)[0].data).toEqual([2]);
    expect(tooltip(v, 0)).toContain('原子 2 · O');
    expect(tooltip(v, 0)).not.toContain('9 μB');
  });

  it('uses the same legacy element filter for bars, categories and tooltip', () => {
    const d: MagnetizationPlotData = { x_label: '', y_label: '', series: [
      { atom_index: 1, element: 'Fe', initial_moment: 0, final_moment: 5 },
      { atom_index: 2, element: 'O', initial_moment: 0, final_moment: 2 },
    ] };
    const v = buildMagneticView(d, mode, { element: 'O' });
    expect(v.rows.map(r => r.atom_index)).toEqual([2]);
    expect((v.option.xAxis as { data: string[] }).data).toEqual(['2']);
    expect(series(v)[0].data).toEqual([2]);
    expect(tooltip(v, 0)).toContain('原子 2 · O');
  });

  it('escapes source tokens in actual HTML tooltip', () => {
    const v = buildMagneticView(data([atom(1, { element: '<svg>', output_raw: '<img onerror="bad">' })]), mode);
    const html = tooltip(v, 0);
    expect(html).toContain('&lt;img onerror=&quot;bad&quot;&gt;');
    expect(html).not.toContain('<img');
    expect(html).not.toContain('<svg>');
  });

  it('renders SVG with consistent fixed output bar/legend colors and signed bars at wide/narrow widths', () => {
    for (const width of [1100, 230]) {
      const v = buildMagneticView(data([atom(1), atom(2, { output_moment: -1.8, orientation: 'reversed' }), atom(3, { output_moment: .2, attenuated: true })]), mode, { compact: width === 230 });
      expect(series(v)[1].itemStyle.color).toBe('#8b5cf6');
      expect(series(v)[1].data).toEqual([1.8, -1.8, .2]);
      const chart = init(null, undefined, { renderer: 'svg', ssr: true, width, height: 360 });
      try {
        chart.setOption(v.option);
        const svg = chart.renderToSVGString();
        expect(svg).toContain('最近观测局域投影');
        expect(svg.match(/fill="#8b5cf6"/g)!.length).toBeGreaterThanOrEqual(4);
        expect(svg).toContain('原子索引');
      } finally { chart.dispose(); }
    }
  });

  it('uses actual sparse indices with a zoom control for many observations', () => {
    const v = buildMagneticView(data(Array.from({ length: 60 }, (_, i) => atom(i * 2 + 1))), mode);
    expect((v.option.xAxis as { data: string[] }).data.slice(0, 3)).toEqual(['1', '3', '5']);
    expect(v.option.dataZoom).toHaveLength(2);
  });
});
