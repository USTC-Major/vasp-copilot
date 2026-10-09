"""Pure display preparation. Scientific arrays are never cropped by plot limits."""
import math
from bisect import bisect_left, bisect_right

from ..contracts import ToolboxError

AXES_VERSION = 'pp.axes.v1'
VIEW_VERSION = 'pp.view.v2'
DEFAULT_ENERGY_WINDOWS = {'dos': (-5.0, 3.0), 'band': (-3.0, 2.0)}
MAX_AXIS_TICKS = 200
AXIS_FIELDS = {'version', 'x_min', 'x_max', 'x_interval', 'y_min', 'y_max', 'y_interval'}


def validate_axes(axes):
    if not isinstance(axes, dict) or set(axes) != AXIS_FIELDS or axes['version'] != AXES_VERSION:
        raise ToolboxError('PP_INVALID_REQUEST', '坐标配置不完整或版本不受支持', 400)
    for axis in ('x', 'y'):
        low, high, interval = (axes[f'{axis}_{key}'] for key in ('min', 'max', 'interval'))
        if any(type(value) not in (int, float) or not math.isfinite(value) for value in (low, high, interval)):
            raise ToolboxError('PP_INVALID_REQUEST', '坐标起止值与刻度间隔必须为有限数值', 400)
        span = high - low
        if not math.isfinite(span) or low >= high or interval <= 0:
            raise ToolboxError('PP_INVALID_REQUEST', '坐标起点必须小于终点，刻度间隔必须大于零', 400)
        if not math.isfinite(span / interval) or span / interval > MAX_AXIS_TICKS:
            raise ToolboxError('PP_INVALID_REQUEST', f'刻度过密：每轴最多 {MAX_AXIS_TICKS} 个间隔，请增大刻度间隔', 400)
    return axes


def energy_reference(result, view):
    """Source arrays stay in their original coordinate; apply one display shift."""
    mode = view['reference']
    if mode not in ('raw', 'fermi', 'custom', 'legacy_absolute'):
        raise ToolboxError('PP_INVALID_REQUEST', '能量参考模式不受支持', 400)
    if mode == 'raw':
        return 0.0
    if mode == 'legacy_absolute' or (mode == 'custom' and view.get('version') != VIEW_VERSION):
        return view['reference_ev']
    efermi = result['efermi_ev']
    if efermi is None:
        raise ToolboxError('PP_MISSING_FERMI', '数据缺少可信费米能级；请选择原始能量，不能设置相对费米偏移', 400)
    return efermi + (view['reference_ev'] if mode == 'custom' else 0.0)


def orbital_matches(orbital, selected):
    # These are angular-momentum families, not assumptions about crystal fields.
    return not selected or orbital in selected or orbital[:1] in selected


def prepare_curves(result, view):
    data = result['data']
    reference = energy_reference(result, view)
    curves = []
    if result['kind'] == 'dos':
        x = [value - reference for value in data['energy_ev']]
        for channel, values in data['channels'].items():
            curves.append({'id': f'dos.total.{channel}', 'name': f'总 DOS · {channel}', 'channel': channel,
                           'density_unit': data.get('density_unit', 'states/eV/cell'), 'x': x, 'y': values})
        elements, atoms, orbitals = view.get('elements', []), view['atoms'], view['orbitals']
        projected_selection = bool((atoms or elements) and orbitals) if view.get('version') == VIEW_VERSION else bool(atoms or orbitals)
        if projected_selection:
            selected = [p for p in data['projected']
                        if (not atoms or p['atom'] in atoms) and (not elements or p['element'] in elements)
                        and orbital_matches(p['orbital'], orbitals)]
            grouped = view.get('projection_grouping', 'combined') == 'element'
            source_elements = list(dict.fromkeys(p['element'] for p in sorted(data['projected'], key=lambda p: p['atom'])))
            groups = [(element, [p for p in selected if p['element'] == element]) for element in sorted({p['element'] for p in selected})] if grouped else [(None, selected)]
            for element, projection in groups:
                atom_ids = sorted({p['atom'] for p in projection})
                for channel in data['channels']:
                    values = [sum(p['channels'][channel][i] for p in projection) for i in range(len(x))]
                    curves.append({'id': f'dos.projection.{element + "." if element else ""}{channel}',
                                   'name': f'{element} 投影之和 · {channel}' if element else f'所选投影之和 · {channel}',
                                   'channel': channel, 'element': element,
                                   'element_index': source_elements.index(element) if element else None, 'atom_ids': atom_ids,
                                   'density_unit': 'states/eV', 'normalization': 'sum_over_selected_atoms', 'x': x, 'y': values})
    else:
        for channel, values in data['channels'].items():
            for band in range(view['band_start'] - 1, min(view['band_end'], data['band_count'])):
                for segment_index, segment in enumerate(data['segments']):
                    start, end = segment['start'], segment['end'] + 1
                    curves.append({'id': f'band.{channel}.{band+1}', 'name': f'band {band+1} · {channel}', 'channel': channel, 'segment_index': segment_index,
                                   'x': data['distance_inv_angstrom'][start:end], 'y': [values[i][band] - reference for i in range(start, end)]})
    if any(not math.isfinite(value) for curve in curves for key in ('x', 'y') for value in curve[key]):
        raise ToolboxError('PP_INVALID_REQUEST', '当前能量参考超出可表示的数值范围，请调整参考能量', 400)
    return curves, reference


