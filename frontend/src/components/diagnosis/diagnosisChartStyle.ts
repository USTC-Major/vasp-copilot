import type { EChartsOption } from 'echarts';

interface ChartColors {
  colorText: string;
  colorTextSecondary: string;
  colorBorder: string;
  colorBgContainer: string;
  colorPrimary: string;
  colorWarning: string;
  colorFillSecondary: string;
}

type Part = Record<string, unknown>;
const object = (value: unknown): Part => value && typeof value === 'object' ? value as Part : {};
const mapParts = (value: unknown, style: (part: Part) => Part): unknown =>
  Array.isArray(value) ? value.map(item => style(object(item))) : value ? style(object(value)) : value;

/** Presentation only: retain data, formatters, ranges, references and interaction options. */
export function diagnosisChartStyle(option: EChartsOption, colors: ChartColors, seriesColors: Record<string, string>): EChartsOption {
  const axis = (part: Part): Part => ({
    ...part,
    axisLabel: { ...object(part.axisLabel), color: colors.colorTextSecondary },
    nameTextStyle: { ...object(part.nameTextStyle), color: colors.colorTextSecondary },
    axisLine: { ...object(part.axisLine), lineStyle: { ...object(object(part.axisLine).lineStyle), color: colors.colorBorder } },
    splitLine: { ...object(part.splitLine), lineStyle: { ...object(object(part.splitLine).lineStyle), color: colors.colorBorder } },
  });
  return {
    ...option,
    textStyle: { ...option.textStyle, color: colors.colorText },
    title: mapParts(option.title, part => ({ ...part, textStyle: { ...object(part.textStyle), color: colors.colorText } })),
    legend: mapParts(option.legend, part => ({ ...part, textStyle: { ...object(part.textStyle), color: colors.colorTextSecondary } })),
    xAxis: mapParts(option.xAxis, axis),
    yAxis: mapParts(option.yAxis, axis),
    tooltip: mapParts(option.tooltip, part => ({
      ...part, backgroundColor: colors.colorBgContainer, borderColor: colors.colorBorder,
      textStyle: { ...object(part.textStyle), color: colors.colorText },
    })),
    dataZoom: mapParts(option.dataZoom, part => ({
      ...part, textStyle: { ...object(part.textStyle), color: colors.colorTextSecondary },
      borderColor: colors.colorBorder, fillerColor: colors.colorFillSecondary,
      handleStyle: { ...object(part.handleStyle), color: colors.colorPrimary },
    })),
    series: mapParts(option.series, part => {
      const color = seriesColors[String(part.name)];
      const markLine = object(part.markLine);
      return {
        ...part,
        ...(color ? { color, itemStyle: { ...object(part.itemStyle), color } } : {}),
        ...(part.markLine ? { markLine: {
          ...markLine,
          label: { ...object(markLine.label), color: colors.colorText, backgroundColor: colors.colorBgContainer },
          lineStyle: { ...object(markLine.lineStyle), color: colors.colorWarning },
        } } : {}),
      };
    }),
  } as EChartsOption;
}
