"""Deterministic scientific, lifecycle and HTTP regressions; no external services."""
import copy
from concurrent.futures import ThreadPoolExecutor
import gzip
import hashlib
import json
from pathlib import Path
import time
import xml.etree.ElementTree as ET

import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.toolbox.api import create_toolbox_app
from backend.toolbox.contracts import ToolboxError
from backend.toolbox.postprocessing.parser import parse, eigenval_checked
from backend.toolbox.postprocessing.store import AnalysisStore, atomic
from backend.toolbox.postprocessing.plotting import fit_axes, prepare_curves, upgrade_view, validate_axes

FIXTURES = Path(__file__).parent / 'fixtures' / 'postprocessing'
BASE = '/api/v1/toolbox/postprocessing/datasets'


def xml_file(tmp_path, name='vasprun.Al.xml.gz'):
    path = tmp_path / 'vasprun.xml'
    path.write_bytes(gzip.decompress((FIXTURES / name).read_bytes()))
    return path


def dos_files(tmp_path, spin=1, truncated=False):
    (tmp_path / 'INCAR').write_text(f'ISPIN={spin}\nNSW=0\n', encoding='utf-8')
    rows = '-1 2 3\n0 4 7\n1 3 10\n' if spin == 1 else '-1 2 1 3 2\n0 4 3 7 5\n1 3 2 10 7\n'
    (tmp_path / 'DOSCAR').write_text('1 1 0 0\n8 1 1 1 1\n1\nCAR\nsynthetic\n1 -1 3 0.5 1\n' + (rows[:-6] if truncated else rows), encoding='utf-8')


def band_files(tmp_path):
    (tmp_path / 'INCAR').write_text('ISPIN=1\n', encoding='utf-8')
    (tmp_path / 'POSCAR').write_text('synthetic\n1\n2 0 0\n0 2 0\n0 0 2\nH\n1\nDirect\n0 0 0\n', encoding='utf-8')
    (tmp_path / 'KPOINTS').write_text('path\n2\nLine-mode\nReciprocal\n0 0 0 ! G\n.5 0 0 ! X\n\n0 .5 0 ! Y\n0 .5 .5 ! Z\n', encoding='utf-8')
    rows = ['0 0 0', '.5 0 0', '0 .5 0', '0 .5 .5']
    (tmp_path / 'EIGENVAL').write_text('1 1 1 1\na\nb\nc\nsynthetic\n2 4 2\n' + ''.join(f'\n {row} 0.25\n1 {-2+i*.1} 2\n2 {2+i*.2} 0\n' for i, row in enumerate(rows)), encoding='utf-8')


def test_public_xml_dos_and_orbital_reference(tmp_path):
    path = xml_file(tmp_path)
    result = parse(tmp_path, 'dos')
    root = ET.parse(path)
    dos = root.findall('.//dos')[-1]
    for index, channel in enumerate(('up', 'down')):
        rows = np.array([[float(v) for v in n.text.split()] for n in dos.findall('total/array/set/set')[index].findall('r')])
        np.testing.assert_array_equal(result['data']['energy_ev'], rows[:, 0])
        np.testing.assert_array_equal(result['data']['channels'][channel], rows[:, 1])
    partial = dos.find('partial/array')
    labels = [n.text.strip() for n in partial.findall('field')][1:]
    for atom, node in enumerate(partial.findall('set/set')):
        for spin_index, channel in enumerate(('up', 'down')):
            rows = np.array([[float(v) for v in n.text.split()] for n in node.findall('set')[spin_index].findall('r')])
            projected = [p for p in result['data']['projected'] if p['atom'] == atom+1]
            assert len(projected) == len(labels)
            for index, p in enumerate(projected):
                np.testing.assert_array_equal(p['channels'][channel], rows[:, index+1])
    assert result['efermi_ev'] == float(dos.find("i[@name='efermi']").text)
    display = upgrade_view(result, {'reference': 'raw', 'reference_ev': 0.0, 'mirror_down': False,
                                  'atoms': [], 'orbitals': [], 'band_start': 1, 'band_end': 20}, new_analysis=True)
    assert (display['energy_min_ev'], display['energy_max_ev']) == (-5.0, 3.0)
    curves, effective = prepare_curves(result, {**display, 'reference': 'custom', 'reference_ev': 1.0})
    assert effective == result['efermi_ev'] + 1.0
    np.testing.assert_allclose(curves[0]['x'], np.array(result['data']['energy_ev']) - result['efermi_ev'] - 1.0)
    raw_curves, _ = prepare_curves(result, {**display, 'reference': 'raw'})
    np.testing.assert_array_equal(raw_curves[0]['x'], result['data']['energy_ev'])


