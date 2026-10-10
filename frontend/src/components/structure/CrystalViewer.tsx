import { useEffect, useId, useMemo, useRef, useState } from 'react';
import type { CSSProperties, PointerEvent as ReactPointerEvent } from 'react';
import { Alert, Button, Checkbox, Modal, Spin, theme } from 'antd';
import { useStructureGeometry } from '../../hooks/useApi';
import type { Matrix3, StructureGeometry, Vec3 } from '../../types/structure-geometry';
import { alongAxis, cellCorners, CELL_EDGES, centeredProjectionLayout, MAX_CRYSTAL_ZOOM, MIN_CRYSTAL_ZOOM, norm, project, projectionExtent, projectionLayout, rotate, standardCrystalOrientation, unit, wheelZoomFactor, zoomByFactor } from './crystalMath';
import type { ProjectionExtent } from './crystalMath';
import './crystal-viewer.css';

type Mode = 'cell' | 'a' | 'b' | 'c' | 'custom';
const descriptions: Record<Mode,string> = {cell:'标准晶形朝向',a:'沿 a 轴 · 从 +a 端看向原点',b:'沿 b 轴 · 从 +b 端看向原点',c:'沿 c 轴 · 从 +c 端看向原点',custom:'自由旋转'};
const palette = ['#90B3EE','#D69A82','#95B6A1','#B7A3D1','#CEAE6C','#7DBAC2','#C994AC','#A4ADC1'];
const elementColor = (element: string) => element === 'Fe' ? '#82AAF4' : element === 'O' ? '#D68D7E' : palette[Array.from(element).reduce((sum,ch)=>sum+ch.charCodeAt(0),0)%palette.length];
const format = (n: number) => n.toFixed(6);
export type CrystalMarker = { id: string; label: string; cartesian: Vec3; selected?: boolean };

