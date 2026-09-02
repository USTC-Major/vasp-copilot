// ============================================================
// Router — React Router 路由配置（页面级懒加载）
// ============================================================

import React, { lazy, Suspense } from 'react';
import { createBrowserRouter } from 'react-router-dom';
import type { RouteObject } from 'react-router-dom';
import App from './App';
import HomePage from './pages/HomePage';
import AiProjectsPage from './pages/AiProjectsPage';
import AiProjectPage from './pages/AiProjectPage';
import AiProgressPage from './pages/AiProgressPage';
import AiSettingsPage from './pages/AiSettingsPage';
import PageLoading from './components/common/PageLoading';

const WorkflowBuilderPage = lazy(() => import('./pages/WorkflowBuilderPage'));
const DiagnosisUploadPage = lazy(() => import('./pages/DiagnosisUploadPage'));
const DiagnosisResultPage = lazy(() => import('./pages/DiagnosisResultPage'));
const HpcDeploymentPage = lazy(() => import('./pages/HpcDeploymentPage'));
const RemoteJobPage = lazy(() => import('./pages/RemoteJobPage'));

const withSuspense = (element: React.ReactNode) => (
  <Suspense fallback={<PageLoading />}>{element}</Suspense>
);

/** 导出路由表供测试以 createMemoryRouter 复用。 */
export const routes: RouteObject[] = [
  {
    path: '/',
    element: <App />,
    children: [
      { path: 'ai', element: <AiProjectsPage /> },
      { path: 'ai/projects/:projectId', element: <AiProjectPage /> },
      { path: 'ai/projects/:projectId/progress/:taskId', element: <AiProgressPage /> },
      { path: 'ai/settings', element: <AiSettingsPage /> },
      { index: true, element: <HomePage /> },
      { path: 'workflow', element: withSuspense(<WorkflowBuilderPage />) },
      { path: 'diagnosis/upload', element: withSuspense(<DiagnosisUploadPage />) },
      { path: 'diagnosis/:id', element: withSuspense(<DiagnosisResultPage />) },
      { path: 'hpc/deploy', element: withSuspense(<HpcDeploymentPage />) },
      { path: 'hpc/jobs/:id', element: withSuspense(<RemoteJobPage />) },
    ],
  },
];

export const router = createBrowserRouter(routes);
