import { describe, expect, it } from 'vitest';
import type { Matrix3, Vec3 } from '../../types/structure-geometry';
import { alongAxis, cellCorners, centeredProjectionLayout, dot, multiply, norm, project, projectionExtent, projectionLayout, rotate, standardCrystalOrientation, unit, wheelZoomFactor, zoomByFactor } from './crystalMath';

const hex: Matrix3 = [[5,0,0],[-2.5,5*Math.sqrt(3)/2,0],[0,0,13.7]];
const triclinic: Matrix3 = [[3,.2,.1],[1,4,.3],[.2,.8,5]];
const transpose=(m:Matrix3)=>m[0].map((_,i)=>m.map(row=>row[i])) as Matrix3;
function expectRotation(m:Matrix3) {
  for(let i=0;i<3;i++) for(let j=0;j<3;j++) expect(dot(m[i],m[j])).toBeCloseTo(i===j?1:0,12);
  expect(dot(m[0],[m[1][1]*m[2][2]-m[1][2]*m[2][1],m[1][2]*m[2][0]-m[1][0]*m[2][2],m[1][0]*m[2][1]-m[1][1]*m[2][0]])).toBeCloseTo(1,12);
}
describe('approved crystal projection math',()=>{
  it.each([[hex],[triclinic]])('standard camera preserves lengths, angles and chirality',basis=>{
    const camera=standardCrystalOrientation(basis);expectRotation(camera);
    for(const v of basis)expect(norm(project(camera,v))).toBeCloseTo(norm(v),12);
    expect(dot(project(camera,basis[0]),project(camera,basis[1]))).toBeCloseTo(dot(basis[0],basis[1]),12);
  });
  it('standard camera is covariant under rotation of the entire lattice',()=>{
    const world=rotate([[1,0,0],[0,1,0],[0,0,1]],.53,-.42);
    const moved=triclinic.map(v=>project(world,v)) as Matrix3;
    const expected=multiply(standardCrystalOrientation(triclinic),transpose(world));
    const actual=standardCrystalOrientation(moved);
    for(let i=0;i<3;i++)for(let j=0;j<3;j++)expect(actual[i][j]).toBeCloseTo(expected[i][j],12);
  });
  it('axis presets look from the actual positive direct axis, including nonorthogonal b',()=>{
    for(const basis of [hex,triclinic])for(const axis of [0,1,2] as const){
      const m=alongAxis(basis,axis);expectRotation(m);
      const p=project(m,unit(basis[axis]));expect(Math.hypot(p[0],p[1])).toBeLessThan(1e-12);expect(p[2]).toBeCloseTo(1,12);
    }
    const camera=alongAxis(hex,2),a=project(camera,hex[0]),b=project(camera,hex[1]);
    expect(Math.acos((a[0]*b[0]+a[1]*b[1])/(Math.hypot(a[0],a[1])*Math.hypot(b[0],b[1])))*180/Math.PI).toBeCloseTo(120,12);
  });
  it('repeated drag rotations retain orthogonality and physical distances',()=>{
    let camera=standardCrystalOrientation(triclinic);
    for(let i=0;i<1000;i++)camera=rotate(camera,.008,-.007);
    expectRotation(camera);const v:Vec3=[1.7,-3.4,2.8];expect(norm(project(camera,v))).toBeCloseTo(norm(v),12);
  });
  it('fit includes cell corners and outside sites with default label margins on narrow screens',()=>{
    const matrix=standardCrystalOrientation(hex);
    const points=[...cellCorners(hex),[14,-8,22] as Vec3].map(v=>project(matrix,v));
    for(const [width,height,compact] of [[120,120,true],[298,340,false],[640,440,false]] as const){
      const xy=projectionLayout(points,width,height,compact);
      for(const p of points){const [x,y]=xy(p);expect(x).toBeGreaterThanOrEqual(compact?14:27-1e-9);expect(x).toBeLessThanOrEqual(width-(compact?14:27)+1e-9);expect(y).toBeGreaterThanOrEqual(compact?14:65-1e-9);expect(y).toBeLessThanOrEqual(height-(compact?14:110)+1e-9);}
    }
  });
  it('captures a centered fit that holds scale and pivot through rotation and explicit refits',()=>{
    const center=cellCorners(triclinic)[7].map(v=>v/2) as Vec3;
    const points=[...cellCorners(triclinic),[14,-8,22] as Vec3].map(v=>v.map((value,i)=>value-center[i]) as Vec3);
    const initial=standardCrystalOrientation(triclinic),rotated=rotate(initial,.9,.7);
    const extent=projectionExtent(points.map(v=>project(initial,v)));
    const xy=centeredProjectionLayout(extent,640,440,1.2),origin=xy([0,0,0]);
    const scale=xy([1,0,0])[0]-origin[0];
    for(const camera of [initial,rotated,alongAxis(triclinic,0)])for(const point of points){
      const projected=project(camera,point),screen=xy(projected);
      expect(screen[0]-origin[0]).toBeCloseTo(projected[0]*scale,12);
      expect(screen[1]-origin[1]).toBeCloseTo(-projected[1]*scale,12);
    }
    const refit=projectionExtent(points.map(v=>project(rotated,v)));
    for(const [width,height] of [[298,340],[640,440]] as const){
      const resized=centeredProjectionLayout(refit,width,height);
      expect(resized([0,0,0])).toEqual([width/2,65+(height-175)/2]);
      for(const point of points){
        const [x,y]=resized(project(rotated,point));
        expect(x).toBeGreaterThanOrEqual(27-1e-9);expect(x).toBeLessThanOrEqual(width-27+1e-9);
        expect(y).toBeGreaterThanOrEqual(65-1e-9);expect(y).toBeLessThanOrEqual(height-110+1e-9);
      }
    }
  });
  it('normalizes and bounds wheel inputs while preserving small trackpad movements',()=>{
    expect(wheelZoomFactor(32,0,440)).toBe(wheelZoomFactor(2,1,440));
    expect(wheelZoomFactor(32,0,440)).toBe(wheelZoomFactor(32/440,2,440));
    expect(wheelZoomFactor(Infinity,0,440)).toBe(1);expect(wheelZoomFactor(NaN,0,440)).toBe(1);
    expect(wheelZoomFactor(1e200,0,440)).toBe(wheelZoomFactor(240,0,440));
    expect(wheelZoomFactor(-1e200,0,440)).toBe(wheelZoomFactor(-240,0,440));
    let zoom=1;for(let i=0;i<100;i++)zoom=zoomByFactor(zoom,wheelZoomFactor(-.1,0,440));
    expect(zoom).toBeGreaterThan(1.01);
    expect(zoomByFactor(zoom,Infinity)).toBe(zoom);expect(zoomByFactor(zoom,NaN)).toBe(zoom);expect(zoomByFactor(zoom,-1)).toBe(zoom);
    expect(zoomByFactor(2,2)).toBe(2.5);expect(zoomByFactor(.7,.1)).toBe(.6);
  });
});