function CrystalScene({data, matrix, compact=false, zoom=1, fitExtent, selected=null, showCell=true, showNumbers=false, onSelect, onRotate, onReset, onZoom, markers=[]}: {
  data: StructureGeometry; matrix: Matrix3; compact?: boolean; zoom?: number; selected?: number|null;
  fitExtent?: ProjectionExtent;
  showCell?: boolean; showNumbers?: boolean; onSelect?: (id: number)=>void;
  onRotate?: (matrix: Matrix3)=>void; onReset?: ()=>void; onZoom?: (factor: number)=>void;
  markers?: CrystalMarker[];
}) {
  const svg = useRef<SVGSVGElement>(null);
  const clipId = useId().replace(/:/g,'');
  const [size,setSize] = useState<[number,number]>([640,440]);
  const gesture=useRef<{id:number;x:number;y:number;matrix:Matrix3;atom:number|null;moved:boolean}|null>(null);
  useEffect(()=>{
    if(compact || !svg.current) return;
    const element=svg.current;
    const update=()=>{const r=element.getBoundingClientRect(); if(r.width>0&&r.height>0) setSize([r.width,r.height]);};
    update(); const observer=new ResizeObserver(update); observer.observe(element); return ()=>observer.disconnect();
  },[compact]);
  useEffect(()=>{
    if(compact||!svg.current)return;
    const element=svg.current;
    // React's delegated wheel listener is passive; a local listener can consume scroll.
    const wheel=(event: WheelEvent)=>{
      event.preventDefault();event.stopPropagation();
      onZoom?.(wheelZoomFactor(event.deltaY,event.deltaMode,element.getBoundingClientRect().height));
    };
    element.addEventListener('wheel',wheel,{passive:false});
    return ()=>element.removeEventListener('wheel',wheel);
  },[compact,onZoom]);
  const [width,height] = compact ? [120,120] : size;
  const corners=cellCorners(data.basis_cartesian_angstrom);
  const center=corners[7].map(v=>v/2) as Vec3;
  const view=(v: Vec3)=>project(matrix,v.map((value,i)=>value-center[i]) as Vec3);
  const projectedCorners=corners.map(view);
  const atoms=data.sites.map(site=>({...site,view:view(site.cartesian_angstrom)}));
  const projectedMarkers=markers.filter(marker=>marker.cartesian.length===3&&marker.cartesian.every(Number.isFinite)).map(marker=>({...marker,view:view(marker.cartesian)}));
  const xy=compact ? projectionLayout([...projectedCorners,...atoms.map(site=>site.view),...projectedMarkers.map(marker=>marker.view)],width,height,true) : centeredProjectionLayout(fitExtent!,width,height,zoom);
  const selectedSite=atoms.find(site=>site.id===selected);
  const down=(event: ReactPointerEvent<SVGSVGElement>)=>{
    if(compact||event.button!==0)return;
    const hit=(event.target as Element).closest('[data-atom-id]');
    event.currentTarget.focus(); event.currentTarget.setPointerCapture?.(event.pointerId);
    gesture.current={id:event.pointerId,x:event.clientX,y:event.clientY,matrix,atom:hit?Number(hit.getAttribute('data-atom-id')):null,moved:false};
    event.preventDefault();
  };
  const move=(event: ReactPointerEvent<SVGSVGElement>)=>{
    const start=gesture.current; if(!start||start.id!==event.pointerId)return;
    const dx=event.clientX-start.x,dy=event.clientY-start.y;
    if(Math.hypot(dx,dy)<5&&!start.moved)return;
    start.moved=true;onRotate?.(rotate(start.matrix,dx*.008,dy*.008));event.preventDefault();
  };
  const finish=(event: ReactPointerEvent<SVGSVGElement>)=>{
    const start=gesture.current;if(!start||start.id!==event.pointerId)return;
    gesture.current=null;
    if(event.currentTarget.hasPointerCapture?.(event.pointerId))event.currentTarget.releasePointerCapture(event.pointerId);
    if(event.type==='pointerup'&&!start.moved&&start.atom!==null)onSelect?.(start.atom);
  };
  const plot=<>
    {(compact||showCell)&&<g className="cv-cell-edges">{CELL_EDGES.map(([a,b])=>{const p=xy(projectedCorners[a]),q=xy(projectedCorners[b]);return <path key={`${a}-${b}`} d={`M${p[0]} ${p[1]}L${q[0]} ${q[1]}`} />;})}</g>}
    <g className="cv-atoms">{atoms.toSorted((a,b)=>a.view[2]-b.view[2]).map(site=>{const p=xy(site.view);return <circle key={site.id} cx={p[0]} cy={p[1]} r={compact?3:5} fill={elementColor(site.element)} data-atom-id={compact?undefined:site.id} data-element={site.element} className="cv-atom"><title>{site.id} · {site.element}</title></circle>;})}</g>
    <g className="cv-site-markers">{projectedMarkers.map(marker=>{const p=xy(marker.view);return <g key={marker.id} data-site-marker={marker.id}><circle cx={p[0]} cy={p[1]} r={compact?4:marker.selected?10:7} fill={marker.selected?'var(--cv-primary)':'var(--cv-bg)'} stroke="var(--cv-primary)" strokeWidth={2}/>{!compact&&<text x={p[0]+10} y={p[1]-9} fill="var(--cv-primary)" fontSize={11}>{marker.label}</text>}<title>{marker.label} · 几何位点</title></g>;})}</g>
    {(compact||showCell)&&<g className="cv-direct-axes">{data.basis_cartesian_angstrom.map((vector,i)=>{
      const p=xy(view([0,0,0])),q=xy(view(vector)),dx=q[0]-p[0],dy=q[1]-p[1],distance=Math.hypot(dx,dy);
      if(distance<.5)return null;
      const x=Math.max(10,Math.min(width-10,q[0]+dx/distance*7)),y=Math.max(10,Math.min(height-10,q[1]+dy/distance*7));
      return <g key={i} className={`cv-axis-${'abc'[i]}`}><path d={`M${p[0]} ${p[1]}L${q[0]} ${q[1]}`} /><text x={x} y={y} textAnchor="middle" dominantBaseline="middle">{'abc'[i]}</text></g>;
    })}</g>}
    {!compact&&showNumbers&&<g className="cv-numbers">{atoms.map(site=>{const p=xy(site.view);return <text key={site.id} x={p[0]+8} y={p[1]-7} data-atom-id={site.id}>{site.id}</text>;})}</g>}
    {!compact&&selectedSite&&<circle cx={xy(selectedSite.view)[0]} cy={xy(selectedSite.view)[1]} r={10} className="cv-selected" data-atom-id={selectedSite.id} />}
  </>;
  return <svg ref={svg} className={compact?'cv-thumbnail':'cv-detail-plot'} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={compact?`${data.formula} 实际晶胞固定缩略图，a 红色、b 绿色、c 蓝色`:'实际晶胞与原子。拖动旋转、点击原子；滚轮缩放，方向键旋转，Home复位，加减键缩放。'} tabIndex={compact?undefined:0}
    onPointerDown={compact?undefined:down} onPointerMove={compact?undefined:move} onPointerUp={compact?undefined:finish} onPointerCancel={compact?undefined:finish} onLostPointerCapture={compact?undefined:()=>{gesture.current=null;}}
    onKeyDown={compact?undefined:event=>{
      const angles: Record<string,[number,number]>={ArrowLeft:[-.14,0],ArrowRight:[.14,0],ArrowUp:[0,-.14],ArrowDown:[0,.14]};
      if(angles[event.key]){event.preventDefault();onRotate?.(rotate(matrix,...angles[event.key]));}
      if(event.key==='Home'){event.preventDefault();onReset?.();}
      if(event.key==='+'||event.key==='='){event.preventDefault();onZoom?.(1.2);}
      if(event.key==='-'){event.preventDefault();onZoom?.(1/1.2);}
    }}>
    <title>{data.formula} · {data.atom_count} 个原子 · 只读正交投影</title>
    {!compact&&<defs><clipPath id={clipId}><rect x={0} y={40} width={width} height={height-124} /></clipPath></defs>}
    <g clipPath={compact?undefined:`url(#${clipId})`}>{plot}</g>
    {!compact&&<g className="cv-axis-indicator">{data.basis_cartesian_angstrom.map((v,i)=>{
      const p=project(matrix,unit(v)),visible=Math.hypot(p[0],p[1]),name='abc'[i],x=60+p[0]*31,y=height-55-p[1]*31;
      return <g key={i} className={`cv-axis-${name}`}>{visible<.02?<><circle cx={width-84} cy={height-43} r={6}/>{p[2]>0?<circle cx={width-84} cy={height-43} r={1.5}/>:<path d={`M${width-87} ${height-46}l6 6m-6 0 6-6`}/>}<text x={width-73} y={height-39}>{name}</text></>:<><path d={`M60 ${height-55}L${x} ${y}`}/><text x={x+p[0]/visible*12} y={y-p[1]/visible*12} textAnchor="middle" dominantBaseline="middle">{name}</text></>}</g>;
    })}<text x={112} y={height-22} className="cv-indicator-label">晶格轴方向 · 单位向量</text></g>}
  </svg>;
}

