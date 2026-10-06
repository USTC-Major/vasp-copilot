export type WorkflowTheme = 'dark' | 'light';
export const WORKFLOW_THEME_KEY = 'vasp-copilot.workflow-theme';
export function readWorkflowTheme(): WorkflowTheme {
  try { return localStorage.getItem(WORKFLOW_THEME_KEY) === 'light' ? 'light' : 'dark'; }
  catch { return 'dark'; }
}
