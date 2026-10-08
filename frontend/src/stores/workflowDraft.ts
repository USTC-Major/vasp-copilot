import { useSyncExternalStore } from 'react';
import type { ParameterConfirmFormData } from '../components/workflow/ParameterConfirmForm';
import type { StructureSummary, WorkflowPlan, FileTreeNode, ParameterPatch } from '../types/generated-api';
import type { WorkflowPotcarChoice, WorkflowPotcarState } from '../types/potcar';
import type { PatchDraftRow } from '../components/recipes/ParameterPatchEditor';

export type WorkflowStep = 'upload' | 'confirm' | 'plan' | 'edit' | 'generate' | 'download';
export interface WorkflowDraft {
  currentStep: WorkflowStep;
  structureId: string | null;
  summary: StructureSummary | null;
  sampleName: string;
  formValues: Partial<ParameterConfirmFormData> | undefined;
  workflowPlan: WorkflowPlan | null;
  workflowId: string | null;
  fileTree: FileTreeNode | null;
  patches: ParameterPatch[];
  patchRows: PatchDraftRow[] | undefined;
  potcarChoice: WorkflowPotcarChoice;
  potcarResult: WorkflowPotcarState | undefined;
  generationNeedsCheck: boolean;
  generationRequest: { workflowId: string; revision: number; canRecoverArtifact: boolean } | null;
  lastGenerationKey: string | null;
}

const emptyDraft = (): WorkflowDraft => ({
  currentStep: 'upload', structureId: null, summary: null, sampleName: '', formValues: undefined,
  workflowPlan: null, workflowId: null, fileTree: null, patches: [], patchRows: undefined,
  potcarChoice: { mode: 'omit' }, potcarResult: undefined, generationNeedsCheck: false,
  generationRequest: null, lastGenerationKey: null,
});

// Deliberately memory-only: neither structure data nor scientific inputs go to browser storage.
let draft = emptyDraft();
const listeners = new Set<() => void>();
const publish = () => listeners.forEach(listener => listener());
export const getWorkflowDraft = () => draft;
export function setWorkflowDraftField<K extends keyof WorkflowDraft>(
  key: K, value: WorkflowDraft[K] | ((current: WorkflowDraft[K]) => WorkflowDraft[K]),
) {
  const next = typeof value === 'function'
    ? (value as (current: WorkflowDraft[K]) => WorkflowDraft[K])(draft[key]) : value;
  draft = { ...draft, [key]: next };
  publish();
}
export function resetWorkflowDraft() { draft = emptyDraft(); publish(); }
export function useWorkflowDraft() {
  return useSyncExternalStore(callback => {
    listeners.add(callback);
    return () => { listeners.delete(callback); };
  }, getWorkflowDraft);
}
