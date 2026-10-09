import { request } from './client';

export type PlotPreset = 'scientific' | 'blue_orange' | 'muted' | 'print' | 'custom';
export type PlotPalette = { preset: PlotPreset; colors: string[] };
export type PlotPreferences = { schema_version: 'pp.preferences.v1'; revision: number; palette: PlotPalette };
export type PlotExportSettings = { width_px: number; height_px: number; colors: string[] };

export const PLOT_PALETTES: { id: Exclude<PlotPreset, 'custom'>; label: string; colors: string[] }[] = [
  { id: 'scientific', label: '科研通用（色盲友好）', colors: ['#0072B2', '#D55E00', '#009E73', '#CC79A7', '#333333', '#8B5C00'] },
  { id: 'blue_orange', label: '蓝橙', colors: ['#2166AC', '#B35806', '#4393C3', '#D6604D', '#053061', '#67001F'] },
  { id: 'muted', label: '低饱和', colors: ['#4C657A', '#916A4B', '#667B61', '#806C8A', '#8A6164', '#57606A'] },
  { id: 'print', label: '黑白打印', colors: ['#111111', '#444444', '#777777', '#222222', '#555555', '#666666'] },
];
export const defaultPlotPreferences = (): PlotPreferences => ({ schema_version: 'pp.preferences.v1', revision: 0,
  palette: { preset: 'scientific', colors: [...PLOT_PALETTES[0].colors] } });
export const plotPreferencesApi = {
  get: (signal?: AbortSignal) => request<{ preferences: PlotPreferences; presets: typeof PLOT_PALETTES }>('/toolbox/postprocessing/preferences', { signal }),
  save: (expected_revision: number, palette: PlotPalette) => request<{ preferences: PlotPreferences }>('/toolbox/postprocessing/preferences', {
    method: 'PUT', body: { expected_revision, palette },
  }),
};
export const paletteError = (palette: PlotPalette): string | null => {
  if (palette.colors.length < 2 || palette.colors.length > 16) return '配色须包含 2–16 个颜色';
  if (palette.colors.some(color => !/^#[\da-fA-F]{6}$/.test(color))) return '颜色须为六位十六进制值，如 #0072B2';
  return null;
};
export const colorContrastOnWhite = (color: string): number => {
  if (!/^#[\da-fA-F]{6}$/.test(color)) return 0;
  const rgb = [1, 3, 5].map(start => parseInt(color.slice(start, start + 2), 16) / 255)
    .map(value => value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4);
  return 1.05 / (rgb[0] * .2126 + rgb[1] * .7152 + rgb[2] * .0722 + .05);
};