def energy_bounds(result, view):
    """Full available energy range for the current reference, without cropping."""
    reference = energy_reference(result, view)
    data = result['data']
    if result['kind'] == 'dos':
        values = data['energy_ev']
    else:
        values = [energy for channel in data['channels'].values() for row in channel
                  for energy in row[view['band_start'] - 1:view['band_end']]]
    low, high = min(values) - reference, max(values) - reference
    if not math.isfinite(low) or not math.isfinite(high):
        raise ToolboxError('PP_INVALID_REQUEST', '当前能量参考超出可表示的数值范围', 400)
    if low == high:
        padding = max(abs(low) * .01, 1.0)
        low, high = low - padding, high + padding
    return {'min_ev': low, 'max_ev': high}


def validate_energy_window(view):
    low, high = view['energy_min_ev'], view['energy_max_ev']
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in (low, high)):
        raise ToolboxError('PP_INVALID_REQUEST', '能量起止值必须为有限数值', 400)
    if low >= high or not math.isfinite(high - low):
        raise ToolboxError('PP_INVALID_REQUEST', '能量起点必须小于终点且跨度必须有限', 400)


def upgrade_view(result, view, *, new_analysis=False):
    """One-time v1 migration preserves its energy window and absolute custom shift."""
    upgraded = dict(view)
    if view.get('version') not in (None, 'pp.view.v1', VIEW_VERSION):
        raise ToolboxError('PP_INVALID_DATA', '已保存视图的版本不受支持，请保留快照', 400)
    if view.get('version') != VIEW_VERSION:
        if view['reference'] == 'custom':
            if result['efermi_ev'] is None:
                upgraded['reference'] = 'legacy_absolute'
            else:
                upgraded['reference_ev'] = view['reference_ev'] - result['efermi_ev']
        upgraded['version'] = VIEW_VERSION
        axes = view.get('axes')
        if axes:
            validate_axes(axes)
            energy_axis = 'x' if result['kind'] == 'dos' else 'y'
            low, high = axes[f'{energy_axis}_min'], axes[f'{energy_axis}_max']
        else:
            bounds = energy_bounds(result, upgraded)
            low, high = bounds['min_ev'], bounds['max_ev']
        elements = sorted({p['element'] for p in result['data'].get('projected', [])}) if view['orbitals'] and not view['atoms'] else []
        upgraded.update(version=VIEW_VERSION, energy_min_ev=low, energy_max_ev=high,
                        elements=elements, projection_grouping='combined',
                        orbitals=view['orbitals'] or sorted({p['orbital'] for p in result['data'].get('projected', [])}))
    if new_analysis:
        upgraded.update(version=VIEW_VERSION, reference='fermi' if result['efermi_ev'] is not None else 'raw',
                        reference_ev=0.0, elements=[], projection_grouping='element',
                        orbitals=sorted({p['orbital'] for p in result['data'].get('projected', [])}))
        if result['efermi_ev'] is not None:
            low, high = DEFAULT_ENERGY_WINDOWS[result['kind']]
        else:
            bounds = energy_bounds(result, upgraded)
            low, high = bounds['min_ev'], bounds['max_ev']
        upgraded.update(energy_min_ev=low, energy_max_ev=high)
    validate_energy_window(upgraded)
    upgraded['axes'] = fit_axes(result, upgraded)
    return upgraded


def displayed_bounds(curves, kind, mirror_down):
    xs, ys = [], []
    for curve in curves:
        xs.extend(curve['x'])
        ys.extend(-value if kind == 'dos' and mirror_down and curve['channel'] == 'down' else value for value in curve['y'])
    if not xs or not ys:
        raise ToolboxError('PP_INVALID_REQUEST', '当前选择没有可适配的曲线', 400)
    return {'x_min': min(xs), 'x_max': max(xs), 'y_min': min(ys), 'y_max': max(ys)}


