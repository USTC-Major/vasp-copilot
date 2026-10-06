import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ConfigProvider, theme } from 'antd';
import { http, HttpResponse } from 'msw';
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';
import CrystalViewer from './CrystalViewer';
import { server } from '../../mocks/server';
import type { StructureGeometry } from '../../types/structure-geometry';

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
describe('real structure inspector interactions',()=>{
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
