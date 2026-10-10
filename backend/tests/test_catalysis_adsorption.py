"""CAT-03/04 focused synthetic geometry, persistence, API and export contracts."""
import copy
import io
import itertools
import json
from types import SimpleNamespace
import zipfile

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from pymatgen.core import Lattice, Structure
from pymatgen.io.vasp.inputs import Poscar

from backend.input_validation import validate_poscar
from backend.toolbox.api import router, error_handler
from backend.toolbox.contracts import ToolboxError
from backend.toolbox.catalysis import science, adsorption
from backend.toolbox.catalysis.schemas import (CreateDraft, BuildSurfaces, SetConstraints, PatchDraft,
    SetAdsorbate, FindAdsorptionSites, BuildAdsorptionCandidates, SelectAdsorptionCandidates,
    ExportAdsorptionCandidates, DraftResponse)
from backend.toolbox.catalysis.service import CatalysisService


def pt_surface(svc):
    draft = svc.create(CreateDraft(source={'kind': 'example', 'role': 'bulk'}))
    return svc.build(draft['draft_id'], BuildSurfaces(revision=draft['revision']))


def oblique_slab(svc, *, tilt=False):
    matrix = np.array([[2., 0, 0], [.5, 2, 0], [1, 2, 10]])
    coords = np.array([[0., 0, 9], [.5, .5, 0], [1, 1, 1], [1.5, 1.5, 0]])
    if tilt:
        c, s = np.cos(.4), np.sin(.4)
        rotation = np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])
        matrix, coords = matrix @ rotation.T, coords @ rotation.T
    structure = Structure(matrix, ['Pt', 'C', 'Pt', 'O'], coords, coords_are_cartesian=True,
                          site_properties={'selective_dynamics': [[False]*3, [True, True, False], [True]*3, [True, False, True]]})
    return svc.create(CreateDraft(source={'kind': 'poscar', 'role': 'slab', 'content': Poscar(structure).get_str(direct=False)}))


def prepare(svc, draft, *, source=None, anchor=0, kinds=None, manual=None):
    draft = svc.set_adsorbate(draft['draft_id'], SetAdsorbate(revision=draft['revision'], source=source or {'kind': 'co_example'}, anchor_index=anchor))
    return svc.find_adsorption_sites(draft['draft_id'], FindAdsorptionSites(revision=draft['revision'],
        kinds=['ontop', 'bridge', 'hollow'] if kinds is None else kinds, manual_sites=manual or []))


def generate(svc, draft, site_ids=None, **placement):
    ids = site_ids or [draft['adsorption']['sites'][0]['site_id']]
    return svc.build_adsorption_candidates(draft['draft_id'], BuildAdsorptionCandidates(revision=draft['revision'], site_ids=ids, placement=placement))


def independent_planar_distances(point, parent, normal):
    matrix = np.array(parent['lattice'])
    values = []
    for atom in parent['atoms']:
        for i, j in itertools.product(range(-2, 3), repeat=2):
            delta = point - np.array(atom['cartesian']) - i*matrix[0] - j*matrix[1]
            delta -= np.dot(delta, normal)*normal
            values.append(float(np.linalg.norm(delta)))
    return np.sort(values)


