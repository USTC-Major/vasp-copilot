import { useState } from 'react';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import type { PPView } from '../../api/postprocessing';
import ProjectionSelector from './ProjectionSelector';
import { selectedAtoms } from './viewState';

const atoms: [number, string][] = [[1, 'Fe'], [2, 'Fe'], [3, 'O']];
const initial: PPView = { version: 'pp.view.v2', reference: 'fermi', reference_ev: 0, mirror_down: false, atoms: [], elements: [], orbitals: ['s', 'px', 'py', 'dxy'], projection_grouping: 'element', energy_min_ev: -5, energy_max_ev: 3, band_start: 1, band_end: 2 };
function Harness({ many = false }: { many?: boolean }) {
  const [view, setView] = useState(initial);
  const source = many ? Array.from({ length: 20 }, (_, i) => [i + 1, i % 2 ? 'O' : 'Fe'] as [number, string]) : atoms;
  return <><ProjectionSelector view={view} atoms={source} orbitals={initial.orbitals} onChange={patch => setView(old => ({ ...old, ...patch }))} /><output data-testid="selection">{JSON.stringify({ ...view, selected: selectedAtoms(view, source) })}</output></>;
}
afterEach(cleanup);

it('selects whole elements and maintains precise half-selected parents without resurrecting empty selections', () => {
  render(<Harness />);
  fireEvent.click(screen.getByLabelText('Fe 全部原子'));
  fireEvent.click(screen.getByLabelText('O 全部原子'));
  expect(screen.getByTestId('selection')).toHaveTextContent('"selected":[1,2,3]');
  expect(screen.getByTestId('selection')).toHaveTextContent('"atoms":[]');
  fireEvent.click(screen.getByLabelText('Fe #1'));
  expect(screen.getByLabelText('Fe 全部原子').closest('.ant-checkbox')).toHaveClass('ant-checkbox-indeterminate');
  expect(screen.getByTestId('selection')).toHaveTextContent('"atoms":[2,3]');
  fireEvent.click(screen.getByLabelText('Fe #2'));
  expect(screen.getByTestId('selection')).toHaveTextContent('"elements":["O"]');
  fireEvent.click(screen.getByLabelText('O 全部原子'));
  expect(screen.getByTestId('selection')).toHaveTextContent('"elements":[]');
  expect(screen.getByTestId('selection')).toHaveTextContent('"selected":[]');
});

it('offers only source orbital groups/components and accurately represents no orbitals', () => {
  render(<Harness />);
  expect(screen.queryByLabelText('f 全部轨道')).not.toBeInTheDocument();
  expect(screen.queryByText('t2g')).not.toBeInTheDocument();
  fireEvent.click(screen.getByLabelText('py 轨道分量'));
  expect(screen.getByLabelText('p 全部轨道').closest('.ant-checkbox')).toHaveClass('ant-checkbox-indeterminate');
  fireEvent.click(screen.getByLabelText('p 全部轨道'));
  fireEvent.click(screen.getByLabelText('p 全部轨道'));
  fireEvent.click(screen.getByLabelText('s 全部轨道'));
  fireEvent.click(screen.getByLabelText('d 全部轨道'));
  expect(screen.getByTestId('selection')).toHaveTextContent('"orbitals":[]');
});

it('searches many atoms without changing hidden selections', () => {
  render(<Harness many />);
  fireEvent.click(screen.getByLabelText('Fe 全部原子'));
  fireEvent.change(screen.getByLabelText('搜索原子'), { target: { value: 'O #20' } });
  expect(screen.queryByLabelText('Fe #1')).not.toBeInTheDocument();
  fireEvent.click(screen.getByLabelText('O #20'));
  expect(screen.getByTestId('selection')).toHaveTextContent('"selected":[1,3,5,7,9,11,13,15,17,19,20]');
});