def test_synthetic_nonspin_xml_branch(tmp_path):
    # Controlled mutation of public XML, explicitly NOT a real non-spin run.
    path = xml_file(tmp_path)
    tree = ET.parse(path)
    for node in tree.findall(".//i[@name='ISPIN']"):
        node.text = '1'
    for parent in tree.iter():
        for child in list(parent):
            if child.tag == 'set' and child.get('comment') == 'spin 2':
                parent.remove(child)
    tree.write(path, encoding='utf-8')
    result = parse(tmp_path, 'dos')
    assert result['spin_mode'] == 'non_spin'
    assert set(result['data']['channels']) == {'total'}


def test_public_actual_band_path(tmp_path):
    xml_path = xml_file(tmp_path, 'vasprun_Si_bands.xml.gz')
    (tmp_path / 'KPOINTS').write_bytes((FIXTURES / 'KPOINTS_Si_bands').read_bytes())
    result = parse(tmp_path, 'band')
    assert len(result['data']['segments']) == 10
    assert len(result['data']['kpoints_fractional']) == 160
    assert result['data']['segments'][0]['labels'][1] == 'X'
    assert result['data']['distance_inv_angstrom'][-1] > 0
    assert result['efermi_ev'] == float(ET.parse(xml_path).findall('.//dos')[-1].find("i[@name='efermi']").text)
    display = upgrade_view(result, {'reference': 'raw', 'reference_ev': 0.0, 'mirror_down': False,
                                  'atoms': [], 'orbitals': [], 'band_start': 1, 'band_end': result['data']['band_count']}, new_analysis=True)
    assert (display['energy_min_ev'], display['energy_max_ev']) == (-3.0, 2.0)
    assert (display['axes']['x_min'], display['axes']['x_max']) == (0.0, result['data']['distance_inv_angstrom'][-1])
    calc = ET.parse(xml_path).findall('.//calculation')[-1]
    for spin_node, channel in zip(calc.findall('./eigenvalues/array/set/set'), result['data']['channels'].values()):
        source = [[float(row.text.split()[0]) for row in kpoint.findall('r')] for kpoint in spin_node.findall('set')]
        np.testing.assert_array_equal(channel, source)
    curves, _ = prepare_curves(result, {**display, 'reference': 'custom', 'reference_ev': 1.0})
    values = next(iter(result['data']['channels'].values()))
    np.testing.assert_allclose(curves[0]['y'], [values[i][0] - result['efermi_ev'] - 1.0 for i in range(16)])


@pytest.mark.parametrize('spin', [1, 2])
def test_doscar_known_values(tmp_path, spin):
    dos_files(tmp_path, spin)
    result = parse(tmp_path, 'dos')
    assert result['data']['channels']['up' if spin == 2 else 'total'] == [2, 4, 3]
    assert result['efermi_ev'] == .5


@pytest.mark.parametrize('change', ['truncate', 'ncl', 'nan', 'duplicate-grid', 'missing-metadata', 'extra-data'])
def test_reject_invalid_dos(tmp_path, change):
    dos_files(tmp_path, truncated=change == 'truncate')
    path = tmp_path / 'DOSCAR'
    if change == 'ncl':
        (tmp_path / 'INCAR').write_text('LNONCOLLINEAR=T\n', encoding='utf-8')
    if change == 'nan':
        path.write_text(path.read_text().replace('0 4 7', '0 nan 7'))
    if change == 'duplicate-grid':
        path.write_text(path.read_text().replace('0 4 7', '-1 4 7'))
    if change == 'missing-metadata':
        (tmp_path / 'INCAR').unlink()
    if change == 'extra-data':
        path.write_text(path.read_text() + 'unrecognized\n')
    with pytest.raises((ToolboxError, ValueError)):
        parse(tmp_path, 'dos')


def test_segment_distances_and_truncation(tmp_path):
    band_files(tmp_path)
    result = parse(tmp_path, 'band')
    np.testing.assert_allclose(result['data']['distance_inv_angstrom'], [0, np.pi/2, np.pi/2, np.pi])
    assert result['data']['segments'][1]['start'] == 2
    path = tmp_path / 'EIGENVAL'
    path.write_text('\n'.join(path.read_text().splitlines()[:-2]))
    with pytest.raises(ToolboxError):
        eigenval_checked(path, 1)


