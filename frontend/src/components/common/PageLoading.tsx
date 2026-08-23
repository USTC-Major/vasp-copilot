// ============================================================
// PageLoading — 路由懒加载的 Suspense 占位
// ============================================================

import React from 'react';
import { Spin } from 'antd';

const PageLoading: React.FC = () => (
  <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: 320 }}>
    <Spin size="large" tip="页面加载中…" />
  </div>
);

export default PageLoading;
