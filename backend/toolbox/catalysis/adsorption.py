"""Bounded geometric adsorption preparation, with explicit anchor/frame.

Only top-side single adsorbates are constructed. Sites are nodes, edge
midpoints and acute triangle barycentres of scipy's periodic Delaunay mesh;
they carry no energetic ranking or fcc/hcp chemical classification.
"""
from __future__ import annotations

import copy
import itertools
import math
import uuid

import numpy as np
from pymatgen.core import Element, Lattice
from scipy.spatial import Delaunay, QhullError

from ..contracts import ToolboxError
from . import science

MAX_ADSORBATE_ATOMS = 128
MAX_XYZ_BYTES = 65536
MAX_TOP_ATOMS = 128
MAX_SITES = 256
MAX_CANDIDATES = 16
MAX_CANDIDATE_ATOMS = 8192
HARD_OVERLAP_ANGSTROM = 1e-4
TRANSFORM_VERSION = 'cat-adsorption-1'


def error(code, message):
    raise ToolboxError(code, message, 422)


def parse_adsorbate(source, anchor_index):
    supplied = source.model_dump(mode='json', exclude_unset=True)
    if source.kind == 'atom':
        rows = [(source.element, [0., 0., 0.])]
        source_bytes = science.canonical({'kind': 'atom', 'element': source.element})
    else:
        text = '2\nSYNTHETIC CO, C anchor, bond 1.15 A; not optimized\nC 0 0 0\nO 0 0 1.15\n' if source.kind == 'co_example' else source.content
        source_bytes = text.encode('utf-8')
        if len(source_bytes) > MAX_XYZ_BYTES:
            error('CAT_XYZ_SIZE_LIMIT', 'XYZ 正文超过 64 KiB 上限')
        lines = text.splitlines()
        try:
            count_text = lines[0].strip()
            if not count_text.isascii() or not count_text.isdecimal():
                raise ValueError('count')
            count = int(count_text)
            if not 1 <= count <= MAX_ADSORBATE_ATOMS:
                error('CAT_ADSORBATE_ATOM_LIMIT', '单个吸附物须有 1 至 128 个原子')
            # One plain XYZ frame. Extra columns/frames are rejected rather
            # than silently dropping charge, properties or another molecule.
            if len(lines) != count + 2:
                raise ValueError('single XYZ frame')
            rows = []
            for line in lines[2:]:
                fields = line.split()
                if len(fields) != 4:
                    raise ValueError('XYZ row')
                rows.append((fields[0], [float(v) for v in fields[1:]]))
        except (ValueError, IndexError, TypeError) as exc:
            raise ToolboxError('CAT_XYZ_INVALID', '只接受单帧 XYZ：原子数、注释行、恰好 N 行元素与 x y z（Å），无附加列', 422) from exc
    coords = np.array([r[1] for r in rows])
    if not np.isfinite(coords).all() or np.abs(coords).max() > 1000:
        error('CAT_ADSORBATE_COORDINATES_INVALID', 'XYZ 坐标须有限，绝对值不超过 1000 Å')
    for symbol, _ in rows:
        if not Element.is_valid_symbol(symbol):
            error('CAT_ADSORBATE_ELEMENT_INVALID', '吸附物须使用有效元素符号（区分大小写）')
    if not 0 <= anchor_index < len(rows):
        error('CAT_ANCHOR_INVALID', '锚定原子的零起始索引超出吸附物原子范围')
    if len(coords) > 1:
        differences = coords[:, None, :] - coords[None, :, :]
        distances = np.linalg.norm(differences, axis=2)
        np.fill_diagonal(distances, np.inf)
        if distances.min() < HARD_OVERLAP_ANGSTROM:
            error('CAT_ADSORBATE_OVERLAP', 'XYZ 中存在完全或近乎重合的原子（距离低于 0.0001 Å）')
    source_id = 'ads_' + uuid.uuid4().hex
    return {'source_id': source_id, 'source_sha256': science.sha(source_bytes), 'source': supplied,
            'atoms': [{'atom_id': f'{source_id}_a{i + 1}', 'element': symbol, 'cartesian': list(pos)}
                      for i, (symbol, pos) in enumerate(rows)],
            'anchor_index': anchor_index, 'coordinate_frame': 'orthonormal_surface_frame'}


