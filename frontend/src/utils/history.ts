import type { HistoryRecord } from '../types/history';

export const mergeRecentRecords = (
  groups: HistoryRecord[][],
  limit = 10,
): HistoryRecord[] => {
  const unique = new Map<string, HistoryRecord>();
  for (const item of groups.flat()) {
    const key = `${item.kind}:${item.id}`;
    const existing = unique.get(key);
    const time = Date.parse(item.updated_at);
    const existingTime = existing ? Date.parse(existing.updated_at) : Number.NEGATIVE_INFINITY;
    if (!existing || (Number.isFinite(time) ? time : 0) > (Number.isFinite(existingTime) ? existingTime : 0)) {
      unique.set(key, item);
    }
  }
  return [...unique.values()]
    .sort((a, b) => {
      const aTime = Date.parse(a.updated_at);
      const bTime = Date.parse(b.updated_at);
      return (Number.isFinite(bTime) ? bTime : 0) - (Number.isFinite(aTime) ? aTime : 0);
    })
    .slice(0, limit);
};

export const historyRecordPath = (item: HistoryRecord): string => {
  if (item.kind === 'workflow') return `/workflow/history/${encodeURIComponent(item.id)}`;
  if (item.kind === 'diagnosis') return `/diagnosis/${encodeURIComponent(item.id)}`;
  if (item.kind === 'ai_task' && item.project_id && item.task_id) {
    // 智能模式已移除独立进度页：历史记录回到任务所在的项目页（聊天区）。
    return `/ai/projects/${encodeURIComponent(item.project_id)}`;
  }
  if (item.project_id) return `/ai/projects/${encodeURIComponent(item.project_id)}`;
  return '/ai';
};
