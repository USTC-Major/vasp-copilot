import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ConfigProvider, theme } from 'antd';
import { http, HttpResponse } from 'msw';
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';
import CrystalViewer, { CrystalGeometryViewer } from './CrystalViewer';
import { server } from '../../mocks/server';
import type { Matrix3, StructureGeometry } from '../../types/structure-geometry';
import { alongAxis, project, rotate, standardCrystalOrientation } from './crystalMath';

const geometry: StructureGeometry={structure_id:'str_actual_a',formula:'SiOFe',atom_count:3,basis_cartesian_angstrom:[[4,0,0],[-2,3.4641016151377544,0],[0,0,8]],lattice:{a:4,b:4,c:8,alpha:90,beta:90,gamma:120,volume:110.85125168440814,matrix:[[4,0,0],[-2,3.4641016151377544,0],[0,0,8]]},coordinate_mode:'direct',selective_dynamics:true,selective_flags_basis:'direct_lattice_vectors',sites:[
  {id:1,element:'Si',fractional:[1.2,-.2,.3],cartesian_angstrom:[5.2,-.6928203230275509,2.4],selective_flags:[true,false,true]},
  {id:2,element:'O',fractional:[.2,.3,.4],cartesian_angstrom:[.2,1.0392304845413263,3.2]},
  {id:3,element:'Fe',fractional:[.4,.5,.6],cartesian_angstrom:[.6,1.7320508075688772,4.8]},
],source:{format:'poscar',file_name:'actual-upload.POSCAR',material_id:null,coordinate_source:'StructureSummary.poscar_text',poscar_sha256:'a'.repeat(64)},geometry_sha256:'b'.repeat(64)};

