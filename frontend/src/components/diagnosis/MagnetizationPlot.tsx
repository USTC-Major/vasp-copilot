// ============================================================
// MagnetizationPlot — 磁矩柱状图
// ============================================================

import React, { useMemo } from 'react';
import { Card, Alert, Typography } from 'antd';
import ReactECharts from 'echarts-for-react';
import type { MagnetizationPlotData, CalculationMode } from '../../types/generated-api';

const { Text } = Typography;

interface MagnetizationPlotProps {
  data: MagnetizationPlotData;
  calculationMode: CalculationMode;
}

const MagnetizationPlot: React.FC<MagnetizationPlotProps> = ({ data, calculationMode }) => {
  // SOC / noncollinear 模式下不支持简单符号诊断
  const isUnsupported =
    calculationMode.is_soc ||
    calculationMode.is_noncollinear ||
    calculationMode.magnetization_analysis_mode !== 'collinear';

  const option = useMemo(() => {
    const atoms = data.series;

    return {
      tooltip: {
        trigger: 'axis' as const,
        formatter: (params: unknown[]) => {
          const p = params as { dataIndex: number }[];
          if (!p.length) return '';
          const atom = atoms[p[0].dataIndex];
          return `
            <strong>${atom.element} (原子 ${atom.atom_index})</strong><br/>
            初始磁矩: ${atom.initial_moment.toFixed(3)} μB<br/>
            最终磁矩: ${atom.final_moment.toFixed(3)} μB<br/>
            ${atom.initial_moment * atom.final_moment < 0 ? '<span style="color:#faad14">⚠ 符号翻转</span>' : ''}
            ${Math.abs(atom.initial_moment) > 1 && Math.abs(atom.final_moment) < 0.1 ? '<span style="color:#ff7a45">⚠ 磁矩塌缩</span>' : ''}
          `;
        },
      },
      legend: {
        data: ['初始磁矩', '最终磁矩'],
        top: 0,
      },
      xAxis: {
        type: 'category' as const,
        data: atoms.map((a) => `${a.element}(${a.atom_index})`),
        axisLabel: {
          rotate: 30,
          fontSize: 11,
        },
      },
      yAxis: {
        type: 'value' as const,
        name: '磁矩 (μB)',
      },
      series: [
        {
          name: '初始磁矩',
          type: 'bar' as const,
          data: atoms.map((a) => a.initial_moment),
          itemStyle: { color: '#1677ff', borderRadius: [4, 4, 0, 0] },
          barGap: '10%',
        },
        {
          name: '最终磁矩',
          type: 'bar' as const,
          data: atoms.map((a) => a.final_moment),
          itemStyle: {
            color: (params: { dataIndex: number }) => {
              const atom = atoms[params.dataIndex];
              // 符号翻转 = 橙色
              if (atom.initial_moment * atom.final_moment < 0) return '#faad14';
              // 塌缩 = 红色
              if (Math.abs(atom.initial_moment) > 1 && Math.abs(atom.final_moment) < 0.1) return '#ff7a45';
              return '#52c41a';
            },
            borderRadius: [4, 4, 0, 0],
          },
        },
      ],
      grid: { left: 60, right: 20, top: 40, bottom: 50 },
    };
  }, [data]);

  if (isUnsupported) {
    return (
      <Card title="磁矩分析">
        <Alert
          type="info"
          showIcon
          message="不支持简单符号诊断"
          description={
            <div>
              <p>
                {calculationMode.is_soc ? '该计算启用了自旋轨道耦合 (SOC)' : ''}
                {calculationMode.is_noncollinear ? '该计算使用非共线磁性 (noncollinear)' : ''}
                ，系统不支持当前模式下的简单磁矩符号翻转/塌缩诊断。
              </p>
              <p>如需诊断，请在 collinear 模式下重新计算或使用专业后处理工具。</p>
            </div>
          }
        />
      </Card>
    );
  }

  if (!data.series.length) {
    return (
      <Card title="磁矩分析">
        <Text type="secondary">暂无磁矩数据</Text>
      </Card>
    );
  }

  const hasFlip = data.series.some((a) => a.initial_moment * a.final_moment < 0);
  const hasCollapse = data.series.some(
    (a) => Math.abs(a.initial_moment) > 1 && Math.abs(a.final_moment) < 0.1
  );

  return (
    <Card title="磁矩分析">
      {(hasFlip || hasCollapse) && (
        <Alert
          type="warning"
          showIcon
          message="检测到磁矩异常"
          description={
            <div>
              {hasFlip && <div>• 部分原子磁矩符号翻转（橙色柱），可能进入不同磁态</div>}
              {hasCollapse && <div>• 部分原子磁矩塌缩（红色柱），初始磁矩大但最终趋近于零</div>}
              <div style={{ marginTop: 4, color: '#999' }}>这些现象不一定是错误，请结合物理预期判断。</div>
            </div>
          }
          style={{ marginBottom: 12 }}
        />
      )}
      <ReactECharts option={option} style={{ height: 300 }} />
      <div style={{ marginTop: 8, display: 'flex', gap: 16, fontSize: 12 }}>
        <span><span style={{ color: '#1677ff' }}>■</span> 初始磁矩</span>
        <span><span style={{ color: '#52c41a' }}>■</span> 最终磁矩（正常）</span>
        <span><span style={{ color: '#faad14' }}>■</span> 符号翻转（橙色 = 提示）</span>
        <span><span style={{ color: '#ff7a45' }}>■</span> 磁矩塌缩（红色 = 提示）</span>
      </div>
    </Card>
  );
};

export default MagnetizationPlot;