interface Cameras { standard: Matrix3; axes: [Matrix3,Matrix3,Matrix3] }
function LoadedViewer({data,cameras,markers=[]}: {data:StructureGeometry;cameras:Cameras;markers?:CrystalMarker[]}) {
  const {token}=theme.useToken();
  const standard=cameras.standard;
  const [matrix,setMatrix]=useState(standard),[mode,setMode]=useState<Mode>('cell'),[zoom,setZoom]=useState(1);
  const fit=(camera:Matrix3)=>{
    const corners=cellCorners(data.basis_cartesian_angstrom),center=corners[7].map(v=>v/2) as Vec3;
    const points=[...corners,...data.sites.map(site=>site.cartesian_angstrom),...markers.filter(marker=>marker.cartesian.length===3&&marker.cartesian.every(Number.isFinite)).map(marker=>marker.cartesian)];
    return projectionExtent(points.map(point=>project(camera,point.map((value,i)=>value-center[i]) as Vec3)));
  };
  const [fitExtent,setFitExtent]=useState(()=>fit(standard));
  const [selected,setSelected]=useState<number|null>(null),[showCell,setShowCell]=useState(true),[showNumbers,setShowNumbers]=useState(false),[open,setOpen]=useState(false);
  const opener=useRef<HTMLButtonElement>(null);
  const hasOpened=useRef(false);
  const dark=token.colorBgContainer.toLowerCase()!=='#ffffff'&&token.colorBgContainer.toLowerCase()!=='#fff';
  const variables={'--cv-bg':token.colorBgContainer,'--cv-raised':token.colorBgElevated,'--cv-text':token.colorText,'--cv-muted':token.colorTextSecondary,'--cv-border':token.colorBorder,'--cv-primary':token.colorPrimary,
    '--cv-a':dark?'#E69A94':'#A5443F','--cv-b':dark?'#8BBC9F':'#267451','--cv-c':dark?'#8FB9F3':'#356EBD'} as CSSProperties;
  const elements=Array.from(new Set(data.sites.map(site=>site.element)));
  const chosen=data.sites.find(site=>site.id===selected);
  const preset=(next:'cell'|'a'|'b'|'c')=>{setMode(next);setMatrix(next==='cell'?standard:cameras.axes['abc'.indexOf(next)]);};
  const reset=()=>{preset('cell');setFitExtent(fit(standard));setZoom(1);};
  const fitCurrent=()=>{setFitExtent(fit(matrix));setZoom(1);};
  const changeZoom=(factor:number)=>setZoom(value=>zoomByFactor(value,factor));
  const openDetail=()=>{if(!hasOpened.current){setFitExtent(fit(standard));hasOpened.current=true;}setOpen(true);};
  const elementLegend=<div className="cv-element-legend" aria-label="元素颜色">{elements.map(element=><span key={element}><i style={{background:elementColor(element)}}/>{element}</span>)}</div>;
  const outside=data.sites.some(site=>site.fractional.some(value=>value<0||value>=1));
  return <div className="crystal-viewer" style={variables}>
    <div className="cv-card"><CrystalScene data={data} matrix={standard} compact markers={markers}/><div><strong>实际晶胞</strong><span>{data.atom_count} 个原子 · 只读{markers.length?` · ${markers.length} 个几何位点`:''}</span><span className="cv-axis-legend"><b className="cv-axis-a">a</b><b className="cv-axis-b">b</b><b className="cv-axis-c">c</b> 晶胞棱</span></div></div>
    <Button ref={opener} block onClick={openDetail}>{markers.length?'查看结构与位点':'查看结构'}</Button>
    <Modal open={open} onCancel={()=>setOpen(false)} afterClose={()=>opener.current?.focus()} title={`${data.formula} · 结构检查`} width={1040} style={{top:24,maxWidth:'calc(100vw - 32px)'}} className="crystal-dialog" footer={null} keyboard destroyOnHidden>
      <div className="crystal-viewer cv-inspector" style={variables}>
        <div className="cv-visual-column">
          <div className="cv-controls" aria-label="结构视角">{(['cell','a','b','c'] as const).map(value=><Button key={value} size="small" aria-pressed={mode===value} onClick={()=>preset(value)}>{value==='cell'?'标准视角':`沿 ${value}`}</Button>)}<Button size="small" onClick={reset}>复位</Button></div>
          <div className="cv-plot-wrap">{elementLegend}<CrystalScene data={data} matrix={matrix} zoom={zoom} fitExtent={fitExtent} selected={selected} showCell={showCell} showNumbers={showNumbers} markers={markers} onSelect={setSelected} onRotate={next=>{setMatrix(next);setMode('custom');}} onReset={reset} onZoom={changeZoom}/></div>
          <div className="cv-controls"><Button aria-label="缩小结构" size="small" disabled={zoom<=MIN_CRYSTAL_ZOOM} onClick={()=>changeZoom(1/1.2)}>−</Button><span aria-label="结构缩放">{Math.round(zoom*100)}%</span><Button aria-label="放大结构" size="small" disabled={zoom>=MAX_CRYSTAL_ZOOM} onClick={()=>changeZoom(1.2)}>+</Button><Button size="small" onClick={fitCurrent}>适应</Button><Checkbox checked={showCell} onChange={event=>setShowCell(event.target.checked)}>显示晶胞</Checkbox><Checkbox checked={showNumbers} onChange={event=>setShowNumbers(event.target.checked)}>原子编号</Checkbox></div>
          <p className="cv-note">{descriptions[mode]} · {mode==='cell'?'依据实际晶格，c 向上、b 向右。':mode==='custom'?'原子、晶胞和轴同步旋转。':`从 +${mode} 端看向原点；沿轴投影中原子可能重叠。`} ⊙ 正轴朝向观察者，⊗ 背向。</p>
          <p className="cv-note">拖动或方向键旋转；滚轮或加减键缩放；适应保留当前朝向，Home 复位。原子圆点为标记，不代表真实半径；未判断化学键。{outside?'原始分数坐标含晶胞外位置，按原值显示并纳入适应范围。':''}</p>
        </div>
        <aside className="cv-info-column" aria-label="原子与晶胞信息">
          <h4>晶胞与来源</h4><p>{data.atom_count} 原子 · {data.coordinate_mode==='direct'?'Direct':'Cartesian'} 输入</p>
          <dl><div><dt>a / b / c · Å</dt><dd>{[data.lattice.a,data.lattice.b,data.lattice.c].map(v=>v.toFixed(4)).join(' / ')}</dd></div><div><dt>α / β / γ · °</dt><dd>{[data.lattice.alpha,data.lattice.beta,data.lattice.gamma].map(v=>v.toFixed(3)).join(' / ')}</dd></div><div><dt>体积 · Å³</dt><dd>{data.lattice.volume.toFixed(4)}</dd></div></dl>
          <label className="cv-atom-picker">选择原子<select aria-label="选择原子" value={selected??''} onChange={event=>setSelected(event.target.value===''?null:Number(event.target.value))}><option value="">选择一个原子</option>{data.sites.map(site=><option key={site.id} value={site.id}>{site.id} · {site.element}</option>)}</select></label>
          {chosen?<div className="cv-selected-info"><h4>原子 {chosen.id} · {chosen.element}</h4><dl><div><dt>分数坐标 · 原值</dt><dd>{chosen.fractional.map(format).join(' / ')}</dd></div><div><dt>笛卡尔坐标 · Å</dt><dd>{chosen.cartesian_angstrom.map(format).join(' / ')}</dd></div>{chosen.selective_flags&&<div><dt>Selective flags · a / b / c 方向</dt><dd>{chosen.selective_flags.map(v=>v?'T':'F').join(' / ')}</dd></div>}</dl><p className="cv-note">笛卡尔坐标属于当前晶胞基底，未旋转；与屏幕坐标无关。</p></div>:<p className="cv-note">点击圆点或从列表选择原子，查看稳定编号与坐标。</p>}
          <div className="cv-source"><strong>{data.source.material_id?`Materials Project · ${data.source.material_id}`:data.source.file_name}</strong><span>生成所用 POSCAR · SHA-256</span><code>{data.source.poscar_sha256}</code><span>几何数据 · SHA-256</span><code>{data.geometry_sha256}</code><p className="cv-note">坐标直接来自已保存的 POSCAR。CIF 哈希为转换后的 POSCAR 哈希。</p></div>
        </aside>
        <div className="cv-sr-only" role="status" aria-live="polite">{descriptions[mode]}，缩放 {Math.round(zoom*100)}%。{chosen?`已选择原子 ${chosen.id} ${chosen.element}。`:''}</div>
      </div>
    </Modal>
  </div>;
}

