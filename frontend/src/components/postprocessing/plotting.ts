import type { EChartsOption } from 'echarts';
import type { PPAxes, PPCurves, PPView } from '../../api/postprocessing';
import { PLOT_PALETTES } from '../../api/plotPreferences';
import { effectiveReference } from './viewState';

export const MAX_AXIS_TICKS = 200;
export const DEFAULT_PLOT_COLORS = PLOT_PALETTES[0].colors;
export type PlotTheme = { background: string; foreground: string; secondary: string; border: string };
export type PngExportSettings = { width: number; height: number; colors: string[] };
export const SCIENTIFIC_PLOT_THEME: PlotTheme = { background: '#FFFFFF', foreground: '#1F2937', secondary: '#374151', border: '#D1D5DB' };

export function validateAxes(axes: PPAxes | undefined): string {
  if (!axes) return '坐标配置尚未准备好，请刷新分析';
  for (const axis of ['x', 'y'] as const) {
    const low = axes[`${axis}_min`], high = axes[`${axis}_max`], interval = axes[`${axis}_interval`];
    if (![low, high, interval].every(Number.isFinite)) return '坐标起止值与刻度间隔必须为有限数值';
    const span = high - low;
    if (!Number.isFinite(span) || low >= high || interval <= 0) return '坐标起点必须小于终点，刻度间隔必须大于零';
    if (!Number.isFinite(span / interval) || span / interval > MAX_AXIS_TICKS) return `刻度过密：每轴最多 ${MAX_AXIS_TICKS} 个间隔，请增大刻度间隔`;
  }
  return '';
}

export function curveColor(curve: PPCurves['curves'][number], colors: string[]): string {
  const palette = colors.length ? colors : DEFAULT_PLOT_COLORS;
  // Stable semantic slots, independent of filtering, ordering or band count.
  const projected = curve.id?.startsWith('dos.projection.') ?? curve.name.startsWith('所选');
  const slot = projected && curve.element_index != null ? 2 + curve.element_index : (projected ? 2 : 0) + (curve.channel === 'down' ? 1 : 0);
  return palette[slot % palette.length];
}

function bounds(values: Iterable<number>): [number, number] | undefined {
  let min = Infinity, max = -Infinity;
  for (const value of values) if (Number.isFinite(value)) { min = Math.min(min, value); max = Math.max(max, value); }
  return Number.isFinite(min) && Number.isFinite(max) ? [min, max] : undefined;
}

export function fullEnergyWindow(data: PPCurves, view: PPView, efermi: number | null): Pick<PPView, 'energy_min_ev' | 'energy_max_ev'> | undefined {
  if (data.kind === 'band' && (view.band_start !== data.view.band_start || view.band_end !== data.view.band_end)) return undefined;
  const nextReference = effectiveReference(view, efermi);
  if (nextReference === null || !Number.isFinite(nextReference)) return undefined;
  const range = data.energy_bounds_ev ? [data.energy_bounds_ev.min_ev, data.energy_bounds_ev.max_ev] : bounds(data.curves.flatMap(curve => data.kind === 'dos' ? curve.x : curve.y));
  if (!range || !Number.isFinite(range[0]) || !Number.isFinite(range[1]) || range[0] >= range[1]) return undefined;
  const shift = nextReference - (data.effective_reference_ev ?? data.reference_ev);
  const min = range[0] - shift, max = range[1] - shift;
  return Number.isFinite(min) && Number.isFinite(max) && Number.isFinite(max - min) ? { energy_min_ev: min, energy_max_ev: max } : undefined;
}

export function energyAxisLabel(data: PPCurves): string {
  const { reference, reference_ev: offset } = data.view;
  if (reference === 'raw') return 'Energy E (eV)';
  if (reference === 'fermi') return 'E − EF (eV)';
  if (reference === 'custom' && data.view.version === 'pp.view.v2') return offset === 0 ? 'E − EF (eV)' : `E − EF ${offset < 0 ? '+' : '−'} ${Math.abs(offset)} (eV)`;
  return `E − ${data.effective_reference_ev ?? data.reference_ev} (eV)`;
}

