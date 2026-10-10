import type { PPTaskPreview } from '../../api/postprocessing';

export function taskSelectionError(preview: PPTaskPreview, files: string[]): string {
  if (!files.length) return '请选择本次分析需要的文件。';
  const chosen = preview.files.filter(file => files.includes(file.name));
  if (chosen.length !== files.length || chosen.some(file => !file.available || !file.size_bytes || file.size_bytes > preview.limits.max_file_bytes)) return '所选文件缺失、为空或超过单文件上限，请调整选择。';
  if (chosen.reduce((sum, file) => sum + (file.size_bytes ?? 0), 0) > preview.limits.max_total_bytes) return '所选文件总量超过批次上限，请缩小文件组合。';
  const has = (name: string) => files.includes(name);
  if (has('vasprun.xml') && (has('DOSCAR') || has('EIGENVAL'))) return '请选择 vasprun.xml 或文本结果路线，避免混合多个主结果文件。';
  if (has('POSCAR') && has('CONTCAR')) return '请选择一份匹配的结构文件，避免 POSCAR／CONTCAR 歧义。';
  if (preview.kind === 'dos' && !has('vasprun.xml') && !(has('DOSCAR') && has('INCAR'))) return 'DOS 需要 vasprun.xml，或 DOSCAR＋INCAR。';
  if (preview.kind === 'band' && !(has('KPOINTS') && (has('vasprun.xml') || (has('EIGENVAL') && has('INCAR') && (has('POSCAR') || has('CONTCAR')))))) return '能带需要 vasprun.xml＋KPOINTS，或 EIGENVAL＋INCAR＋POSCAR／CONTCAR＋KPOINTS。';
  return '';
}