@pytest.mark.parametrize('kpoints', ['grid\n0\nGamma\n3 3 3\n0 0 0\n', 'path\n2\nLine-mode\nReciprocal\n0 0 0\n.4 0 0\n'])
def test_reject_grid_and_wrong_path(tmp_path, kpoints):
    band_files(tmp_path)
    (tmp_path / 'KPOINTS').write_text(kpoints)
    with pytest.raises(ToolboxError):
        parse(tmp_path, 'band')


def ready_store(tmp_path):
    store = AnalysisStore(tmp_path / '中文 状态')
    doc = store.create('dos', '合成 DOS')
    directory = store.directory(doc['id'])
    dos_files(directory, 2)
    result = parse(directory, 'dos')
    atomic(directory / 'result.json', result)
    doc.update(status='ready', summary={**{k:v for k,v in result.items() if k != 'data'}, 'atoms': [], 'orbitals': [], 'band_count': 0})
    store.save(doc)
    return store, store.read(doc['id'])


def test_persistence_reference_export_and_conflict(tmp_path):
    store, doc = ready_store(tmp_path)
    updated = store.view(doc['id'], doc['revision'], {**doc['view'], 'reference': 'fermi', 'mirror_down': True})
    reloaded = AnalysisStore(tmp_path / '中文 状态')
    curves = reloaded.curves(doc['id'])
    assert curves['curves'][0]['x'] == [-1.5, -.5, .5]
    assert curves['curves'][1]['y'] == [1, 3, 2]  # mirror never changes numeric sign
    assert curves['revision'] == updated['revision']
    assert 'reference=fermi' in reloaded.csv(doc['id'])
    with pytest.raises(ToolboxError, match='版本'):
        store.view(doc['id'], doc['revision'], doc['view'])
    raw = json.loads((store.directory(doc['id']) / 'result.json').read_text(encoding='utf-8'))
    assert raw['data']['energy_ev'] == [-1, 0, 1]
    store.delete(doc['id'])
    assert not store.list()


@pytest.fixture
def api(tmp_path):
    app = create_toolbox_app(root=tmp_path / '中文 api', monitor_enabled=False)
    with TestClient(app) as client:
        yield client, app


def test_http_full_flow_and_upload_limits(api, tmp_path, monkeypatch):
    client, app = api
    doc = client.post(BASE, json={'kind':'dos', 'title':'本地样例'}).json()['dataset']
    prefix = f"{BASE}/{doc['id']}"
    assert client.put(prefix+'/files/POTCAR', content=b'no').status_code == 400
    from backend.toolbox.postprocessing import api as module
    monkeypatch.setattr(module, 'MAX_FILE', 10)
    assert client.put(prefix+'/files/DOSCAR', content=b'x'*11).status_code == 413
    assert not list(app.state.postprocessing.directory(doc['id']).glob('*.upload'))
    monkeypatch.setattr(module, 'MAX_FILE', 64*1024*1024)
    dos_files(tmp_path)
    for name in ('DOSCAR', 'INCAR'):
        body = (tmp_path/name).read_bytes()
        response = client.put(prefix+'/files/'+name, content=body)
        assert response.status_code == 200
        assert response.json()['dataset']['files'][-1]['sha256'] == hashlib.sha256(body).hexdigest()
    assert client.put(prefix+'/files/DOSCAR', content=b'duplicate').status_code == 409
    assert client.post(prefix+'/analyses').status_code == 202
    deadline = time.monotonic()+45
    while time.monotonic()<deadline:
        doc=client.get(prefix).json()['dataset']
        if doc['status'] != 'processing': break
        time.sleep(.1)
    assert doc['status'] == 'ready', doc
    saved_axes = copy.deepcopy(doc['view']['axes'])
    revision = doc['revision']
    assert client.get(prefix).json()['dataset']['revision'] == revision
    draft_view = {key: value for key, value in doc['view'].items() if key != 'axes'}
    draft_view.update(reference='custom', reference_ev=100)
    fitted = client.post(prefix+'/fit-axes', json={'expected_revision': revision, 'view': draft_view})
    assert fitted.status_code == 200
    assert fitted.json()['axes']['x_max'] < -98
    assert client.get(prefix).json()['dataset']['view']['axes'] == saved_axes
    response = client.patch(prefix+'/view', json={'expected_revision': revision, 'view': draft_view})
    assert response.status_code == 200
    saved = response.json()['dataset']['view']
    assert (saved['energy_min_ev'], saved['energy_max_ev']) == (-5, 3)
    assert (saved['axes']['y_min'], saved['axes']['y_max']) == (0, 1)  # empty energy window has safe automatic density bounds
    assert client.patch(prefix+'/view', json={'expected_revision': revision, 'view': draft_view}).status_code == 409
    assert client.get(prefix+'/export?format=json').json()['view']['axes'] == saved['axes']
    assert client.get(prefix+'/curves').json()['effective_reference_ev'] == 100.5
    assert client.get(prefix+'/curves').json()['curves'][0]['y']==[2,4,3]
    assert client.get(prefix+'/export?format=csv').status_code==200
    assert client.put(prefix+'/files/KPOINTS', content=b'immutable').status_code==409
    assert client.delete(prefix).status_code==200
    assert client.get(prefix).status_code==404