export function buildPlotOption(data: PPCurves, colors: string[], _theme: PlotTheme = SCIENTIFIC_PLOT_THEME, output?: { width: number; height: number }): EChartsOption {
  const theme = SCIENTIFIC_PLOT_THEME;
  const dos = data.kind === 'dos';
  const axes = data.view.axes;
  const path = !dos ? bounds(data.curves.flatMap(curve => curve.x)) : undefined;
  const scale = output ? Math.max(.8, Math.min(3, output.width / 1200, output.height / 800)) : 1;
  const energyLabel = energyAxisLabel(data);
  const displayName = (curve: PPCurves['curves'][number]) => dos ? curve.name : `能带 · ${curve.channel}`;
  const axisCommon = {
    type: 'value' as const, nameLocation: 'middle' as const, scale: true,
    axisLabel: { color: theme.secondary, fontSize: 12 * scale },
    axisLine: { show: true, lineStyle: { color: theme.secondary } },
    axisTick: { show: true }, nameTextStyle: { color: theme.foreground, fontSize: 13 * scale },
    splitLine: { lineStyle: { color: theme.border } },
    axisPointer: { show: dos && !output },
  };
  return {
    animation: false, backgroundColor: theme.background,
    textStyle: { color: theme.foreground, fontSize: 12 * scale },
    tooltip: dos && !output ? { trigger: 'axis', renderMode: 'richText', confine: true } : { show: false, trigger: 'none' },
    axisPointer: { show: dos && !output },
    legend: { type: output ? 'plain' : 'scroll', data: [...new Set(data.curves.map(displayName))], textStyle: { color: theme.foreground, fontSize: 12 * scale }, top: 8 * scale },
    grid: { left: 78 * scale, right: 30 * scale, top: 55 * scale, bottom: 68 * scale },
    xAxis: { ...axisCommon, name: dos ? energyLabel : 'k path (Å⁻¹)', nameGap: 32 * scale, min: dos ? data.view.energy_min_ev ?? axes?.x_min : path?.[0] ?? axes?.x_min, max: dos ? data.view.energy_max_ev ?? axes?.x_max : path?.[1] ?? axes?.x_max, boundaryGap: [0, 0] },
    yAxis: { ...axisCommon, name: dos ? 'DOS (states/eV)' : energyLabel, nameGap: 52 * scale, min: dos ? axes?.y_min : data.view.energy_min_ev ?? axes?.y_min, max: dos ? axes?.y_max : data.view.energy_max_ev ?? axes?.y_max },
    dataZoom: [],
    series: data.curves.map((curve, index) => {
      const color = curveColor(curve, colors);
      return {
        id: `${curve.id ?? curve.name}.${index}`, name: displayName(curve), type: 'line', showSymbol: false, symbol: 'none', silent: !dos,
        itemStyle: { color }, lineStyle: { width: (dos ? 2 : 1.3) * scale, type: curve.channel === 'down' ? 'dashed' : 'solid', color },
        data: curve.x.map((x, i) => [x, dos && curve.channel === 'down' && data.view.mirror_down ? -curve.y[i] : curve.y[i]]),
        markLine: index === 0 && !dos ? { symbol: 'none', silent: true, lineStyle: { color: theme.secondary, type: 'dashed' },
          label: { formatter: '{b}', color: theme.foreground, fontSize: 12 * scale }, data: data.ticks.map(tick => ({ xAxis: tick.x, name: tick.label.replace(/\\Gamma/g, 'Γ') })) } : undefined,
      };
    }),
  };
}

export async function exportPostprocessingPng(data: PPCurves, settings: PngExportSettings): Promise<string> {
  const { width, height, colors } = settings;
  if (![width, height].every(value => Number.isInteger(value) && value >= 240 && value <= 8000) || width * height > 32_000_000) throw new Error('PNG 尺寸须为 240–8000 像素的整数，总像素最多 3200 万');
  const axesError = validateAxes(data.view.axes);
  if (axesError) throw new Error(axesError);
  const echarts = await import('echarts');
  const surface = document.createElement('div');
  surface.style.cssText = `position:fixed;left:-100000px;top:0;width:${width}px;height:${height}px;pointer-events:none;`;
  document.body.append(surface);
  let chart: ReturnType<typeof echarts.init> | undefined;
  try {
    chart = echarts.init(surface, undefined, { renderer: 'canvas', width, height, devicePixelRatio: 1 });
    chart.setOption(buildPlotOption(data, colors, SCIENTIFIC_PLOT_THEME, { width, height }), { notMerge: true });
    chart.getZr().flush();
    return chart.getDataURL({ type: 'png', pixelRatio: 1, backgroundColor: '#FFFFFF' });
  } finally {
    chart?.dispose();
    surface.remove();
  }
}