beforeAll(()=>{
  class Pointer extends MouseEvent { pointerId: number; constructor(type:string,init:PointerEventInit={}){super(type,init);this.pointerId=init.pointerId??1;} }
  vi.stubGlobal('PointerEvent',Pointer);
});
afterAll(()=>vi.unstubAllGlobals());
function mount(id=geometry.structure_id){
  const client=new QueryClient({defaultOptions:{queries:{retry:false}}});
  const view=(structureId:string)=><QueryClientProvider client={client}><ConfigProvider theme={{algorithm:theme.darkAlgorithm,token:{motion:false}}}><CrystalViewer structureId={structureId}/></ConfigProvider></QueryClientProvider>;
  const rendered=render(view(id));return {...rendered,client,view};
}
function screenPoint(plot: HTMLElement,id:number) {
  const atom=plot.querySelector(`.cv-atom[data-atom-id="${id}"]`)!;
  return [Number(atom.getAttribute('cx')),Number(atom.getAttribute('cy'))];
}
function screenScale(plot: HTMLElement,camera:Matrix3) {
  const edges=Array.from(plot.querySelectorAll('.cv-cell-edges path')).slice(0,3);
  const scales=edges.flatMap((edge,i)=>{
    const [x,y,u,v]=edge.getAttribute('d')!.match(/[-+]?(?:\d*\.?\d+)(?:e[-+]?\d+)?/gi)!.map(Number);
    const vector=project(camera,geometry.basis_cartesian_angstrom[i]),length=Math.hypot(vector[0],vector[1]);
    return length>1e-8?[Math.hypot(u-x,v-y)/length]:[];
  });
  for(const scale of scales)expect(scale).toBeCloseTo(scales[0],10);
  return scales[0];
}
const geometryWithCenter: StructureGeometry={...geometry,atom_count:4,sites:[...geometry.sites,{id:4,element:'Si',fractional:[.5,.5,.5],cartesian_angstrom:[1,Math.sqrt(3),4]}]};
describe('real structure inspector interactions',()=>{
  it('holds physical scale and the crystal center during drag, keys and presets, while fit and reset remain distinct',async()=>{
    render(<ConfigProvider theme={{token:{motion:false}}}><CrystalGeometryViewer data={geometryWithCenter}/></ConfigProvider>);
    fireEvent.click(screen.getByRole('button',{name:'查看结构'}));const dialog=await screen.findByRole('dialog');
    const plot=within(dialog).getByRole('img',{name:/实际晶胞与原子/}),thumbnail=screen.getByRole('img',{name:/固定缩略图/});
    const fixed=thumbnail.innerHTML,standard=standardCrystalOrientation(geometry.basis_cartesian_angstrom);
    const initialScale=screenScale(plot,standard),center=screenPoint(plot,4);
    fireEvent.click(within(dialog).getByLabelText('放大结构'));
    expect(screenScale(plot,standard)).toBeCloseTo(initialScale*1.2,10);
    fireEvent.pointerDown(plot,{pointerId:1,button:0,clientX:100,clientY:100});
    fireEvent.pointerMove(plot,{pointerId:1,clientX:142,clientY:129});fireEvent.pointerUp(plot,{pointerId:1});
    let camera=rotate(standard,42*.008,29*.008);
    expect(screenScale(plot,camera)).toBeCloseTo(initialScale*1.2,10);
    fireEvent.keyDown(plot,{key:'ArrowRight'});camera=rotate(camera,.14,0);
    expect(screenScale(plot,camera)).toBeCloseTo(initialScale*1.2,10);expect(screenPoint(plot,4)).toEqual(center);
    for(const axis of [0,1,2] as const){
      fireEvent.click(within(dialog).getByRole('button',{name:`沿 ${'abc'[axis]}`}));
      expect(screenScale(plot,alongAxis(geometry.basis_cartesian_angstrom,axis))).toBeCloseTo(initialScale*1.2,10);
      expect(screenPoint(plot,4)).toEqual(center);expect(within(dialog).getByLabelText('结构缩放')).toHaveTextContent('120%');
    }
    fireEvent.click(within(dialog).getByRole('button',{name:'标准视角'}));
    expect(screenScale(plot,standard)).toBeCloseTo(initialScale*1.2,10);
    fireEvent.click(within(dialog).getByRole('button',{name:'沿 c'}));
    fireEvent.click(within(dialog).getByRole('button',{name:/适\s*应/}));
    expect(within(dialog).getByRole('button',{name:'沿 c'})).toHaveAttribute('aria-pressed','true');
    expect(within(dialog).getByLabelText('结构缩放')).toHaveTextContent('100%');
    camera=alongAxis(geometry.basis_cartesian_angstrom,2);const fittedScale=screenScale(plot,camera);
    expect(fittedScale).not.toBeCloseTo(initialScale,5);expect(screenPoint(plot,4)).toEqual(center);
    fireEvent.keyDown(plot,{key:'ArrowUp'});camera=rotate(camera,0,-.14);
    expect(screenScale(plot,camera)).toBeCloseTo(fittedScale,10);expect(screenPoint(plot,4)).toEqual(center);
    fireEvent.keyDown(plot,{key:'Home'});
    expect(screenScale(plot,standard)).toBeCloseTo(initialScale,10);expect(screenPoint(plot,4)).toEqual(center);
    expect(thumbnail.innerHTML).toBe(fixed);
  });
  it('consumes only canvas wheel events, shares button/key limits, and retains zoom through marker selection and reopening',async()=>{
    const saved=JSON.stringify(geometry),outerWheel=vi.fn();
    const view=(selected:boolean)=><ConfigProvider theme={{token:{motion:false}}}><div onWheel={outerWheel}><CrystalGeometryViewer data={structuredClone(geometry)} markers={[{id:'site-1',label:'S1',cartesian:[20,-8,14],selected}]}/></div></ConfigProvider>;
    const rendered=render(view(false));fireEvent.click(screen.getByRole('button',{name:'查看结构与位点'}));
    const dialog=await screen.findByRole('dialog'),plot=within(dialog).getByRole('img',{name:/实际晶胞与原子/});
    const wheel=(deltaY:number,deltaMode=0)=>{
      const event=new WheelEvent('wheel',{bubbles:true,cancelable:true,deltaY,deltaMode});
      fireEvent(plot,event);expect(event.defaultPrevented).toBe(true);
    };
    wheel(-Math.log(1.2)/.0015);expect(within(dialog).getByLabelText('结构缩放')).toHaveTextContent('120%');
    fireEvent.keyDown(plot,{key:'='});expect(within(dialog).getByLabelText('结构缩放')).toHaveTextContent('144%');
    fireEvent.keyDown(plot,{key:'-'});fireEvent.click(within(dialog).getByLabelText('缩小结构'));
    expect(within(dialog).getByLabelText('结构缩放')).toHaveTextContent('100%');
    wheel(-Math.log(1.2)/(.0015*16),1);expect(within(dialog).getByLabelText('结构缩放')).toHaveTextContent('120%');
    const position=screenPoint(plot,1);rendered.rerender(view(true));
    expect(screenPoint(plot,1)).toEqual(position);expect(within(dialog).getByLabelText('结构缩放')).toHaveTextContent('120%');
    fireEvent.keyDown(plot,{key:'Escape',code:'Escape',keyCode:27});await waitFor(()=>expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole('button',{name:'查看结构与位点'}));const reopened=await screen.findByRole('dialog');
    const reopenedPlot=within(reopened).getByRole('img',{name:/实际晶胞与原子/});
    expect(screenPoint(reopenedPlot,1)).toEqual(position);expect(within(reopened).getByLabelText('结构缩放')).toHaveTextContent('120%');
    for(let i=0;i<12;i++)fireEvent.wheel(reopenedPlot,{deltaY:-1e5});
    expect(within(reopened).getByLabelText('结构缩放')).toHaveTextContent('250%');expect(within(reopened).getByLabelText('放大结构')).toBeDisabled();
    for(let i=0;i<12;i++)fireEvent.wheel(reopenedPlot,{deltaY:1e5});
    expect(within(reopened).getByLabelText('结构缩放')).toHaveTextContent('60%');expect(within(reopened).getByLabelText('缩小结构')).toBeDisabled();
    expect(outerWheel).not.toHaveBeenCalled();
    fireEvent.wheel(within(reopened).getByLabelText('选择原子'),{deltaY:40});expect(outerWheel).toHaveBeenCalledTimes(1);
    expect(JSON.stringify(geometry)).toBe(saved);
  });
  it('responds to resize using the captured fit and resets cameras for changed geometry under the same structure ID',async()=>{
    let width=640,notify:ResizeObserverCallback|undefined;
    const originalObserver=globalThis.ResizeObserver;
    class Observer {constructor(callback:ResizeObserverCallback){notify=callback;}observe(){}disconnect(){}unobserve(){}}
    vi.stubGlobal('ResizeObserver',Observer);
    const bounds=vi.spyOn(SVGElement.prototype,'getBoundingClientRect').mockImplementation(()=>({width,height:440} as DOMRect));
    try{
      const view=(data:StructureGeometry)=><ConfigProvider theme={{token:{motion:false}}}><CrystalGeometryViewer data={data} markers={[{id:'far',label:'远位点',cartesian:[30,2,4]}]}/></ConfigProvider>;
      const rendered=render(view(geometryWithCenter));fireEvent.click(screen.getByRole('button',{name:'查看结构与位点'}));
      const dialog=await screen.findByRole('dialog'),plot=within(dialog).getByRole('img',{name:/实际晶胞与原子/});
      fireEvent.click(within(dialog).getByLabelText('放大结构'));
      const standard=standardCrystalOrientation(geometry.basis_cartesian_angstrom),scale=screenScale(plot,standard);
      fireEvent.keyDown(plot,{key:'ArrowRight'});const camera=rotate(standard,.14,0);
      width=298;act(()=>notify?.([],{} as ResizeObserver));
      expect(screenScale(plot,camera)).toBeCloseTo(scale*(298-54)/(640-54),10);
      expect(screenPoint(plot,4)).toEqual([149,197.5]);expect(within(dialog).getByLabelText('结构缩放')).toHaveTextContent('120%');
      fireEvent.keyDown(plot,{key:'ArrowRight'});expect(screenScale(plot,rotate(camera,.14,0))).toBeCloseTo(scale*(298-54)/(640-54),10);
      const changed={...geometryWithCenter,basis_cartesian_angstrom:geometryWithCenter.basis_cartesian_angstrom.map(row=>row.map(v=>v*2)) as Matrix3,sites:geometryWithCenter.sites.map(site=>({...site,cartesian_angstrom:site.cartesian_angstrom.map(v=>v*2) as [number,number,number]}))};
      rendered.rerender(view(changed));await waitFor(()=>expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
      fireEvent.click(screen.getByRole('button',{name:'查看结构与位点'}));const nextDialog=await screen.findByRole('dialog');
      expect(within(nextDialog).getByLabelText('结构缩放')).toHaveTextContent('100%');
      expect(within(nextDialog).getByRole('button',{name:'标准视角'})).toHaveAttribute('aria-pressed','true');
      expect(screenPoint(within(nextDialog).getByRole('img',{name:/实际晶胞与原子/}),4)).toEqual([149,197.5]);
    }finally{bounds.mockRestore();vi.stubGlobal('ResizeObserver',originalObserver);}
  });
  it('adds optional spatial site markers without changing atom data or default inspector behavior', async () => {
    const data = structuredClone(geometry), saved = JSON.stringify(data);
    render(<ConfigProvider theme={{ token: { motion: false } }}><CrystalGeometryViewer data={data} markers={[{ id: 'site-1', label: 'S1', cartesian: [1, 2, 5], selected: true }]} /></ConfigProvider>);
    fireEvent.click(screen.getByRole('button', { name: '查看结构与位点' }));
    const dialog = await screen.findByRole('dialog');
    expect(dialog.querySelector('[data-site-marker="site-1"]')).toBeInTheDocument();
    expect(dialog.querySelectorAll('.cv-atom')).toHaveLength(3); expect(JSON.stringify(data)).toBe(saved);
  });
  it('keeps fixed thumbnail independent, distinguishes stable atom clicks from drags and restores focus',async()=>{
    server.use(http.get('/api/v1/structure/:id/geometry',()=>HttpResponse.json({data:geometry})));
    const user=userEvent.setup();const rendered=mount();
    const opener=await screen.findByRole('button',{name:'查看结构'});
    const thumbnail=screen.getByRole('img',{name:/固定缩略图/});const fixed=thumbnail.innerHTML;
    expect(thumbnail).not.toHaveAttribute('tabindex');
    await user.click(opener);const dialog=await screen.findByRole('dialog');
    const plot=within(dialog).getByRole('img',{name:/实际晶胞与原子/});
    for(const selector of ['.cv-cell-edges','.cv-direct-axes','.cv-axis-indicator']) {
      expect(getComputedStyle(plot.querySelector(selector)!).pointerEvents).toBe('none');
    }
    const initial=plot.innerHTML;
    const hit=plot.querySelector('[data-atom-id="3"]')!;
    fireEvent.pointerDown(hit,{pointerId:1,button:0,clientX:100,clientY:100});fireEvent.pointerUp(plot,{pointerId:1,button:0,clientX:100,clientY:100});
    expect(await within(dialog).findByRole('heading',{name:'原子 3 · Fe'})).toBeInTheDocument();
    const different=plot.querySelector('[data-atom-id="1"]')!;
    fireEvent.pointerDown(different,{pointerId:2,button:0,clientX:100,clientY:100});fireEvent.pointerMove(plot,{pointerId:2,button:0,clientX:130,clientY:118});fireEvent.pointerUp(plot,{pointerId:2,button:0,clientX:130,clientY:118});
    expect(within(dialog).getByRole('heading',{name:'原子 3 · Fe'})).toBeInTheDocument();
    expect(thumbnail.innerHTML).toBe(fixed);
    const first=plot.querySelector('[data-atom-id="1"]')!;
    fireEvent.pointerDown(first,{pointerId:3,button:0,clientX:140,clientY:120});fireEvent.pointerUp(plot,{pointerId:3,button:0,clientX:140,clientY:120});
    expect(within(dialog).getByRole('heading',{name:'原子 1 · Si'})).toBeInTheDocument();
    expect(within(dialog).getByText('1.200000 / -0.200000 / 0.300000')).toBeInTheDocument();
    expect(within(dialog).getByText('5.200000 / -0.692820 / 2.400000')).toBeInTheDocument();
    expect(within(dialog).getByText('T / F / T')).toBeInTheDocument();
    await user.click(within(dialog).getByLabelText('放大结构'));expect(within(dialog).getByLabelText('结构缩放')).toHaveTextContent('120%');
    await user.click(within(dialog).getByRole('button',{name:/适\s*应/}));expect(within(dialog).getByLabelText('结构缩放')).toHaveTextContent('100%');
    await user.click(within(dialog).getByLabelText('原子编号'));expect(plot.querySelectorAll('.cv-numbers text')).toHaveLength(3);
    await user.click(within(dialog).getByLabelText('显示晶胞'));expect(plot.querySelectorAll('.cv-cell-edges path')).toHaveLength(0);expect(plot.querySelectorAll('.cv-atom')).toHaveLength(3);
    await user.click(within(dialog).getByRole('button',{name:'沿 b'}));expect(within(dialog).getByRole('button',{name:'沿 b'})).toHaveAttribute('aria-pressed','true');
    await user.click(within(dialog).getByRole('button',{name:/复\s*位/}));expect(within(dialog).getByRole('button',{name:/复\s*位/})).not.toHaveAttribute('aria-pressed');
    expect(within(dialog).getByRole('heading',{name:'原子 1 · Si'})).toBeInTheDocument();
    await user.click(within(dialog).getByLabelText('原子编号'));await user.click(within(dialog).getByLabelText('显示晶胞'));
    await user.selectOptions(within(dialog).getByLabelText('选择原子'),'');expect(plot.innerHTML).toBe(initial);expect(thumbnail.innerHTML).toBe(fixed);
    plot.focus();fireEvent.keyDown(plot,{key:'ArrowRight'});expect(plot.innerHTML).not.toBe(initial);fireEvent.keyDown(plot,{key:'Home'});expect(plot.innerHTML).toBe(initial);
    fireEvent.keyDown(plot,{key:'Escape',code:'Escape',keyCode:27});await waitFor(()=>expect(screen.queryByRole('dialog')).not.toBeInTheDocument());await waitFor(()=>expect(opener).toHaveFocus());
    rendered.client.clear();
  });
  it('separates structure IDs and fails without substituting a model, then retries',async()=>{
    let fail=true;
    server.use(http.get('/api/v1/structure/:id/geometry',({params})=>{
      if(params.id==='str_actual_a')return HttpResponse.json({data:geometry});
      if(fail)return HttpResponse.json({error:{code:'STRUCTURE_VIEW_ATOM_LIMIT',message:'只读结构查看最多支持 2048 个原子'}},{status:422});
      return HttpResponse.json({data:{...geometry,structure_id:'str_b',formula:'O3',sites:geometry.sites.map(site=>({...site,element:'O'}))}});
    }));
    const user=userEvent.setup(),rendered=mount();await screen.findByRole('button',{name:'查看结构'});
    rendered.rerender(rendered.view('str_b'));await screen.findByText('结构查看暂不可用');expect(screen.queryByRole('img',{name:/固定缩略图/})).not.toBeInTheDocument();
    expect(screen.getByText('原摘要和工作流操作仍可使用。')).toBeInTheDocument();
    fail=false;await user.click(screen.getByRole('button',{name:'重试结构查看'}));await screen.findByRole('img',{name:/O3 实际晶胞/});
    rendered.rerender(rendered.view('str_actual_a'));await screen.findByRole('img',{name:/SiOFe 实际晶胞/});rendered.client.clear();
  });
  it('contains camera failure for a valid extremely skewed scaled lattice and leaves workflow actions usable',async()=>{
    const actualBasis: StructureGeometry['basis_cartesian_angstrom']=[[0,1,0],[1,0,0],[1,0,1e-13]];
    server.use(http.get('/api/v1/structure/:id/geometry',()=>HttpResponse.json({data:{...geometry,basis_cartesian_angstrom:actualBasis}})));
    const client=new QueryClient({defaultOptions:{queries:{retry:false}}});
    let continued=false;
    render(<QueryClientProvider client={client}><p>Si · 实际结构摘要</p><CrystalViewer structureId="str_valid_skewed"/><button onClick={()=>{continued=true;}}>继续工作流</button></QueryClientProvider>);
    expect(await screen.findByText('当前晶格无法可靠显示')).toBeInTheDocument();
    expect(screen.getByText('Si · 实际结构摘要')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button',{name:'继续工作流'}));expect(continued).toBe(true);
    expect(screen.queryByRole('button',{name:'查看结构'})).not.toBeInTheDocument();client.clear();
  });
});
