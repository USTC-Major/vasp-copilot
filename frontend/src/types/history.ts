export type HistoryKind = 'ai_project' | 'ai_task' | 'workflow' | 'diagnosis';

export interface HistoryRecord {
  id: string;
  kind: HistoryKind;
  title: string;
  status: string;
  updated_at: string;
  created_at?: string;
  expires_at?: string;
  project_id?: string;
  task_id?: string;
  project_name?: string;
  execution_mode?: 'Fake' | 'Real' | 'None';
  demo?: boolean;
}

export interface RecentHistoryResponse {
  request_id?: string;
  mode?: string;
  source: 'ai' | 'workflows' | 'diagnoses';
  retention: 'persistent' | 'ttl';
  records: HistoryRecord[];
  demo?: boolean;
}
