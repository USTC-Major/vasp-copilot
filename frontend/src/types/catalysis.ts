import type { Matrix3, Vec3 } from './structure-geometry';

export type SurfaceParams = {
  miller_index: [number, number, number]; min_slab_size: number; min_vacuum_size: number;
  in_plane_supercell: [number, number]; layer_tolerance: number;
};
export type CatalysisSource = { kind: 'example' | 'poscar' | 'cif' | 'structure_id'; role: 'bulk' | 'slab'; content?: string; name?: string; structure_id?: string };
export type AtomOverride = 'fixed' | 'free';
export type CatalysisSnapshot = {
  snapshot_id: string; coordinate_mode: 'direct' | 'cartesian'; lattice: Matrix3; sha256: string;
  atoms: { atom_id: string; element: string; fractional: Vec3; cartesian: Vec3; selective_dynamics: [boolean, boolean, boolean]; provenance: Record<string, unknown> }[];
};
export type SurfaceOption = {
  surface_id: string; termination_shift: number | null; snapshot: CatalysisSnapshot; transform: Record<string, unknown>;
  surface: {
    normal: Vec3; normal_period_angstrom: number; actual_nuclei_span_angstrom: number; periodic_vacuum_gap_angstrom: number;
    in_plane_lengths_angstrom: [number, number]; layer_tolerance: number; bottom_fixed_layers: number;
    atom_overrides: Record<string, AtomOverride>; reset_existing: boolean; flag_basis: 'direct_lattice_vectors';
    layers: { layer_index: number; atom_ids: string[]; projection_angstrom: number }[];
  };
};
export type CatalysisDraft = {
  draft_id: string; schema_version: 1; revision: number; name: string; created_at: string; updated_at: string;
  source: CatalysisSource; input_snapshot: CatalysisSnapshot; parameters: SurfaceParams | null;
  surfaces: SurfaceOption[]; active_surface_id: string | null; warnings: string[];
};
export type CatalysisDraftSummary = Pick<CatalysisDraft, 'draft_id' | 'revision' | 'name' | 'updated_at' | 'active_surface_id'> & {
  source_role: 'bulk' | 'slab'; atom_count: number; surface_count: number;
};
