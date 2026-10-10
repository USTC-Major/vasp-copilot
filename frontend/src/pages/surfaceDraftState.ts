import type { AtomOverride, CatalysisDraft, SurfaceParams } from '../types/catalysis';

export type SurfaceFields = { h: string; k: string; l: string; thickness: string; vacuum: string; nx: string; ny: string; tolerance: string };
export type SurfaceEditor = { name: string; fields: SurfaceFields; bottom: string; overrides: Record<string, AtomOverride>; reset: boolean };
export const defaultParameters: SurfaceParams = { miller_index: [1, 1, 1], min_slab_size: 8, min_vacuum_size: 15, in_plane_supercell: [2, 2], layer_tolerance: .1 };
export const parameterFields = (p: SurfaceParams): SurfaceFields => ({ h: String(p.miller_index[0]), k: String(p.miller_index[1]), l: String(p.miller_index[2]), thickness: String(p.min_slab_size), vacuum: String(p.min_vacuum_size), nx: String(p.in_plane_supercell[0]), ny: String(p.in_plane_supercell[1]), tolerance: String(p.layer_tolerance) });
export const activeSurface = (doc: CatalysisDraft) => doc.surfaces.find(s => s.surface_id === doc.active_surface_id);
export function editorFor(doc: CatalysisDraft): SurfaceEditor {
  const surface = activeSurface(doc)?.surface;
  return { name: doc.name, fields: parameterFields(doc.parameters ?? defaultParameters), bottom: String(surface?.bottom_fixed_layers ?? 0), overrides: { ...(surface?.atom_overrides ?? {}) }, reset: surface?.reset_existing ?? false };
}
export function parsedParameters(fields: SurfaceFields): SurfaceParams | string {
  const values = Object.values(fields).map(value => value.trim() ? Number(value) : NaN);
  if (!values.every(Number.isFinite)) return '请补全所有参数，并使用有限数值。';
  const [h, k, l] = [fields.h, fields.k, fields.l].map(Number);
  if (![h, k, l].every(Number.isInteger) || (h === 0 && k === 0 && l === 0)) return 'h、k、l 必须为整数，且不能全为 0。';
  if ([h, k, l].some(value => Math.abs(value) > 3)) return '晶面指数各分量须在 −3 到 3 之间。';
  const [thickness, vacuum, nx, ny, tolerance] = [fields.thickness, fields.vacuum, fields.nx, fields.ny, fields.tolerance].map(Number);
  if (thickness < .5 || thickness > 100) return '最小 slab 厚度须为 0.5–100 Å。';
  if (vacuum < 1 || vacuum > 100) return '最小真空须为 1–100 Å。';
  if (tolerance < .001 || tolerance > 1) return '分层容差须为 0.001–1 Å。';
  if (![nx, ny].every(value => Number.isInteger(value) && value >= 1 && value <= 8)) return '面内倍数须为 1–8 的整数。';
  return { miller_index: [h, k, l], min_slab_size: thickness, min_vacuum_size: vacuum, in_plane_supercell: [nx, ny], layer_tolerance: tolerance };
}
const key = (id: string) => `vasp-copilot.cat.surface.pending.v1:${id}`;
export function readRecovery(doc: CatalysisDraft): { revision: number; editor: SurfaceEditor } | null {
  try {
    const value = JSON.parse(localStorage.getItem(key(doc.draft_id)) ?? 'null');
    if (!value || !Number.isInteger(value.revision) || typeof value.editor?.name !== 'string' || !value.editor?.fields || !Object.keys(parameterFields(defaultParameters)).every(k => typeof value.editor.fields[k] === 'string') || typeof value.editor.bottom !== 'string' || !value.editor.overrides || Object.values(value.editor.overrides).some(v => v !== 'fixed' && v !== 'free') || typeof value.editor.reset !== 'boolean') return null;
    return value;
  } catch { return null; }
}
export function storeRecovery(doc: CatalysisDraft, editor: SurfaceEditor, dirty: boolean): boolean {
  try {
    if (dirty) localStorage.setItem(key(doc.draft_id), JSON.stringify({ revision: doc.revision, editor }));
    else localStorage.removeItem(key(doc.draft_id));
    return true;
  } catch { return false; }
}
export const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
