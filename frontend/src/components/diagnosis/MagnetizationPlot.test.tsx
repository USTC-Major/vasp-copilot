import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ChangeEvent, CSSProperties } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { EChartsOption } from 'echarts';
import type { CalculationMode, MagnetizationPlotData } from '../../types/generated-api';
import { buildMagneticView, type MagneticView } from './magneticPlotModel';
import MagnetizationPlot from './MagnetizationPlot';

const harness = vi.hoisted(() => ({
  build: vi.fn(),
  latestChart: null as unknown,
  chartMounts: 0,
}));

vi.mock('./magneticPlotModel', () => ({ buildMagneticView: harness.build }));
vi.mock('antd', async (importOriginal) => {
  const actual = await importOriginal<typeof import('antd')>();
  const React = await import('react');
  return {
    ...actual,
    Select: (props: {
      'aria-label'?: string;
      options: { value: string; label: string }[];
      placeholder?: string;
      value?: string;
      style?: CSSProperties;
      onChange?: (value: string | undefined) => void;
    }) => React.createElement('select', {
      'aria-label': props['aria-label'],
      value: props.value ?? '',
      style: props.style,
      onChange: (event: ChangeEvent<HTMLSelectElement>) => props.onChange?.(event.target.value || undefined),
    }, [React.createElement('option', { key: '', value: '' }, props.placeholder), ...props.options.map(option =>
      React.createElement('option', { key: option.value, value: option.value }, option.label))]),
  };
});
vi.mock('echarts-for-react', async () => {
  const React = await import('react');
  return {
    default: function MockECharts(props: unknown) {
      harness.latestChart = props;
      React.useEffect(() => {
        harness.chartMounts += 1;
        return () => { harness.chartMounts -= 1; };
      }, []);
      return null;
    },
  };
});

const calculations: CalculationMode = {
  is_spin_polarized: true,
  is_dftu: false,
  is_soc: false,
  is_noncollinear: false,
  magnetization_analysis_mode: 'collinear',
};

const legacyData = {
  x_label: '原子序号',
  y_label: '磁矩 (μB)',
  series: [
    { atom_index: 1, element: 'Fe', initial_moment: 99, final_moment: -0.35 },
    { atom_index: 2, element: 'O', initial_moment: 0, final_moment: 0.04 },
  ],
} as MagnetizationPlotData;

const allRows = Array.from({ length: 12 }, (_, index) => {
  const atom_index = index + 1;
  const element = atom_index % 2 === 1 ? 'Fe' : 'O';
  return {
    key: `atom-${atom_index}`,
    atom_index,
    element,
    position: `Direct ${atom_index} 0 0`,
    input: atom_index % 2 === 1 ? '+5.0 μB' : '0 μB',
    output: atom_index % 2 === 1 ? '-4.8 μB' : '+0.04 μB',
    delta: atom_index % 2 === 1 ? '-9.8 μB' : '+0.04 μB',
    referenceGroup: atom_index % 2 === 1 ? '正向参考' : '近零参考',
    orientation: atom_index % 2 === 1 ? '方向相反' : '不可判定',
    magnitude: atom_index % 2 === 1 ? '幅值接近' : '近零',
    changes: atom_index % 2 === 1 ? ['方向反转'] : ['近零输出'],
    evidence: `OUTCAR 第 ${100 + atom_index} 行`,
  };
});

const analysisData = { ...legacyData, analysis: { status: 'ready' } } as MagnetizationPlotData;

function makeView(rows: MagneticView['rows'], overrides: Partial<MagneticView> = {}): MagneticView {
  return {
    statusLabel: '比较信息可用',
    summary: ['相对于 MAGMOM 输入参考，检测到局部方向变化。'],
    notes: ['仅描述最新观测投影，不确认实验磁态或基态。'],
    thresholdNotes: ['展示整理阈值：近零 0.05 μB；仅用于整理显示。'],
    totals: [
      { key: 'cell', label: 'OUTCAR 全胞磁化', value: '0.2 μB', evidence: 'OUTCAR 第 42 行；全胞直接值' },
      { key: 'projection', label: '投影报告总量', value: '0.1 μB', evidence: 'OUTCAR 表尾；投影量' },
      { key: 'sum', label: '展示行投影和', value: '部分和 0.08 μB', evidence: '仅含当前可见行' },
    ],
    rows,
    allRows,
    rawRows: [{ key: 'raw-1', axis: 'tot', atom: '1', values: 's=0.1 p=0.2 d=0.3 tot=0.6', evidence: 'OUTCAR:90（表头第 80 行）' }],
    filterOptions: {
      elements: ['Fe', 'O'],
      groups: [
        { value: 'positive', label: '正向参考' },
        { value: 'near_zero', label: '近零参考' },
      ],
      changes: [
        { value: 'reversed', label: '方向反转' },
        { value: 'near_zero', label: '近零输出' },
      ],
    },
    option: { xAxis: { type: 'category', data: rows.map((row) => row.atom_index) }, yAxis: { type: 'value' }, series: [] } as EChartsOption,
    chartAvailable: true,
    ...overrides,
  };
}

