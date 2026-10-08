import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { vi } from 'vitest';
import GeneratedFilesPreview from './GeneratedFilesPreview';

vi.mock('../common/CodePreview', () => ({ default: ({ fileName }: { fileName: string }) => <div data-testid="ordinary-preview">{fileName}</div> }));
afterEach(cleanup);
it('POTCAR仅列元数据，即使错误预览标志为true也不能进入普通正文预览', () => {
  render(<ConfigProvider theme={{ token: { motion: false } }}><GeneratedFilesPreview fileTree={{ name: 'synthetic-workflow', relative_path: '', type: 'directory', children: [{ name: 'POTCAR', relative_path: 'static/POTCAR', type: 'file', size_bytes: 4, sha256: 'synthetic-sha', file_id: 'must-not-open', preview_available: true }, { name: 'INCAR', relative_path: 'static/INCAR', type: 'file', file_id: 'synthetic-incar', preview_available: true }] }} /></ConfigProvider>);
  expect(screen.getByText('仅随工作流下载')).toBeInTheDocument();
  fireEvent.click(screen.getByText('POTCAR'));
  expect(screen.queryByTestId('ordinary-preview')).not.toBeInTheDocument();
  fireEvent.click(screen.getByText('INCAR'));
  expect(screen.getByTestId('ordinary-preview')).toHaveTextContent('INCAR');
});
