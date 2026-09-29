import type { MaterialCandidate } from './generated-api';

export interface MaterialCriteria {
  formula?: string;
  elements?: string[];
  chemsys?: string;
  band_gap?: { min?: number; max?: number };
  is_stable?: boolean;
  is_metal?: boolean;
}

export interface MaterialInterpretation {
  query: string;
  criteria: MaterialCriteria | null;
  interpreted_conditions: string[];
  unresolved_conditions: string[];
  warnings: string[];
  status: 'ready_for_confirmation' | 'needs_clarification';
}

export interface MaterialSearchRequest {
  query?: string;
  criteria?: MaterialCriteria;
  confirmed?: boolean;
  unresolved_conditions?: string[];
  limit?: number;
}

export interface MaterialSearchResponse {
  request_id: string;
  query: string;
  criteria: MaterialCriteria;
  llm_used: boolean;
  count: number;
  materials: MaterialCandidate[];
}
