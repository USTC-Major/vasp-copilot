"""Bounded VASP adapters. Input directory contains only uploaded snapshots."""
from __future__ import annotations

import importlib.metadata
from pathlib import Path
import warnings
import xml.etree.ElementTree as ET

import numpy as np
from pymatgen.io.vasp.inputs import Incar, Kpoints, Poscar
from pymatgen.io.vasp.outputs import Eigenval, Vasprun

from ..contracts import ToolboxError

MAX_VALUES = 2_000_000
NAMES = {'vasprun.xml', 'DOSCAR', 'EIGENVAL', 'KPOINTS', 'POSCAR', 'CONTCAR', 'INCAR'}


def fail(message, code='PP_INVALID_DATA'):
    raise ToolboxError(code, message)


def finite(value):
    array = np.asarray(value, dtype=float)
    if array.size > MAX_VALUES:
        fail('数据超过当前分析规模，请选择较小的数据集', 'PP_TOO_LARGE')
    if not np.isfinite(array).all():
        fail('数据包含非有限数值，不能绘图')
    return array


def spin_mode(settings):
    if settings.get('LNONCOLLINEAR', False) or settings.get('LSORBIT', False):
        fail('首版暂不支持 NCL／SOC；请保留原文件，后续版本单独支持', 'PP_UNSUPPORTED_SPIN')
    spin = int(settings.get('ISPIN', 1))
    if spin not in (1, 2):
        fail('无法确认自旋模式')
    return spin


def preflight_xml(path):
    # Reject DTD/entity declarations before passing XML to either parser.
    previous = b''
    with path.open('rb') as file:
        while block := file.read(65536):
            scan = (previous + block).upper()
            if b'<!DOCTYPE' in scan or b'<!ENTITY' in scan or b'\x00' in scan:
                fail('仅支持不含 DTD／实体声明的 UTF-8 XML')
            previous = block[-16:]
    count, dos_depth, efermi = 0, 0, None
    for event, node in ET.iterparse(path, events=('start', 'end')):
        if event == 'start':
            if node.tag == 'dos':
                dos_depth += 1
            continue
        if dos_depth and node.tag == 'i' and node.get('name') == 'efermi':
            efermi = float(finite(node.text))
        if node.tag in ('r', 'v') and node.text:
            count += len(node.text.split())
            if count > MAX_VALUES * 4:
                fail('XML 数值规模超过当前解析上限', 'PP_TOO_LARGE')
        if node.tag == 'dos':
            dos_depth -= 1
        node.clear()
    return efermi


def channel_map(data, spin):
    result = {('up' if int(key) == 1 else 'down') if spin == 2 else 'total': finite(val).tolist()
              for key, val in data.items()}
    if set(result) != ({'up', 'down'} if spin == 2 else {'total'}):
        fail('自旋通道与输出模式不一致')
    return result


def dos_xml(run, spin):
    if run.tdos is None:
        fail('XML 中没有 DOS 数据，请选择 DOS 计算输出', 'PP_MISSING_DATA')
    energy = finite(run.tdos.energies)
    if len(energy) < 2 or not (np.diff(energy) > 0).all():
        fail('DOS 能量网格必须严格递增')
    channels = channel_map(run.tdos.densities, spin)
    projected = []
    for atom, orbitals in enumerate(run.pdos or []):
        for orbital, values in orbitals.items():
            projected.append({'atom': atom + 1, 'element': str(run.final_structure[atom].specie),
                              'orbital': str(orbital), 'channels': channel_map(values, spin)})
    for item in [channels, *(p['channels'] for p in projected)]:
        if any(len(v) != len(energy) for v in item.values()):
            fail('DOS 或投影网格长度不一致')
    return {'energy_ev': energy.tolist(), 'channels': channels, 'projected': projected,
            'density_unit': 'states/eV/cell', 'projected_unit': 'states/eV/atom'}