def frame_and_unwrapped(surface):
    snap, geometry = surface['snapshot'], surface['surface']
    matrix = np.array(snap['lattice'], dtype=float)
    normal = np.array(geometry['normal'])
    x = matrix[0] / np.linalg.norm(matrix[0])
    y = np.cross(normal, x)
    frame = {'x': x.tolist(), 'y': y.tolist(), 'z': normal.tolist(),
             'rotation_convention': 'fixed_surface_xyz_X_then_Y_then_Z'}
    coords = np.array([a['cartesian'] for a in snap['atoms']])
    projected = coords @ normal
    period, origin = geometry['normal_period_angstrom'], geometry['projection_origin_angstrom']
    unfolded = origin + np.mod(projected - origin, period)
    unfolded[np.isclose(unfolded, origin + period, atol=1e-8, rtol=0)] = origin
    images = np.rint((unfolded - projected) / period).astype(int)
    # Move by the entire c vector. A normal-only translation would corrupt
    # in-plane identity for an oblique c or a slab straddling the boundary.
    unwrapped = coords + images[:, None] * matrix[2]
    return matrix, frame, unwrapped


def canonical_ab(point, lattice):
    fractional = lattice.get_fractional_coords(point)
    fractional[:2] -= np.floor(fractional[:2] + 1e-10)
    fractional[:2][np.isclose(fractional[:2], 0, atol=1e-10)] = 0
    return lattice.get_cartesian_coords(fractional), fractional


def find_sites(surface, body):
    matrix, frame, unwrapped = frame_and_unwrapped(surface)
    lattice = Lattice(matrix)
    normal = np.array(frame['z'])
    axes = np.array([frame['x'], frame['y']])
    snap = surface['snapshot']
    top_ids = set(surface['surface']['layers'][-1]['atom_ids'])
    indices = [i for i, a in enumerate(snap['atoms']) if a['atom_id'] in top_ids]
    sites = []
    # An orthogonal normal vector isolates a/b-periodic distance reduction.
    planar_lattice = Lattice([matrix[0], matrix[1], normal * 10000])
    seen = {}
    tolerance = body.dedup_tolerance_angstrom
    inverse = np.linalg.inv(matrix)
    fractional_tolerance = tolerance * np.linalg.norm(inverse[:, :2], axis=0)
    bins = np.maximum(1, np.floor(1 / fractional_tolerance).astype(int))

    def add(kind, point, source_ids, label=None):
        point, fractional = canonical_ab(np.array(point), lattice)
        # Spatial buckets in periodic a/b, followed by a true Å check. Keep
        # distinct kinds even if their geometry coincides, e.g. user manual.
        key = np.floor(np.mod(fractional[:2], 1) * bins).astype(int) % bins
        nearby = []
        for offset in itertools.product((-1, 0, 1), repeat=2):
            bucket = (kind, *((key + offset) % bins).tolist())
            nearby.extend(seen.get(bucket, []))
        for old in nearby:
            delta = point - np.array(old['cartesian'])
            dist, _ = planar_lattice.get_distance_and_image([0, 0, 0], planar_lattice.get_fractional_coords(delta))
            if dist <= tolerance:
                return
        if len(sites) >= MAX_SITES:
            error('CAT_SITE_COUNT_LIMIT', '周期去重后的位点超过 256 个；请缩小表面或减少位点类型，未截断结果')
        site = {'site_id': 'site_' + uuid.uuid4().hex, 'kind': kind, 'label': label or f'{kind} {len(sites) + 1}',
                'cartesian': point.tolist(), 'fractional': fractional.tolist(),
                'source_atom_ids': list(dict.fromkeys(source_ids))}
        sites.append(site)
        seen.setdefault((kind, *key.tolist()), []).append(site)

    if body.kinds:
        lengths = [np.linalg.norm(v) for v in matrix[:2]]
        dot = abs(float(np.dot(matrix[0], matrix[1])))
        squared_minimum = min(lengths) ** 2
        # A finite periodic mesh is only offered for an already reduced
        # in-plane basis; importing a highly sheared cell remains supported,
        # but its automatic neighbour graph cannot use a guessed image range.
        reduced = dot <= squared_minimum / 2 + 1e-8 * squared_minimum
        if len(indices) > MAX_TOP_ATOMS or max(lengths) / min(lengths) > 4 or not reduced:
            error('CAT_AUTO_SITE_BUDGET_LIMIT', '自动位点限顶层 128 原子、a/b 长度比 ≤4，且 |a·b|≤min(|a|²,|b|²)/2 的二维约化晶胞；请缩小或显式调整晶胞，也可使用手动位点。未抽样')
        top = [canonical_ab(unwrapped[i], lattice)[0] for i in indices]
        ids = [snap['atoms'][i]['atom_id'] for i in indices]
        if 'ontop' in body.kinds:
            for point, ident in zip(top, ids):
                add('ontop', point, [ident])
        if 'bridge' in body.kinds or 'hollow' in body.kinds:
            # Bound before allocating or triangulating: at most 128*25 points.
            mesh, mesh_ids = [], []
            for i, j in itertools.product(range(-2, 3), repeat=2):
                for point, ident in zip(top, ids):
                    mesh.append(point + i * matrix[0] + j * matrix[1])
                    mesh_ids.append(ident)
            mesh = np.array(mesh)
            try:
                triangles = Delaunay(mesh @ axes.T).simplices
            except (QhullError, ValueError) as exc:
                raise ToolboxError('CAT_SITE_TRIANGULATION_FAILED', '顶层位点无法可靠三角化；请检查重合/退化表面，或使用手动位点', 422) from exc

            def central(point):
                uv = lattice.get_fractional_coords(point)[:2]
                return bool(np.all(uv >= -1e-10) and np.all(uv < 1 - 1e-10))

            for triangle in triangles:
                points = mesh[triangle]
                if 'bridge' in body.kinds:
                    for a, b in ((0, 1), (1, 2), (2, 0)):
                        point = (points[a] + points[b]) / 2
                        if central(point):
                            add('bridge', point, [mesh_ids[triangle[a]], mesh_ids[triangle[b]]])
                point = points.mean(axis=0)
                if 'hollow' in body.kinds and central(point):
                    planar = points @ axes.T
                    acute = all(np.dot(planar[(i + 1) % 3] - planar[i], planar[(i + 2) % 3] - planar[i]) > 1e-8 for i in range(3))
                    if acute:
                        add('hollow', point, [mesh_ids[i] for i in triangle])
    top_height = float((unwrapped @ normal).max())
    for manual in body.manual_sites:
        point = manual.uv[0] * matrix[0] + manual.uv[1] * matrix[1] + top_height * normal
        label = manual.label.strip() if manual.label else None
        if label and any(ord(c) < 32 for c in label):
            error('CAT_SITE_LABEL_INVALID', '手动位点名称不能包含控制字符')
        add('manual', point, [], label)
    if not sites:
        error('CAT_NO_ADSORPTION_SITES', '未生成所选类型的几何位点；可选择顶位或添加手动位置')
    return sites, frame


