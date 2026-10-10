import type { AdsorbateSource, AdsorptionPlacement, AdsorptionSiteKind, CatalysisDraft } from '../types/catalysis';
import { same } from './surfaceDraftState';

export type AdsorptionEditor = {
  kind: AdsorbateSource['kind']; element: string; content: string; sourceName: string; anchor: string;
  height: string; rx: string; ry: string; rz: string; screening: string; tolerance: string;
  kinds: Exclude<AdsorptionSiteKind, 'manual'>[]; manualEnabled: boolean; u: string; v: string; manualLabel: string;
  siteIds: string[]; candidateIds: string[];
};
export const coXYZ = '2\nSynthetic CO, bond length 1.15 A; geometry example only\nC 0 0 0\nO 0 0 1.15';
export const kindLabels = { ontop: '顶位', bridge: '桥位', hollow: '空位', manual: '手动' };
export function adsorptionEditorFor(doc: CatalysisDraft): AdsorptionEditor {
  const state = doc.adsorption, source = state?.adsorbate?.source, p = state?.placement, sites = state?.site_settings, manual = sites?.manual_sites[0];
  return { kind: source?.kind ?? 'co_example', element: source?.element ?? 'H', content: source?.content ?? '', sourceName: source?.name ?? '', anchor: String(state?.adsorbate?.anchor_index ?? 0), height: String(p?.height_angstrom ?? 2), rx: String(p?.rotation_degrees[0] ?? 0), ry: String(p?.rotation_degrees[1] ?? 0), rz: String(p?.rotation_degrees[2] ?? 0), screening: String(p?.screening_distance_angstrom ?? .8), tolerance: String(sites?.dedup_tolerance_angstrom ?? .05), kinds: sites?.kinds ?? ['ontop', 'bridge', 'hollow'], manualEnabled: !!manual, u: String(manual?.uv[0] ?? .5), v: String(manual?.uv[1] ?? .5), manualLabel: manual?.label ?? '', siteIds: state?.selected_site_ids ?? [], candidateIds: state?.selected_candidate_ids ?? [] };
}
export function adsorbateInput(editor: AdsorptionEditor): { source: AdsorbateSource; anchor: number } | string {
  let count = 1;
  if (editor.kind === 'xyz') {
    const parsed = parseXYZ(editor.content); if (typeof parsed === 'string') return parsed; count = parsed.length;
  } else if (editor.kind === 'co_example') count = 2;
  else if (!/^[A-Z][a-z]?$/.test(editor.element.trim())) return '请输入元素符号，例如 H、C 或 Pt。';
  const anchor = editor.anchor.trim() ? Number(editor.anchor) : NaN;
  if (!Number.isInteger(anchor) || anchor < 0 || anchor >= count) return '请选择分子中的有效锚定原子。';
  return { source: editor.kind === 'atom' ? { kind: 'atom', element: editor.element.trim() } : editor.kind === 'xyz' ? { kind: 'xyz', content: editor.content, ...(editor.sourceName ? { name: editor.sourceName } : {}) } : { kind: 'co_example' }, anchor };
}
export function parseXYZ(text: string): { element: string; xyz: [number, number, number] }[] | string {
  if (new TextEncoder().encode(text).length > 65536) return 'XYZ 文件须不超过 64 KiB。';
  const lines = text.replace(/\r/g, '').split('\n'); while (lines.at(-1)?.trim() === '') lines.pop();
  const count = /^\d+$/.test(lines[0]?.trim() ?? '') ? Number(lines[0]) : NaN;
  if (!Number.isInteger(count) || count < 1 || count > 128) return 'XYZ 首行须为 1–128 个原子，第二行为注释。';
  if (lines.length !== count + 2) return 'XYZ 须包含单个完整构型：原子数、注释和对应原子行。';
  const atoms: { element: string; xyz: [number, number, number] }[] = [];
  for (const line of lines.slice(2)) {
    const values = line.trim().split(/\s+/), xyz = values.slice(1).map(Number);
    if (values.length !== 4 || !/^[A-Z][a-z]?$/.test(values[0]) || xyz.length !== 3 || !xyz.every(v => Number.isFinite(v) && Math.abs(v) <= 1000)) return 'XYZ 每行须为元素符号和三个有限坐标（Å，绝对值不超过 1000）。';
    atoms.push({ element: values[0], xyz: xyz as [number, number, number] });
  }
  return atoms;
}
export function placementInput(editor: AdsorptionEditor): AdsorptionPlacement | string {
  const raw = [editor.height, editor.rx, editor.ry, editor.rz, editor.screening];
  const [height, rx, ry, rz, screening] = raw.map(v => v.trim() ? Number(v) : NaN);
  if (![height, rx, ry, rz, screening].every(Number.isFinite)) return '请补全高度、旋转和距离筛查参数，使用有限数值。';
  if (height < -10 || height > 100) return '初始锚点高度须为 −10–100 Å。';
  if ([rx, ry, rz].some(v => Math.abs(v) > 360)) return '各旋转角须为 −360–360°。';
  if (screening < 0 || screening > 5) return '距离筛查须为 0–5 Å，0 表示关闭提示筛查。';
  return { height_angstrom: height, rotation_degrees: [rx, ry, rz], screening_distance_angstrom: screening };
}
export function siteInput(editor: AdsorptionEditor) {
  const tolerance = editor.tolerance.trim() ? Number(editor.tolerance) : NaN;
  if (!Number.isFinite(tolerance) || tolerance < .001 || tolerance > .5) return '位点去重容差须为 0.001–0.5 Å。';
  const u = editor.u.trim() ? Number(editor.u) : NaN, v = editor.v.trim() ? Number(editor.v) : NaN;
  if (editor.manualEnabled && (![u, v].every(Number.isFinite) || [u, v].some(n => n < 0 || n >= 1))) return '手动位置 u、v 须满足 0 ≤ u、v < 1。';
  if (!editor.kinds.length && !editor.manualEnabled) return '至少选择一种自动位点，或启用手动位置。';
  return { kinds: editor.kinds, manual_sites: editor.manualEnabled ? [{ uv: [u, v] as [number, number], ...(editor.manualLabel.trim() ? { label: editor.manualLabel.trim() } : {}) }] : [], dedup_tolerance_angstrom: tolerance };
}
export const sourceEqual = (a: AdsorptionEditor, b: AdsorptionEditor) => same([a.kind, a.element, a.content, a.sourceName, a.anchor], [b.kind, b.element, b.content, b.sourceName, b.anchor]);
export const placementEqual = (a: AdsorptionEditor, b: AdsorptionEditor) => same([a.height, a.rx, a.ry, a.rz, a.screening], [b.height, b.rx, b.ry, b.rz, b.screening]);
const key = (id: string) => `vasp-copilot.cat.adsorption.pending.v1:${id}`;
export function readAdsorptionRecovery(doc: CatalysisDraft): { revision: number; editor: AdsorptionEditor } | null {
  try {
    const data = JSON.parse(localStorage.getItem(key(doc.draft_id)) ?? 'null');
    if (!data || !Number.isInteger(data.revision) || !data.editor || !['atom', 'xyz', 'co_example'].includes(data.editor.kind)) return null;
    const defaults = adsorptionEditorFor(doc);
    if (Object.entries(defaults).some(([k, v]) => typeof v === 'string' && typeof data.editor[k] !== 'string') || typeof data.editor.manualEnabled !== 'boolean' || !Array.isArray(data.editor.kinds) || !data.editor.kinds.every((v: string) => ['ontop', 'bridge', 'hollow'].includes(v)) || !['siteIds', 'candidateIds'].every(k => Array.isArray(data.editor[k]) && data.editor[k].every((v: unknown) => typeof v === 'string'))) return null;
    return data;
  } catch { return null; }
}
export function storeAdsorptionRecovery(doc: CatalysisDraft, editor: AdsorptionEditor, dirty: boolean): boolean {
  try { if (dirty) localStorage.setItem(key(doc.draft_id), JSON.stringify({ revision: doc.revision, editor })); else localStorage.removeItem(key(doc.draft_id)); return true; } catch { return false; }
}