def test_recovery_and_cancel_without_killing_unrelated_process(tmp_path):
    store = AnalysisStore(tmp_path)
    doc = store.create('dos','interrupted')
    doc['status']='processing';store.save(doc)
    restarted = AnalysisStore(tmp_path)
    assert restarted.read(doc['id'])['error']['code']=='PP_INTERRUPTED'
    with pytest.raises(ToolboxError):
        restarted.directory('../../outside')


@pytest.mark.parametrize('flag', ['LNONCOLLINEAR', 'LSORBIT'])
def test_xml_unsupported_spin_modes(tmp_path, flag):
    path = xml_file(tmp_path)
    tree = ET.parse(path)
    for node in tree.findall(f".//i[@name='{flag}']"):
        node.text = 'T'
    tree.write(path, encoding='utf-8')
    with pytest.raises(ToolboxError, match='NCL'):
        parse(tmp_path, 'dos')


@pytest.mark.parametrize('body', [b'<!DOCTYPE x [<!ENTITY y "test">]><x>&y;</x>', '<root/>'.encode('utf-16')])
def test_xml_entities_and_unaccepted_encoding(tmp_path, body):
    (tmp_path/'vasprun.xml').write_bytes(body)
    with pytest.raises(ToolboxError, match='XML'):
        parse(tmp_path, 'dos')


def test_xml_conflicting_incar(tmp_path):
    xml_file(tmp_path)
    (tmp_path/'INCAR').write_text('ISPIN=1\n')
    with pytest.raises(ToolboxError, match='冲突'):
        parse(tmp_path, 'dos')


def test_source_change_prevents_worker_start(api, tmp_path):
    client, app = api
    doc = client.post(BASE,json={'kind':'dos'}).json()['dataset']
    endpoint = f"{BASE}/{doc['id']}"
    client.put(endpoint+'/files/DOSCAR',content=b'initial snapshot')
    (app.state.postprocessing.directory(doc['id'])/'DOSCAR').write_bytes(b'changed')
    client.post(endpoint+'/analyses')
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        doc=client.get(endpoint).json()['dataset']
        if doc['status']!='processing':break
        time.sleep(.05)
    assert doc['status']=='failed'
    assert doc['error']['code']=='PP_SOURCE_CHANGED'


def test_cancel_real_worker_is_scoped_to_analysis(api, tmp_path):
    client, app = api
    doc=client.post(BASE,json={'kind':'dos'}).json()['dataset']
    endpoint=f"{BASE}/{doc['id']}"
    client.put(endpoint+'/files/vasprun.xml',content=gzip.decompress((FIXTURES/'vasprun.Al.xml.gz').read_bytes()))
    client.post(endpoint+'/analyses')
    response=client.post(endpoint+'/cancel')
    assert response.status_code==200
    assert response.json()['dataset']['status'] in ('cancelled','ready')
    app.state.postprocessing.close()
    assert app.state.postprocessing.active is None


def test_invalid_view_shape_returns_validation_error(api):
    client,_=api
    response=client.patch(BASE+'/ds_'+'a'*32+'/view',json={'expected_revision':1,'view':{'atoms':{}}})
    assert response.status_code==422