def rotation_matrix(angles):
    x, y, z = np.radians(angles)
    rx = np.array([[1, 0, 0], [0, np.cos(x), -np.sin(x)], [0, np.sin(x), np.cos(x)]])
    ry = np.array([[np.cos(y), 0, np.sin(y)], [0, 1, 0], [-np.sin(y), 0, np.cos(y)]])
    rz = np.array([[np.cos(z), -np.sin(z), 0], [np.sin(z), np.cos(z), 0], [0, 0, 1]])
    return rz @ ry @ rx


def nearest_self_image(lattice, fractional):
    """Exact bounded nonzero-image minimum, excluding intramolecular bonds.

    A lattice basis image gives an upper bound. Reciprocal-space coordinate
    bounds include every image that can beat it, including oblique c images.
    Refuse an oversized search before creating distance arrays.
    """
    upper = min(lattice.abc)
    inverse = np.linalg.inv(lattice.matrix)
    bounds = np.ceil(np.ptp(fractional, axis=0) + upper * np.linalg.norm(inverse, axis=0) + 1e-10).astype(int)
    images = math.prod(2 * int(b) + 1 for b in bounds) - 1
    if images > 4096 or images * len(fractional) ** 2 > 4_000_000:
        error('CAT_PERIODIC_CHECK_LIMIT', '吸附物尺寸或晶胞倾斜使周期副本检查超出预算；请缩小分子/显式调整晶胞')
    delta = fractional[:, None, :] - fractional[None, :, :]
    nearest = upper
    for image in itertools.product(*(range(-int(b), int(b) + 1) for b in bounds)):
        if image == (0, 0, 0):
            continue
        cartesian = (delta + image) @ lattice.matrix
        nearest = min(nearest, float(np.linalg.norm(cartesian, axis=2).min()))
    return nearest