def _nice_axis(low, high, include_zero=False):
    if include_zero:
        low, high = min(low, 0), max(high, 0)
    span = high - low
    if not math.isfinite(span):
        raise ToolboxError('PP_INVALID_REQUEST', '当前数据跨度超出可表示的坐标范围', 400)
    if span == 0:
        padding = max(abs(low) * .1, 1)
        low, high, span = low - padding, high + padding, 2 * padding
    interval = _nice_interval(span)
    # A little headroom avoids placing extrema against the frame. Only axes change.
    padded_low, padded_high = low - span * .02, high + span * .02
    if not math.isfinite(padded_low) or not math.isfinite(padded_high):
        return low, high, interval
    start = math.floor(padded_low / interval) * interval
    end = math.ceil(padded_high / interval) * interval
    if not math.isfinite(start) or not math.isfinite(end):
        return low, high, interval
    return start, end, interval


def _nice_interval(span):
    if not math.isfinite(span) or span <= 0:
        raise ToolboxError('PP_INVALID_REQUEST', '坐标跨度必须为有限正数', 400)
    rough = span / 6
    if rough == 0:
        return span
    power = 10 ** math.floor(math.log10(rough))
    if power == 0:
        return rough
    return next(value * power for value in (1, 2, 2.5, 5, 10) if value * power >= rough)


def _window_values(curve, low, high):
    """Include sampled points and line intersections with either window edge."""
    x, y = curve['x'], curve['y']
    if not x or high < x[0] or low > x[-1]:
        return []
    start, end = bisect_left(x, low), bisect_right(x, high)
    values = y[start:end]
    for edge in (low, high):
        index = bisect_left(x, edge)
        if 0 < index < len(x) and x[index - 1] < edge < x[index]:
            fraction = (edge - x[index - 1]) / (x[index] - x[index - 1])
            values.append(y[index - 1] * (1 - fraction) + y[index] * fraction)
    return values


def window_density_bounds(curves, view):
    values = []
    for curve in curves:
        visible = _window_values(curve, view['energy_min_ev'], view['energy_max_ev'])
        values.extend(-value if view['mirror_down'] and curve['channel'] == 'down' else value for value in visible)
    if not values or all(value == 0 for value in values):
        return (-1.0, 1.0) if view['mirror_down'] and any(c['channel'] == 'down' for c in curves) else (0.0, 1.0)
    low, high = min(min(values), 0), max(max(values), 0)
    start, end, _ = _nice_axis(low, high)
    return (0.0 if low == 0 else start), (0.0 if high == 0 else end)


def fit_axes(result, view):
    curves, _ = prepare_curves(result, view)
    if view.get('version') == VIEW_VERSION:
        validate_energy_window(view)
        low, high = view['energy_min_ev'], view['energy_max_ev']
        if result['kind'] == 'dos':
            ymin, ymax = window_density_bounds(curves, view)
            limits = {'x_min': low, 'x_max': high, 'y_min': ymin, 'y_max': ymax}
        else:
            distance = result['data']['distance_inv_angstrom']
            limits = {'x_min': min(distance), 'x_max': max(distance), 'y_min': low, 'y_max': high}
        axes = {'version': AXES_VERSION, **limits,
                'x_interval': _nice_interval(limits['x_max'] - limits['x_min']),
                'y_interval': _nice_interval(limits['y_max'] - limits['y_min'])}
        return validate_axes(axes)
    bounds = displayed_bounds(curves, result['kind'], view['mirror_down'])
    axes = {'version': AXES_VERSION}
    for axis in ('x', 'y'):
        values = _nice_axis(bounds[f'{axis}_min'], bounds[f'{axis}_max'], include_zero=axis == 'y' and result['kind'] == 'dos')
        axes.update({f'{axis}_{key}': value for key, value in zip(('min', 'max', 'interval'), values)})
    return validate_axes(axes)


def bounds_warning(curves, kind, view):
    if view.get('version') == VIEW_VERSION:
        warnings = ['此分析缺少费米能级，保留旧版绝对参考；相对费米偏移不可用。'] if view['reference'] == 'legacy_absolute' else []
        if kind == 'dos' and not any(_window_values(curve, view['energy_min_ev'], view['energy_max_ev']) for curve in curves):
            warnings.append('当前能量窗内没有数据；可查看完整能量范围。原始数组未截断。')
        if kind == 'band' and not any(min(curve['y']) <= view['energy_max_ev'] and max(curve['y']) >= view['energy_min_ev'] for curve in curves):
            warnings.append('当前所选能带在能量窗内没有数据；可查看完整能量范围。原始数组未截断。')
        return warnings
    bounds = displayed_bounds(curves, kind, view['mirror_down'])
    axes = view['axes']
    outside = any(bounds[f'{axis}_min'] < axes[f'{axis}_min'] or bounds[f'{axis}_max'] > axes[f'{axis}_max'] for axis in ('x', 'y'))
    return ['当前曲线超出已保存坐标范围；可手动调整或按当前数据适配范围，原始数组未截断。'] if outside else []
