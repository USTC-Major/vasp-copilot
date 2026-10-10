import { useEffect, useState } from 'react';
import { Alert, Button, Card, Space, Typography } from 'antd';
import { paletteError, type PlotPalette } from '../../api/plotPreferences';
import { usePlotPalette } from '../../hooks/usePlotPreferences';
import PlotPaletteEditor from './PlotPaletteEditor';

export default function PlotPreferencesSettings({ onDirtyChange }: { onDirtyChange?: (dirty: boolean) => void }) {
  const defaults = usePlotPalette();
  const [draftPalette, setDraftPalette] = useState<PlotPalette | null>(null);
  const palette = draftPalette ?? defaults.preferences.palette;
  const dirty = draftPalette !== null;
  useEffect(() => { onDirtyChange?.(dirty); }, [dirty, onDirtyChange]);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<{ kind: 'error' | 'success'; text: string } | null>(null);
  const save = async () => {
    setSaving(true); setNotice(null);
    try { await defaults.saveDefault(palette); setDraftPalette(null); setNotice({ kind: 'success', text: '默认配色已保存（仅本地）' }); }
    catch (err) { setNotice({ kind: 'error', text: err instanceof Error ? err.message : '默认配色保存失败' }); }
    finally { setSaving(false); }
  };
  return <Card title="绘图与导出" className="settings-section plot-preferences-card" aria-label="绘图与导出设置">
    <Typography.Paragraph type="secondary">后处理图形共用此配色顺序。PNG 使用白底、深色文字；页面深浅主题独立设置。无需配置模型。</Typography.Paragraph>
    {defaults.error && <Alert type="error" showIcon title="默认配色读取失败" description={defaults.error} />}
    <PlotPaletteEditor value={palette} disabled={defaults.loading || saving || Boolean(defaults.error)} onChange={value => { setDraftPalette(value); setNotice(null); }} />
    <Space wrap style={{ marginTop: 12 }}>
      <Button type="primary" onClick={() => void save()} loading={saving} disabled={defaults.loading || Boolean(defaults.error) || Boolean(paletteError(palette))}>保存默认配色</Button>
      <Button disabled={saving} onClick={() => { setDraftPalette(null); setNotice(null); defaults.reload(); }}>重新读取配色</Button>
      {dirty && <Typography.Text type="secondary">有未保存配色</Typography.Text>}
    </Space>
    {notice && <Alert type={notice.kind} showIcon title={notice.text} style={{ marginTop: 12 }} />}
  </Card>;
}
