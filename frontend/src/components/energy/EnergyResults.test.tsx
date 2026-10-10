import { cleanup, render, screen } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { afterEach, expect, it } from 'vitest';
import EnergyResults from './EnergyResults';
import { energyFixture } from './energyTestFixtures';

afterEach(cleanup);
it('keeps warning inheritance visible as one collapsed, deduplicated summary', () => {
  const collection = energyFixture(true);
  collection.result!.warnings = ['同一条未完成提示'];
  collection.result!.groups[0].warnings = ['同一条未完成提示', '方法未知'];
  collection.result!.groups[0].rows[0].warnings = ['同一条未完成提示'];
  render(<ConfigProvider><EnergyResults collection={collection} valid /></ConfigProvider>);
  expect(screen.getByText('包含 2 项状态与可比性提示')).toBeInTheDocument();
  expect(screen.getAllByText('同一条未完成提示')).toHaveLength(1);
  expect(document.querySelectorAll('.ant-alert-warning')).toHaveLength(1);
  expect(screen.getByText('同一条未完成提示').closest('details')).not.toHaveAttribute('open');
  expect(screen.getByText('含状态／可比性提示')).toBeInTheDocument();
  expect(screen.getByText('-2 eV', { selector: 'strong' })).toBeVisible();
});
it('withholds stale results and export while an input draft has changed', () => {
  render(<ConfigProvider><EnergyResults collection={energyFixture(true)} valid={false} expired /></ConfigProvider>);
  expect(screen.queryByText('-2 eV', { selector: 'strong' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '导出能量 CSV' })).toBeDisabled();
  expect(screen.getByRole('button', { name: '导出能量 JSON' })).toBeDisabled();
  expect(screen.getByText('旧结果已过期。重新确认锁定并计算后可查看及导出当前结果。')).toBeInTheDocument();
});