def test_pt111_sites_anchor_constraints_candidate_identity_and_restart(tmp_path):
    svc = CatalysisService(tmp_path)
    draft = pt_surface(svc)
    surface = svc.selected(draft)
    released = surface['surface']['layers'][0]['atom_ids'][0]
    draft = svc.constraints(draft['draft_id'], SetConstraints(revision=draft['revision'], bottom_fixed_layers=2, atom_overrides={released:'free'}))
    parent = copy.deepcopy(svc.selected(draft)['snapshot'])
    draft = prepare(svc, draft)
    sites = draft['adsorption']['sites']
    n = len(svc.selected(draft)['surface']['layers'][-1]['atom_ids'])
    # Independent fcc triangular mesh has N nodes, 3N edges and 2N faces.
    assert {kind:sum(s['kind']==kind for s in sites) for kind in ('ontop','bridge','hollow')} == {'ontop':n,'bridge':3*n,'hollow':2*n}
    normal = np.array(svc.selected(draft)['surface']['normal'])
    top_atoms = set(svc.selected(draft)['surface']['layers'][-1]['atom_ids'])
    top_snapshot = {**parent, 'atoms':[a for a in parent['atoms'] if a['atom_id'] in top_atoms]}
    nearest = 3.92/np.sqrt(2)
    # Complete independent reference: enumerate neighbours at the analytic
    # fcc nearest-neighbour spacing, then midpoints and equilateral triangles.
    # No Delaunay or AdsorbateSiteFinder is used for expected positions.
    matrix = np.array(parent['lattice'])
    inverse = np.linalg.inv(matrix)
    top_coords = [np.array(a['cartesian']) for a in top_snapshot['atoms']]
    expected = {kind: [] for kind in ('ontop', 'bridge', 'hollow')}
    def expected_add(kind, point):
        fractional = point @ inverse
        fractional[:2] -= np.floor(fractional[:2] + 1e-10)
        point = fractional @ matrix
        if not any(np.linalg.norm(point-old) < 1e-8 for old in expected[kind]):
            expected[kind].append(point)
    for point in top_coords:
        expected_add('ontop', point)
        neighbours = []
        for other in top_coords:
            for i,j in itertools.product(range(-2,3), repeat=2):
                neighbour = other+i*matrix[0]+j*matrix[1]
                if abs(np.linalg.norm(neighbour-point)-nearest) < 1e-8:
                    neighbours.append(neighbour)
                    expected_add('bridge', (point+neighbour)/2)
        assert len(neighbours)==6
        for a,b in itertools.combinations(neighbours,2):
            if abs(np.linalg.norm(a-b)-nearest) < 1e-8:
                expected_add('hollow', (point+a+b)/3)
    for kind, points in expected.items():
        actual = [np.array(s['cartesian']) for s in sites if s['kind']==kind]
        assert len(actual)==len(points)
        def match(a,b):
            return min(np.linalg.norm(a-b-i*matrix[0]-j*matrix[1]) for i,j in itertools.product(range(-2,3),repeat=2)) < 1e-8
        assert all(any(match(a,b) for b in points) for a in actual)
        assert all(any(match(a,b) for a in actual) for b in points)
    representatives = []
    for kind, coordination, distance in (('ontop',1,0),('bridge',2,nearest/2),('hollow',3,nearest/np.sqrt(3))):
        selected = next(s for s in sites if s['kind']==kind)
        distances = independent_planar_distances(np.array(selected['cartesian']), top_snapshot, normal)
        np.testing.assert_allclose(distances[:coordination], distance, atol=1e-9)
        representatives.append(selected)
    # All periodic positions within each kind differ by more than user dedup.
    for kind in ('ontop','bridge','hollow'):
        group = [s for s in sites if s['kind']==kind]
        lattice = Lattice([parent['lattice'][0], parent['lattice'][1], (normal*10000).tolist()])
        for a,b in itertools.combinations(group,2):
            delta = np.array(a['cartesian'])-b['cartesian']
            assert lattice.get_distance_and_image([0,0,0], lattice.get_fractional_coords(delta))[0] > .05
    draft = generate(svc, draft, [s['site_id'] for s in representatives])
    assert len(draft['adsorption']['candidates']) == 3
    new_ids = set()
    for candidate, site in zip(draft['adsorption']['candidates'], representatives):
        snap = candidate['snapshot']
        assert snap['atoms'][:-2] == parent['atoms']
        assert snap['lattice'] == parent['lattice']
        added = snap['atoms'][-2:]
        np.testing.assert_allclose(added[0]['cartesian'], np.array(site['cartesian'])+2*normal, atol=1e-12)
        np.testing.assert_allclose(np.array(added[1]['cartesian'])-added[0]['cartesian'], 1.15*normal, atol=1e-12)
        assert all(a['selective_dynamics']==[True]*3 for a in added)
        assert candidate['validation']['screening_passed']
        ids = {a['atom_id'] for a in added}
        assert new_ids.isdisjoint(ids)
        new_ids.update(ids)
        assert all(a['provenance']['source_id']==draft['adsorption']['adsorbate']['source_id'] for a in added)
    assert CatalysisService(tmp_path).get(draft['draft_id']) == draft
    assert draft['adsorption']['site_settings']['kinds'] == ['ontop','bridge','hollow']


