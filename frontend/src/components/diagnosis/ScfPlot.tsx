// ============================================================
// ScfPlot — ECharts SCF 收敛曲线
// ============================================================

import React, { useMemo, useState } from 'react';
import { Card, Segmented, Typography } from 'antd';
import ReactECharts from 'echarts-for-react';
import type { ScfPlotData } from '../../types/generated-api';

const { Text } = Typography;

interface ScfPlotProps {
  data: ScfPlotData;
  nelm?: number;
}

const ScfPlot: React.FC<ScfPlotProps> = ({ data, nelm = 200 }) => {
  const ionicSteps = useMemo(() => {
    const steps = new Set<number>();
    data.series.forEach((s) => steps.add(s.ionic_step));
    return Array.from(steps).sort((a, b) => a - b);
  }, [data.series]);

  const [selectedIonic, setSelectedIonic] = useState<number>(ionicSteps[0] || 1);

  const filteredSeries = useMemo(() => {
    return data.series.filter((s) => s.ionic_step === selectedIonic);
  }, [data.series, selectedIonic]);

  const option = useMemo(() => ({
    tooltip: {
      trigger: 'axis' as const,
      formatter: (params: unknown[]) => {
        const p = params as { data: [number, number] }[];
        if (!p.length) return '';
        const d = p[0].data;
        return `电子步: ${d[0]}<br/>能量: ${d[1].toFixed(6)} eV`;
      },
    },
    xAxis: {
      type: 'value' as const,
      name: data.x_label || '电子步',
      min: 0,
      max: Math.max(nelm, Math.max(...filteredSeries.map((s) => s.electronic_step))),
    },
    yAxis: {
      type: 'value' as const,
      name: data.y_label || '能量 (eV)',
      axisLabel: {
        formatter: (val: number) => val.toFixed(4),
      },
    },
    series: [{
      data: filteredSeries.map((s) => [s.electronic_step, s.energy]),
      type: 'line' as const,
      smooth: false,
      symbol: 'none',
      lineStyle: { width: 1.5 },
      // 中国股市惯例：能量下降=红(好)，能量上升=绿(问题)
      // SCF 曲线：收敛=能量下降趋势，用红蓝色区分不同离子步
      color: '#1677ff',
    }],
    grid: { left: 60, right: 20, top: 20, bottom: 40 },
    // 标注 NELM 边界
    markLine: nelm ? {
      silent: true,
      symbol: 'none',
      lineStyle: { color: '#ff4d4f', type: 'dashed' as const, width: 1 },
      data: [{ xAxis: nelm, label: { formatter: `NELM=${nelm}`, position: 'end' } }],
    } : undefined,
  }), [filteredSeries, data, nelm]);

  if (!data.series.length) {
    return (
      <Card title="SCF 收敛曲线">
        <Text type="secondary">暂无 SCF 数据</Text>
      </Card>
    );
  }

  return (
    <Card
      title="SCF 收敛曲线"
      extra={
        <Segmented
          size="small"
          options={ionicSteps.map((s) => ({ label: `离子步 ${s}`, value: s }))}
          value={selectedIonic}
          onChange={(v) => setSelectedIonic(v as number)}
        />
      }
    >
      <ReactECharts option={option} style={{ height: 300 }} />
      <Text type="secondary" style={{ fontSize: 12 }}>
        注：NELM 边界用红色虚线表示。能量下降趋近收敛为正常行为。
      </Text>
    </Card>
  );
};

export default ScfPlot;