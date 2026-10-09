import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { vi } from 'vitest';
import { colorContrastOnWhite, defaultPlotPreferences, paletteError, PLOT_PALETTES, plotPreferencesApi, type PlotPreferences } from '../../api/plotPreferences';
import PlotExportDialog, { exportSizeError } from './PlotExportDialog';
import PlotPreferencesSettings from './PlotPreferencesSettings';

let saved: PlotPreferences;
const exporter = vi.fn();
const cancel = vi.fn();
function mount(props: Partial<Parameters<typeof PlotExportDialog>[0]> = {}) {
  return render(<ConfigProvider theme={{ token: { motion: false } }}><PlotExportDialog open onCancel={cancel} onExport={exporter} {...props} /></ConfigProvider>);
}
async function ready() { await waitFor(() => expect(screen.getByRole('button', { name: '下载 PNG' })).toBeEnabled()); }
beforeEach(() => {
  saved = defaultPlotPreferences(); exporter.mockReset().mockResolvedValue(undefined); cancel.mockReset();
  vi.spyOn(plotPreferencesApi, 'get').mockImplementation(async () => ({ preferences: saved, presets: PLOT_PALETTES }));
  vi.spyOn(plotPreferencesApi, 'save').mockImplementation(async (revision, palette) => {
    if (revision !== saved.revision) throw new Error('默认配色已在别处更新，请重新读取后保存');
    saved = { ...saved, revision: saved.revision + 1, palette }; return { preferences: saved };
  });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it('exports exact dimensions and temporary custom order without changing defaults', async () => {
  mount(); await ready();
  fireEvent.click(screen.getByRole('checkbox', { name: '锁定宽高比' }));
  fireEvent.change(screen.getByRole('spinbutton', { name: 'PNG 宽度（像素）' }), { target: { value: '1200' } });
  fireEvent.change(screen.getByRole('spinbutton', { name: 'PNG 高度（像素）' }), { target: { value: '800' } });
  fireEvent.click(screen.getByRole('button', { name: '颜色 1 后移' }));
  fireEvent.change(screen.getByRole('textbox', { name: '颜色 1' }), { target: { value: '#112233' } });
  fireEvent.click(screen.getByRole('button', { name: '下载 PNG' }));
  await waitFor(() => expect(exporter).toHaveBeenCalledWith({ width_px: 1200, height_px: 800, colors: ['#112233', '#0072B2', '#009E73', '#CC79A7', '#333333', '#8B5C00'] }));
  expect(plotPreferencesApi.save).not.toHaveBeenCalled();
  expect(saved.revision).toBe(0); expect(cancel).toHaveBeenCalledTimes(1);
});

it('changes the paired dimension while ratio is locked', async () => {
  mount(); await ready();
  fireEvent.change(screen.getByRole('spinbutton', { name: 'PNG 宽度（像素）' }), { target: { value: '1920' } });
  expect(screen.getByRole('spinbutton', { name: 'PNG 高度（像素）' })).toHaveValue('1200');
  fireEvent.click(screen.getByRole('button', { name: '下载 PNG' }));
  await waitFor(() => expect(exporter).toHaveBeenCalledWith(expect.objectContaining({ width_px: 1920, height_px: 1200 })));
});

it('persists only when the explicit default button is clicked and restores it next open', async () => {
  const rendered = mount(); await ready();
  fireEvent.change(screen.getByRole('textbox', { name: '颜色 1' }), { target: { value: '#334455' } });
  expect(plotPreferencesApi.save).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '将当前配色设为默认' }));
  expect(await screen.findByText('当前配色已保存为全局默认')).toBeInTheDocument();
  expect(plotPreferencesApi.save).toHaveBeenCalledWith(0, expect.objectContaining({ preset: 'custom', colors: expect.arrayContaining(['#334455']) }));
  rendered.unmount(); mount(); await ready();
  expect(screen.getByRole('textbox', { name: '颜色 1' })).toHaveValue('#334455');
});

it('warns about pale colors and rejects malformed color values', async () => {
  mount(); await ready();
  fireEvent.change(screen.getByRole('textbox', { name: '颜色 1' }), { target: { value: '#FFFFFF' } });
  expect(screen.getByText(/颜色 1 在白底上较浅/)).toBeInTheDocument();
  fireEvent.change(screen.getByRole('textbox', { name: '颜色 1' }), { target: { value: '#xyzxyz' } });
  expect(screen.getByRole('button', { name: '下载 PNG' })).toBeDisabled();
  expect(screen.getByRole('button', { name: '将当前配色设为默认' })).toBeDisabled();
});