def test_oblique_period_crossing_full_c_anchor_and_tilted_fixed_frame(tmp_path):
    svc = CatalysisService(tmp_path)
    draft = prepare(svc, oblique_slab(svc, tilt=True), source={'kind':'xyz','content':'2\nnonzero anchor\nC 4 3 2\nO 5 3 2\n','name':'分子 XYZ'}, anchor=1, kinds=['ontop'])
    parent = svc.selected(draft)['snapshot']
    original_top = parent['atoms'][2]
    site = draft['adsorption']['sites'][0]
    matrix = np.array(parent['lattice'])
    # Unwrapping the top original z=1 image requires full +c (z=11),
    # followed only by a/b images, not normal-only displacement.
    delta = np.array(site['cartesian'])-np.array(original_top['cartesian'])
    fractional_delta = Lattice(matrix).get_fractional_coords(delta)
    np.testing.assert_allclose(fractional_delta, np.rint(fractional_delta), atol=1e-10)
    assert round(fractional_delta[2]) == 1
    draft = generate(svc, draft, rotation_degrees=[0,0,90])
    candidate = draft['adsorption']['candidates'][0]
    added = candidate['snapshot']['atoms'][-2:]
    normal = np.array(svc.selected(draft)['surface']['normal'])
    e1 = matrix[0]/np.linalg.norm(matrix[0])
    e2 = np.cross(normal,e1)
    target = np.array(site['cartesian'])+2*normal
    # Explicit O anchor (index1), local C is -x, then Z90 -> -surface y.
    np.testing.assert_allclose(added[1]['cartesian'], target, atol=1e-10)
    np.testing.assert_allclose(added[0]['cartesian'], target-e2, atol=1e-10)
    assert candidate['snapshot']['atoms'][:-2] == parent['atoms']
    # Independent full 3D periodic image enumeration checks closest distance.
    distances = [np.linalg.norm(np.array(a['cartesian'])-(np.array(b['cartesian'])+np.array(image)@matrix))
                 for a in added for b in parent['atoms'] for image in itertools.product(range(-2,3),repeat=3)]
    assert candidate['validation']['minimum_adsorbate_surface_distance_angstrom'] == pytest.approx(min(distances),abs=1e-10)
    self_distances = [np.linalg.norm(np.array(a['cartesian'])-(np.array(b['cartesian'])+np.array(image)@matrix))
                     for a in added for b in added for image in itertools.product(range(-2,3),repeat=3) if image!=(0,0,0)]
    assert candidate['validation']['minimum_periodic_self_image_distance_angstrom'] == pytest.approx(min(self_distances),abs=1e-10)


def test_hard_overlap_batch_transaction_screen_warning_and_periodic_self(tmp_path):
    svc = CatalysisService(tmp_path)
    draft = prepare(svc, pt_surface(svc))
    draft = generate(svc,draft,screening_distance_angstrom=2.5)
    assert not draft['adsorption']['candidates'][0]['validation']['screening_passed']
    assert len(draft['adsorption']['candidates'][0]['validation']['warnings']) == 2
    before = copy.deepcopy(draft)
    sites = draft['adsorption']['sites']
    ids = [next(s['site_id'] for s in sites if s['kind']=='bridge'),next(s['site_id'] for s in sites if s['kind']=='ontop')]
    with pytest.raises(ToolboxError) as caught:
        generate(svc,draft,ids,height_angstrom=0)
    assert caught.value.code == 'CAT_ADSORPTION_OVERLAP'
    assert caught.value.payload()['details']['site_id'] == ids[1]
    assert caught.value.payload()['details']['published_count'] == 0
    assert ids[1] in str(caught.value)
    assert next(s['label'] for s in sites if s['site_id']==ids[1]) in str(caught.value)
    assert svc.get(draft['draft_id']) == before
    with pytest.raises(ToolboxError) as caught:
        generate(svc,draft,height_angstrom=100)
    assert caught.value.code == 'CAT_ADSORPTION_OUTSIDE_TOP_GAP'
    other = prepare(svc, oblique_slab(svc), source={'kind':'xyz','content':'2\nself image overlap\nC 0 0 0\nO 1.99995 0 0\n'}, kinds=['ontop'])
    with pytest.raises(ToolboxError) as caught:
        generate(svc,other)
    assert caught.value.code == 'CAT_ADSORPTION_OVERLAP'


