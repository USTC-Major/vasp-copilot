import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { afterEach, expect, it, vi } from 'vitest';
import { energyApi } from '../../api/energy';
import EnergyImports from './EnergyImports';
import * as draftHelpers from './energyDraft';
import { energyCsvFixture, energyFixture } from './energyTestFixtures';

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const file = () => new File(['合成软件测试 CSV，不是科研数据'], '中文样本.csv');
function props() {
  return { collection: energyFixture(), disabled: false, draftDirty: false, onFiles: vi.fn(), onManual: vi.fn(),
    onCsvPreview: vi.fn().mockResolvedValue({ preview: energyCsvFixture(), revision: 4 }), onCsv: vi.fn().mockResolvedValue(true) };
}
function mount(input: ReturnType<typeof props>) { return render(<ConfigProvider theme={{ token: { motion: false } }}><EnergyImports {...input} /></ConfigProvider>); }
async function chooseBasis(value: 'file' | 'sigma_to_zero_ev' | 'without_entropy_ev') {
  fireEvent.mouseDown(screen.getByRole('combobox', { name: 'CSV 能量口径' }));
  fireEvent.click(await screen.findByText(value === 'file' ? '按文件声明' : draftHelpers.basisLabel(value), { selector: '.ant-select-item-option-content' }));
}
function selectCsv(input: File) { fireEvent.change(screen.getByLabelText('选择能量 CSV'), { target: { files: [input] } }); }

it('downloads the empty template with the chosen basis and explains readable composition without scientific examples', async () => {
  const blob = new Blob(['name,composition,energy_basis,energy_ev,unit,relative_path,reference_note\r\n']);
  vi.spyOn(energyApi, 'template').mockResolvedValue(blob);
  const save = vi.spyOn(draftHelpers, 'saveEnergyBlob').mockImplementation(() => {});
  mount(props()); fireEvent.click(screen.getByRole('button', { name: 'CSV' }));
  expect(screen.getByText(/元素计数例如 Pt:4 C:1 O:1。模板不含科研数值/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '下载 CSV 模板' }));
  await waitFor(() => expect(save).toHaveBeenCalledWith(blob, 'energy-samples-template.csv'));
  expect(energyApi.template).toHaveBeenLastCalledWith(undefined);
  await chooseBasis('without_entropy_ev');
  fireEvent.click(screen.getByRole('button', { name: '下载 CSV 模板' }));
  await waitFor(() => expect(energyApi.template).toHaveBeenLastCalledWith('without_entropy_ev'));
});

it('shows server row numbers, missing-field and conflicting-basis errors and blocks the entire CSV import', async () => {
  const input = props(); const preview = energyCsvFixture();
  preview.can_import = false; preview.valid_count = 1;
  preview.rows[0] = { ...preview.rows[0], energy_basis: null, composition: null, issues: [{ code: 'COMPOSITION_REQUIRED', message: '第2行缺少元素计数' }, { code: 'ENERGY_BASIS_REQUIRED', message: '第2行缺少能量口径' }] };
  preview.rows[1].issues = [{ code: 'ENERGY_BASIS_CONFLICT', message: '第3行文件口径与选定口径冲突，不会覆盖' }];
  input.onCsvPreview.mockResolvedValue({ preview, revision: 4 }); mount(input);
  fireEvent.click(screen.getByRole('button', { name: 'CSV' })); selectCsv(file());
  fireEvent.click(screen.getByRole('button', { name: '预览 CSV' }));
  const section = await screen.findByRole('region', { name: 'CSV 导入预览' });
  expect(within(section).getByText('2', { selector: 'td' })).toBeInTheDocument();
  expect(within(section).getByText('合成表面')).toBeInTheDocument();
  expect(within(section).getByText('第2行缺少元素计数')).toBeInTheDocument();
  expect(within(section).getByText('第2行缺少能量口径')).toBeInTheDocument();
  expect(within(section).getByText('第3行文件口径与选定口径冲突，不会覆盖')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '确认导入预览中的 CSV' })).toBeDisabled();
  expect(input.onCsv).not.toHaveBeenCalled();
});

it('requires a fresh preview after file, basis, dirty draft or saved revision changes and forwards the valid snapshot', async () => {
  const input = props(); const view = mount(input); const first = file();
  fireEvent.click(screen.getByRole('button', { name: 'CSV' })); selectCsv(first);
  const preview = async () => { fireEvent.click(screen.getByRole('button', { name: /预览 CSV$/ })); await waitFor(() => expect(screen.getByRole('button', { name: '确认导入预览中的 CSV' })).toBeEnabled()); };
  await preview(); expect(input.onCsvPreview).toHaveBeenLastCalledWith(first, undefined);
  await chooseBasis('without_entropy_ev');
  expect(screen.getByRole('button', { name: '确认导入预览中的 CSV' })).toBeDisabled();
  await chooseBasis('file');
  expect(screen.getByRole('button', { name: '确认导入预览中的 CSV' })).toBeDisabled();
  await preview(); const second = file(); selectCsv(second);
  expect(screen.getByRole('button', { name: '确认导入预览中的 CSV' })).toBeDisabled();
  await preview(); view.rerender(<ConfigProvider theme={{ token: { motion: false } }}><EnergyImports {...input} draftDirty /></ConfigProvider>);
  expect(screen.getByRole('button', { name: '保存草稿并预览 CSV' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '确认导入预览中的 CSV' })).toBeDisabled();
  const next = { ...input, collection: { ...input.collection, revision: 5 } };
  next.onCsvPreview.mockResolvedValue({ preview: energyCsvFixture(), revision: 5 });
  view.rerender(<ConfigProvider theme={{ token: { motion: false } }}><EnergyImports {...next} /></ConfigProvider>);
  expect(screen.getByText('CSV 预览已失效，请重新预览。')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '确认导入预览中的 CSV' })).toBeDisabled();
  await chooseBasis('without_entropy_ev'); await preview();
  fireEvent.click(screen.getByRole('button', { name: '确认导入预览中的 CSV' }));
  await waitFor(() => expect(input.onCsv).toHaveBeenCalledWith(second, 'without_entropy_ev', 5));
  await waitFor(() => expect(screen.queryByRole('region', { name: 'CSV 导入预览' })).not.toBeInTheDocument());
});
