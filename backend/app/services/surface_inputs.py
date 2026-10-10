"""Validate an unchanged surface cell and its immutable CAT atom/flag mapping.

Reciprocal coordinates are row-vector coefficients of the POSCAR dual lattice.
For skew c, the third coefficient varies to keep every sampled k in the a/b
plane. Setting a third mesh subdivision to one alone would not achieve this.
"""
from __future__ import annotations

import hashlib
import json
import math
import numpy as np

from backend.input_validation import InputValidationError, validate_poscar
from backend.app.schemas.surface import SurfacePolicy

SOURCES = [
    'https://vasp.at/wiki/index.php/ISIF',
    'https://vasp.at/wiki/Surfaces,_thin_films,_and_2D_materials',
    'https://vasp.at/wiki/index.php/KPOINTS',
]
WARNINGS = [
    '表面松弛只优化允许移动的原子（ISIF=2），固定整个晶胞；固定原子不等于固定晶胞。',
    '二维 Gamma 采样沿 a/b 张成的面内倒空间，法向仅取一个采样；网格密度、真空和厚度仍须做收敛检查。',
    '不自动判断极性、重构、磁性、带电或偶极修正；请人工核对适用性和计算参数。',
]


def canonical_sha(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(',', ':'), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def surface_basis(matrix, declared_normal):
    cell = np.asarray(matrix, dtype=float)
    normal = np.asarray(declared_normal, dtype=float)
    if (cell.shape != (3, 3) or normal.shape != (3,) or
            not np.isfinite(cell).all() or not np.isfinite(normal).all()):
        raise InputValidationError('CAT_SURFACE_CONTEXT_INVALID', '表面晶胞或法向无效')
    cross = np.cross(cell[0], cell[1])
    length = np.linalg.norm(cross)
    if length <= 1e-8 or np.linalg.det(cell) <= 1e-6:
        raise InputValidationError('CAT_SURFACE_CONTEXT_INVALID', '表面晶胞退化或不是右手系')
    actual_normal = cross / length
    if not np.allclose(normal, actual_normal, rtol=0, atol=1e-8):
        raise InputValidationError('CAT_SURFACE_CONTEXT_INVALID', '保存的表面法向与 a×b 不一致')
    projection = np.dot(cell[2], actual_normal)
    c_parallel = bool(np.linalg.norm(cell[2] - projection * actual_normal) <= 1e-8 * np.linalg.norm(cell[2]))
    # Dual of a/b restricted to their plane, without the conventional 2π.
    plane = cell[:2]
    dual = np.linalg.solve(plane @ plane.T, plane)
    return cell, actual_normal, dual, c_parallel


def surface_policy(matrix, normal):
    _, actual_normal, _, c_parallel = surface_basis(matrix, normal)
    warnings = list(WARNINGS)
    if not c_parallel:
        warnings.append('c 含面内分量：使用显式纯面内二维采样，第三倒格坐标随点变化；不改变晶胞或约束。该模式不提供四面体积分表。')
    return SurfacePolicy(normal=tuple(actual_normal), c_parallel_to_normal=c_parallel,
                         kpoint_mode='gamma_2d' if c_parallel else 'explicit_gamma_2d',
                         sources=SOURCES, warnings=warnings)


def validate_surface_context(structure):
    binding, policy = structure.catalysis_binding, structure.surface_policy
    if binding is None and policy is None:
        return
    if binding is None or policy is None:
        raise InputValidationError('CAT_BINDING_INVALID', '表面策略与不可变 CAT 来源须同时存在')
    snap = binding.snapshot
    payload = {key: value for key, value in snap.items() if key != 'sha256'}
    if (canonical_sha(payload) != binding.snapshot_sha256 or
            snap.get('sha256') != binding.snapshot_sha256 or
            snap.get('snapshot_id') != binding.snapshot_id):
        raise InputValidationError('CAT_BINDING_INVALID', 'CAT 快照身份或哈希不匹配')
    clean = binding.parent_clean_snapshot
    if (canonical_sha({k: v for k, v in clean.items() if k != 'sha256'}) != binding.parent_clean_snapshot_sha256 or
            clean.get('snapshot_id') != binding.parent_clean_snapshot_id):
        raise InputValidationError('CAT_BINDING_INVALID', '清洁表面父快照身份或哈希不匹配')
    info = validate_poscar(structure.poscar_text, include_coordinates=True)
    if hashlib.sha256(structure.poscar_text.encode()).hexdigest() != binding.poscar_sha256:
        raise InputValidationError('CAT_BINDING_INVALID', '传入 POSCAR 已改变；请重新从 CAT 传入')
    expected = surface_policy(info.matrix, binding.surface['normal'])
    if (expected.model_dump(exclude={'normal'}) != policy.model_dump(exclude={'normal'}) or
            not np.allclose(expected.normal, policy.normal, rtol=0, atol=1e-10) or
            not np.allclose(info.matrix, snap['lattice'], rtol=0, atol=1e-10)):
        raise InputValidationError('CAT_BINDING_INVALID', '表面策略或晶胞与快照不一致')
    atoms = snap['atoms']
    rows = binding.poscar_row_mapping
    if (len(atoms) != info.atom_count or len(rows) != info.atom_count or
            {row.snapshot_index for row in rows} != set(range(len(atoms))) or
            len({atom['atom_id'] for atom in atoms}) != len(atoms)):
        raise InputValidationError('CAT_BINDING_INVALID', '原子映射数量或唯一性无效')
    species = [element for element, count in zip(info.elements, info.counts) for _ in range(count)]
    coords = np.asarray(info.coordinates, dtype=float)
    if info.coordinate_mode == 'direct':
        coords = coords @ np.asarray(info.matrix)
    else:
        coords *= np.asarray(info.cartesian_scale)
    flags = info.selective_flags if info.selective_dynamics else [[True]*3 for _ in atoms]
    for index, row in enumerate(rows):
        atom = atoms[row.snapshot_index]
        if (row.poscar_row != index + 1 or row.atom_id != atom['atom_id'] or species[index] != atom['element'] or
                list(flags[index]) != atom['selective_dynamics'] or
                not np.allclose(coords[index], atom['cartesian'], rtol=0, atol=1e-8)):
            raise InputValidationError('CAT_BINDING_INVALID', '原子顺序、坐标或有效约束与 CAT 快照不一致')


def surface_grid(matrix, normal, kppa, atom_count):
    """2D density retains the existing KPPA-per-atom budget and a/b aspect ratio.

    A near-square mesh in reciprocal length has N1*N2 >= KPPA/Natoms.
    Its dimensions do not shrink when only vacuum c is enlarged.
    """
    _, _, dual, _ = surface_basis(matrix, normal)
    lengths = np.linalg.norm(dual, axis=1)
    target = kppa / atom_count
    n1 = max(1, math.ceil(math.sqrt(target * lengths[0] / lengths[1])))
    n2 = max(1, math.ceil(math.sqrt(target * lengths[1] / lengths[0])))
    if n1 * n2 > 65536:
        raise InputValidationError('CAT_KPOINT_BUDGET_LIMIT', '二维采样超过 65536 点；请调整结构或精度后重试')
    return [n1, n2, 1]