def test_http_v2_energy_window_reference_units_and_validation(api, tmp_path):
    client, app = api
    doc = client.post(BASE, json={'kind': 'dos'}).json()['dataset']
    store = app.state.postprocessing
    directory = store.directory(doc['id'])
    dos_files(directory, spin=2)
    parsed = parse(directory, 'dos')
    atomic(directory / 'result.json', parsed)
    doc.update(status='ready', summary={**{k: v for k, v in parsed.items() if k != 'data'},
               'atoms': [], 'orbitals': [], 'band_count': 0})
    doc['view'] = upgrade_view(parsed, doc['view'], new_analysis=True)
    store.save(doc)
    endpoint = f"{BASE}/{doc['id']}"
    draft = {**doc['view'], 'reference': 'custom', 'reference_ev': 1.0,
             'energy_min_ev': -2.0, 'energy_max_ev': 0.0, 'mirror_down': True}
    response = client.patch(endpoint + '/view', json={'expected_revision': doc['revision'], 'view': draft})
    assert response.status_code == 200
    saved = response.json()['dataset']
    curves = client.get(endpoint + '/curves').json()
    assert curves['view'] == saved['view']
    assert curves['curves'][0]['x'] == [-2.5, -1.5, -.5]
    assert curves['effective_reference_ev'] == curves['reference_ev'] == 1.5
    assert curves['energy_bounds_ev'] == {'min_ev': -2.5, 'max_ev': -.5}
    assert curves['units']['energy'] == 'eV' and curves['units']['dos_total'] == 'states/eV/cell'
    assert curves['view']['axes']['x_interval'] > 0 and curves['view']['axes']['y_interval'] > 0
    assert len(curves['curves'][0]['x']) == 3  # narrow viewport never crops export arrays
    for patch in ({'energy_min_ev': 0.0, 'energy_max_ev': 0.0}, {'elements': ['Fe']}, {'orbitals': ['p']}, {'reference': 'legacy_absolute'}):
        bad = client.patch(endpoint + '/view', json={'expected_revision': saved['revision'], 'view': {**saved['view'], **patch}})
        assert bad.status_code == 400
        assert client.get(endpoint).json()['dataset']['revision'] == saved['revision']
    assert client.patch(endpoint + '/view', json={'expected_revision': doc['revision'], 'view': draft}).status_code == 409
    assert client.patch(endpoint + '/view', json={'expected_revision': saved['revision'], 'view': {**saved['view'], 'unknown': 1}}).status_code == 422
    parsed['efermi_ev'] = None
    atomic(directory / 'result.json', parsed)
    saved['summary']['efermi_ev'] = None
    saved['view']['reference'] = 'raw'
    store.save(saved)
    missing = client.patch(endpoint + '/view', json={'expected_revision': saved['revision'], 'view': {**saved['view'], 'reference': 'custom'}})
    assert missing.status_code == 400 and missing.json()['error']['code'] == 'PP_MISSING_FERMI'


def test_legacy_axes_migrate_once_and_old_clients_preserve_them(tmp_path):
    store, doc = ready_store(tmp_path)
    directory = store.directory(doc['id'])
    before_result = (directory / 'result.json').read_bytes()
    legacy = copy.deepcopy(doc)
    for key in ('axes', 'version', 'energy_min_ev', 'energy_max_ev', 'elements', 'projection_grouping'):
        legacy['view'].pop(key)
    atomic(directory / 'metadata.json', legacy)
    with ThreadPoolExecutor(max_workers=4) as pool:
        readings = list(pool.map(store.read, [doc['id']] * 8))
    migrated = readings[0]
    assert all(reading == migrated for reading in readings)
    assert migrated['revision'] == legacy['revision'] + 1
    assert migrated['view']['axes']['version'] == 'pp.axes.v1'
    assert store.read(doc['id']) == migrated
    assert AnalysisStore(tmp_path / '中文 状态').read(doc['id']) == migrated
    with pytest.raises(ToolboxError, match='版本'):
        store.view(doc['id'], legacy['revision'], legacy['view'])
    old_client_view = {**legacy['view'], 'reference': 'custom', 'reference_ev': 100, 'mirror_down': True}
    saved = store.view(doc['id'], migrated['revision'], old_client_view)
    assert (saved['view']['energy_min_ev'], saved['view']['energy_max_ev']) == (migrated['view']['energy_min_ev'], migrated['view']['energy_max_ev'])
    assert saved['view']['reference_ev'] == 99.5  # old absolute 100 eV becomes EF-relative offset
    curves = store.curves(doc['id'])
    assert curves['plot_warnings']
    assert curves['curves'][0]['x'] == [-101, -100, -99]
    assert curves['curves'][1]['y'] == [1, 3, 2]
    assert (directory / 'result.json').read_bytes() == before_result
    assert len([row for row in store.csv(doc['id']).splitlines() if row.startswith('总 DOS')]) == 6


