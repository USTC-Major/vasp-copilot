import { matchPath } from 'react-router-dom';

const matches = (pattern: string, path: string) => matchPath({ path: pattern, end: true }, path) !== null;

/** Uses the router's case/trailing-slash rules without modifying record IDs. */
export function workspaceLocation(path: string): { current: string; group: string; title: string } {
  if (matches('/', path)) return { current: '/', group: '工作区', title: '首页' };
  if (matches('/ai/settings', path)) return { current: '/ai/settings', group: '智能模式', title: '智能设置' };
  if (matches('/toolbox/settings', path)) return { current: '/toolbox/settings', group: '工具箱', title: '执行设置' };
  if (matches('/toolbox/potcar', path)) return { current: '/toolbox/settings', group: '工具箱', title: '本地赝势库' };
  if (matches('/toolbox/potcar/assemble', path)) return { current: '/toolbox/settings', group: '工具箱', title: '拼接 POTCAR' };
  if (matches('/workflow', path)) return { current: '/workflow', group: '工具箱', title: '生成工作流' };
  if (matches('/workflow/history/:id', path)) return { current: '/workflow', group: '生成工作流', title: '工作流详情' };
  if (matches('/diagnosis/upload', path)) return { current: '/diagnosis/upload', group: '工具箱', title: '诊断计算' };
  if (matches('/diagnosis/:id', path)) return { current: '/diagnosis/upload', group: '诊断计算', title: '诊断结果' };
  if (matches('/toolbox', path) || matches('/toolbox/projects', path)) return { current: '/toolbox/projects', group: '工具箱', title: '计算任务' };
  if (matches('/toolbox/projects/:projectId/tasks/:taskId', path)) return { current: '/toolbox/projects', group: '计算任务', title: '任务详情' };
  if (matches('/ai', path)) return { current: '/ai', group: '工作区', title: '智能模式' };
  if (matches('/ai/projects/:projectId', path) || matches('/ai/projects/:projectId/progress/:taskId', path)) return { current: '/ai', group: '智能模式', title: '项目详情' };
  if (matches('/hpc/deploy', path)) return { current: '/hpc/deploy', group: '工具箱', title: '远程部署（离线演示）' };
  if (matches('/hpc/jobs/:id', path)) return { current: '/hpc/deploy', group: '远程部署', title: '远程作业' };
  return { current: '', group: '工作区', title: '页面' };
}

export const hasScientificContent = (path: string) => matches('/', path) || matches('/workflow', path)
  || matches('/diagnosis/upload', path) || matches('/diagnosis/:id', path)
  || matches('/toolbox/projects', path) || matches('/toolbox/projects/:projectId/tasks/:taskId', path)
  || matches('/toolbox/settings', path) || matches('/toolbox/potcar', path) || matches('/toolbox/potcar/assemble', path) || matches('/ai/settings', path) || matches('/ai', path)
  || matches('/ai/projects/:projectId', path);