def test_candidates_remain_valid_across_selection_revision_and_invalidate_upstream(tmp_path):
    svc = CatalysisService(tmp_path)
    draft = generate(svc,prepare(svc,pt_surface(svc)))
    candidate = copy.deepcopy(draft['adsorption']['candidates'][0])
    cid = candidate['candidate_id']
    draft = svc.patch(draft['draft_id'],PatchDraft(revision=draft['revision'],name='new name'))
    draft = svc.select_adsorption_candidates(draft['draft_id'],SelectAdsorptionCandidates(revision=draft['revision'],selected_candidate_ids=[cid]))
    assert draft['adsorption']['candidates'][0] == candidate
    assert svc.geometry(draft['draft_id'],draft['revision'],candidate_id=cid)['candidate_id'] == cid
    draft = svc.constraints(draft['draft_id'],SetConstraints(revision=draft['revision'],bottom_fixed_layers=0,reset_existing=True))
    assert draft['adsorption']['candidates'][0]['status']=='stale'
    assert not draft['adsorption']['sites'] and not draft['adsorption']['selected_candidate_ids']
    assert draft['adsorption']['site_settings'] is not None
    for action in (lambda:svc.geometry(draft['draft_id'],draft['revision'],candidate_id=cid),
                   lambda:svc.export_adsorption_candidates(draft['draft_id'],ExportAdsorptionCandidates(revision=draft['revision'],candidate_ids=[cid]))):
        with pytest.raises(ToolboxError) as caught: action()
        assert caught.value.code=='CAT_CANDIDATE_STALE'
    with pytest.raises(ToolboxError) as caught:
        svc.build_adsorption_candidates(draft['draft_id'],BuildAdsorptionCandidates(revision=draft['revision'],site_ids=[candidate['site_id']]))
    assert caught.value.code=='CAT_ADSORPTION_SITES_STALE'
    draft = prepare(svc,draft,kinds=['ontop'])
    draft = generate(svc,draft)
    assert all(a['selective_dynamics']==[True]*3 for a in draft['adsorption']['candidates'][0]['snapshot']['atoms'])
    current = draft['adsorption']['candidates'][0]['candidate_id']
    draft = svc.set_adsorbate(draft['draft_id'],SetAdsorbate(revision=draft['revision'],source={'kind':'atom','element':'H'},anchor_index=0))
    assert draft['adsorption']['candidates'][0]['candidate_id']==current
    assert draft['adsorption']['candidates'][0]['status']=='stale'
    # Choosing another termination also invalidates downstream source pinning.
    draft = prepare(svc,draft,kinds=['ontop'])
    draft = generate(svc,draft)
    second = next(s['surface_id'] for s in draft['surfaces'] if s['surface_id']!=draft['active_surface_id'])
    draft = svc.patch(draft['draft_id'],PatchDraft(revision=draft['revision'],active_surface_id=second))
    assert draft['adsorption']['candidates'][0]['status']=='stale'