const chartProps = () => harness.latestChart as { option: EChartsOption; style: { height: number | string } };
const builder = vi.mocked(buildMagneticView);
const visibleAtomIndexes = () => within(screen.getAllByRole('table')[0])
  .getAllByRole('row').slice(1).map((row) => within(row).getAllByRole('cell')[0].textContent);

beforeEach(() => {
  harness.latestChart = null;
  harness.chartMounts = 0;
  builder.mockReset();
  builder.mockImplementation((_data, _mode, filters = {}) => {
    const visible = allRows.filter((row) =>
      (!filters.element || filters.element === row.element) &&
      (!filters.group || (filters.group === 'positive' ? row.atom_index % 2 === 1 : row.atom_index % 2 === 0)) &&
      (!filters.change || (filters.change === 'reversed' ? row.atom_index % 2 === 1 : row.atom_index % 2 === 0)));
    return makeView(visible);
  });
});

afterEach(cleanup);

describe('MagnetizationPlot UI', () => {
  it('renders helper overview, display thresholds, separate totals, raw evidence and an original-order paginated table', async () => {
    render(<MagnetizationPlot data={analysisData} calculationMode={calculations} />);

    expect(builder).toHaveBeenCalledWith(analysisData, calculations, expect.objectContaining({ compact: expect.any(Boolean) }));
    expect(screen.getByText('磁性排列对照')).toBeInTheDocument();
    expect(screen.getByText('磁性排列对照').closest('.ant-card-head-title')).toHaveStyle({ whiteSpace: 'normal' });
    expect(screen.getByText('磁性排列对照').closest('.ant-card-head-wrapper')).toHaveStyle({ flexWrap: 'wrap' });
    expect(screen.getByText('相对于 MAGMOM 输入参考，检测到局部方向变化。')).toBeInTheDocument();
    expect(screen.getByText('仅描述最新观测投影，不确认实验磁态或基态。').closest('details')).not.toHaveAttribute('open');
    expect(screen.getByText('展示整理阈值：近零 0.05 μB；仅用于整理显示。').closest('details')).not.toHaveAttribute('open');
    await userEvent.click(screen.getByText('比较依据与限制和显示整理阈值'));
    expect(screen.getByText('仅描述最新观测投影，不确认实验磁态或基态。')).toBeInTheDocument();
    expect(screen.getByText('展示整理阈值：近零 0.05 μB；仅用于整理显示。')).toBeInTheDocument();
    expect(screen.getByText('OUTCAR 全胞磁化')).toBeInTheDocument();
    expect(screen.getByText('投影报告总量')).toBeInTheDocument();
    expect(screen.getByText('展示行投影和')).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: /MAGMOM 输入参考/ })).toBeInTheDocument();
    expect(screen.getByText(/不代表实测初始磁矩/)).toBeInTheDocument();
    expect(visibleAtomIndexes()).toEqual(['1', '2', '3', '4', '5', '6', '7', '8', '9', '10']);
    expect(chartProps().option).toEqual(expect.objectContaining({ xAxis: expect.any(Object), series: expect.any(Array) }));
    expect(harness.chartMounts).toBe(1);

    await userEvent.click(screen.getByText('查看原始磁性输出证据'));
    expect(screen.getByText('s=0.1 p=0.2 d=0.3 tot=0.6')).toBeInTheDocument();
    expect(screen.getByText('OUTCAR:90（表头第 80 行）')).toBeInTheDocument();
    fireEvent.click(document.querySelector('.ant-pagination-next button') as HTMLButtonElement);
    await waitFor(() => expect(visibleAtomIndexes()).toEqual(['11', '12']));
  });

  it('passes all three filter choices to the helper and keeps matching rows in original atom order', async () => {
    const user = userEvent.setup();
    render(<MagnetizationPlot data={analysisData} calculationMode={calculations} />);

    await user.selectOptions(screen.getByRole('combobox', { name: '按元素筛选' }), 'Fe');
    await user.selectOptions(screen.getByRole('combobox', { name: '按MAGMOM输入参考组筛选' }), 'positive');
    await user.selectOptions(screen.getByRole('combobox', { name: '按变化类别筛选' }), 'reversed');

    await waitFor(() => expect(builder).toHaveBeenCalledWith(
      analysisData,
      calculations,
      expect.objectContaining({ element: 'Fe', group: 'positive', change: 'reversed' }),
    ));
    expect(visibleAtomIndexes()).toEqual(['1', '3', '5', '7', '9', '11']);
  });

  it('keeps legacy output-only responses conservative and does not label an old value as MAGMOM input', () => {
    builder.mockReturnValue(makeView([{
      ...allRows[0],
      input: '',
      referenceGroup: '',
      orientation: '',
      magnitude: '',
      changes: [],
    }], { chartAvailable: false, rawRows: [] }));
    render(<MagnetizationPlot data={legacyData} calculationMode={calculations} />);

    expect(screen.getByText('旧版诊断未提供磁性比较数据')).toBeInTheDocument();
    expect(screen.getByText(/不根据旧字段推断磁性排列/)).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: '最新观测投影' })).toBeInTheDocument();
    expect(screen.queryByRole('columnheader', { name: /MAGMOM 输入参考/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('columnheader', { name: '方向描述' })).not.toBeInTheDocument();
    expect(screen.queryByText('99')).not.toBeInTheDocument();
    expect(harness.latestChart).toBeNull();
  });

  it('shows raw evidence and helper limitations for unsupported SOC data without asking for a mode change', () => {
    builder.mockReturnValue(makeView([allRows[0]], {
      statusLabel: '标量排列不可用',
      chartAvailable: false,
      notes: ['该输出含 SOC 自旋向量分量，当前不能作标量排列比较。'],
      rawRows: [{ key: 'soc-x', axis: 'x', atom: '1', values: 'x=-0.2 y=0.1 z=0.5', evidence: 'OUTCAR:210' }],
    }));
    const soc: CalculationMode = { ...calculations, is_soc: true, magnetization_analysis_mode: 'unsupported_noncollinear_or_soc' };
    render(<MagnetizationPlot data={analysisData} calculationMode={soc} />);

    expect(screen.getByText('该输出含 SOC 自旋向量分量，当前不能作标量排列比较。')).toBeInTheDocument();
    expect(screen.getByText('当前没有可用的标量排列图；请查看上方限制说明和下方原始输出。')).toBeInTheDocument();
    expect(harness.latestChart).toBeNull();
    expect(screen.queryByRole('columnheader', { name: /MAGMOM 输入参考/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('combobox', { name: '按元素筛选' })).not.toBeInTheDocument();
    expect(screen.queryByRole('combobox', { name: '按MAGMOM输入参考组筛选' })).not.toBeInTheDocument();
    expect(screen.queryByRole('combobox', { name: '按变化类别筛选' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByText('查看原始磁性输出证据'));
    expect(screen.getByRole('columnheader', { name: '原始值' })).toBeInTheDocument();
    expect(screen.getByText('x=-0.2 y=0.1 z=0.5')).toBeInTheDocument();
    expect(screen.queryByText(/请在 collinear 模式下重新计算/)).not.toBeInTheDocument();
  });

  it('shows the no-records state and retains helper explanations when no atom rows are available', () => {
    builder.mockReturnValue(makeView([], {
      statusLabel: '证据不足',
      summary: [],
      notes: ['缺少可比较的逐原子输出记录。'],
      chartAvailable: false,
      rawRows: [],
      filterOptions: { elements: [], groups: [], changes: [] },
    }));
    render(<MagnetizationPlot data={analysisData} calculationMode={calculations} />);
    expect(screen.getByText('没有符合条件的原子记录')).toBeInTheDocument();
    expect(screen.getByText('缺少可比较的逐原子输出记录。')).toBeInTheDocument();
    expect(harness.latestChart).toBeNull();
  });

  it('opens explanatory details by default when the helper reports partial evidence', () => {
    const partialData = { ...analysisData, analysis: { status: 'partial' } } as MagnetizationPlotData;
    builder.mockReturnValue(makeView([allRows[0]], { notes: ['这份比较只有部分证据。'] }));
    render(<MagnetizationPlot data={partialData} calculationMode={calculations} />);
    expect(screen.getByText('这份比较只有部分证据。')).toBeInTheDocument();
    expect(screen.getByText('比较依据与限制和显示整理阈值').closest('details')).toHaveAttribute('open');
  });

  it('passes narrow ResizeObserver state into the helper and keeps chart/table inside the local scroll area', async () => {
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
      render(<MagnetizationPlot data={analysisData} calculationMode={calculations} />);
      expect(chartProps().style.height).toBe(340);
      width = 375;
      act(() => observerCallback?.([], {} as ResizeObserver));
      await waitFor(() => {
        expect(chartProps().style.height).toBe(420);
        expect(builder).toHaveBeenLastCalledWith(analysisData, calculations, expect.objectContaining({ compact: true }));
      });
      expect(document.querySelectorAll('[style*="overflow-x: auto"]').length).toBeGreaterThan(0);
    } finally {
      if (oldWidth) Object.defineProperty(HTMLElement.prototype, 'clientWidth', oldWidth);
      else delete (HTMLElement.prototype as unknown as { clientWidth?: number }).clientWidth;
      if (oldObserver) Object.defineProperty(globalThis, 'ResizeObserver', oldObserver);
      else delete (globalThis as unknown as { ResizeObserver?: unknown }).ResizeObserver;
    }
  });
});
