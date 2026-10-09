import { vi } from 'vitest';
import type { PPCurves } from '../../api/postprocessing';
import { buildPlotOption, curveColor, energyAxisLabel, exportPostprocessingPng, fullEnergyWindow, validateAxes } from './plotting';

const renderer = vi.hoisted(() => ({ setOption: vi.fn(), getDataURL: vi.fn(() => 'data:image/png;base64,test'), dispose: vi.fn(), getZr: vi.fn(() => ({ flush: vi.fn() })) }));
const init = vi.hoisted(() => vi.fn());
vi.mock('echarts', () => ({ init }));
const axes = { version: 'pp.axes.v1' as const, x_min: -2, x_max: 2, x_interval: .5, y_min: -4, y_max: 4, y_interval: 1 };
const data: PPCurves = { id: 'ds-test', kind: 'dos', revision: 1, reference_ev: .5,
  view: { reference: 'fermi', reference_ev: 0, mirror_down: true, atoms: [], orbitals: [], band_start: 1, band_end: 2, axes }, ticks: [],
  curves: [{ id: 'dos.total.up', name: '总 DOS · up', channel: 'up', x: [-1, 0, 1], y: [2, 3, 2] }, { id: 'dos.total.down', name: '总 DOS · down', channel: 'down', x: [-1, 0, 1], y: [1, 2, 1] }] };
const theme = { background: '#000000', foreground: '#FFFFFF', secondary: '#DDDDDD', border: '#444444' };
const colors = ['#112233', '#445566', '#778899', '#AABBCC'];
beforeEach(() => { vi.clearAllMocks(); init.mockReturnValue(renderer); });

it('shares stable colors with legends and retains unmodified scientific arrays', () => {
  const original = structuredClone(data);
  const option = buildPlotOption(data, colors, theme);
  expect(option.xAxis).toMatchObject({ min: -2, max: 2 });
  expect(option.yAxis).toMatchObject({ min: -4, max: 4, name: 'DOS (states/eV)' });
  expect(option.xAxis).not.toHaveProperty('interval');
  expect(option.yAxis).not.toHaveProperty('interval');
  expect(option.backgroundColor).toBe('#FFFFFF');
  expect(option.textStyle).toMatchObject({ color: '#1F2937' });
  expect(option.series).toEqual(expect.arrayContaining([expect.objectContaining({ itemStyle: { color: colors[1] }, lineStyle: expect.objectContaining({ color: colors[1], type: 'dashed' }), data: [[-1, -1], [0, -2], [1, -1]] })]));
  expect(curveColor(data.curves[1], colors)).toBe(curveColor([...data.curves].reverse()[0], colors));
  expect(data).toEqual(original);
  expect(option.tooltip).toMatchObject({ trigger: 'axis' });
});

it('disables every band hover layer while preserving segment and high-symmetry marks', () => {
  const band = { ...data, kind: 'band' as const, ticks: [{ x: 0, label: '\\Gamma' }, { x: 1, label: 'X' }] };
  const option = buildPlotOption(band, colors, theme);
  expect(option.tooltip).toMatchObject({ show: false, trigger: 'none' });
  expect(option.axisPointer).toMatchObject({ show: false });
  expect(option.xAxis).toMatchObject({ axisPointer: { show: false } });
  expect(option.yAxis).toMatchObject({ axisPointer: { show: false } });
  expect(option.xAxis).toMatchObject({ min: -1, max: 1, name: 'k path (Å⁻¹)' });
  expect(option.series).toEqual(expect.arrayContaining([expect.objectContaining({ silent: true, markLine: expect.objectContaining({ data: [{ xAxis: 0, name: 'Γ' }, { xAxis: 1, name: 'X' }] }) })]));
});

it('renders a new white canvas at the requested pixel size without zoom controls', async () => {
  const result = await exportPostprocessingPng(data, { width: 1600, height: 900, colors });
  expect(result).toBe('data:image/png;base64,test');
  expect(init).toHaveBeenCalledWith(expect.any(HTMLElement), undefined, { renderer: 'canvas', width: 1600, height: 900, devicePixelRatio: 1 });
  expect(renderer.setOption).toHaveBeenCalledWith(expect.objectContaining({ backgroundColor: '#FFFFFF', dataZoom: [], textStyle: expect.objectContaining({ color: '#1F2937' }), xAxis: expect.objectContaining({ min: -2, max: 2 }) }), { notMerge: true });
  expect(renderer.getDataURL).toHaveBeenCalledWith({ type: 'png', pixelRatio: 1, backgroundColor: '#FFFFFF' });
  expect(renderer.dispose).toHaveBeenCalledOnce();
  expect(document.querySelector('[style*="-100000"]')).toBeNull();
});

it('keeps distinct element colors stable when filters reorder curves and shares a color across that element spins', () => {
  const fe = { ...data.curves[0], id: 'dos.projection.Fe.up', element: 'Fe', element_index: 0 };
  const o = { ...data.curves[0], id: 'dos.projection.O.up', element: 'O', element_index: 1 };
  expect(curveColor(fe, colors)).toBe(colors[2]);
  expect(curveColor(o, colors)).toBe(colors[3]);
  expect(curveColor({ ...fe, channel: 'down' }, colors)).toBe(curveColor(fe, colors));
  expect(curveColor([o, fe][1], colors)).toBe(colors[2]);
});

it('labels the v2 relative custom reference and shifts complete source bounds for a draft reference', () => {
  const custom = { ...data, view: { ...data.view, version: 'pp.view.v2' as const, reference: 'custom' as const, reference_ev: 1 } };
  expect(energyAxisLabel(custom)).toBe('E − EF − 1 (eV)');
  expect(fullEnergyWindow({ ...data, energy_bounds_ev: { min_ev: -8, max_ev: 5 } }, custom.view, .5)).toEqual({ energy_min_ev: -9, energy_max_ev: 4 });
  expect(fullEnergyWindow(data, custom.view, null)).toBeUndefined();
  expect(energyAxisLabel({ ...data, view: { ...data.view, reference: 'raw' } })).toBe('Energy E (eV)');
});

it('waits for curves of the current band selection before offering that selection’s complete energy window', () => {
  const saved = { ...data, kind: 'band' as const, energy_bounds_ev: { min_ev: -8, max_ev: 5 } };
  const draft = { ...saved.view, band_end: 1 };
  expect(fullEnergyWindow(saved, draft, .5)).toBeUndefined();
  const current = { ...saved, view: draft, energy_bounds_ev: { min_ev: -2, max_ev: 1 } };
  expect(fullEnergyWindow(current, draft, .5)).toEqual({ energy_min_ev: -2, energy_max_ev: 1 });
});

it('cleans up an export canvas after a rendering failure and rejects invalid dimensions', async () => {
  renderer.setOption.mockImplementationOnce(() => { throw new Error('绘制失败'); });
  await expect(exportPostprocessingPng(data, { width: 600, height: 400, colors })).rejects.toThrow('绘制失败');
  expect(renderer.dispose).toHaveBeenCalledOnce();
  await expect(exportPostprocessingPng(data, { width: 0, height: 900, colors })).rejects.toThrow('PNG 尺寸');
  expect(document.querySelector('[style*="-100000"]')).toBeNull();
});

it('validates finite ordered ranges and a bounded number of ticks', () => {
  expect(validateAxes(axes)).toBe('');
  expect(validateAxes({ ...axes, y_max: Infinity })).toContain('有限');
  expect(validateAxes({ ...axes, x_interval: 0 })).toContain('大于零');
  expect(validateAxes({ ...axes, x_interval: .00001 })).toContain('刻度过密');
});
