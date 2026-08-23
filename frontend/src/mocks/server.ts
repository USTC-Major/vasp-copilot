// ============================================================
// MSW Node Server — 仅供 Vitest（jsdom）测试使用
//
// 与浏览器运行时的 setupWorker（main.tsx 的 ?mock=1 模式）完全隔离，
// 两套实例不共享状态；复用同一份 handlers 定义。
//
// 注意：vitest 的 setupFiles 与测试文件可能把本模块解析为两个独立模块
// 实例（模块图不一致）。若直接导出 setupServer() 的结果，setup 侧
// listen() 的实例与测试侧 server.use() 的实例会不是同一个，导致
// 运行时覆盖 handler 全部失效。因此将实例挂载到 globalThis 做单例，
// 确保所有导入方共享同一实例。
// ============================================================

import { setupServer } from 'msw/node';
import { handlers } from './handlers';

type Server = ReturnType<typeof setupServer>;

const GLOBAL_KEY = '__msw_node_server__';

const existing = (globalThis as Record<string, unknown>)[GLOBAL_KEY] as Server | undefined;

const instance: Server = existing ?? setupServer(...handlers);
if (!existing) {
  (globalThis as Record<string, unknown>)[GLOBAL_KEY] = instance;
}

export const server = instance;