def test_fit_uses_unsaved_mirror_reference_and_projection_without_persisting(tmp_path):
    store, doc = ready_store(tmp_path)
    path = store.directory(doc['id']) / 'result.json'
    result = json.loads(path.read_text(encoding='utf-8'))
    result['data']['projected'] = [{'atom': 1, 'element': 'Fe', 'orbital': 's', 'channels': {'up': [20, 40, 30], 'down': [10, 30, 20]}}]
    atomic(path, result)
    doc['summary'].update(atoms=[[1, 'Fe']], orbitals=['s'])
    store.save(doc)
    draft = {**doc['view'], 'reference': 'custom', 'reference_ev': 100, 'mirror_down': True, 'atoms': [1], 'orbitals': ['s']}
    fitted = store.fit(doc['id'], doc['revision'], draft)['axes']
    assert (fitted['x_min'], fitted['x_max']) == (-101.5, -99.5)
    assert fitted['y_min'] <= -30 and fitted['y_max'] >= 40
    assert store.read(doc['id'])['view'] == doc['view']
    saved = store.view(doc['id'], doc['revision'], draft)
    assert (saved['view']['energy_min_ev'], saved['view']['energy_max_ev']) == (doc['view']['energy_min_ev'], doc['view']['energy_max_ev'])
    fitted_save = store.view(doc['id'], saved['revision'], {**draft, 'energy_min_ev': fitted['x_min'], 'energy_max_ev': fitted['x_max']})
    assert store.curves(doc['id'])['view']['axes'] == fitted_save['view']['axes']
    assert not store.curves(doc['id'])['plot_warnings']


def test_band_axes_remain_stable_and_fit_selected_bands(tmp_path):
    store = AnalysisStore(tmp_path / 'band-state')
    doc = store.create('band', '合成路径')
    directory = store.directory(doc['id'])
    band_files(directory)
    result = parse(directory, 'band')
    atomic(directory / 'result.json', result)
    doc.update(status='ready', summary={**{k: v for k, v in result.items() if k != 'data'}, 'atoms': [], 'orbitals': [], 'band_count': 2})
    doc['view']['band_end'] = 2
    store.save(doc)
    doc = store.read(doc['id'])
    original_axes = doc['view']['axes']
    draft = {**doc['view'], 'band_start': 2, 'band_end': 2}
    fitted = store.fit(doc['id'], doc['revision'], draft)['axes']
    assert fitted['y_min'] > 0 and fitted['y_max'] >= 2.6
    saved = store.view(doc['id'], doc['revision'], draft)
    assert saved['view']['axes'] == original_axes
    curves = store.curves(doc['id'])
    assert len(curves['curves']) == 2
    assert curves['curves'][0]['id'] == curves['curves'][1]['id'] == 'band.total.2'
    assert [curve['segment_index'] for curve in curves['curves']] == [0, 1]
    assert [tick['label'] for tick in curves['ticks']] == ['G', 'X|Y', 'Z']
    assert sum(len(curve['x']) for curve in curves['curves']) == 4


@pytest.mark.parametrize('patch', [{'x_min': 1, 'x_max': 1}, {'y_interval': 0}, {'x_interval': -1}, {'x_interval': .000001}, {'y_max': float('inf')}, {'x_min': True}, {'version': 'future'}])
def test_reject_invalid_axes_without_losing_saved_configuration(tmp_path, patch):
    store, doc = ready_store(tmp_path)
    with pytest.raises(ToolboxError):
        store.view(doc['id'], doc['revision'], {**doc['view'], 'axes': {**doc['view']['axes'], **patch}})
    assert store.read(doc['id']) == doc


def test_default_axes_cover_current_curves_and_zero_density():
    result = {'kind': 'dos', 'efermi_ev': None, 'data': {'energy_ev': [0, 1], 'channels': {'total': [0, 0]}, 'projected': []}}
    view = {'reference': 'raw', 'reference_ev': 0, 'mirror_down': False, 'atoms': [], 'orbitals': []}
    axes = fit_axes(result, view)
    assert axes['x_min'] <= 0 and axes['x_max'] >= 1
    assert axes['y_min'] < 0 < axes['y_max']
    validate_axes(axes)
