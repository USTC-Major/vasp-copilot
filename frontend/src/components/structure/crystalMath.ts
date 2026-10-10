import type { Matrix3, Vec3 } from '../../types/structure-geometry';

export const dot = (a: Vec3, b: Vec3) => a.reduce((sum, value, i) => sum + value * b[i], 0);
export const norm = (v: Vec3) => Math.hypot(...v);
export const unit = (v: Vec3): Vec3 => {
  const n = norm(v);
  if (!Number.isFinite(n) || n < 1e-12) throw new Error('晶格方向无法可靠显示');
  return v.map(value => value / n) as Vec3;
};
export const cross = (a: Vec3, b: Vec3): Vec3 => [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]];
export const project = (matrix: Matrix3, v: Vec3): Vec3 => matrix.map(row => dot(row, v)) as Vec3;
export const multiply = (a: Matrix3, b: Matrix3): Matrix3 => a.map(row => b[0].map((_, j) => dot(row, b.map(other => other[j]) as Vec3))) as Matrix3;

export function orthogonal(matrix: Matrix3): Matrix3 {
  const right = unit(matrix[0]);
  const up = unit(matrix[1].map((value, i) => value - dot(matrix[1], right) * right[i]) as Vec3);
  return [right, up, unit(cross(right, up))];
}
export function rotate(matrix: Matrix3, yaw: number, pitch: number): Matrix3 {
  const cx=Math.cos(pitch), sx=Math.sin(pitch), cy=Math.cos(yaw), sy=Math.sin(yaw);
  return orthogonal(multiply([[1,0,0],[0,cx,-sx],[0,sx,cx]], multiply([[cy,0,sy],[0,1,0],[-sy,0,cy]], matrix)));
}

/** Approved prototype convention, Megane #661/#694 reference; not an official VESTA formula. */
export function standardCrystalOrientation(basis: Matrix3): Matrix3 {
  const [, b, c] = basis;
  const upright=unit(c), right=unit(b.map((value,i)=>value-dot(b,upright)*upright[i]) as Vec3);
  const toward=unit(cross(right,upright));
  const azimuth=Math.atan(1/3), elevation=Math.atan(1/6);
  const horizontal=toward.map((value,i)=>value*Math.cos(azimuth)+right[i]*Math.sin(azimuth)) as Vec3;
  const eye=horizontal.map((value,i)=>value*Math.cos(elevation)+upright[i]*Math.sin(elevation)) as Vec3;
  const up=upright.map((value,i)=>value*Math.cos(elevation)-horizontal[i]*Math.sin(elevation)) as Vec3;
  return [unit(cross(up,eye)), unit(up), unit(eye)];
}
export function alongAxis(basis: Matrix3, axis: 0 | 1 | 2): Matrix3 {
  const eye=unit(basis[axis]);
  // Prefer c upright for a/b; c view uses laboratory y as the prior prototype.
  let up: Vec3 = axis===2 ? [0,1,0] : unit(basis[2]);
  if (norm(cross(up,eye)) < 1e-8) up = Math.abs(eye[0]) < .8 ? [1,0,0] : [0,0,1];
  const right=unit(cross(up,eye));
  return [right, unit(cross(eye,right)), eye];
}
export const cellCorners = (basis: Matrix3): Vec3[] => Array.from({length:8}, (_,mask) => [0,1,2].map(j => basis.reduce((sum,row,i) => sum + ((mask>>i)&1)*row[j],0)) as Vec3);
export const CELL_EDGES: [number,number][] = Array.from({length:8}, (_,mask) => [0,1,2].filter(i => !(mask&(1<<i))).map(i => [mask, mask|(1<<i)] as [number,number])).flat();
export type ProjectionExtent = [number, number];
/** Symmetric bounds keep the crystal's fixed rotation center at the plot center. */
export function projectionExtent(points: Vec3[]): ProjectionExtent {
  return points.reduce<ProjectionExtent>((extent,p)=>[Math.max(extent[0],Math.abs(p[0])),Math.max(extent[1],Math.abs(p[1]))],[1e-9,1e-9]);
}
/** The extent is captured on fit; rotating the scene must not recompute it. */
export function centeredProjectionLayout(extent: ProjectionExtent, width: number, height: number, zoom=1) {
  const top=65, areaHeight=Math.max(1,height-top-110);
  const scale=Math.min(Math.max(1,width-54)/(extent[0]*2),areaHeight/(extent[1]*2))*zoom;
  return (p: Vec3): [number,number] => [p[0]*scale+width/2,-p[1]*scale+top+areaHeight/2];
}
export const MIN_CRYSTAL_ZOOM=.6, MAX_CRYSTAL_ZOOM=2.5;
export function zoomByFactor(zoom: number, factor: number) {
  return Number.isFinite(factor)&&factor>0 ? Math.max(MIN_CRYSTAL_ZOOM,Math.min(MAX_CRYSTAL_ZOOM,zoom*factor)) : zoom;
}
/** Normalize mouse/trackpad units and cap one event without losing tiny deltas. */
export function wheelZoomFactor(deltaY: number, deltaMode: number, pageHeight: number) {
  if(!Number.isFinite(deltaY))return 1;
  const unit=deltaMode===1?16:deltaMode===2&&Number.isFinite(pageHeight)?Math.max(1,pageHeight):1;
  const pixels=Math.max(-240,Math.min(240,deltaY*unit));
  return Math.exp(-pixels*.0015);
}
export function projectionLayout(points: Vec3[], width: number, height: number, compact: boolean, zoom=1) {
  const xs=points.map(p=>p[0]), ys=points.map(p=>p[1]);
  const lo=[Math.min(...xs),Math.min(...ys)], hi=[Math.max(...xs),Math.max(...ys)];
  const side=compact?14:27, top=compact?14:65, bottom=compact?14:110;
  const areaHeight=Math.max(1,height-top-bottom);
  const scale=Math.min((width-side*2)/Math.max(hi[0]-lo[0],1e-9),areaHeight/Math.max(hi[1]-lo[1],1e-9))*zoom;
  return (p: Vec3): [number,number] => [(p[0]-(lo[0]+hi[0])/2)*scale+width/2, -(p[1]-(lo[1]+hi[1])/2)*scale+top+areaHeight/2];
}
