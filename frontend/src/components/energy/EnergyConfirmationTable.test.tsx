import { cleanup, render, screen, within } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { afterEach, expect, it, vi } from 'vitest';
import EnergyConfirmationTable from './EnergyConfirmationTable';
import { basisLabel, draftFromCollection } from './energyDraft';
import { energyFixture } from './energyTestFixtures';

afterEach(cleanup);
it('renders long Chinese CSV notes as unverified plain text and preserves the declared value source without inheriting review', () => {
  const collection = energyFixture(false); const sample = collection.samples[2];
  const longNote = '中文长来源说明：这是软件测试合成数据。'.repeat(120) + '\n<img src=x onerror=alert(1)>';
  sample.source = { kind: 'csv', original_name: '中文回导.csv', reference_note: '原始参考备注独立保留，未拼接附注。', csv_metadata: { value_source: 'effective', source_notes: [longNote], override_notes: ['人工修订依据：合成值 -113 eV，原值 -112 eV。'], original_energies: [{ energy_basis: 'sigma_to_zero_ev', energy_ev: -112 }] } };
  collection.samples = [sample]; collection.groups = [];
  const ui = () => <ConfigProvider><EnergyConfirmationTable collection={collection} draft={draftFromCollection(collection)} disabled={false} selectedIds={[]} onSelection={vi.fn()} onDelete={vi.fn()} onClear={vi.fn()} onRow={vi.fn()} onIncludeAll={vi.fn()} onAcceptRisks={vi.fn()} /></ConfigProvider>;
  const view = render(ui());
  const details = screen.getByText('原值与来源明细').closest('details')!;
  expect(within(details).getByText('原始参考备注独立保留，未拼接附注。')).toBeInTheDocument();
  expect(details.textContent).toContain(longNote);
  expect(details.querySelector('img')).toBeNull();
  expect(within(details).getByText('人工修订依据：合成值 -113 eV，原值 -112 eV。')).toBeInTheDocument();
  expect(within(details).getByText(`${basisLabel('sigma_to_zero_ev')}：-112 eV`)).toBeInTheDocument();
  expect(within(details).getByText('有效值（CSV 声明，可能包含人工修订）')).toBeInTheDocument();
  expect(within(details).getByText(/未经来源验证；不代表已核对或已接受风险/)).toBeInTheDocument();
  expect(screen.getByRole('checkbox', { name: '人工确认 es_target' })).not.toBeChecked();
  expect(screen.getByRole('checkbox', { name: '接受风险 es_target' })).not.toBeChecked();
  sample.source.csv_metadata!.value_source = 'original'; view.rerender(ui());
  expect(within(details).getByText('原始值（CSV 声明）')).toBeInTheDocument();
});
