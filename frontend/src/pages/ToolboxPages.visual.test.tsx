import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { HttpResponse, http } from 'msw';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import App from '../App';
import ToolboxProjectsPage from './ToolboxProjectsPage';
import { server } from '../mocks/server';

const base = '/api/v1/toolbox';
const projectName = '合成项目：长名称与目录证据'.repeat(5);
const taskTitle = '合成任务：已有记录的长标题'.repeat(5);
const longPath = `/synthetic/${'long-directory/'.repeat(18)}`;
const projects = [{ id: 'project-one', name: projectName, description: '合成项目说明'.repeat(15) }, { id: 'project-other', name: '另一个合成项目' }];
const tasks = [{ id: 'task-two', project_id: 'project-one', title: taskTitle, goal: '保留原始目标'.repeat(12), local_workspace: longPath, hpc_workspace: '/synthetic/remote', status: 'unknown', updated_at: '2026-10-07T00:00:00Z' }];
let client: QueryClient;
let router: ReturnType<typeof createMemoryRouter>;

function setup() {
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  // Keep the real shell/theme and page; jsdom cannot finish CSS modal transitions.
  router = createMemoryRouter([{ element: <App />, children: [{ path: '/toolbox/projects', element: <ConfigProvider locale={zhCN} theme={{ token: { motion: false } }}><ToolboxProjectsPage /></ConfigProvider> }] }], { initialEntries: ['/toolbox/projects'] });
  render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>);
}
function lists() {
  server.use(
    http.get(`${base}/projects`, () => HttpResponse.json({ mode: 'toolbox', projects })),
    http.get(`${base}/projects/project-one/tasks`, () => HttpResponse.json({ mode: 'toolbox', tasks })),
    http.get(`${base}/projects/project-other/tasks`, () => HttpResponse.json({ mode: 'toolbox', tasks: [] })),
  );
}
beforeEach(() => { localStorage.clear(); lists(); });
afterEach(() => { cleanup(); router?.dispose(); client?.clear(); });

