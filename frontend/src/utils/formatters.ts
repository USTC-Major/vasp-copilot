// ============================================================
// 工具函数 — 格式化、严重度、文件树
// ============================================================

import type { Severity, FileTreeNode } from '../types/generated-api';
import {
  SEVERITY_MAP,
  WORKFLOW_STATUS_MAP,
  DIAGNOSIS_STATUS_MAP,
  HPC_JOB_STATUS_MAP,
  DEPLOYMENT_STATUS_MAP,
} from './enums-local';

// re-export for convenience
export { SEVERITY_MAP, WORKFLOW_STATUS_MAP, DIAGNOSIS_STATUS_MAP, HPC_JOB_STATUS_MAP, DEPLOYMENT_STATUS_MAP };

// ---- 文件大小格式化 ----
export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

// ---- 时间格式化 ----
export function formatDateTime(isoString: string): string {
  try {
    const d = new Date(isoString);
    return d.toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
  } catch {
    return isoString;
  }
}

export function formatDuration(walltime: string): string {
  const parts = walltime.split(':');
  if (parts.length === 3) {
    const [h, m, s] = parts;
    if (s === '00') return `${h}小时${m}分钟`;
    return `${h}小时${m}分${s}秒`;
  }
  return walltime;
}

// ---- 严重度 ----
export function getSeverityColor(severity: Severity): string {
  return SEVERITY_MAP[severity]?.color || 'default';
}

export function getSeverityLabel(severity: Severity): string {
  return SEVERITY_MAP[severity]?.label || severity;
}

export function getStatusConfig(status: string, type: 'workflow' | 'diagnosis' | 'hpc_job' | 'deployment') {
  const maps = {
    workflow: WORKFLOW_STATUS_MAP,
    diagnosis: DIAGNOSIS_STATUS_MAP,
    hpc_job: HPC_JOB_STATUS_MAP,
    deployment: DEPLOYMENT_STATUS_MAP,
  };
  const map = maps[type] as Record<string, { color: string; label: string }>;
  return map[status] || { color: 'default', label: status };
}

// ---- 文件树工具 ----
export function flattenFileTree(node: FileTreeNode, basePath = ''): FileTreeNode[] {
  const path = basePath ? `${basePath}/${node.name}` : node.name;
  const result: FileTreeNode[] = [{ ...node, relative_path: path }];
  if (node.children) {
    for (const child of node.children) {
      result.push(...flattenFileTree(child, path));
    }
  }
  return result;
}

export function findFileInTree(tree: FileTreeNode, relativePath: string): FileTreeNode | null {
  if (tree.relative_path === relativePath) return tree;
  if (tree.children) {
    for (const child of tree.children) {
      const found = findFileInTree(child, relativePath);
      if (found) return found;
    }
  }
  return null;
}

// ---- 元素格式化 ----
export function formatFormula(elements: string[], counts: number[]): string {
  if (!elements.length) return '未知';
  return elements.map((el, i) => `${el}${counts[i] > 1 ? counts[i] : ''}`).join('');
}


// ---- 能量格式化 ----
export function formatEnergy(ev: number): string {
  if (Math.abs(ev) < 1e-3) return '0.000 eV';
  return `${ev.toFixed(6)} eV`;
}

// ---- 密码脱敏显示 ----
export function truncateHash(hash: string, len = 8): string {
  if (hash.length <= len * 2) return hash;
  return `${hash.substring(0, len)}…`;
}

// ---- 百分比 ----
export function formatPercent(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}