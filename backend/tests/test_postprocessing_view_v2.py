"""Display-coordinate and projection regressions; all states are pytest fixtures."""
import copy
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.toolbox.contracts import ToolboxError
from backend.toolbox.postprocessing.plotting import (
    VIEW_VERSION, bounds_warning, energy_bounds, fit_axes, prepare_curves, upgrade_view,
)
from backend.toolbox.postprocessing.store import AnalysisStore, atomic


def result(kind='dos', efermi=10.0):
    data = {'energy_ev': [8.0, 10.0, 12.0, 14.0], 'channels': {'up': [100.0, 2.0, 4.0, 100.0], 'down': [80.0, 1.0, 3.0, 90.0]},
            'projected': [], 'density_unit': 'states/eV/cell', 'projected_unit': 'states/eV/atom'}
    if kind == 'band':
        data = {'distance_inv_angstrom': [0.0, 1.234, 1.234, 2.789], 'segments': [{'start': 0, 'end': 1, 'labels': ['G', 'X']}, {'start': 2, 'end': 3, 'labels': ['Y', 'Z']}],
                'channels': {'total': [[8.0, 12.0], [9.0, 13.0], [8.5, 12.5], [10.0, 14.0]]}, 'band_count': 2}
    return {'schema_version': 'pp.v1', 'kind': kind, 'spin_mode': 'collinear' if kind == 'dos' else 'non_spin',
            'efermi_ev': efermi, 'warnings': [], 'parser': {'library': 'synthetic'}, 'data': data}


def view(kind='dos', **updates):
    low, high = (-5.0, 3.0) if kind == 'dos' else (-3.0, 2.0)
    return {'version': VIEW_VERSION, 'reference': 'fermi', 'reference_ev': 0.0, 'mirror_down': False,
            'atoms': [], 'elements': [], 'orbitals': [], 'projection_grouping': 'element',
            'band_start': 1, 'band_end': 2, 'energy_min_ev': low, 'energy_max_ev': high, **updates}


def projections(data):
    for atom, element, orbital, density in [(1, 'Fe', 's', 2), (1, 'Fe', 'px', 3), (1, 'Fe', 'py', 5), (2, 'Fe', 'px', 7), (3, 'O', 'px', 11)]:
        data['projected'].append({'atom': atom, 'element': element, 'orbital': orbital,
                                  'channels': {'up': [density] * 4, 'down': [density / 2] * 4}})


def store_result(tmp_path, parsed, saved_view=None):
    store = AnalysisStore(tmp_path / 'isolated-state')
    doc = store.create(parsed['kind'], 'explicit synthetic display regression')
    atomic(store.directory(doc['id']) / 'result.json', parsed)
    doc.update(status='ready', summary={**{k: v for k, v in parsed.items() if k != 'data'},
               'atoms': sorted({p['atom']: p['element'] for p in parsed['data'].get('projected', [])}.items()),
               'orbitals': sorted({p['orbital'] for p in parsed['data'].get('projected', [])}),
               'band_count': parsed['data'].get('band_count', 0)})
    doc['view'] = saved_view or upgrade_view(parsed, doc['view'], new_analysis=True)
    store.save(doc)
    return store, doc


@pytest.mark.parametrize('kind', ['dos', 'band'])
def test_relative_custom_zero_equals_fermi_and_positive_offset_lowers_energy(kind):
    parsed = result(kind)
    source_before = copy.deepcopy(parsed)
    fermi, _ = prepare_curves(parsed, view(kind))
    zero, ref = prepare_curves(parsed, view(kind, reference='custom', reference_ev=0.0))
    shifted, shifted_ref = prepare_curves(parsed, view(kind, reference='custom', reference_ev=1.0))
    assert zero == fermi
    axis = 'x' if kind == 'dos' else 'y'
    assert shifted[0][axis] == [energy - 1.0 for energy in zero[0][axis]]
    assert (ref, shifted_ref) == (10.0, 11.0)
    raw, raw_ref = prepare_curves(parsed, view(kind, reference='raw'))
    assert raw[0][axis][0] == 8.0 and raw_ref == 0.0
    assert parsed == source_before


@pytest.mark.parametrize('kind,window', [('dos', (-5.0, 3.0)), ('band', (-3.0, 2.0))])
def test_new_analysis_defaults_are_fermi_windows_and_missing_fermi_uses_raw(kind, window):
    parsed = result(kind)
    initialized = upgrade_view(parsed, view(kind, reference='raw'), new_analysis=True)
    assert initialized['reference'] == 'fermi'
    assert (initialized['energy_min_ev'], initialized['energy_max_ev']) == window
    assert initialized['projection_grouping'] == 'element' and initialized['elements'] == []
    if kind == 'band':
        assert (initialized['axes']['x_min'], initialized['axes']['x_max']) == (0.0, 2.789)
    parsed['efermi_ev'] = None
    initialized = upgrade_view(parsed, view(kind, reference='raw'), new_analysis=True)
    assert initialized['reference'] == 'raw'
    assert initialized['energy_min_ev'] == 8.0 and initialized['energy_max_ev'] == 14.0


