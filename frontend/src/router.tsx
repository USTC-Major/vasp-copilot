// ============================================================
// Router — React Router v6 路由配置
// ============================================================

import { createBrowserRouter } from 'react-router-dom';
import App from './App';
import HomePage from './pages/HomePage';
import WorkflowBuilderPage from './pages/WorkflowBuilderPage';
import DiagnosisUploadPage from './pages/DiagnosisUploadPage';
import DiagnosisResultPage from './pages/DiagnosisResultPage';
import HpcDeploymentPage from './pages/HpcDeploymentPage';
import RemoteJobPage from './pages/RemoteJobPage';

export const router = createBrowserRouter([
  {
    path: '/',
    element: <App />,
    children: [
      { index: true, element: <HomePage /> },
      { path: 'workflow', element: <WorkflowBuilderPage /> },
      { path: 'diagnosis/upload', element: <DiagnosisUploadPage /> },
      { path: 'diagnosis/:id', element: <DiagnosisResultPage /> },
      { path: 'hpc/deploy', element: <HpcDeploymentPage /> },
      { path: 'hpc/jobs/:id', element: <RemoteJobPage /> },
    ],
  },
]);