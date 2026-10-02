import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Card, Segmented, Select, Space, Table, Typography } from 'antd';
import ReactECharts from 'echarts-for-react';
import type { ScfPlotData } from '../../types/generated-api';
import { buildScfView, getScfBlocks, readScfZoom, type ScfViewMode, type ScfZoom } from './scfPlotModel';

const { Text } = Typography;

interface ScfPlotProps {
  data: ScfPlotData;
}

const pointColumns = [
  { title: '电子步', dataIndex: 'step', key: 'step', width: 90, onCell: () => ({ style: { whiteSpace: 'normal' as const, overflowWrap: 'anywhere' as const } }) },
  { title: '总能量 E', dataIndex: 'energy', key: 'energy', width: 230, onCell: () => ({ style: { whiteSpace: 'normal' as const, overflowWrap: 'anywhere' as const } }) },
  { title: '相邻变化 dE', dataIndex: 'deltaEnergy', key: 'deltaEnergy', width: 280, onCell: () => ({ style: { whiteSpace: 'normal' as const, overflowWrap: 'anywhere' as const } }) },
  { title: 'd epsilon', dataIndex: 'deltaEpsilon', key: 'deltaEpsilon', width: 230, onCell: () => ({ style: { whiteSpace: 'normal' as const, overflowWrap: 'anywhere' as const } }) },
  { title: '来源', dataIndex: 'source', key: 'source', width: 190, onCell: () => ({ style: { whiteSpace: 'normal' as const, overflowWrap: 'anywhere' as const } }) },
];

const ScfPlot: React.FC<ScfPlotProps> = ({ data }) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const blocks = useMemo(() => getScfBlocks(data), [data]);
  const [blockId, setBlockId] = useState<number>();
  const [mode, setMode] = useState<ScfViewMode>('tail');
  const [zoom, setZoom] = useState<ScfZoom>();
  const [compact, setCompact] = useState(() => typeof window !== 'undefined' && window.innerWidth < 760);

  useEffect(() => {
    const element = containerRef.current;
    if (!element) return undefined;
    const updateLayout = () => setCompact(element.clientWidth < 760);
    updateLayout();
    const observer = new ResizeObserver(updateLayout);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (blockId !== undefined && !blocks.some((block) => block.id === blockId)) {
      setBlockId(undefined);
      setZoom(undefined);
    }
  }, [blocks, blockId]);

  const view = useMemo(() => buildScfView(data, { blockId, mode, zoom, compact }), [data, blockId, mode, zoom, compact]);
  const onEvents = useMemo(() => ({
    datazoom: (event: unknown) => {
      const nextZoom = readScfZoom(event);
      if (nextZoom) setZoom(nextZoom);
    },
  }), []);

  const handleModeChange = (value: string | number) => {
    setMode(value as ScfViewMode);
    setZoom(undefined);
  };

  const handleBlockChange = (value: number) => {
    setBlockId(value);
    setZoom(undefined);
  };

  const annotations = (
    <>
      {view.notes.length > 0 && (
        <Space orientation="vertical" size={4} style={{ width: '100%' }} aria-label="SCF 数据说明">
          {view.notes.map((note, index) => <Text key={`note-${index}`} type="secondary">{note}</Text>)}
        </Space>
      )}
      <section aria-label="计算报告的电子停止证据">
        <Text strong>计算报告的电子停止证据</Text>
        <Space orientation="vertical" size={4} style={{ width: '100%', marginTop: 4 }}>
          {view.evidenceNotes.map((note, index) => <Text key={`evidence-${index}`} type="secondary">{note}</Text>)}
        </Space>
      </section>
    </>
  );

  return (
    <div ref={containerRef} style={{ width: '100%', minWidth: 0 }}>
      <Card title="SCF 双视图">
        {blocks.length === 0 ? (
          <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
            <Text type="secondary">暂无真实电子步数据</Text>
            {annotations}
          </Space>
        ) : (
          <Space orientation="vertical" size="middle" style={{ width: '100%', minWidth: 0 }}>
            <Space wrap style={{ maxWidth: '100%' }}>
              <Text>数据块</Text>
              <Select
                aria-label="选择离子步数据块"
                style={{ minWidth: 190, maxWidth: '100%' }}
                value={view.selectedBlock?.id}
                options={view.blocks.map((block) => ({ label: block.label, value: block.id }))}
                onChange={handleBlockChange}
              />
              <Segmented
                aria-label="查看区间"
                options={[
                  { label: '全程', value: 'full' },
                  { label: '末期（最后20条）', value: 'tail' },
                ]}
                value={mode}
                onChange={handleModeChange}
              />
            </Space>

            <ReactECharts
              option={view.option}
              onEvents={onEvents}
              notMerge
              autoResize
              style={{ height: compact ? 600 : 390, width: '100%' }}
            />

            {annotations}

            <details>
              <summary>查看逐电子步明细</summary>
              <div style={{ maxWidth: '100%', overflowX: 'auto', marginTop: 8 }}>
                <Table
                  size="small"
                  pagination={false}
                  rowKey="step"
                  columns={pointColumns}
                  dataSource={view.pointDetails}
                  scroll={{ x: 1020, y: 360 }}
                />
              </div>
            </details>
          </Space>
        )}
      </Card>
    </div>
  );
};

export default ScfPlot;
