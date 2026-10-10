import { adoptCatalysisWorkflow, getWorkflowDraft, hasWorkflowDraft, resetWorkflowDraft, setWorkflowDraftField } from './workflowDraft';
import { catalysisWorkflowResponseFixture } from '../mocks/catalysisWorkflowFixture';

beforeEach(() => resetWorkflowDraft());

it('atomically imports a CAT snapshot and retains independent provenance across draft edits', () => {
  const response = catalysisWorkflowResponseFixture();
  expect(adoptCatalysisWorkflow(response, getWorkflowDraft())).toBe(true);
  response.binding.snapshot.atoms[0].selective_dynamics = [true, true, true];
  setWorkflowDraftField('sampleName', '用户参数草稿');
  expect(getWorkflowDraft()).toMatchObject({ currentStep: 'confirm', structureId: 'str-cat-immutable', formValues: { tasks: ['relax', 'static'], magnetic: false },
    catalysisBinding: { revision: 7, snapshot: { atoms: [{ selective_dynamics: [false, false, false] }] } }, surfacePolicy: { relax_isif: 2 } });
  expect(hasWorkflowDraft(getWorkflowDraft())).toBe(true);
  resetWorkflowDraft();
  expect(getWorkflowDraft().catalysisBinding).toBeNull(); expect(getWorkflowDraft().surfacePolicy).toBeNull();
});

it('does not replace a Workflow draft changed after handoff confirmation', () => {
  const expected = getWorkflowDraft();
  setWorkflowDraftField('sampleName', '期间的新输入');
  expect(adoptCatalysisWorkflow(catalysisWorkflowResponseFixture(), expected)).toBe(false);
  expect(getWorkflowDraft().sampleName).toBe('期间的新输入');
  expect(getWorkflowDraft().catalysisBinding).toBeNull();
});