describe('scientific toolbox project presentation', () => {
  it('keeps long records and keyboard project selection, with themed creation and directory dialogs', async () => {
    setup();
    const user = userEvent.setup();
    const projectRow = (await screen.findByText(projectName)).closest('.toolbox-project-row') as HTMLElement;
    const project = within(projectRow).getByRole('button', { name: projectName });
    const otherRow = screen.getByText('另一个合成项目').closest('.toolbox-project-row') as HTMLElement;
    const other = within(otherRow).getByRole('button', { name: '另一个合成项目' });
    other.focus();
    await user.keyboard('{Enter}');
    expect(other).toHaveAttribute('aria-pressed', 'true');
    project.focus();
    expect(project).toHaveFocus();
    await user.keyboard('{Enter}');
    expect(project).toHaveAttribute('aria-pressed', 'true');
    expect(await screen.findByText(taskTitle)).toBeInTheDocument();
    expect(screen.getByText(longPath)).toBeInTheDocument();
    expect(screen.getByText('unknown')).toBeInTheDocument();

    const page = within(document.querySelector('.toolbox-projects-page') as HTMLElement);
    fireEvent.click(page.getByRole('button', { name: /新建项目/ }));
    const projectDialog = await screen.findByRole('dialog', { name: '新建 Toolbox 项目' });
    expect(projectDialog).toHaveClass('toolbox-modal');
    expect(within(projectDialog).getByLabelText('项目名称')).toBeInTheDocument();
    fireEvent.click(within(projectDialog).getByRole('button', { name: /取\s*消/ }));
    await waitFor(() => expect(projectDialog).not.toBeVisible());

    fireEvent.click(page.getByRole('button', { name: /新建任务/ }));
    // Ant assigns all modal titles the same test-id; stacked dialogs need their visible title.
    const taskDialog = (await screen.findByText('新建计算任务')).closest('[role="dialog"]') as HTMLElement;
    await waitFor(() => expect(taskDialog).toBeVisible());
    expect(taskDialog).toHaveClass('toolbox-modal');
    const localControls = within(within(taskDialog).getByLabelText('本地工作区（必填）').closest('.toolbox-path-controls') as HTMLElement);
    fireEvent.click(localControls.getByRole('button', { name: /浏\s*览/ }));
    const directory = (await screen.findByText('选择本地工作区目录')).closest('[role="dialog"]') as HTMLElement;
    await waitFor(() => expect(directory).toBeVisible());
    expect(directory).toHaveClass('toolbox-directory-picker');
    expect(within(directory).getByRole('button', { name: /选择当前文件夹/ })).toBeDisabled();
  });

  it('preserves both record-deletion confirmations and sends no deletion before explicit approval', async () => {
    const deleted: string[] = [];
    server.use(
      http.delete(`${base}/projects/:projectId/tasks/:taskId`, ({ params }) => {
        deleted.push(`task:${params.projectId}/${params.taskId}`);
        return HttpResponse.json({ mode: 'toolbox', deleted: true });
      }),
      http.delete(`${base}/projects/:projectId`, ({ params }) => {
        deleted.push(`project:${params.projectId}`);
        return HttpResponse.json({ mode: 'toolbox', deleted: true });
      }),
    );
    setup();
    const task = await screen.findByText(taskTitle);
    fireEvent.click(within(task.closest('.ant-list-item') as HTMLElement).getByRole('button', { name: '删除记录' }));
    const taskDialog = await screen.findByRole('dialog', { name: '删除任务记录？' });
    expect(within(taskDialog).getByText('只删除任务记录，不删除计算目录，也不会停止或取消远端作业。')).toBeInTheDocument();
    expect(deleted).toEqual([]);
    fireEvent.click(within(taskDialog).getByRole('button', { name: '删除记录' }));
    await waitFor(() => expect(deleted).toEqual(['task:project-one/task-two']));
    await waitFor(() => expect(taskDialog).not.toBeInTheDocument());

    const projectRow = screen.getByText(projectName).closest('.toolbox-project-row') as HTMLElement;
    fireEvent.click(within(projectRow).getByRole('button', { name: '删除记录' }));
    const projectDialog = await screen.findByRole('dialog', { name: '删除项目记录？' });
    expect(within(projectDialog).getByText('只删除 Toolbox 记录，不删除用户文件，也不会取消远端作业。')).toBeInTheDocument();
    expect(deleted).toHaveLength(1);
    fireEvent.click(within(projectDialog).getByRole('button', { name: /取\s*消/ }));
    expect(deleted).toEqual(['task:project-one/task-two']);
  });

  it('reports a failed task-list read without claiming that the project is empty, then retries the same read', async () => {
    let failed = true;
    let reads = 0;
    server.use(http.get(`${base}/projects/project-one/tasks`, () => {
      reads += 1;
      return failed ? HttpResponse.json({ error: { message: '合成读取失败' } }, { status: 503 })
        : HttpResponse.json({ mode: 'toolbox', tasks });
    }));
    setup();
    const error = await screen.findByText('无法读取该项目的计算任务');
    expect(screen.queryByText('该项目还没有计算任务')).not.toBeInTheDocument();
    failed = false;
    fireEvent.click(within(error.closest('.ant-alert') as HTMLElement).getByRole('button', { name: /重试读取/ }));
    expect(await screen.findByText(taskTitle)).toBeInTheDocument();
    expect(reads).toBe(2);
  });

  it('keeps a failed project read distinct from a genuinely empty workspace', async () => {
    server.use(http.get(`${base}/projects`, () => HttpResponse.json({ error: { message: '合成项目读取失败' } }, { status: 503 })));
    setup();
    expect(await screen.findByText('Toolbox 服务不可用')).toBeInTheDocument();
    expect(screen.getByText('项目列表暂不可用')).toBeInTheDocument();
    expect(screen.queryByText('还没有 Toolbox 项目')).not.toBeInTheDocument();
  });
});
