// ============================================================
// AI 演示数据后端（MSW 交互层的有状态存储）
// M13 对接真实 /ai/v1 后端前，由这里提供项目/任务/消息/上下文演示数据。
// 设置类接口（/settings*）走真实后端逻辑，不在本演示存储中伪造。
// ============================================================

import type {
  AiProject, AiTask, AiMessage, AiJobRecord, AiWaitQueueEntry, AiContextSummary,
} from '../types/ai';

const now = (offsetMs = 0) => new Date(Date.now() + offsetMs).toISOString();

function seedJob(): AiJobRecord {
  return {
    job_id: 'ai_job_001',
    goal_text: '对 Fe2O3 结构做「relax → static → dos」三步计算',
    status: 'submitted',
    degraded: false,
    coverage: { start: 'understand', end: 'submit_monitor' },
    current_step: 'submit_monitor',
    timeline: [
      { status: 'planned', note: 'AI 已规划', at: Date.now() - 3600_000 },
      { status: 'generated', note: '输入包已生成', at: Date.now() - 2000_000 },
      { status: 'submitted', note: 'sbatch 已提交', at: Date.now() - 1000_000 },
    ],
  };
}

function seedTasks(job: AiJobRecord): AiTask[] {
  return [
    {
      id: 'tsk_001',
      project_id: 'prj_001',
      title: '结构优化 + 静态 + DOS',
      goal: '对 Fe2O3 结构做 relax → static → dos 计算并出报告',
      status: 'submitted',
      local_workspace: 'D:\\\\calc\\\\fe2o3_relax',
      hpc_workspace: '/lustre/hpc_home/u01/fe2o3_relax',
      job,
      updated_at: now(-1000_000),
    },
    {
      id: 'tsk_002',
      project_id: 'prj_001',
      title: '带结构计算的能带',
      goal: '基于优化后结构做能带计算',
      status: 'planned',
      local_workspace: 'D:\\\\calc\\\\fe2o3_band',
      updated_at: now(-600_000),
    },
  ];
}

export function emptyContext() {
  return { ratio: 0, used: 0, capacity: 65536 };
}

export class AiDemoBackend {
  projects: AiProject[] = [];
  tasks: AiTask[] = [];
  messages: Record<string, AiMessage[]> = {};
  context: { ratio: number; used: number; capacity: number };

  constructor() {
    this.context = { ratio: 0.38, used: 24904, capacity: 65536 };
    this.seed();
  }

  private seed() {
    this.projects = [
      {
        id: 'prj_001',
        name: 'Fe2O3 优化工程',
        description: '演示项目：从结构优化到能带计算的完整流程',
        created_at: now(-7200_000),
        updated_at: now(-1800_000),
        job_count: 2,
        context_ratio: 0.38,
      },
    ];
    const job = seedJob();
    this.tasks = seedTasks(job);
    this.messages['prj_001:tsk_001'] = [
      { role: 'assistant', content: '你好，我是你的 VASP 计算助手。每个计算任务都是一段独立对话。请描述计算需求（如"优化 Fe2O3 结构并求 DOS"）。', at: now(-3800_000) },
      { role: 'user', content: '请对 Fe2O3 结构做 relax → static → dos 三步计算', at: now(-3600_000) },
      { role: 'assistant', content: '已生成计划并提交作业，等待计算完成。', at: now(-2500_000) },
    ];
  }

  reset() {
    this.projects = [];
    this.tasks = [];
    this.messages = {};
    this.seed();
  }

  listProjects(): AiProject[] {
    return [...this.projects].sort((a, b) => b.created_at.localeCompare(a.created_at));
  }

  getProject(id: string): AiProject | undefined {
    return this.projects.find((p) => p.id === id);
  }

  createProject(name: string, description?: string): AiProject {
    const project: AiProject = {
      id: `prj_${Math.random().toString(36).slice(2, 8)}`,
      name: name.trim().slice(0, 80) || '未命名项目',
      description,
      created_at: now(),
      job_count: 0,
      context_ratio: 0,
    };
    this.projects.push(project);
    return project;
  }

  deleteProject(id: string): boolean {
    const before = this.projects.length;
    this.projects = this.projects.filter((p) => p.id !== id);
    this.tasks = this.tasks.filter((t) => t.project_id !== id);
    return this.projects.length < before;
  }

  listTasks(projectId: string): AiTask[] {
    return this.tasks.filter((t) => t.project_id === projectId)
      .sort((a, b) => b.updated_at.localeCompare(a.updated_at));
  }