def test_multiselected_zip_mixed_species_flags_rows_hashes_and_batch_replacement(tmp_path):
    svc = CatalysisService(tmp_path)
    draft = prepare(svc,oblique_slab(svc),kinds=['ontop'],manual=[{'label':'手动 中文','uv':[.2,.3]}])
    draft = generate(svc,draft,[s['site_id'] for s in draft['adsorption']['sites']],height_angstrom=2,rotation_degrees=[0,0,0])
    candidates = draft['adsorption']['candidates']
    assert len(candidates)==2
    ids = [c['candidate_id'] for c in candidates]
    draft = svc.select_adsorption_candidates(draft['draft_id'],SelectAdsorptionCandidates(revision=draft['revision'],selected_candidate_ids=[ids[1]]))
    assert CatalysisService(tmp_path).get(draft['draft_id'])['adsorption']['selected_candidate_ids']==[ids[1]]
    bundle = svc.export_adsorption_candidates(draft['draft_id'],ExportAdsorptionCandidates(revision=draft['revision'],candidate_ids=ids))
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        manifest=json.loads(archive.read('manifest.json'))
        assert manifest['candidate_ids']==ids and manifest['candidate_count']==2
        assert len(manifest['files'])==4
        for filename,entry in manifest['files'].items(): assert science.sha(archive.read(filename))==entry['sha256']
        for candidate in candidates:
            prefix=candidate['candidate_id']+'/'
            metadata=json.loads(archive.read(prefix+'metadata.json'))
            poscar=archive.read(prefix+'POSCAR')
            assert metadata['poscar_sha256']==science.sha(poscar)
            assert metadata['candidate']==candidate
            assert metadata['parent_surface']['snapshot']==svc.selected(draft)['snapshot']
            info=validate_poscar(poscar.decode(),include_coordinates=True)
            assert list(info.elements)==['Pt','C','O']
            assert [r['snapshot_index'] for r in metadata['poscar_row_mapping']]==[0,2,1,4,3,5]
            flags={a['atom_id']:a['selective_dynamics'] for a in candidate['snapshot']['atoms']}
            for row,flag in zip(metadata['poscar_row_mapping'],info.selective_flags): assert list(flag)==flags[row['atom_id']]
            parsed=Poscar.from_str(poscar.decode())
            for row,site in zip(metadata['poscar_row_mapping'],parsed.structure):
                np.testing.assert_allclose(site.coords,candidate['snapshot']['atoms'][row['snapshot_index']]['cartesian'],atol=1e-12)
    previous_revision=draft['revision']
    draft=generate(svc,draft,site_ids=[draft['adsorption']['sites'][0]['site_id']],height_angstrom=3)
    assert len(draft['adsorption']['candidates'])==1
    assert draft['adsorption']['candidates'][0]['candidate_id'] not in ids
    with pytest.raises(ToolboxError) as caught: svc.geometry(draft['draft_id'],draft['revision'],candidate_id=ids[0])
    assert caught.value.code=='CAT_CANDIDATE_NOT_FOUND'
    folder=svc._folder(draft['draft_id'])
    previous=json.loads(next(folder.glob(f'revision-{previous_revision:06d}-*.json')).read_text(encoding='utf-8'))
    assert previous['adsorption']['candidates']==candidates


def test_limits_reject_before_triangulation_and_old_draft_response_hash(tmp_path,monkeypatch):
    svc=CatalysisService(tmp_path)
    draft=oblique_slab(svc)
    assert 'adsorption' not in draft
    response=DraftResponse.model_validate({'draft':draft}).model_dump(mode='json',exclude_unset=True)
    assert response['draft']==draft
    for snap in [response['draft']['input_snapshot'],response['draft']['surfaces'][0]['snapshot']]:
        assert science.sha(science.canonical({k:v for k,v in snap.items() if k!='sha256'}))==snap['sha256']
        assert all('source_id' not in a['provenance'] for a in snap['atoms'])
    assert svc.get(draft['draft_id'])==draft
    for source,anchor in (({'kind':'atom','element':'Xx'},0),({'kind':'co_example'},2),
                          ({'kind':'xyz','content':'2\nframe\nC 0 0 0\nO nan 0 1\n'},0),
                          ({'kind':'xyz','content':'1\nx\nH 0 0 0\n1\ny\nH 0 0 0\n'},0),
                          ({'kind':'xyz','content':'129\nx\n'},0)):
        with pytest.raises(ToolboxError): svc.set_adsorbate(draft['draft_id'],SetAdsorbate(revision=draft['revision'],source=source,anchor_index=anchor))
        assert svc.get(draft['draft_id'])==draft
    for args in ({'height_angstrom':float('nan')},{'rotation_degrees':[0,0,float('inf')]},{'screening_distance_angstrom':6}):
        with pytest.raises(ValidationError): BuildAdsorptionCandidates(revision=1,site_ids=['site'],placement=args)
    with pytest.raises(ValidationError): FindAdsorptionSites(revision=1,kinds=[],manual_sites=[{'uv':[1,0]}])
    surface=copy.deepcopy(svc.selected(draft))
    top=surface['surface']['layers'][-1]['atom_ids'][0]
    surface['surface']['layers'][-1]['atom_ids']=[top]*129
    surface['snapshot']['atoms']=[{**surface['snapshot']['atoms'][-2], 'atom_id':f'top{i}'} for i in range(129)]
    surface['surface']['layers'][-1]['atom_ids']=[a['atom_id'] for a in surface['snapshot']['atoms']]
    def forbidden(*args,**kwargs): raise AssertionError('triangulation must not allocate')
    monkeypatch.setattr(adsorption,'Delaunay',forbidden)
    with pytest.raises(ToolboxError) as caught: adsorption.find_sites(surface,FindAdsorptionSites(revision=1))
    assert caught.value.code=='CAT_AUTO_SITE_BUDGET_LIMIT'
    skew=copy.deepcopy(svc.selected(draft))
    skew['snapshot']['lattice'][1]=[200,0.01,0]
    with pytest.raises(ToolboxError) as caught: adsorption.find_sites(skew,FindAdsorptionSites(revision=1))
    assert caught.value.code=='CAT_AUTO_SITE_BUDGET_LIMIT'
    # This cell was within the old ratio/cos bounds but is not a reduced
    # basis: neighbours can require images outside the bounded mesh.
    sheared=copy.deepcopy(svc.selected(draft))
    sheared['snapshot']['lattice'][1]=[6.4,4.8,0]
    with pytest.raises(ToolboxError) as caught: adsorption.find_sites(sheared,FindAdsorptionSites(revision=1))
    assert caught.value.code=='CAT_AUTO_SITE_BUDGET_LIMIT'


