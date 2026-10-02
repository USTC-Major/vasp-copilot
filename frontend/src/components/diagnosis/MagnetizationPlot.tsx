import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Card, Col, Row, Select, Space, Table, Tag, Typography } from 'antd';
import ReactECharts from 'echarts-for-react';
import type { CalculationMode, MagnetizationPlotData } from '../../types/generated-api';
import { buildMagneticView, type MagneticFilters, type MagneticView } from './magneticPlotModel';

const { Text } = Typography;

interface MagnetizationPlotProps {
  data: MagnetizationPlotData;
  calculationMode: CalculationMode;
}

const cellWrap = () => ({ style: { whiteSpace: 'normal' as const, overflowWrap: 'anywhere' as const } });
const rawColumns = [
  { title: '坐标轴/表格', dataIndex: 'axis', key: 'axis', width: 110, onCell: cellWrap },
  { title: '原子序号', dataIndex: 'atom', key: 'atom', width: 110, onCell: cellWrap },
  { title: '原始值', dataIndex: 'values', key: 'values', width: 420, onCell: cellWrap },
  { title: '来源行', dataIndex: 'evidence', key: 'evidence', width: 240, onCell: cellWrap },
];

const MagnetizationPlot: React.FC<MagnetizationPlotProps> = ({ data, calculationMode }) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const [compact, setCompact] = useState(() => typeof window !== 'undefined' && window.innerWidth < 760);
  const [filters, setFilters] = useState<MagneticFilters>({});

  useEffect(() => {
    const element = containerRef.current;
    if (!element) return undefined;
    const updateLayout = () => setCompact(element.clientWidth < 760);
    updateLayout();
    const observer = new ResizeObserver(updateLayout);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const view = useMemo(() => buildMagneticView(data, calculationMode, { ...filters, compact }), [data, calculationMode, filters, compact]);
  const hasAnalysis = 'analysis' in data && Boolean(data.analysis);
  const scalarComparisonUnsupported = calculationMode.is_soc
    || calculationMode.is_noncollinear
    || calculationMode.magnetization_analysis_mode === 'unsupported_noncollinear_or_soc'
    || data.analysis?.status === 'unsupported';
  const expandEvidenceDetails = !hasAnalysis || data.analysis?.status !== 'ready';

  const updateFilter = <K extends keyof MagneticFilters>(key: K, value: MagneticFilters[K] | undefined) => {
    setFilters((previous) => ({ ...previous, [key]: value }));
  };

  const wrapCell = () => ({ style: { whiteSpace: 'normal' as const, overflowWrap: 'anywhere' as const } });
  const columns = [
    { title: '序号', dataIndex: 'atom_index', key: 'atom_index', width: 72, fixed: 'left' as const, onCell: wrapCell },
    { title: '元素', dataIndex: 'element', key: 'element', width: 76, fixed: 'left' as const, onCell: wrapCell },
    { title: '位置（原坐标）', dataIndex: 'position', key: 'position', width: 150, onCell: wrapCell },
    ...(hasAnalysis ? [
      { title: 'MAGMOM 输入参考', dataIndex: 'input', key: 'input', width: 165, onCell: wrapCell },
      { title: '最新观测投影', dataIndex: 'output', key: 'output', width: 150, onCell: wrapCell },
      { title: '差值', dataIndex: 'delta', key: 'delta', width: 110, onCell: wrapCell },
      { title: '参考组', dataIndex: 'referenceGroup', key: 'referenceGroup', width: 90, onCell: wrapCell },
      { title: '方向', dataIndex: 'orientation', key: 'orientation', width: 95, onCell: wrapCell },
      { title: '幅值', dataIndex: 'magnitude', key: 'magnitude', width: 95, onCell: wrapCell },
      { title: '变化说明', dataIndex: 'changes', key: 'changes', width: 140, render: (items: string[]) => items.join('、'), onCell: wrapCell },
      { title: '依据', dataIndex: 'evidence', key: 'evidence', width: 190, onCell: wrapCell },
    ] : [
      { title: '最新观测投影', dataIndex: 'output', key: 'output', width: 180, onCell: wrapCell },
      { title: '输出依据', dataIndex: 'evidence', key: 'evidence', width: 220, onCell: wrapCell },
    ]),
  ];

  return (
    <div ref={containerRef} style={{ width: '100%', minWidth: 0 }}>
      <style>{`
        .magnetization-plot-card .ant-card-head-wrapper { flex-wrap: wrap; gap: 4px 12px; }
        .magnetization-plot-card .ant-card-head-title { min-width: 0; white-space: normal; overflow-wrap: anywhere; flex: 1 1 12rem; }
        .magnetization-plot-card .ant-card-extra { margin-inline-start: 0; white-space: normal; flex: 0 1 auto; }
        .magnetization-plot-card .ant-card-extra .ant-tag { height: auto; white-space: normal; }
      `}</style>
      <Card
        className="magnetization-plot-card"
        title="磁性排列对照"
        extra={<Tag>{view.statusLabel}</Tag>}
      >
        <Space orientation="vertical" size="middle" style={{ width: '100%', minWidth: 0 }}>
          {!hasAnalysis && (
            <Alert
              type="info"
              showIcon
              title="旧版诊断未提供磁性比较数据"
              description="这里只显示已有的输出投影，不根据旧字段推断磁性排列；输入标签不会被解释为实测初始磁矩。"
            />
          )}
          {hasAnalysis && (
            <Text type="secondary" style={{ overflowWrap: 'anywhere' }}>
              “MAGMOM 输入参考”是 INCAR 中的输入参数，不代表实测初始磁矩；输出列表示最近观测到的局域投影。表格保持结构原子顺序。
            </Text>
          )}

          {view.summary.length > 0 && (
            <Alert
              type="info"
              showIcon
              title="排列概览"
              description={(
                <Space orientation="vertical" size={4} style={{ width: '100%', overflowWrap: 'anywhere' }} aria-label="排列概览说明">
                  {view.summary.map((item, index) => <Text key={`summary-${index}`}>{item}</Text>)}
                </Space>
              )}
            />
          )}

          {view.totals.length > 0 && (
            <Row gutter={[12, 12]}>
              {view.totals.map((total) => (
                <Col key={total.key} xs={24} sm={12} lg={8}>
                  <Card size="small">
                    <Space orientation="vertical" size={4} style={{ width: '100%', overflowWrap: 'anywhere' }}>
                      <Text strong style={{ overflowWrap: 'anywhere' }}>{total.label}</Text>
                      <Text strong>{total.value}</Text>
                      <Text type="secondary">{total.evidence}</Text>
                    </Space>
                  </Card>
                </Col>
              ))}
            </Row>
          )}

          {view.chartAvailable ? (
            <ReactECharts
              option={view.option}
              notMerge
              autoResize
              style={{ height: compact ? 420 : 340, width: '100%' }}
            />
          ) : (
            <Text type="secondary">当前没有可用的标量排列图；请查看上方限制说明和下方原始输出。</Text>
          )}

          {(view.notes.length > 0 || view.thresholdNotes.length > 0) && (
            <details open={expandEvidenceDetails}>
              <summary>比较依据与限制{view.thresholdNotes.length > 0 ? '和显示整理阈值' : ''}</summary>
              <Space orientation="vertical" size="middle" style={{ width: '100%', marginTop: 8 }}>
                {view.notes.length > 0 && (
                  <section aria-label="比较依据与限制">
                    <Space orientation="vertical" size={4} style={{ width: '100%', overflowWrap: 'anywhere' }}>
                      {view.notes.map((note, index) => <Text key={`note-${index}`} type="secondary">{note}</Text>)}
                    </Space>
                  </section>
                )}
                {view.thresholdNotes.length > 0 && (
                  <section aria-label="显示整理阈值">
                    <Text strong>显示整理阈值</Text>
                    <Space orientation="vertical" size={4} style={{ width: '100%', marginTop: 4, overflowWrap: 'anywhere' }}>
                      {view.thresholdNotes.map((note, index) => <Text key={`threshold-${index}`} type="secondary">{note}</Text>)}
                    </Space>
                  </section>
                )}
              </Space>
            </details>
          )}

          {!scalarComparisonUnsupported && view.filterOptions.elements.length > 0 && (
            <Space wrap style={{ width: '100%' }}>
              <Select
                aria-label="按元素筛选"
                allowClear
                placeholder="全部元素"
                value={filters.element}
                options={view.filterOptions.elements.map((element) => ({ value: element, label: element || '元素未知' }))}
                onChange={(value) => updateFilter('element', value)}
                style={{ minWidth: 140, maxWidth: '100%' }}
              />
              {hasAnalysis && (
                <>
                  <Select
                    aria-label="按MAGMOM输入参考组筛选"
                    allowClear
                    placeholder="全部输入参考组"
                    value={filters.group}
                    options={view.filterOptions.groups}
                    onChange={(value) => updateFilter('group', value)}
                    style={{ minWidth: 180, maxWidth: '100%' }}
                  />
                  <Select
                    aria-label="按变化类别筛选"
                    allowClear
                    placeholder="全部变化类别"
                    value={filters.change}
                    options={view.filterOptions.changes}
                    onChange={(value) => updateFilter('change', value)}
                    style={{ minWidth: 170, maxWidth: '100%' }}
                  />
                </>
              )}
            </Space>
          )}

          {!scalarComparisonUnsupported && (
            <div style={{ maxWidth: '100%', overflowX: 'auto' }}>
              <Table<MagneticView['rows'][number]>
                size="small"
                rowKey="key"
                columns={columns}
                dataSource={view.rows}
                pagination={{ defaultPageSize: 10, showSizeChanger: true, pageSizeOptions: [10, 20, 50] }}
                scroll={{ x: hasAnalysis ? 1150 : 600 }}
                locale={{ emptyText: '没有符合条件的原子记录' }}
              />
            </div>
          )}

          {view.rawRows.length > 0 && (
            <details>
              <summary>查看原始磁性输出证据</summary>
              <div style={{ maxWidth: '100%', overflowX: 'auto', marginTop: 8 }}>
                <Table
                  size="small"
                  rowKey="key"
                  columns={rawColumns}
                  dataSource={view.rawRows}
                  pagination={{ defaultPageSize: 10, showSizeChanger: true, pageSizeOptions: [10, 20, 50] }}
                  scroll={{ x: 880 }}
                />
              </div>
            </details>
          )}
        </Space>
      </Card>
    </div>
  );
};

export default MagnetizationPlot;