  getTask(projectId: string, taskId: string): AiTask | undefined {
    return this.tasks.find((t) => t.project_id === projectId && t.id === taskId);
  }

  createTask(projectId: string, title: string, goal: string, workspaces?: { local_workspace?: string; hpc_workspace?: string }): AiTask {
    const task: AiTask = {
      id: `tsk_${Math.random().toString(36).slice(2, 8)}`,
      project_id: projectId,
      title: title.trim().slice(0, 80) || goal.trim().slice(0, 60) || '新计算任务',
      goal,
      local_workspace: workspaces?.local_workspace || undefined,
      hpc_workspace: workspaces?.hpc_workspace || undefined,
      status: 'idle',
      updated_at: now(),
    };
    this.tasks.push(task);
    this.messages[`${projectId}:${task.id}`] = [];
    const project = this.getProject(projectId);
    if (project) {
      project.job_count = this.listTasks(projectId).length;
    }
    return task;
  }

  updateTask(projectId: string, taskId: string, patch: { title?: string; goal?: string; local_workspace?: string; hpc_workspace?: string }): AiTask | undefined {
    const task = this.getTask(projectId, taskId);
    if (!task) return undefined;
    if (patch.title !== undefined) task.title = patch.title.trim().slice(0, 80);
    if (patch.goal !== undefined) task.goal = patch.goal.trim();
    if (patch.local_workspace !== undefined) task.local_workspace = patch.local_workspace.trim() || undefined;
    if (patch.hpc_workspace !== undefined) task.hpc_workspace = patch.hpc_workspace.trim() || undefined;
    task.updated_at = now();
    return task;
  }

  deleteTask(projectId: string, taskId: string): boolean {
    const before = this.tasks.length;
    this.tasks = this.tasks.filter((t) => !(t.project_id === projectId && t.id === taskId));
    delete this.messages[`${projectId}:${taskId}`];
    const project = this.getProject(projectId);
    if (project) {
      project.job_count = this.listTasks(projectId).length;
    }
    return this.tasks.length < before;
  }

  getMessages(projectId: string, taskId: string): AiMessage[] {
    return this.messages[`${projectId}:${taskId}`] ?? [];
  }

  appendMessage(projectId: string, taskId: string, message: AiMessage): void {
    const key = `${projectId}:${taskId}`;
    this.messages[key] = [...(this.messages[key] ?? []), message];
  }

  sendMessage(projectId: string, taskId: string, content: string): string {
    this.appendMessage(projectId, taskId, { role: 'user', content, at: now() });
    const task = this.getTask(projectId, taskId);
    const isCompute = /(计算|优化|结构|静态|dos|能带|relax|跑|作业|提交|生成)/i.test(content) && !task?.job;
    if (isCompute && task) {
      task.status = 'planned';
      task.job = seedJob();
      task.job.status = 'planned';
      task.job.timeline = [{ status: 'planned', note: 'AI 已规划（演示）', at: Date.now() }];
      task.updated_at = now();
      this.appendMessage(projectId, taskId, {
        role: 'assistant',
        content: `已生成计算计划（演示数据）。可在「查看进度」中看到任务进度与作业 ai_job_001。`,
        at: now(),
      });
      return `已规划作业 ai_job_001（演示）。`;
    }
    const reply = '收到。这是演示环境对话回复；接入真实 LLM 后我会生成 VASP 输入文件并推进作业状态。';
    this.appendMessage(projectId, taskId, { role: 'assistant', content: reply, at: now() });
    return reply;
  }

  taskContext(projectId: string, taskId: string): AiContextSummary {
    const msgs = this.getMessages(projectId, taskId);
    let used = 0;
    for (const m of msgs) {
      const text = (m.content || '') + (m.thinking ? '[thinking] ' + m.thinking : '');
      used += Math.max(1, Math.round(text.length / 4));
    }
    const capacity = 65536;
    return { ratio: Math.min(1, used / capacity), used, capacity };
  }

  waiting: AiWaitQueueEntry[] = [];

  getWaitQueue(): { waiting: AiWaitQueueEntry[]; count: number } {
    return { waiting: this.waiting, count: this.waiting.length };
  }

  enqueue(reason: string, taskTitle?: string): void {
    this.waiting.push({ task_title: taskTitle, queued_at: now(), reason });
  }

  getProjectSettings(projectId: string) {
    return { project_id: projectId, accuracy: [] };
  }
}

export const aiDemo = new AiDemoBackend();