@pytest.mark.parametrize('kind', ['dos', 'band'])
def test_legacy_absolute_reference_and_saved_energy_window_migrate_without_jump(tmp_path, kind):
    parsed = result(kind)
    legacy = {k: v for k, v in view(kind, reference='custom', reference_ev=12.5).items()
              if k not in ('version', 'energy_min_ev', 'energy_max_ev', 'elements', 'projection_grouping')}
    legacy['axes'] = {'version': 'pp.axes.v1', 'x_min': -8.5, 'x_max': 7.5, 'x_interval': 2.0,
                      'y_min': -4.5, 'y_max': 8.5, 'y_interval': 2.0}
    expected, reference = prepare_curves(parsed, legacy)
    store, doc = store_result(tmp_path, parsed, legacy)
    path = store.directory(doc['id']) / 'result.json'
    before = path.read_bytes()
    migrated = store.read(doc['id'])
    assert migrated['revision'] == doc['revision'] + 1
    assert migrated['view']['version'] == VIEW_VERSION
    assert migrated['view']['reference_ev'] == 2.5
    axis = 'x' if kind == 'dos' else 'y'
    assert (migrated['view']['energy_min_ev'], migrated['view']['energy_max_ev']) == (legacy['axes'][f'{axis}_min'], legacy['axes'][f'{axis}_max'])
    assert prepare_curves(parsed, migrated['view']) == (expected, reference)
    assert store.read(doc['id']) == migrated
    assert AnalysisStore(tmp_path / 'isolated-state').read(doc['id']) == migrated
    assert path.read_bytes() == before
    with pytest.raises(ToolboxError, match='版本'):
        store.view(doc['id'], doc['revision'], migrated['view'])


def test_missing_fermi_preserves_legacy_absolute_and_refuses_new_relative_custom(tmp_path):
    parsed = result(efermi=None)
    legacy = {k: v for k, v in view(reference='custom', reference_ev=3.5).items()
              if k not in ('version', 'energy_min_ev', 'energy_max_ev', 'elements', 'projection_grouping')}
    legacy['axes'] = fit_axes(parsed, legacy)
    store, doc = store_result(tmp_path, parsed, legacy)
    migrated = store.read(doc['id'])
    assert migrated['view']['reference'] == 'legacy_absolute'
    assert migrated['view']['reference_ev'] == 3.5
    assert prepare_curves(parsed, migrated['view']) == prepare_curves(parsed, legacy)
    assert '缺少费米' in store.curves(doc['id'])['plot_warnings'][0]
    for mode in ('fermi', 'custom'):
        with pytest.raises(ToolboxError, match='费米'):
            store.view(doc['id'], migrated['revision'], {**migrated['view'], 'reference': mode})
    raw = store.view(doc['id'], migrated['revision'], {**migrated['view'], 'reference': 'raw'})
    assert store.curves(doc['id'])['curves'][0]['x'] == parsed['data']['energy_ev']
    with pytest.raises(ToolboxError, match='旧版'):
        store.view(doc['id'], raw['revision'], {**raw['view'], 'reference': 'legacy_absolute'})


def test_dos_window_interpolates_boundaries_without_interior_samples_and_scales_mirror():
    parsed = result(efermi=0.0)
    parsed['data'].update(energy_ev=[0.0, 10.0], channels={'up': [0.0, 20.0], 'down': [0.0, 100.0]})
    draft = view(energy_min_ev=4.0, energy_max_ev=6.0, mirror_down=True)
    axes = fit_axes(parsed, draft)
    assert axes['x_min'] == 4.0 and axes['x_max'] == 6.0
    assert -100.0 < axes['y_min'] <= -60.0
    assert 12.0 <= axes['y_max'] <= 20.0
    assert not bounds_warning(prepare_curves(parsed, draft)[0], 'dos', draft)
    draft.update(energy_min_ev=20.0, energy_max_ev=30.0)
    assert fit_axes(parsed, draft)['y_min'] == -1.0 and fit_axes(parsed, draft)['y_max'] == 1.0
    assert '没有数据' in bounds_warning(prepare_curves(parsed, draft)[0], 'dos', draft)[0]
    parsed['data']['channels'] = {'up': [0.0, 0.0], 'down': [0.0, 0.0]}
    draft.update(energy_min_ev=4.0, energy_max_ev=6.0, mirror_down=False)
    axes = fit_axes(parsed, draft)
    assert (axes['y_min'], axes['y_max']) == (0.0, 1.0)


