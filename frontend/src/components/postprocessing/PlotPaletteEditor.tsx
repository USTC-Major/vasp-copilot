import { Alert, Button, Input, Select, Space, Typography } from 'antd';
import { colorContrastOnWhite, paletteError, PLOT_PALETTES, type PlotPalette } from '../../api/plotPreferences';
import './plot-preferences.css';

export function PalettePreview({ colors }: { colors: string[] }) {
  return <svg className="plot-palette-preview" viewBox="0 0 500 170" role="img" aria-label="白底深字配色示意预览">
    <rect width="500" height="170" fill="#FFFFFF" />
    <path d="M42 16V137H482" fill="none" stroke="#252525" />
    {[0, 1, 2].map(index => <text key={index} x="20" y={133 - index * 48} fill="#252525" fontSize="11">{index}</text>)}
    {colors.filter(color => /^#[\da-fA-F]{6}$/.test(color)).slice(0, 6).map((color, index) => <g key={index}>
      <path d={`M48 ${120 - index * 10} C145 ${128 - index * 10},180 ${36 + index * 11},260 ${58 + index * 8} S380 ${127 - index * 9},473 ${26 + index * 13}`} fill="none" stroke={color} strokeWidth="2.5" strokeDasharray={index % 2 ? '7 4' : undefined} />
      <path d={`M${52 + index * 72} 154h15`} stroke={color} strokeWidth="2.5" />
      <text x={72 + index * 72} y="158" fill="#252525" fontSize="10">曲线 {index + 1}</text>
    </g>)}
  </svg>;
}

export default function PlotPaletteEditor({ value, onChange, disabled = false }: {
  value: PlotPalette; onChange: (palette: PlotPalette) => void; disabled?: boolean;
}) {
  const update = (colors: string[]) => onChange({ preset: 'custom', colors });
  const invalid = paletteError(value);
  const pale = value.colors.map((color, index) => /^#[\da-fA-F]{6}$/.test(color) && colorContrastOnWhite(color) < 3 ? index + 1 : null).filter(Boolean);
  return <div className="plot-palette-editor">
    <label htmlFor="plot-palette-preset"><Typography.Text strong>导出配色</Typography.Text></label>
    <Select id="plot-palette-preset" aria-label="导出配色" value={value.preset} disabled={disabled} style={{ width: '100%' }}
      options={[...PLOT_PALETTES.map(p => ({ value: p.id, label: p.label })), { value: 'custom', label: '自定义' }]}
      onChange={preset => {
        const found = PLOT_PALETTES.find(p => p.id === preset);
        onChange(found ? { preset: found.id, colors: [...found.colors] } : { ...value, preset: 'custom' });
      }} />
    <Typography.Text type="secondary">颜色按固定曲线语义分配；自旋保留线型区别。修改颜色或顺序后成为自定义配色。</Typography.Text>
    <div className="plot-color-list">
      {value.colors.map((color, index) => <div className="plot-color-row" key={index}>
        <input type="color" aria-label={`选择颜色 ${index + 1}`} value={/^#[\da-fA-F]{6}$/.test(color) ? color : '#000000'} disabled={disabled}
          onChange={event => update(value.colors.map((item, i) => i === index ? event.target.value.toUpperCase() : item))} />
        <Input aria-label={`颜色 ${index + 1}`} value={color} maxLength={7} disabled={disabled} status={!/^#[\da-fA-F]{6}$/.test(color) ? 'error' : undefined}
          onChange={event => update(value.colors.map((item, i) => i === index ? event.target.value : item))} />
        <Space size={4}>
          <Button aria-label={`颜色 ${index + 1} 前移`} disabled={disabled || index === 0} onClick={() => { const colors = [...value.colors]; [colors[index - 1], colors[index]] = [colors[index], colors[index - 1]]; update(colors); }}>↑</Button>
          <Button aria-label={`颜色 ${index + 1} 后移`} disabled={disabled || index === value.colors.length - 1} onClick={() => { const colors = [...value.colors]; [colors[index], colors[index + 1]] = [colors[index + 1], colors[index]]; update(colors); }}>↓</Button>
          <Button aria-label={`删除颜色 ${index + 1}`} disabled={disabled || value.colors.length <= 2} onClick={() => update(value.colors.filter((_, i) => i !== index))}>删除</Button>
        </Space>
      </div>)}
    </div>
    <Button disabled={disabled || value.colors.length >= 16} onClick={() => update([...value.colors, '#333333'])}>添加颜色</Button>
    {invalid && <Alert type="error" showIcon title={invalid} />}
    {pale.length > 0 && <Alert type="warning" showIcon title={`颜色 ${pale.join('、')} 在白底上较浅，细线与打印可能不清楚`} />}
    <PalettePreview colors={value.colors} />
  </div>;
}
