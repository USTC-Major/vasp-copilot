export interface DagBox { id: string; x: number; y: number; width: number; height: number }
export interface DagDependency { id: string; source: string; target: string; label: string }
export interface DagTrack { id: string; x: number; y: number; width: number; height: number; sourceEscape: number; targetEscape: number }

/** Keep file names above every card, and separate overlapping label intervals. */
export function layoutDagTracks(boxes: DagBox[], dependencies: DagDependency[]): DagTrack[] {
  if (!boxes.length) return [];
  const top = Math.min(...boxes.map(box => box.y));
  const lanes: Array<Array<[number, number]>> = [];
  return dependencies.flatMap(dep => {
    const from = boxes.find(box => box.id === dep.source);
    const to = boxes.find(box => box.id === dep.target);
    if (!from || !to) return [];
    const width = Math.max(148, Math.ceil(Array.from(dep.label).reduce((sum, char) => sum + (char.charCodeAt(0) > 255 ? 12 : 7.4), 0) + 24));
    const sourceEscape = from.x + from.width + 16;
    const targetEscape = to.x - 16;
    const x = (sourceEscape + targetEscape) / 2 - width / 2;
    let lane = lanes.findIndex(intervals => intervals.every(([left, right]) => x + width + 16 <= left || x >= right + 16));
    if (lane < 0) { lane = lanes.length; lanes.push([]); }
    lanes[lane].push([x, x + width]);
    return [{ id: dep.id, x, y: top - 50 - lane * 42, width, height: 30, sourceEscape, targetEscape }];
  });
}
