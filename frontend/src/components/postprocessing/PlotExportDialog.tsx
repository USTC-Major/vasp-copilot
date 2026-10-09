import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Checkbox, InputNumber, Modal, Select, Space, Typography } from 'antd';
import { paletteError, type PlotExportSettings, type PlotPalette } from '../../api/plotPreferences';
import { usePlotPalette } from '../../hooks/usePlotPreferences';
import PlotPaletteEditor from './PlotPaletteEditor';

const ratios = [
  { value: 16 / 9, label: '16:9' }, { value: 4 / 3, label: '4:3' }, { value: 3 / 2, label: '3:2' },
  { value: 1, label: '1:1' }, { value: 3 / 4, label: '3:4' },
];
export function exportSizeError(width: number | null, height: number | null): string | null {
  if (width === null || height === null || !Number.isInteger(width) || !Number.isInteger(height) || width < 240 || width > 8000 || height < 240 || height > 8000) return '宽高须为 240–8000 的整数像素';
  if (width * height > 32_000_000) return '总像素最多 3200 万，请减小宽度或高度';
  return null;
}

export default function PlotExportDialog({ open, onCancel, onExport, preview }: {
  open: boolean; onCancel: () => void; onExport: (settings: PlotExportSettings) => Promise<void>;
  preview?: (settings: PlotExportSettings) => Promise<string>;
}) {
  const defaults = usePlotPalette(open);
  const [width, setWidth] = useState<number | null>(1600);
  const [height, setHeight] = useState<number | null>(1000);
  const [locked, setLocked] = useState(true);
  const ratio = useRef(1.6);
  const [draftPalette, setDraftPalette] = useState<PlotPalette | null>(null);
  const palette = draftPalette ?? defaults.preferences.palette;
  const [busy, setBusy] = useState<'export' | 'save' | 'preview' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const previewGeneration = useRef(0);
  useEffect(() => {
    if (!open) return;
    setWidth(1600); setHeight(1000); setLocked(true); ratio.current = 1.6;
    setDraftPalette(null); setError(null); setNotice(null); setPreviewUrl(null);
  }, [open]);
  useEffect(() => { previewGeneration.current += 1; setPreviewUrl(null); }, [open, width, height, palette]);
  const sizeError = exportSizeError(width, height);
  const invalid = sizeError ?? paletteError(palette);
  const settings = (): PlotExportSettings => ({ width_px: width!, height_px: height!, colors: palette.colors.map(color => color.toUpperCase()) });
  const perform = async (action: 'export' | 'save' | 'preview') => {
    const validation = action === 'save' ? paletteError(palette) : invalid;
    if (validation) { setError(validation); return; }
    setBusy(action); setError(null); setNotice(null);
    try {
      if (action === 'save') {
        await defaults.saveDefault(palette);
        setDraftPalette(null); setNotice('当前配色已保存为全局默认');
      } else if (action === 'preview' && preview) {
        const generation = previewGeneration.current;
        const url = await preview(settings());
        if (generation === previewGeneration.current) setPreviewUrl(url);
      } else if (action === 'export') {
        await onExport(settings()); onCancel();
      }
    } catch (err) { setError(err instanceof Error ? err.message : '导出操作失败'); }
    finally { setBusy(null); }
  };
  const disabled = defaults.loading || busy !== null;
  return <Modal title="导出 PNG" open={open} onCancel={() => { if (!busy) onCancel(); }} width={650}
    mask={{ closable: !busy }} closable={!busy}
    footer={<Space wrap><Button disabled={busy !== null} onClick={onCancel}>取消</Button>
      {preview && <Button disabled={disabled || Boolean(invalid)} loading={busy === 'preview'} onClick={() => void perform('preview')}>预览 PNG</Button>}
      <Button type="primary" disabled={disabled || Boolean(invalid)} loading={busy === 'export'} onClick={() => void perform('export')}>下载 PNG</Button></Space>}>
    <Typography.Paragraph>PNG 使用白底、深色文字。配色独立于页面主题；本次修改仅用于本次导出。</Typography.Paragraph>
    <div className="plot-export-size">
      <label htmlFor="plot-export-width">宽度（像素）<InputNumber id="plot-export-width" aria-label="PNG 宽度（像素）" style={{ width: '100%' }} value={width} min={240} max={8000} step={1} disabled={disabled}
        onChange={value => { setWidth(value); if (locked && value !== null) setHeight(Math.round(value / ratio.current)); }} /></label>
      <label htmlFor="plot-export-height">高度（像素）<InputNumber id="plot-export-height" aria-label="PNG 高度（像素）" style={{ width: '100%' }} value={height} min={240} max={8000} step={1} disabled={disabled}
        onChange={value => { setHeight(value); if (locked && value !== null) setWidth(Math.round(value * ratio.current)); }} /></label>
    </div>
    <div className="plot-export-controls">
      <Checkbox checked={locked} disabled={disabled} onChange={event => { setLocked(event.target.checked); if (width && height) ratio.current = width / height; }}>锁定宽高比</Checkbox>
      <Select aria-label="常用宽高比" placeholder="常用比例" style={{ minWidth: 140 }} disabled={disabled}
        value={width && height ? ratios.find(item => Math.abs(width / height - item.value) < .002)?.value : undefined}
        options={ratios} onChange={value => { ratio.current = value; setLocked(true); if (width) setHeight(Math.round(width / value)); }} />
    </div>
    {sizeError && <Alert type="error" showIcon title={sizeError} />}
    {defaults.error && <Alert type="warning" showIcon title="默认配色暂不可读取，当前可临时导出科研通用配色" description={defaults.error}
      action={<Button size="small" onClick={defaults.reload} disabled={disabled}>重新读取配色</Button>} />}
    <PlotPaletteEditor value={palette} disabled={disabled} onChange={value => { setDraftPalette(value); setNotice(null); }} />
    <div className="plot-export-controls"><Button disabled={disabled || Boolean(defaults.error) || Boolean(paletteError(palette))} loading={busy === 'save'} onClick={() => void perform('save')}>将当前配色设为默认</Button>
      <Typography.Text type="secondary">仅此按钮会更改全局默认配色。</Typography.Text></div>
    {notice && <Alert type="success" showIcon title={notice} />}
    {error && <Alert type="error" showIcon title={error} />}
    {previewUrl && <><Typography.Paragraph>实际 PNG 预览：{width} × {height} 像素</Typography.Paragraph><img className="plot-export-preview" src={previewUrl} alt={`实际 PNG 预览 ${width} × ${height} 像素`} /></>}
  </Modal>;
}
