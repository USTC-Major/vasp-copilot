import { fireEvent, render, screen } from '@testing-library/react';
import { vi } from 'vitest';
import SecretInput from './SecretInput';

it('keeps saved secrets write-only and emits replacement and clear actions', () => {
  const change = vi.fn();
  const clear = vi.fn();
  render(<SecretInput hasSecret value="" onChange={change} onClear={clear} placeholder="输入新值以整体替换" />);
  const input = screen.getByLabelText('输入新的密钥以整体替换');
  expect(input).toHaveAttribute('type', 'password');
  expect(input).toHaveValue('');
  expect(screen.getByText('已保存')).toBeInTheDocument();
  fireEvent.change(input, { target: { value: 'synthetic-replacement' } });
  expect(change).toHaveBeenCalledWith('synthetic-replacement');
  fireEvent.click(screen.getByRole('button', { name: /清\s*除/ }));
  expect(clear).toHaveBeenCalledTimes(1);
});

it('keeps environment credentials uneditable and explains where to manage them', () => {
  const change = vi.fn();
  const clear = vi.fn();
  render(<SecretInput hasSecret source="environment" manageable={false} value="" onChange={change} onClear={clear} placeholder="输入新值以整体替换" />);
  expect(screen.getByLabelText('输入新的密钥以整体替换')).toBeDisabled();
  expect(screen.getByLabelText('输入新的密钥以整体替换')).toHaveValue('');
  expect(screen.getByRole('button', { name: /清\s*除/ })).toBeDisabled();
  expect(screen.getByText('环境变量')).toBeInTheDocument();
  expect(screen.getByText('由环境变量管理；请在运行环境中替换或清除。')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: /清\s*除/ }));
  expect(clear).not.toHaveBeenCalled();
  expect(change).not.toHaveBeenCalled();
});

it('keeps an unconfigured secret writable and its clear action disabled by default', () => {
  render(<SecretInput hasSecret={false} value="" onChange={vi.fn()} onClear={vi.fn()} placeholder="填写后保存" />);
  expect(screen.getByLabelText('输入新的密钥以整体替换')).toBeEnabled();
  expect(screen.getByText('未配置')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /清\s*除/ })).toBeDisabled();
});