def doscar(path, settings):
    spin = spin_mode(settings)
    with path.open(encoding='utf-8') as file:
        header = file.readline().split()
        ions, partial = int(header[1]), int(header[2])
        if ions < 1 or partial not in (0, 1):
            fail('DOSCAR 文件头无效')
        for _ in range(4):
            if not file.readline():
                fail('DOSCAR 文件头被截断')
        info = finite(file.readline().split())
        count = int(info[2])
        if count != info[2] or count < 2 or count * (ions + 1) * (5 if spin == 2 else 3) > MAX_VALUES:
            fail('DOSCAR 点数或原子规模不受支持', 'PP_TOO_LARGE')
        rows = finite([file.readline().split() for _ in range(count)])
        if rows.shape != (count, 5 if spin == 2 else 3):
            fail('DOSCAR 总 DOS 布局与 ISPIN 不一致或文件截断')
        if not (np.diff(rows[:, 0]) > 0).all():
            fail('DOSCAR 能量网格必须严格递增')
        # Validate every projected block, even though this slice exposes total DOS only.
        columns = None
        for _ in range(ions if partial else 0):
            block_header = finite(file.readline().split())
            if len(block_header) != 5 or block_header[2] != count:
                fail('DOSCAR 投影块缺失或 NEDOS 不一致')
            block = finite([file.readline().split() for _ in range(count)])
            if block.ndim != 2 or block.shape[0] != count or block.shape[1] < 2:
                fail('DOSCAR 投影块被截断')
            if columns is not None and columns != block.shape[1]:
                fail('DOSCAR 投影块列数不一致')
            columns = block.shape[1]
            if not np.allclose(block[:, 0], rows[:, 0], atol=1e-7, rtol=0):
                fail('DOSCAR 投影能量网格不一致')
        if file.read().strip():
            fail('DOSCAR 含未识别的尾部数据')
    return {'energy_ev': rows[:, 0].tolist(),
            'channels': {'up': rows[:, 1].tolist(), 'down': rows[:, 2].tolist()} if spin == 2 else {'total': rows[:, 1].tolist()},
            'projected': [], 'density_unit': 'states/eV/cell', 'projected_unit': 'states/eV/atom'}, float(info[3])


def eigenval_checked(path, spin):
    # The mature reader preallocates arrays and can silently accept missing blocks.
    with path.open(encoding='utf-8') as file:
        head = file.readline().split()
        if int(head[-1]) != spin:
            fail('EIGENVAL 与 INCAR 的自旋模式不一致')
        for _ in range(4):
            file.readline()
        _, nk, nb = map(int, file.readline().split())
        if nk < 2 or nb < 1 or nk * nb * spin * 2 > MAX_VALUES:
            fail('EIGENVAL 数据规模不受支持', 'PP_TOO_LARGE')
        for _ in range(nk):
            line = file.readline()
            while line and not line.strip():
                line = file.readline()
            if finite(line.split()).shape != (4,):
                fail('EIGENVAL k 点块缺失或无效')
            for band in range(nb):
                values = finite(file.readline().split())
                if values.shape != (5 if spin == 2 else 3,) or values[0] != band + 1:
                    fail('EIGENVAL 能带记录缺失或顺序错误')
        if file.read().strip():
            fail('EIGENVAL 实际记录多于声明数量')
    result = Eigenval(path)
    if len(result.kpoints) != nk:
        fail('EIGENVAL k 点数量不完整')
    return result


def band_data(eigen, kpoints, structure, path, spin):
    kp = Kpoints.from_file(path)
    if kp.style != Kpoints.supported_modes.Line_mode or not str(kp.coord_type).lower().startswith('r'):
        fail('普通 k 网格不能作为路径能带；请提供本次计算的倒空间 line-mode KPOINTS', 'PP_PATH_REQUIRED')
    n = kp.num_kpts
    points = finite(kpoints)
    endpoints = finite(kp.kpts)
    if n < 2 or len(endpoints) % 2 or len(points) != len(endpoints) // 2 * n:
        fail('KPOINTS 路径采样数与实际输出不一致')
    segments, x, offset = [], [], 0.0
    reciprocal = structure.lattice.reciprocal_lattice.matrix
    labels = kp.labels or [None] * len(endpoints)
    for index in range(0, len(endpoints), 2):
        start = index // 2 * n
        expected = np.linspace(endpoints[index], endpoints[index + 1], n)
        if not np.allclose(points[start:start+n], expected, rtol=0, atol=1e-5):
            fail('实际 k 点与所提供路径不匹配，请选择同一次计算的文件')
        cart = points[start:start+n] @ reciprocal
        distance = np.r_[0, np.cumsum(np.linalg.norm(np.diff(cart, axis=0), axis=1))]
        if distance[-1] <= 0:
            fail('路径包含零长度线段')
        x.extend((offset + distance).tolist())
        segments.append({'start': start, 'end': start+n-1, 'labels': [labels[index], labels[index+1]]})
        offset += distance[-1]
    channels = channel_map({key: finite(value)[:, :, 0] for key, value in eigen.items()}, spin)
    if any(len(v) != len(points) for v in channels.values()):
        fail('能带数组与 k 点数量不一致')
    return {'distance_inv_angstrom': x, 'kpoints_fractional': points.tolist(),
            'reciprocal_lattice_2pi': reciprocal.tolist(), 'segments': segments,
            'channels': channels, 'band_count': len(next(iter(channels.values()))[0])}


