"""Bounded local geometry; no primitive conversion or cell standardization.

Stable output IDs are newly assigned. Source IDs express a replication relation,
never one-to-one identity through cutting or a POSCAR round trip.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import math
import uuid
from functools import reduce

import numpy as np
from pymatgen.core import Lattice, Structure
from pymatgen.core.surface import SlabGenerator
from pymatgen.io.cif import CifFile
from pymatgen.io.vasp.inputs import Poscar

from backend.input_validation import InputValidationError, validate_poscar
from backend.app.schemas.structure import build_structure_summary
from backend.app.services.structure_geometry import build_structure_geometry
from backend.app.services.cif_converter import convert_cif_to_poscar
from backend.app.core.errors import AppError
from ..contracts import ToolboxError

MAX_ATOMS = 2048
MAX_BULK_ATOMS = 512
MAX_OUC_ATOMS = 512
MAX_TERMINATIONS = 16
MAX_TOTAL_ATOMS = 8192
MAX_TEXT_BYTES = 2 * 1024 * 1024
TRANSFORM_VERSION = 'cat-surface-1'


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def snapshot(structure, *, coordinate_mode='direct', provenance=None, atom_ids=None):
    count = len(structure)
    matrix = np.array(structure.lattice.matrix)
    if count < 1 or count > MAX_ATOMS or not structure.is_ordered:
        raise ToolboxError('CAT_STRUCTURE_LIMIT', '只支持 1 至 2048 个有序原子的结构', 422)
    values = np.concatenate((matrix.ravel(), structure.frac_coords.ravel(), structure.cart_coords.ravel()))
    if not np.isfinite(values).all() or np.abs(values).max() > 1e6:
        raise ToolboxError('CAT_NONFINITE_STRUCTURE', '晶胞与坐标须为有限数值，绝对值不超过 1e6', 422)
    if np.linalg.det(matrix) < 0:
        raise ToolboxError('CAT_LEFT_HANDED_CELL_UNSUPPORTED', '首版须使用右手晶胞；左手晶胞请显式转换后重新导入，不自动改变基矢或约束', 422)
    if min(structure.lattice.abc) < 0.1 or np.linalg.det(matrix) < 1e-6:
        raise ToolboxError('CAT_CELL_INVALID', '晶胞退化或长度低于 0.1 Å', 422)
    sid = 'snap_' + uuid.uuid4().hex
    atom_ids = atom_ids or [f'{sid}_a{i + 1}' for i in range(count)]
    if len(atom_ids) != count or len(set(atom_ids)) != count:
        raise ToolboxError('CAT_ATOM_ID_INVALID', '原子身份数量或唯一性无效', 422)
    flags = structure.site_properties.get('selective_dynamics', [[True] * 3 for _ in structure])
    if len(flags) != count or any(len(f) != 3 for f in flags):
        raise ToolboxError('CAT_FLAGS_INVALID', '约束与原子数量不一致', 422)
    atoms = []
    for i, site in enumerate(structure):
        atoms.append({'atom_id': atom_ids[i], 'element': site.specie.symbol,
                      'fractional': site.frac_coords.tolist(), 'cartesian': site.coords.tolist(),
                      'selective_dynamics': [bool(v) for v in flags[i]],
                      'provenance': provenance[i] if provenance else {'kind': 'import', 'source_index': i}})
    result = {'snapshot_id': sid, 'coordinate_mode': coordinate_mode, 'lattice': matrix.tolist(), 'atoms': atoms}
    result['sha256'] = sha(canonical(result))
    return result


def from_snapshot(snap):
    return Structure(snap['lattice'], [a['element'] for a in snap['atoms']],
                     [a['fractional'] for a in snap['atoms']],
                     site_properties={'selective_dynamics': [a['selective_dynamics'] for a in snap['atoms']],
                                      'cat_source_index': list(range(len(snap['atoms'])))})


def import_poscar(text, maximum):
    try:
        info = validate_poscar(text, include_coordinates=True)
    except InputValidationError as exc:
        raise ToolboxError(exc.code, str(exc), 422) from exc
    if info.vasp4:
        raise ToolboxError('CAT_SPECIES_REQUIRED', 'POSCAR 必须含元素符号行', 422)
    if info.atom_count > maximum:
        raise ToolboxError('CAT_INPUT_ATOM_LIMIT', f'该来源最多支持 {maximum} 个原子', 422)
    elements = [element for element, count in zip(info.elements, info.counts) for _ in range(count)]
    coords = np.array(info.coordinates, dtype=float)
    if info.coordinate_mode == 'cartesian':
        coords *= np.array(info.cartesian_scale)
    structure = Structure(info.matrix, elements, coords, coords_are_cartesian=info.coordinate_mode == 'cartesian',
                          site_properties={'selective_dynamics': list(info.selective_flags) if info.selective_dynamics else [[True] * 3 for _ in elements]})
    return snapshot(structure, coordinate_mode=info.coordinate_mode)


def cif_poscar(text, maximum):
    # Bound the symmetry expansion BEFORE CifParser constructs a Structure.
    try:
        blocks = CifFile.from_str(text).data
        if len(blocks) != 1:
            raise ToolboxError('CIF_MULTIPLE_STRUCTURES_NOT_SUPPORTED', 'CIF 须只包含一个结构块', 422)
        data = {k.lower(): v for k, v in next(iter(blocks.values())).data.items()}
        coordinates = data.get('_atom_site_fract_x', data.get('_atom_site_cartn_x', []))
        if not isinstance(coordinates, list) or not coordinates:
            raise ToolboxError('CIF_MISSING_COORDINATES', 'CIF 缺少原子坐标', 422)
        operations = data.get('_space_group_symop_operation_xyz', data.get('_symmetry_equiv_pos_as_xyz'))
        multiplicity = len(operations) if isinstance(operations, list) else 1 if operations else 192
        if multiplicity > 192 or len(coordinates) * multiplicity > maximum:
            raise ToolboxError('CAT_CIF_EXPANSION_LIMIT', f'CIF 对称展开的保守预算超过 {maximum} 个原子；请提供显式有序 POSCAR 或较小 CIF', 422)
        return convert_cif_to_poscar(text, standardize=False).poscar_text
    except ToolboxError:
        raise
    except AppError as exc:
        raise ToolboxError(exc.code, exc.message, 422) from exc
    except Exception as exc:
        raise ToolboxError('CIF_PARSE_FAILED', 'CIF 无法可靠转换为有序结构', 422) from exc


def ideal_pt():
    structure = Structure(Lattice.cubic(3.92), ['Pt'] * 4,
                          [[0, 0, 0], [0, .5, .5], [.5, 0, .5], [.5, .5, 0]])
    return Poscar(structure, comment='SYNTHETIC ideal fcc Pt; a=3.92 A; not DFT optimized').get_str()


def surface_geometry(snap, tolerance):
    matrix = np.array(snap['lattice'])
    normal = np.cross(matrix[0], matrix[1])
    normal /= np.linalg.norm(normal)
    if np.dot(normal, matrix[2]) < 0:
        normal *= -1
    period = float(np.dot(normal, matrix[2]))
    if period < 0.1:
        raise ToolboxError('CAT_NORMAL_PERIOD_INVALID', '表面法向周期过小', 422)
    projected = np.array([a['cartesian'] for a in snap['atoms']]) @ normal
    circular = np.mod(projected, period)
    ordered = np.sort(circular)
    gaps = np.diff(np.r_[ordered, ordered[0] + period])
    cut = int(np.argmax(gaps))
    origin = float(ordered[(cut + 1) % len(ordered)])
    # Unwrap only analysis coordinates across the largest nuclei-free gap.
    heights = np.mod(circular - origin, period)
    heights[np.isclose(heights, period, atol=1e-8, rtol=0)] = 0
    groups = []
    for index in np.argsort(heights, kind='stable'):
        height = float(heights[index])
        if not groups or height - groups[-1]['minimum'] > tolerance:
            groups.append({'minimum': height, 'heights': [], 'atom_ids': []})
        groups[-1]['heights'].append(height)
        groups[-1]['atom_ids'].append(snap['atoms'][index]['atom_id'])
    span = float(heights.max() - heights.min())
    return {'normal': normal.tolist(), 'normal_period_angstrom': period,
            'actual_nuclei_span_angstrom': span, 'periodic_vacuum_gap_angstrom': period - span,
            'projection_origin_angstrom': origin, 'projection_method': 'largest_periodic_nuclei_free_gap',
            'in_plane_lengths_angstrom': [float(np.linalg.norm(matrix[i])) for i in (0, 1)],
            'layers': [{'layer_index': i, 'atom_ids': g['atom_ids'], 'projection_angstrom': float(np.mean(g['heights']))}
                       for i, g in enumerate(groups)],
            'layer_tolerance': tolerance, 'bottom_fixed_layers': 0, 'atom_overrides': {},
            'reset_existing': False, 'flag_basis': 'direct_lattice_vectors'}


def allocation_budget(structure, params):
    """Compute library scaling without allocating a supercell (locked algorithm).

    The post-constructor equality check fails closed if the library changes.
    max_normal_search=1 bounds the finite search; no primitive reduction follows.
    """
    hkl = tuple(params.miller_index)
    divisor = abs(reduce(math.gcd, hkl))
    hkl = tuple(v // divisor for v in hkl)
    lattice = structure.lattice
    normal = np.array(lattice.reciprocal_lattice_crystallographic.get_cartesian_coords(hkl))
    normal /= np.linalg.norm(normal)
    scale, other = [], []
    eye = np.eye(3, dtype=int)
    for i, v in enumerate(hkl):
        if v == 0:
            scale.append(eye[i])
        else:
            other.append((i, abs(np.dot(normal, lattice.matrix[i])) / lattice.abc[i]))
    if len(other) > 1:
        multiple = math.lcm(*(hkl[i] for i, _ in other))
        for (i, _), (j, _) in itertools.combinations(other, 2):
            vector = [0, 0, 0]
            vector[i], vector[j] = -round(multiple / hkl[i]), round(multiple / hkl[j])
            scale.append(vector)
            if len(scale) == 2:
                break
    candidates = []
    for vector in itertools.product([-1, 1, 0], repeat=3):
        if not any(vector) or abs(np.linalg.det([*scale, vector])) < 1e-8:
            continue
        cartesian = lattice.get_cartesian_coords(vector)
        length = float(np.linalg.norm(cartesian))
        cosine = abs(float(np.dot(cartesian, normal) / length))
        candidates.append((vector, cosine, length))
        if math.isclose(cosine, 1, abs_tol=1e-8):
            break
    scale.append(max(candidates, key=lambda v: (v[1], -v[2]))[0])
    scale = np.array(scale, dtype=int)
    if np.linalg.det(scale) < 0:
        scale *= -1
    scale = np.array([row // abs(reduce(math.gcd, row)) for row in scale], dtype=int)
    oriented_atoms = len(structure) * abs(round(float(np.linalg.det(scale))))
    height = abs(float(np.dot(normal, (scale @ lattice.matrix)[2])))
    if height < 0.1:
        raise ToolboxError('CAT_SURFACE_BUDGET_LIMIT', '定向胞法向高度过小', 422)
    slab_atoms = oriented_atoms * math.ceil(params.min_slab_size / height)
    output_atoms = slab_atoms * math.prod(params.in_plane_supercell)
    if oriented_atoms > MAX_OUC_ATOMS or output_atoms > MAX_ATOMS:
        raise ToolboxError('CAT_SURFACE_BUDGET_LIMIT', f'生成前预算 {output_atoms} 个原子（定向胞 {oriented_atoms}）；每个表面上限 2048，定向胞上限 512', 422)
    return scale, output_atoms


def generate_surfaces(input_snapshot, params):
    if any(len(set(a['selective_dynamics'])) > 1 for a in input_snapshot['atoms']):
        raise ToolboxError('CAT_MIXED_FLAGS_TRANSFORM_UNSUPPORTED', '体相含混合 T/F；切面改变晶格基矢，无法可靠映射约束。请显式修改源文件，或作为已有 slab 导入保留', 422)
    structure = from_snapshot(input_snapshot)
    # Suppress automatic symmetry analysis: these properties are not used for
    # identity or filtering, and no magnetic/polar reconstruction is performed.
    structure.add_site_property('bulk_wyckoff', ['unknown'] * len(structure))
    structure.add_site_property('bulk_equivalent', list(range(len(structure))))
    expected_scale, output_atoms = allocation_budget(structure, params)
    generator = SlabGenerator(structure, params.miller_index, params.min_slab_size, params.min_vacuum_size,
                              primitive=False, lll_reduce=False, center_slab=True,
                              max_normal_search=1, reorient_lattice=True)
    if not np.array_equal(generator.slab_scale_factor, expected_scale):
        raise ToolboxError('CAT_LIBRARY_CONTRACT_CHANGED', '定向胞与预分配预算不一致，已阻止生成', 422)
    shifts = generator.gen_possible_terminations(ftol=params.layer_tolerance)
    if len(shifts) > MAX_TERMINATIONS or len(shifts) * output_atoms > MAX_TOTAL_ATOMS:
        raise ToolboxError('CAT_TERMINATION_LIMIT', '终止面超过 16 个或总原子预算超过 8192；请缩小结构或超胞', 422)
    # Enumerate geometric shifts explicitly. No symmetry merging, bond repair,
    # reconstruction, or claim that any termination is chemically stable.
    options = []
    for shift in shifts:
        slab = generator.get_slab(shift=shift)
        slab.make_supercell([*params.in_plane_supercell, 1])
        indices = slab.site_properties.get('cat_source_index', [])
        if len(indices) != len(slab) or len(slab) != output_atoms:
            raise ToolboxError('CAT_SOURCE_MAPPING_INVALID', '生成原子与来源映射不一致', 422)
        provenance, copies = [], {}
        for i, site in zip(indices, slab):
            if not isinstance(i, (int, np.integer)) or not 0 <= i < len(input_snapshot['atoms']):
                raise ToolboxError('CAT_SOURCE_MAPPING_INVALID', '来源原子索引无效', 422)
            source = input_snapshot['atoms'][int(i)]
            if source['element'] != site.specie.symbol or list(site.properties['selective_dynamics']) != source['selective_dynamics']:
                raise ToolboxError('CAT_SOURCE_MAPPING_INVALID', '来源物种或 FFF/TTT 约束不一致', 422)
            ordinal = copies.get(int(i), 0)
            copies[int(i)] = ordinal + 1
            provenance.append({'kind': 'surface_replica', 'source_atom_id': source['atom_id'],
                               'source_index': int(i), 'replica_ordinal': ordinal,
                               'mapping': 'replicated_source_site; not unique physical identity'})
        snap = snapshot(slab, provenance=provenance)
        options.append({'surface_id': 'surf_' + uuid.uuid4().hex, 'termination_shift': float(shift),
                        'snapshot': snap, 'baseline_flags': [a['selective_dynamics'] for a in snap['atoms']],
                        'surface': surface_geometry(snap, params.layer_tolerance),
                        'transform': {'version': TRANSFORM_VERSION, 'input_snapshot_id': input_snapshot['snapshot_id'],
                                      'miller_index_of_input_cell': list(params.miller_index),
                                      'cell_convention': 'supplied_input_cell', 'primitive': False, 'standardize': False,
                                      'oriented_cell_scale_matrix': expected_scale.tolist(),
                                      'in_plane_supercell': list(params.in_plane_supercell),
                                      'termination_shift': float(shift), 'identity_relation': 'one_source_to_multiple_new_ids'}})
    if not options:
        raise ToolboxError('CAT_NO_TERMINATIONS', '未生成可用几何终止面', 422)
    return options


def poscar_export(snap):
    order = {}
    for a in snap['atoms']:
        order.setdefault(a['element'], len(order))
    indices = sorted(range(len(snap['atoms'])), key=lambda i: order[snap['atoms'][i]['element']])
    atoms = [snap['atoms'][i] for i in indices]
    structure = Structure(snap['lattice'], [a['element'] for a in atoms], [a['fractional'] for a in atoms],
                          site_properties={'selective_dynamics': [a['selective_dynamics'] for a in atoms]})
    text = Poscar(structure, comment='CAT local surface snapshot ' + snap['snapshot_id'],
                  selective_dynamics=[a['selective_dynamics'] for a in atoms]).get_str(direct=snap['coordinate_mode'] == 'direct', significant_figures=16)
    mapping = [{'poscar_row': row + 1, 'snapshot_index': i, 'atom_id': snap['atoms'][i]['atom_id']}
               for row, i in enumerate(indices)]
    return text, mapping


def viewer_geometry(snap):
    text, mapping = poscar_export(snap)
    info = validate_poscar(text)
    summary = build_structure_summary(poscar_text=text, elements=list(info.elements), counts=list(info.counts))
    geometry = build_structure_geometry(snap['snapshot_id'], summary)
    for site, row in zip(geometry['sites'], mapping):
        site['atom_id'] = row['atom_id']
    return geometry
