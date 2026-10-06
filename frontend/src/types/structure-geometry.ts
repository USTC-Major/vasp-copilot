export type Vec3 = [number, number, number];
export type Matrix3 = [Vec3, Vec3, Vec3];
export interface StructureSite {
  id: number;
  element: string;
  fractional: Vec3;
  cartesian_angstrom: Vec3;
  selective_flags?: [boolean, boolean, boolean];
}
export interface StructureGeometry {
  structure_id: string;
  formula: string;
  atom_count: number;
  basis_cartesian_angstrom: Matrix3;
  lattice: { a: number; b: number; c: number; alpha: number; beta: number; gamma: number; volume: number; matrix: Matrix3 };
  coordinate_mode: 'direct' | 'cartesian';
  selective_dynamics: boolean;
  selective_flags_basis: 'direct_lattice_vectors';
  sites: StructureSite[];
  source: { format: 'poscar' | 'cif' | 'materials_project'; file_name: string; material_id: string | null; coordinate_source: string; poscar_sha256: string };
  geometry_sha256: string;
}