def parse(directory: Path, kind: str):
    messages = []
    efermi = None
    convergence = 'unknown'
    if (directory / 'vasprun.xml').exists():
        xml_fermi = preflight_xml(directory / 'vasprun.xml')
        with warnings.catch_warnings(record=True) as caught:
            run = Vasprun(directory / 'vasprun.xml', parse_dos=kind == 'dos', parse_eigen=kind == 'band',
                          parse_projected_eigen=False, parse_potcar_file=False, exception_on_bad_xml=True)
        if caught:
            messages.append('解析库返回提示；请结合原始计算检查结果，不以读入成功代替收敛判断。')
        spin = spin_mode(run.parameters)
        if (directory / 'INCAR').exists():
            supplied = Incar.from_file(directory / 'INCAR')
            if spin_mode(supplied) != spin:
                fail('所选 INCAR 与 XML 自旋模式冲突，请使用同次计算的文件')
        if int(run.parameters.get('IBRION', -1)) == 0:
            fail('首版不分析分子动力学预测态，请提供静态计算结果', 'PP_UNSUPPORTED_MODE')
        if int(run.parameters.get('NSW', 0)) > 0:
            messages.append('输入包含离子步；建议使用后续静态计算输出分析电子结构。')
        # parse_dos=False skips the DOS node (including efermi) in Vasprun;
        # retain the explicitly stored value found by the bounded XML pass.
        efermi = float(finite(run.efermi)) if run.efermi is not None else xml_fermi
        convergence = 'converged' if run.converged else 'not_converged'
        if kind == 'dos':
            data = dos_xml(run, spin)
        else:
            if not (directory / 'KPOINTS').exists():
                fail('能带还需要同次计算的 line-mode KPOINTS', 'PP_MISSING_DATA')
            data = band_data(run.eigenvalues, run.actual_kpoints, run.final_structure, directory / 'KPOINTS', spin)
    else:
        if not (directory / 'INCAR').exists():
            fail('请补充同次计算的 INCAR，以确认自旋／SOC 模式', 'PP_MISSING_DATA')
        settings = Incar.from_file(directory / 'INCAR')
        spin = spin_mode(settings)
        if int(settings.get('IBRION', -1)) == 0:
            fail('首版不分析分子动力学预测态，请提供静态计算结果', 'PP_UNSUPPORTED_MODE')
        if int(settings.get('NSW', 0)) > 0:
            messages.append('输入来自离子步计算；DOS 可能是步平均值，建议使用后续静态计算输出。')
        if kind == 'dos':
            if not (directory / 'DOSCAR').exists():
                fail('请选择 vasprun.xml 或 DOSCAR＋INCAR', 'PP_MISSING_DATA')
            data, efermi = doscar(directory / 'DOSCAR', settings)
            messages.append('DOSCAR 当前显示总 DOS；原子／轨道投影请导入 vasprun.xml。')
        else:
            structure_path = next((directory / name for name in ('POSCAR', 'CONTCAR') if (directory / name).exists()), None)
            if structure_path is None or not all((directory / name).exists() for name in ('EIGENVAL', 'KPOINTS')):
                fail('需要 EIGENVAL、INCAR、POSCAR（或 CONTCAR）和 line-mode KPOINTS', 'PP_MISSING_DATA')
            structure = Poscar.from_file(structure_path, check_for_potcar=False).structure
            eigen = eigenval_checked(directory / 'EIGENVAL', spin)
            data = band_data(eigen.eigenvalues, eigen.kpoints, structure, directory / 'KPOINTS', spin)
            messages.append('EIGENVAL 不含费米能级；当前使用原始能量，可手动填写可信参考值。')
    if convergence != 'converged':
        messages.append('计算收敛状态为未确认或未收敛；曲线读取成功不代表科学结果已验收。')
    def count(value):
        if isinstance(value, dict):
            return sum(count(v) for v in value.values())
        if isinstance(value, list):
            return sum(count(v) for v in value)
        return int(isinstance(value, (float, int)))
    if count(data) > MAX_VALUES:
        fail('归一化数据超过当前分析规模', 'PP_TOO_LARGE')
    return {'schema_version': 'pp.v1', 'kind': kind, 'spin_mode': 'collinear' if spin == 2 else 'non_spin',
            'soc': False, 'efermi_ev': efermi, 'convergence': convergence, 'completeness': 'complete',
            'warnings': messages, 'parser': {'library': 'pymatgen', 'version': importlib.metadata.version('pymatgen'), 'adapter': '1'},
            'data': data}