it('renders a real PNG preview from the same settings and keeps export errors visible', async () => {
  const preview = vi.fn().mockResolvedValue('data:image/png;base64,test-preview');
  exporter.mockRejectedValue(new Error('PNG 重绘失败'));
  mount({ preview }); await ready();
  fireEvent.click(screen.getByRole('button', { name: '预览 PNG' }));
  expect(await screen.findByAltText('实际 PNG 预览 1600 × 1000 像素')).toHaveAttribute('src', 'data:image/png;base64,test-preview');
  expect(preview).toHaveBeenCalledWith({ width_px: 1600, height_px: 1000, colors: PLOT_PALETTES[0].colors });
  fireEvent.change(screen.getByRole('textbox', { name: '颜色 1' }), { target: { value: '#123456' } });
  expect(screen.queryByAltText('实际 PNG 预览 1600 × 1000 像素')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '下载 PNG' }));
  expect(await screen.findByText('PNG 重绘失败')).toBeInTheDocument();
  expect(cancel).not.toHaveBeenCalled();
});

it('lets a failed preferences read fall back for temporary export but prevents saving defaults', async () => {
  vi.mocked(plotPreferencesApi.get).mockRejectedValue(new Error('离线测试读取失败'));
  mount(); await ready();
  expect(screen.getByText('默认配色暂不可读取，当前可临时导出科研通用配色')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '将当前配色设为默认' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: '下载 PNG' }));
  await waitFor(() => expect(exporter).toHaveBeenCalled());
  expect(plotPreferencesApi.save).not.toHaveBeenCalled();
});

it('preserves a draft on save conflict, without claiming that it was saved', async () => {
  mount(); await ready(); saved = { ...saved, revision: 1 };
  fireEvent.change(screen.getByRole('textbox', { name: '颜色 1' }), { target: { value: '#123456' } });
  fireEvent.click(screen.getByRole('button', { name: '将当前配色设为默认' }));
  expect(await screen.findByText('默认配色已在别处更新，请重新读取后保存')).toBeInTheDocument();
  expect(screen.getByRole('textbox', { name: '颜色 1' })).toHaveValue('#123456');
  expect(screen.queryByText('当前配色已保存为全局默认')).not.toBeInTheDocument();
});

it('saves settings through the independent preferences endpoint', async () => {
  render(<ConfigProvider theme={{ token: { motion: false } }}><PlotPreferencesSettings /></ConfigProvider>);
  await waitFor(() => expect(screen.getByRole('button', { name: '保存默认配色' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: '颜色 1 后移' }));
  fireEvent.click(screen.getByRole('button', { name: '保存默认配色' }));
  expect(await screen.findByText('默认配色已保存（仅本地）')).toBeInTheDocument();
  expect(saved.palette.colors.slice(0, 2)).toEqual(['#D55E00', '#0072B2']);
});

it('validates pixel safety limits and computes white-background contrast', () => {
  expect(exportSizeError(240, 240)).toBeNull();
  expect(exportSizeError(8000, 4000)).toBeNull();
  expect(exportSizeError(8000, 8000)).toMatch(/3200 万/);
  for (const width of [null, 239, 8001, 400.5, Infinity, NaN]) expect(exportSizeError(width, 500)).not.toBeNull();
  expect(paletteError({ preset: 'custom', colors: ['#111111'] })).not.toBeNull();
  expect(colorContrastOnWhite('#FFFFFF')).toBe(1);
  expect(colorContrastOnWhite('#000000')).toBe(21);
});


it('applies common ratios and preset colors to the actual export', async () => {
  mount(); await ready();
  fireEvent.mouseDown(screen.getByRole('combobox', { name: '常用宽高比' }));
  fireEvent.click(await screen.findByText('1:1'));
  expect(screen.getByRole('spinbutton', { name: 'PNG 高度（像素）' })).toHaveValue('1600');
  fireEvent.mouseDown(screen.getByRole('combobox', { name: '导出配色' }));
  fireEvent.click(await screen.findByText('蓝橙'));
  fireEvent.click(screen.getByRole('button', { name: '下载 PNG' }));
  await waitFor(() => expect(exporter).toHaveBeenCalledWith({ width_px: 1600, height_px: 1600, colors: PLOT_PALETTES[1].colors }));
  expect(plotPreferencesApi.save).not.toHaveBeenCalled();
});

it('disables export when the combined pixel count exceeds the canvas budget', async () => {
  mount(); await ready();
  fireEvent.click(screen.getByRole('checkbox', { name: '锁定宽高比' }));
  fireEvent.change(screen.getByRole('spinbutton', { name: 'PNG 宽度（像素）' }), { target: { value: '8000' } });
  fireEvent.change(screen.getByRole('spinbutton', { name: 'PNG 高度（像素）' }), { target: { value: '8000' } });
  expect(screen.getByText('总像素最多 3200 万，请减小宽度或高度')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '下载 PNG' })).toBeDisabled();
  expect(exporter).not.toHaveBeenCalled();
});