def test_projection_elements_exact_atoms_orbital_families_and_deselect_all():
    parsed = result()
    projections(parsed['data'])
    draft = view(elements=['Fe', 'O'], orbitals=['p', 'px'])
    curves, _ = prepare_curves(parsed, draft)
    sums = {c['id']: c['y'][0] for c in curves}
    assert sums['dos.projection.Fe.up'] == 15.0  # px+py once, both Fe atoms
    assert sums['dos.projection.O.up'] == 11.0
    assert next(c for c in curves if c['id'] == 'dos.projection.Fe.up')['atom_ids'] == [1, 2]
    assert next(c for c in curves if c['id'] == 'dos.projection.O.up')['element_index'] == 1
    precise, _ = prepare_curves(parsed, {**draft, 'atoms': [1]})
    assert [c['y'][0] for c in precise if c['id'].startswith('dos.projection.')] == [8.0, 4.0]
    independent, _ = prepare_curves(parsed, {**draft, 'elements': [], 'atoms': [3]})
    assert [c['element'] for c in independent if c['id'].startswith('dos.projection.')] == ['O', 'O']
    assert [c['element_index'] for c in independent if c['id'].startswith('dos.projection.')] == [1, 1]
    for deselected in ({'elements': [], 'atoms': []}, {'orbitals': []}):
        only_total, _ = prepare_curves(parsed, {**draft, **deselected})
        assert [c['id'] for c in only_total] == ['dos.total.up', 'dos.total.down']


def test_legacy_empty_filters_become_explicit_all_selections_with_same_sum():
    parsed = result()
    projections(parsed['data'])
    for filters in ({'atoms': [1], 'orbitals': []}, {'atoms': [], 'orbitals': ['px']}):
        legacy = {'reference': 'fermi', 'reference_ev': 0.0, 'mirror_down': False, 'band_start': 1, 'band_end': 20, **filters}
        old, ref = prepare_curves(parsed, legacy)
        migrated = upgrade_view(parsed, legacy)
        assert migrated['projection_grouping'] == 'combined'
        assert migrated['orbitals']
        assert prepare_curves(parsed, migrated) == (old, ref)


def test_projection_controls_drive_dos_auto_scale_and_keep_full_arrays(tmp_path):
    parsed = result()
    projections(parsed['data'])
    store, doc = store_result(tmp_path, parsed)
    saved = store.view(doc['id'], doc['revision'], {**doc['view'], 'energy_min_ev': -0.1, 'energy_max_ev': 0.1,
                       'elements': ['Fe'], 'orbitals': ['p'], 'mirror_down': True})
    curves = store.curves(doc['id'])
    assert saved['view']['axes']['y_max'] >= 15.0
    assert saved['view']['axes']['y_min'] <= -7.5
    assert all(len(c['x']) == 4 for c in curves['curves'])
    assert curves['energy_bounds_ev'] == {'min_ev': -2.0, 'max_ev': 4.0}
    assert curves['units']['dos_total'] == 'states/eV/cell' and curves['units']['reciprocal_convention'] == '2pi'
    assert curves['reference_ev'] == curves['effective_reference_ev'] == 10.0
    assert 'projection_DOS=states/eV (selected atom sum)' in store.csv(doc['id'])


def test_compare_and_swap_rejects_concurrent_old_revision_without_overwrite(tmp_path):
    store, doc = store_result(tmp_path, result())
    def change(offset):
        try:
            return store.view(doc['id'], doc['revision'], {**doc['view'], 'reference': 'custom', 'reference_ev': offset})
        except ToolboxError:
            return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(change, [1.0, 2.0, 3.0, 4.0]))
    successful = [outcome for outcome in outcomes if outcome is not None]
    assert len(successful) == 1
    assert store.read(doc['id']) == successful[0]
    assert store.read(doc['id'])['revision'] == doc['revision'] + 1


def test_reject_future_persisted_view_version_without_rewriting_results(tmp_path):
    store, doc = store_result(tmp_path, result())
    directory = store.directory(doc['id'])
    before = (directory / 'result.json').read_bytes()
    doc['view']['version'] = 'pp.view.future'
    atomic(directory / 'metadata.json', doc)
    with pytest.raises(ToolboxError, match='版本'):
        store.read(doc['id'])
    assert (directory / 'result.json').read_bytes() == before


@pytest.mark.parametrize('density', [5e-324, 1.79e308])
def test_automatic_density_ticks_remain_finite_for_supported_finite_values(density):
    parsed = result(efermi=0.0)
    parsed['data'].update(energy_ev=[0.0, 10.0], channels={'total': [density, density]})
    axes = fit_axes(parsed, view(energy_min_ev=0.0, energy_max_ev=10.0))
    assert axes['y_min'] == 0.0 and axes['y_max'] >= density
    assert axes['y_interval'] > 0.0
