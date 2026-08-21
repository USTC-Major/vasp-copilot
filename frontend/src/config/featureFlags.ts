// ============================================================
// Feature Flags 配置
// ============================================================

import type { FeatureFlags } from '../types/generated-api';

// 离线演示默认值（ENABLE_LLM=false, ENABLE_FAKE_HPC=true）
export const DEFAULT_FEATURE_FLAGS: FeatureFlags = {
  ENABLE_LLM: false,
  ENABLE_HPC_BRIDGE: false,
  ENABLE_FAKE_HPC: true,
  ENABLE_POTCAR_ASSEMBLY: false,
  ENABLE_BAND_WORKFLOW: true,
  MAX_UPLOAD_SIZE_MB: 100,
  MAX_TEXT_PREVIEW_BYTES: 524288,
  MAX_OUTCAR_PREVIEW_LINES: 500,
};

// 从 bootstrap API 或本地覆盖加载
let currentFlags: FeatureFlags = { ...DEFAULT_FEATURE_FLAGS };

export function getFeatureFlags(): FeatureFlags {
  return currentFlags;
}

export function setFeatureFlags(flags: Partial<FeatureFlags>): void {
  currentFlags = { ...currentFlags, ...flags };
}

export function isFeatureEnabled(key: keyof FeatureFlags): boolean {
  return Boolean(currentFlags[key]);
}