def test_api_optional_models_snapshot_hashes_revision_and_selected_export(tmp_path):
    app=FastAPI()
    app.include_router(router,prefix='/api/v1')
    app.add_exception_handler(ToolboxError,error_handler)
    app.state.toolbox=SimpleNamespace(root=tmp_path)
    client=TestClient(app)
    draft=pt_surface(CatalysisService(tmp_path))
    path='/api/v1/toolbox/catalysis/drafts/'+draft['draft_id']
    def post(suffix,body):
        nonlocal draft
        response=client.post(path+suffix,json={'revision':draft['revision'],**body})
        assert response.status_code==200,response.text
        draft=response.json()['draft']
    old_sha=draft['input_snapshot']['sha256']
    post('/adsorbate',{'source':{'kind':'co_example'},'anchor_index':0})
    post('/adsorption/sites',{'kinds':['ontop']})
    post('/adsorption/candidates',{'site_ids':[draft['adsorption']['sites'][0]['site_id']],'placement':{'height_angstrom':2}})
    candidate=draft['adsorption']['candidates'][0]
    assert draft['input_snapshot']['sha256']==old_sha
    for snap in [draft['input_snapshot'],draft['surfaces'][0]['snapshot'],candidate['snapshot']]:
        assert science.sha(science.canonical({k:v for k,v in snap.items() if k!='sha256'}))==snap['sha256']
    cid=candidate['candidate_id']
    assert client.get(path+'/geometry',params={'revision':draft['revision'],'candidate_id':cid}).status_code==200
    selection=client.patch(path+'/adsorption/selection',json={'revision':draft['revision'],'selected_candidate_ids':[cid]})
    assert selection.status_code==200,selection.text
    current=selection.json()['draft']
    exported=client.post(path+'/adsorption/export',json={'revision':current['revision'],'candidate_ids':[cid]})
    assert exported.status_code==200 and exported.headers['content-type']=='application/zip'
    conflict=client.post(path+'/adsorption/export',json={'revision':draft['revision'],'candidate_ids':[cid]})
    assert conflict.status_code==409 and conflict.json()['error']['code']=='CAT_REVISION_CONFLICT'
    invalid=client.post(path+'/adsorption/candidates',json={'revision':current['revision'],'site_ids':['one']*17})
    assert invalid.status_code==422
    assert client.get(path).json()['draft']==current


def test_candidate_hash_guard_detects_corruption_even_with_valid_document_head(tmp_path):
    svc=CatalysisService(tmp_path)
    draft=generate(svc,prepare(svc,pt_surface(svc)))
    folder=svc._folder(draft['draft_id'])
    head=json.loads((folder/'head.json').read_text())
    corrupted=copy.deepcopy(draft)
    corrupted['adsorption']['candidates'][0]['snapshot']['atoms'][-1]['cartesian'][0]+=.1
    raw=science.canonical(corrupted)
    (folder/head['file']).write_bytes(raw)
    head['sha256']=science.sha(raw)
    (folder/'head.json').write_bytes(science.canonical(head))
    with pytest.raises(ToolboxError) as caught: svc.get(draft['draft_id'])
    assert caught.value.code=='CAT_DRAFT_STORE_INVALID'
