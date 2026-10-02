import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { EChartsOption } from 'echarts';
import type { ScfPlotData } from '../../types/generated-api';
import ScfPlot from './ScfPlot';

const chartHarness = vi.hoisted(() => ({ latest: null as unknown, mounts: 0, unmounts: 0 }));

vi.mock('echarts-for-react', async () => {
  const React = await import('react');
  return {
    default: function MockECharts(props: unknown) {
      chartHarness.latest = props;
      React.useEffect(() => {
        chartHarness.mounts += 1;
        return () => { chartHarness.unmounts += 1; };
      }, []);
      return null;
    },
  };
});

const scfFixture: ScfPlotData = {
  x_label: '电子步',
  y_label: '自由能变化 (eV)',
  series: [
    { block_id: 10, ionic_step: 1, electronic_step: 1, energy: -10, energy_raw: '-10.000', energy_status: 'available', delta_energy_ev: -0.2, delta_energy_raw: '-0.200', delta_energy_status: 'available', delta_energy_source: 'oszicar', delta_epsilon_ev: 0.1, delta_epsilon_raw: '0.100', delta_epsilon_status: 'available', source_file: 'OSZICAR', source_line: 10 },
    { block_id: 10, ionic_step: 1, electronic_step: 2, energy: -10.2, energy_raw: '-10.200', energy_status: 'available', delta_energy_ev: 0, delta_energy_raw: '0.000', delta_energy_status: 'zero', delta_energy_source: 'oszicar', delta_epsilon_ev: 0, delta_epsilon_raw: '0.000', delta_epsilon_status: 'zero', source_file: 'OSZICAR', source_line: 11 },
    { block_id: 11, ionic_step: 1, electronic_step: 1, energy: -0.2, energy_raw: '-0.200', energy_status: 'available', delta_energy_ev: null, delta_energy_raw: null, delta_energy_status: 'missing', delta_energy_source: null, delta_epsilon_ev: null, delta_epsilon_raw: null, delta_epsilon_status: 'missing', source_file: 'OSZICAR', source_line: 20 },
  ],
  parameters: {
    ediff: { value: 1e-5, source: 'OUTCAR', source_line: 4, raw: '1E-5', status: 'available', input_conflict: false, scope: 'run_unassigned', candidates: [] },
    nelm: { value: 2, source: 'OUTCAR', source_line: 5, raw: '2', status: 'available', input_conflict: false, scope: 'run_unassigned', candidates: [] },
  },
  convergence_evidence: [{ kind: 'electronic_ediff_stop', source: 'OUTCAR', source_line: 42, scope: 'unassigned', block_id: null }],
};

interface ChartProps {
  option: EChartsOption;
  onEvents: { datazoom: (event: unknown) => void };
  style: { height: number | string };
}

const lastChart = (): ChartProps => chartHarness.latest as ChartProps;
const zoomRanges = () => (lastChart().option.dataZoom as { start: number; end: number }[]).map(({ start, end }) => ({ start, end }));

beforeEach(() => {
  chartHarness.latest = null;
  chartHarness.mounts = 0;
  chartHarness.unmounts = 0;
});

afterEach(cleanup);

