import type { Matrix3, Vec3 } from './structure-geometry';
import type { StructureSummary } from './generated-api';

export type SurfaceParams = {
  miller_index: [number, number, number]; min_slab_size: number; min_vacuum_size: number;
  in_plane_supercell: [number, number]; layer_tolerance: number;
};
export type CatalysisSource = { kind: 'example' | 'poscar' | 'cif' | 'structure_id'; role: 'bulk' | 'slab'; content?: string; name?: string; structure_id?: string };
export type AtomOverride = 'fixed' | 'free';
export type AdsorbateSource = { kind: 'atom' | 'xyz' | 'co_example'; element?: string; content?: string; name?: string };
export type Adsorbate = { source_id: string; source_sha256: string; source: AdsorbateSource; atoms: { atom_id: string; element: string; cartesian: Vec3 }[]; anchor_index: number; coordinate_frame?: 'orthonormal_surface_frame' };
export type AdsorptionSiteKind = 'ontop' | 'bridge' | 'hollow' | 'manual';
export type AdsorptionSite = { site_id: string; kind: AdsorptionSiteKind; cartesian: Vec3; fractional: Vec3; label: string; source_atom_ids: string[] };
export type AdsorptionPlacement = { height_angstrom: number; rotation_degrees: Vec3; screening_distance_angstrom: number };
export type AdsorptionCandidate = {
  candidate_id: string; site_id: string; label: string; parent_revision: number; parent_surface_id: string; parent_snapshot_sha256: string; adsorbate_source_id: string;
  status: 'valid' | 'stale'; invalidation_reason?: string; snapshot: CatalysisSnapshot; placement: AdsorptionPlacement; transform: Record<string, unknown>;
  validation: { minimum_adsorbate_surface_distance_angstrom: number; minimum_periodic_self_image_distance_angstrom: number; screening_distance_angstrom: number; screening_passed: boolean; warnings: string[] };
};
export type AdsorptionState = {
  adsorbate?: Adsorbate | null; sites: AdsorptionSite[]; site_parent_snapshot_sha256?: string | null; site_parent_surface_id?: string | null; site_parent_revision?: number | null;
  frame?: { x: Vec3; y: Vec3; z: Vec3; rotation_convention: 'fixed_surface_xyz_X_then_Y_then_Z' } | null; placement?: AdsorptionPlacement | null;
  site_settings?: { kinds: Exclude<AdsorptionSiteKind, 'manual'>[]; manual_sites: { label?: string | null; uv: [number, number] }[]; dedup_tolerance_angstrom: number } | null;
  site_surface_atoms?: { atom_id: string; element?: string; cartesian: Vec3 }[]; site_map_origin_cartesian?: Vec3 | null;
  selected_site_ids: string[]; candidates: AdsorptionCandidate[]; selected_candidate_ids: string[]; warnings: string[];
};
export type CatalysisSnapshot = {
  snapshot_id: string; coordinate_mode: 'direct' | 'cartesian'; lattice: Matrix3; sha256: string;
  atoms: { atom_id: string; element: string; fractional: Vec3; cartesian: Vec3; selective_dynamics: [boolean, boolean, boolean]; provenance: Record<string, unknown> }[];
};
export type SurfaceOption = {
  surface_id: string; termination_shift: number | null; snapshot: CatalysisSnapshot; transform: Record<string, unknown>;
  surface: {
    normal: Vec3; normal_period_angstrom: number; actual_nuclei_span_angstrom: number; periodic_vacuum_gap_angstrom: number;
    in_plane_lengths_angstrom: [number, number]; layer_tolerance: number; bottom_fixed_layers: number;
    projection_origin_angstrom?: number; projection_method?: 'largest_periodic_nuclei_free_gap';
    atom_overrides: Record<string, AtomOverride>; reset_existing: boolean; flag_basis: 'direct_lattice_vectors';
    layers: { layer_index: number; atom_ids: string[]; projection_angstrom: number }[];
  };
};
export type CatalysisDraft = {
  draft_id: string; schema_version: 1; revision: number; name: string; created_at: string; updated_at: string;
  source: CatalysisSource; input_snapshot: CatalysisSnapshot; parameters: SurfaceParams | null;
  surfaces: SurfaceOption[]; active_surface_id: string | null; warnings: string[]; adsorption?: AdsorptionState;
};
export type CatalysisDraftSummary = Pick<CatalysisDraft, 'draft_id' | 'revision' | 'name' | 'updated_at' | 'active_surface_id'> & {
  source_role: 'bulk' | 'slab'; atom_count: number; surface_count: number;
};

export type CatalysisWorkflowTarget = { surface_id: string; candidate_id?: never } | { candidate_id: string; surface_id?: never };
export type CatalysisWorkflowBinding = {
  schema_version: 1; binding_id: string; draft_id: string; revision: number;
  model_kind: 'clean_surface' | 'adsorption_candidate'; surface_id: string; candidate_id: string | null;
  snapshot_id: string; snapshot_sha256: string; parent_clean_snapshot_id: string; parent_clean_snapshot_sha256: string;
  parent_clean_snapshot: CatalysisSnapshot;
  poscar_sha256: string; poscar_row_mapping: { poscar_row: number; snapshot_index: number; atom_id: string }[];
  selective_flags_basis: 'direct_lattice_vectors'; source: CatalysisSource; parameters: SurfaceParams | null;
  surface: SurfaceOption['surface'] & { model_geometry?: SurfaceOption['surface'] }; transform: Record<string, unknown>;
  adsorbate: Adsorbate | null; candidate: AdsorptionCandidate | null; snapshot: CatalysisSnapshot; warnings: string[];
};
export type CatalysisSurfacePolicy = {
  policy_id: 'cat_surface_fixed_cell_2d_v1'; cell_relaxation: 'fixed'; relax_isif: 2;
  kpoint_mode: 'gamma_2d' | 'explicit_gamma_2d'; vacuum_axis: 2; normal: Vec3;
  c_parallel_to_normal: boolean; normal_sampling_count: 1; sources: string[]; warnings: string[];
};
export type CatalysisWorkflowBindingResponse = {
  mode: 'toolbox'; structure_id: string; summary: StructureSummary;
  binding: CatalysisWorkflowBinding; surface_policy: CatalysisSurfacePolicy;
};
