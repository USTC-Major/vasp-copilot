import type { CatalysisDraft, CatalysisWorkflowBindingResponse } from '../types/catalysis';
import { structureAnalysisFixture } from './fixtures';

export const catalysisWorkflowDraftFixture: CatalysisDraft = {
  draft_id: 'cat-workflow-test', schema_version: 1, revision: 7, name: 'Pt 表面 中文路径', created_at: '', updated_at: '',
  source: { kind: 'poscar', role: 'slab', name: '中文路径/POSCAR' }, parameters: null, warnings: [], active_surface_id: 'surface-1',
  input_snapshot: { snapshot_id: 'snapshot-1', coordinate_mode: 'cartesian', lattice: [[4, 0, 0], [0, 4, 0], [0, 0, 20]], sha256: 'a'.repeat(64),
    atoms: [{ atom_id: 'Pt-1', element: 'Pt', fractional: [0, 0, .25], cartesian: [0, 0, 5], selective_dynamics: [false, false, false], provenance: { kind: 'import', source_index: 0 } }] },
  surfaces: [],
};
catalysisWorkflowDraftFixture.surfaces = [{ surface_id: 'surface-1', termination_shift: null, snapshot: catalysisWorkflowDraftFixture.input_snapshot, transform: {},
  surface: { normal: [0, 0, 1], normal_period_angstrom: 20, actual_nuclei_span_angstrom: 0, periodic_vacuum_gap_angstrom: 20, in_plane_lengths_angstrom: [4, 4], layer_tolerance: .1, bottom_fixed_layers: 1, atom_overrides: {}, reset_existing: false, flag_basis: 'direct_lattice_vectors', layers: [{ layer_index: 0, atom_ids: ['Pt-1'], projection_angstrom: 5 }] } }];

export function catalysisWorkflowResponseFixture(doc = catalysisWorkflowDraftFixture, candidateId: string | null = null): CatalysisWorkflowBindingResponse {
  const parent = doc.surfaces.find(surface => surface.surface_id === doc.active_surface_id)!;
  const candidate = candidateId ? doc.adsorption?.candidates.find(value => value.candidate_id === candidateId) ?? null : null;
  const snapshot = candidate?.snapshot ?? parent.snapshot;
  const structureId = 'str-cat-immutable';
  return structuredClone({
    mode: 'toolbox', structure_id: structureId,
    summary: { ...structureAnalysisFixture.summary, structure_id: structureId, formula: 'Pt', elements: ['Pt'], counts: [snapshot.atoms.length], atom_count: snapshot.atoms.length,
      coordinate_mode: snapshot.coordinate_mode, selective_dynamics: true, source_format: 'poscar', source_sha256: 'c'.repeat(64), source_material_id: null },
    binding: { schema_version: 1, binding_id: 'binding-1', draft_id: doc.draft_id, revision: doc.revision, model_kind: candidateId ? 'adsorption_candidate' : 'clean_surface',
      surface_id: parent.surface_id, candidate_id: candidateId, snapshot_id: snapshot.snapshot_id, snapshot_sha256: snapshot.sha256,
      parent_clean_snapshot_id: parent.snapshot.snapshot_id, parent_clean_snapshot_sha256: parent.snapshot.sha256, parent_clean_snapshot: parent.snapshot,
      poscar_sha256: 'c'.repeat(64), poscar_row_mapping: snapshot.atoms.map((atom, index) => ({ poscar_row: index + 1, snapshot_index: index, atom_id: atom.atom_id })),
      selective_flags_basis: 'direct_lattice_vectors', source: doc.source, parameters: doc.parameters, surface: parent.surface, transform: candidate?.transform ?? parent.transform,
      adsorbate: candidateId ? doc.adsorption?.adsorbate ?? null : null, candidate, snapshot, warnings: [] },
    surface_policy: { policy_id: 'cat_surface_fixed_cell_2d_v1', cell_relaxation: 'fixed', relax_isif: 2, kpoint_mode: 'gamma_2d', vacuum_axis: 2,
      c_parallel_to_normal: true, normal_sampling_count: 1, normal: parent.surface.normal, sources: ['https://vasp.at/wiki/index.php/ISIF', 'https://vasp.at/wiki/index.php/KPOINTS'], warnings: [] },
  });
}