export function CrystalGeometryViewer({data,markers=[]}: {data:StructureGeometry;markers?:CrystalMarker[]}) {
  // Valid generation inputs can be too ill-conditioned for a dependable camera.
  // Scope all standard/axis math failures to the optional viewer, not workflow.
  const cameras=useMemo<Cameras|null>(()=>{
    try {
      if(!data.sites?.length||!data.sites.every(site=>site.fractional?.length===3&&site.cartesian_angstrom?.length===3&&[...site.fractional,...site.cartesian_angstrom].every(Number.isFinite))||
         data.basis_cartesian_angstrom?.length!==3||data.basis_cartesian_angstrom.some(v=>v.length!==3||!v.every(Number.isFinite)||norm(v)<1e-8))return null;
      return {standard:standardCrystalOrientation(data.basis_cartesian_angstrom),axes:[alongAxis(data.basis_cartesian_angstrom,0),alongAxis(data.basis_cartesian_angstrom,1),alongAxis(data.basis_cartesian_angstrom,2)]};
    } catch { return null; }
  },[data]);
  if(!cameras)return <Alert type="warning" showIcon title="当前晶格无法可靠显示" description="晶格方向或坐标无法可靠投影。"/>;
  const geometryKey=JSON.stringify([data.structure_id,data.basis_cartesian_angstrom,data.sites.map(site=>[site.id,site.element,site.cartesian_angstrom])]);
  return <LoadedViewer key={geometryKey} data={data} cameras={cameras} markers={markers}/>;
}

export default function CrystalViewer({structureId}: {structureId:string}) {
  const query=useStructureGeometry(structureId);
  if(query.isPending)return <div className="cv-loading"><Spin size="small"/> 正在读取结构坐标</div>;
  if(query.isError)return <Alert type="warning" showIcon title="结构查看暂不可用" description={<><p>{query.error.message}</p><Button size="small" onClick={()=>query.refetch()}>重试结构查看</Button><p>原摘要和工作流操作仍可使用。</p></>}/>;
  if(!query.data)return null;
  return <CrystalGeometryViewer data={query.data}/>;
}