describe('ScfPlot', () => {
  it('wires dual views, block and interval selection, linked zoom, and inspectable evidence details', async () => {
    const user = userEvent.setup();
    render(<ScfPlot data={scfFixture} />);

    expect(screen.getByText('SCF 双视图')).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: '选择离子步数据块' })).toBeInTheDocument();
    expect(screen.getByText('末期（最后20条）')).toBeInTheDocument();
    const option = lastChart().option;
    expect((option.grid as unknown[]).length).toBe(2);
    expect((option.xAxis as unknown[]).length).toBe(2);
    const deltaSeries = (option.series as { data: { value: [number, number | null] }[] }[])[1];
    expect(deltaSeries.data).toEqual([{ value: [1, 0.2], rowIndex: 0 }, { value: [2, null], rowIndex: 1 }]);
    expect(chartHarness.mounts).toBe(1);
    expect(screen.getByText('电子步 2 存在输出零值：对数轴不绘制，不替换为小正值；输出零值不表示已收敛。')).toBeInTheDocument();
    expect(screen.getByText('EDIFF=0.00001 eV，来源 OUTCAR:4；输出参考，无法归属所选块。')).toBeInTheDocument();
    expect(screen.getByText('计算报告的电子停止证据')).toBeInTheDocument();
    expect(screen.getByText('OUTCAR:42 报告因达到 EDIFF 而停止电子循环；无法归属所选块，不能据此判定此块或整个计算已收敛。')).toBeInTheDocument();

    act(() => lastChart().onEvents.datazoom({ start: 18, end: 72 }));
    await waitFor(() => expect(zoomRanges()).toEqual([{ start: 18, end: 72 }, { start: 18, end: 72 }]));
    await user.click(screen.getByText('全程'));
    await waitFor(() => expect(zoomRanges()).toEqual([{ start: 0, end: 100 }, { start: 0, end: 100 }]));

    const selector = screen.getByRole('combobox', { name: '选择离子步数据块' });
    await user.click(selector);
    await user.click(await screen.findByText('离子步 1 · 块 2'));
    const selectedEnergySeries = (lastChart().option.series as { data: { value: [number, number | null] }[] }[])[0];
    expect(selectedEnergySeries.data).toEqual([{ value: [1, -0.2], rowIndex: 0 }]);
    expect(chartHarness.mounts).toBe(1);

    await user.click(selector);
    await user.click(await screen.findByText('离子步 1 · 块 1'));
    fireEvent.click(screen.getByText('查看逐电子步明细'));
    expect(screen.getByText('OSZICAR:10')).toBeInTheDocument();
    expect(screen.getByText('OSZICAR:11')).toBeInTheDocument();
    expect(screen.queryByText(/能量下降趋近收敛为正常行为/)).not.toBeInTheDocument();
  });

  it('uses compact height after ResizeObserver reports a narrow component and keeps one real point', async () => {
    let width = 900;
    let observerCallback: ResizeObserverCallback | undefined;
    const oldWidth = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'clientWidth');
    const oldObserver = Object.getOwnPropertyDescriptor(globalThis, 'ResizeObserver');
    Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, get: () => width });
    class ObserverStub {
      constructor(callback: ResizeObserverCallback) { observerCallback = callback; }
      observe() {}
      disconnect() {}
      unobserve() {}
    }
    Object.defineProperty(globalThis, 'ResizeObserver', { configurable: true, value: ObserverStub });

    try {
      const onePoint = { ...scfFixture, series: [scfFixture.series[2]], parameters: undefined };
      render(<ScfPlot data={onePoint} />);
      expect(lastChart().style.height).toBe(390);
      expect(screen.queryByText('暂无真实电子步数据')).not.toBeInTheDocument();
      expect(((lastChart().option.series as { data: unknown[] }[])[0]).data).toHaveLength(1);
      expect(JSON.stringify(lastChart().option)).not.toContain('NELM=200');
      width = 375;
      act(() => observerCallback?.([], {} as ResizeObserver));
      await waitFor(() => expect(lastChart().style.height).toBe(600));
      expect((lastChart().option.grid as unknown[]).length).toBe(2);
    } finally {
      if (oldWidth) Object.defineProperty(HTMLElement.prototype, 'clientWidth', oldWidth);
      else delete (HTMLElement.prototype as unknown as { clientWidth?: number }).clientWidth;
      if (oldObserver) Object.defineProperty(globalThis, 'ResizeObserver', oldObserver);
      else delete (globalThis as unknown as { ResizeObserver?: unknown }).ResizeObserver;
    }
  });

  it('shows the explicit empty state without rendering a chart for no electronic records', () => {
    render(<ScfPlot data={{ ...scfFixture, series: [] }} />);
    expect(screen.getByText('暂无真实电子步数据')).toBeInTheDocument();
    expect(screen.getByText('计算报告的电子停止证据')).toBeInTheDocument();
    expect(chartHarness.latest).toBeNull();
  });
});