def build_candidate(surface, adsorbate, site, placement, parent_revision):
    parent = surface['snapshot']
    if len(parent['atoms']) + len(adsorbate['atoms']) > science.MAX_ATOMS:
        error('CAT_CANDIDATE_ATOM_LIMIT', '表面加吸附物超过每候选 2048 个原子上限')
    matrix, frame, unwrapped = frame_and_unwrapped(surface)
    lattice = Lattice(matrix)
    basis = np.array([frame['x'], frame['y'], frame['z']]).T
    local = np.array([a['cartesian'] for a in adsorbate['atoms']])
    anchor = local[adsorbate['anchor_index']].copy()
    rotation = rotation_matrix(placement.rotation_degrees)
    target = np.array(site['cartesian']) + placement.height_angstrom * basis[:, 2]
    placed = (local - anchor) @ rotation.T @ basis.T + target
    if not np.isfinite(placed).all() or np.abs(placed).max() > 1e6:
        error('CAT_ADSORPTION_COORDINATES_INVALID', '变换后的吸附物坐标无效')
    normal = basis[:, 2]
    top = float((unwrapped @ normal).max())
    bottom_next = surface['surface']['projection_origin_angstrom'] + surface['surface']['normal_period_angstrom']
    projected = placed @ normal
    # A single top-side molecule must stay in the selected nuclei-free gap;
    # no wrapping across c to the other face, or silently embedding atoms.
    if projected.min() < top - HARD_OVERLAP_ANGSTROM or projected.max() >= bottom_next - HARD_OVERLAP_ANGSTROM:
        error('CAT_ADSORPTION_OUTSIDE_TOP_GAP', '吸附物超出顶面与下一个周期底面之间的空隙；请调整高度、旋转或真空，不自动换面/包裹')
    fractional = lattice.get_fractional_coords(placed)
    surface_frac = [a['fractional'] for a in parent['atoms']]
    minimum = float(lattice.get_all_distances(fractional, surface_frac).min())
    image_minimum = nearest_self_image(lattice, fractional)
    if min(minimum, image_minimum) < HARD_OVERLAP_ANGSTROM:
        error('CAT_ADSORPTION_OVERLAP', '吸附物与表面或其周期副本完全/近乎重合（低于 0.0001 Å）；请调整位点、高度或晶胞')
    threshold = placement.screening_distance_angstrom
    passed = min(minimum, image_minimum) >= threshold
    warnings = ['仅几何初始构型；距离筛查阈值单位 Å，由用户指定，不是普适成键、化学合理性或稳定性判据。']
    if not passed:
        warnings.append(f'最近表面距离 {minimum:.6g} Å，最近吸附物周期副本距离 {image_minimum:.6g} Å；低于用户筛查阈值 {threshold:g} Å。')
    ident = 'cand_' + uuid.uuid4().hex
    atoms = copy.deepcopy(parent['atoms'])
    for i, (source, cartesian, frac) in enumerate(zip(adsorbate['atoms'], placed, fractional)):
        atoms.append({'atom_id': f'{ident}_ads{i + 1}', 'element': source['element'],
                      'fractional': frac.tolist(), 'cartesian': cartesian.tolist(), 'selective_dynamics': [True] * 3,
                      'provenance': {'kind': 'adsorbate', 'source_id': adsorbate['source_id'],
                                     'source_atom_id': source['atom_id'], 'source_index': i,
                                     'mapping': 'explicit_anchor_rigid_transform; new_candidate_atom_identity'}})
    snap = {'snapshot_id': 'snap_' + uuid.uuid4().hex, 'coordinate_mode': parent['coordinate_mode'],
            'lattice': copy.deepcopy(parent['lattice']), 'atoms': atoms}
    snap['sha256'] = science.sha(science.canonical(snap))
    return {'candidate_id': ident, 'site_id': site['site_id'], 'label': site['label'],
            'parent_revision': parent_revision, 'parent_surface_id': surface['surface_id'],
            'parent_snapshot_sha256': parent['sha256'], 'adsorbate_source_id': adsorbate['source_id'],
            'status': 'valid', 'snapshot': snap, 'placement': placement.model_dump(mode='json'),
            'validation': {'minimum_adsorbate_surface_distance_angstrom': minimum,
                           'minimum_periodic_self_image_distance_angstrom': image_minimum,
                           'screening_distance_angstrom': threshold, 'screening_passed': passed, 'warnings': warnings},
            'transform': {'version': TRANSFORM_VERSION, 'kind': 'single_adsorbate_rigid_transform',
                          'parent_snapshot_id': parent['snapshot_id'], 'parent_snapshot_sha256': parent['sha256'],
                          'adsorbate_source_sha256': adsorbate['source_sha256'], 'anchor_index': adsorbate['anchor_index'],
                          'anchor_source_atom_id': adsorbate['atoms'][adsorbate['anchor_index']]['atom_id'],
                          'site': copy.deepcopy(site), 'frame': frame, 'rotation_matrix': rotation.tolist(),
                          'anchor_target_cartesian': target.tolist(), 'surface_atoms_unchanged': True,
                          'new_adsorbate_flags': [True, True, True], 'selected_side': 'top',
                          'periodic_distance_method': 'pymatgen_shortest_vectors_and_bounded_nonzero_image_search',
                          'hard_overlap_angstrom': HARD_OVERLAP_ANGSTROM}}
