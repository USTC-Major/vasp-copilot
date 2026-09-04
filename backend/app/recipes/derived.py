"""DerivedParameterResolver（设计文档 4.1 节第 7 步、8.2/10.4 节）。

白名单注册表：派生参数只能调用注册函数；无 eval/import。
每个函数返回 typed value，不返回 INCAR 文本。

注册函数：
- generate_magmom_from_structure：按 POSCAR 元素顺序展开 MAGMOM，长度==原子数
- generate_ldau_arrays：按 POSCAR 元素顺序生成 LDAUL/LDAUU/LDAUJ，长度==元素种类数
- generate_kpoint_grid：结构 + KPPA + 原子数 → 均匀网格与 centering
- derive_system_label：化学式 + task → SYSTEM 标签
- derive_encut_from_precision：精度档位 → ENCUT 初始推荐
"""

from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Sequence, Tuple

from backend.app.recipes.errors import DerivedParameterUnresolved
from backend.app.schemas.recipe import DerivedParameterRef

TRANSITION_METALS = {
    "Sc", "Ti", "V", "Cr", "Mn", "Fe", "Co", "Ni", "Cu", "Zn",
    "Y", "Zr", "Nb", "Mo", "Tc", "Ru", "Rh", "Pd", "Ag", "Cd",
    "Hf", "Ta", "W", "Re", "Os", "Ir", "Pt", "Au",
}

DEFAULT_TM_MOMENT = 5.0
DEFAULT_OTHER_MOMENT = 0.6

ENCUT_BY_PRECISION: Dict[str, float] = {
    "quick": 400.0,
    "standard": 520.0,
    "high": 600.0,
}

# KPPA 配置表（10.6 节：数值由 quick/standard/high 版本化决定）
# relax/static/dos 行为 KPPA 语义：N_total ≈ kppa / atom_count，各方向 n_i ∝ |b_i|。
# band 行不是 KPPA，而是 line-mode 每条线段的插值点数（divisions）。
KPPA_TABLE: Dict[str, Dict[str, float]] = {
    "relax": {"quick": 500.0, "standard": 1000.0, "high": 1500.0},
    "static": {"quick": 500.0, "standard": 1000.0, "high": 1500.0},
    "dos": {"quick": 800.0, "standard": 1500.0, "high": 2000.0},
    "band": {"quick": 40.0, "standard": 60.0, "high": 80.0},  # line-mode 每线段插值点数
}

# 晶格几何校验容差：matrix 为唯一真值，abc/angles 仅做一致性交叉校验，
# 容差需覆盖 POSCAR 文本舍入（如 4.356 vs 4.356048）级别的不一致。
_LENGTH_REL_TOL = 1e-4
_ANGLE_ABS_TOL_DEG = 0.01
_MIN_CELL_VOLUME = 1e-10
_MIN_SIN_GAMMA = 1e-12


def generate_magmom_from_structure(inputs: Dict[str, Any]) -> List[float]:
    """按 POSCAR 元素顺序逐原子展开初始磁矩。

    inputs: elements, counts, element_initial_moments(可选用户确认值)。
    """

    elements: Sequence[str] = inputs["elements"]
    counts: Sequence[int] = inputs["counts"]
    user_moments: Dict[str, float] = inputs.get("element_initial_moments") or {}
    if len(elements) != len(counts):
        raise DerivedParameterUnresolved(
            "elements/counts length mismatch in MAGMOM derivation",
            details={"elements": list(elements), "counts": list(counts)},
        )
    magmom: List[float] = []
    for element, count in zip(elements, counts):
        if element in user_moments:
            moment = float(user_moments[element])
        elif element in TRANSITION_METALS:
            moment = DEFAULT_TM_MOMENT
        else:
            moment = DEFAULT_OTHER_MOMENT
        magmom.extend([moment] * int(count))
    return magmom


def generate_ldau_arrays(inputs: Dict[str, Any]) -> Dict[str, List[float]]:
    """按 POSCAR 元素顺序生成 LDAUL/LDAUU/LDAUJ。

    inputs: elements, dftu_entries=[{element,l,u_ev,j_ev}]。
    未施加 U 的元素显式为 L=-1, U=0, J=0（10.5 节）。
    """

    elements: Sequence[str] = inputs["elements"]
    entries: Sequence[Dict[str, Any]] = inputs.get("dftu_entries") or []
    entry_by_element: Dict[str, Dict[str, Any]] = {}
    for entry in entries:
        element = entry["element"]
        if element not in set(elements):
            raise DerivedParameterUnresolved(
                f"DFT+U entry for unknown element {element!r}",
                details={"element": element, "structure_elements": list(elements)},
            )
        entry_by_element[element] = entry
    ldau_l: List[float] = []
    ldau_u: List[float] = []
    ldau_j: List[float] = []
    for element in elements:
        entry = entry_by_element.get(element)
        if entry is None:
            ldau_l.append(-1.0)
            ldau_u.append(0.0)
            ldau_j.append(0.0)
        else:
            ldau_l.append(float(entry["l"]))
            ldau_u.append(float(entry["u_ev"]))
            ldau_j.append(float(entry.get("j_ev", 0.0)))
    if len(ldau_l) != len(elements) or len(ldau_u) != len(elements) or len(ldau_j) != len(elements):
        raise DerivedParameterUnresolved(
            "LDAU array length mismatch",
            details={"elements": list(elements)},
        )
    return {"LDAUL": ldau_l, "LDAUU": ldau_u, "LDAUJ": ldau_j}


def _finite_number(value: Any, *, field: str) -> float:
    """校验单个数值：拒绝 bool、非 (int, float)、NaN 与 ±Inf，返回 float。

    NaN 能绕过普通大小比较（NaN > 0 为 False 但 NaN <= 0 也为 False），
    因此必须显式 isfinite，不能只靠 > 0 判断。
    """

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DerivedParameterUnresolved(
            f"{field} must be a finite real number, got {value!r}",
            details={"field": field, "value": repr(value)},
        )
    number = float(value)
    if not math.isfinite(number):
        raise DerivedParameterUnresolved(
            f"{field} must be a finite real number, got {value!r}",
            details={"field": field, "value": repr(value)},
        )
    return number


def _dot(u: Sequence[float], v: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(u, v))


def _cross(u: Sequence[float], v: Sequence[float]) -> List[float]:
    return [
        u[1] * v[2] - u[2] * v[1],
        u[2] * v[0] - u[0] * v[2],
        u[0] * v[1] - u[1] * v[0],
    ]


def _norm(u: Sequence[float]) -> float:
    return math.sqrt(_dot(u, u))


def _cell_volume(matrix: Sequence[Sequence[float]]) -> float:
    """V = a1 · (a2 × a3)（带符号）。"""

    return _dot(matrix[0], _cross(matrix[1], matrix[2]))


def _validated_matrix(raw: Any) -> List[List[float]]:
    if not isinstance(raw, (list, tuple)) or len(raw) != 3:
        raise DerivedParameterUnresolved(
            "lattice.matrix must be a 3x3 sequence",
            details={"matrix": repr(raw)},
        )
    matrix: List[List[float]] = []
    for row_index, row in enumerate(raw):
        if not isinstance(row, (list, tuple)) or len(row) != 3:
            raise DerivedParameterUnresolved(
                f"lattice.matrix row {row_index} must contain exactly 3 numbers",
                details={"row_index": row_index, "row": repr(row)},
            )
        matrix.append([
            _finite_number(value, field=f"lattice.matrix[{row_index}][{col}]")
            for col, value in enumerate(row)
        ])
    return matrix


def _angles_from_matrix(matrix: Sequence[Sequence[float]]) -> List[float]:
    """由 matrix 派生实空间角度 [alpha, beta, gamma]（度）。"""

    norms = [_norm(matrix[i]) for i in range(3)]

    def _angle(u: Sequence[float], v: Sequence[float], nu: float, nv: float) -> float:
        cosine = _dot(u, v) / (nu * nv)
        return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))

    return [
        _angle(matrix[1], matrix[2], norms[1], norms[2]),
        _angle(matrix[0], matrix[2], norms[0], norms[2]),
        _angle(matrix[0], matrix[1], norms[0], norms[1]),
    ]


def _cross_check_provided(lattice: Dict[str, Any], abc: List[float],
                          angles: List[float]) -> None:
    """matrix 为真值时，并行提供的 abc/angles 只做一致性交叉校验。

    明显不一致 → fail closed；绝不分别取用两套矛盾的晶格数据。
    """

    provided_abc = lattice.get("abc")
    if provided_abc:
        if not isinstance(provided_abc, (list, tuple)) or len(provided_abc) != 3:
            raise DerivedParameterUnresolved(
                "lattice.abc must contain exactly 3 numbers",
                details={"abc": repr(provided_abc)},
            )
        for index, value in enumerate(provided_abc):
            given = _finite_number(value, field=f"lattice.abc[{index}]")
            if given <= 0.0 or abs(given - abc[index]) > _LENGTH_REL_TOL * abc[index]:
                raise DerivedParameterUnresolved(
                    "lattice.abc contradicts lattice.matrix beyond tolerance",
                    details={"index": index, "abc": given, "from_matrix": abc[index],
                             "rel_tol": _LENGTH_REL_TOL},
                )
    provided_angles = lattice.get("angles")
    if provided_angles:
        if not isinstance(provided_angles, (list, tuple)) or len(provided_angles) != 3:
            raise DerivedParameterUnresolved(
                "lattice.angles must contain exactly 3 numbers",
                details={"angles": repr(provided_angles)},
            )
        for index, value in enumerate(provided_angles):
            given = _finite_number(value, field=f"lattice.angles[{index}]")
            if abs(given - angles[index]) > _ANGLE_ABS_TOL_DEG:
                raise DerivedParameterUnresolved(
                    "lattice.angles contradicts lattice.matrix beyond tolerance",
                    details={"index": index, "angles": given, "from_matrix": angles[index],
                             "abs_tol_deg": _ANGLE_ABS_TOL_DEG},
                )


def _matrix_from_abc_angles(abc: List[float], angles: List[float]) -> List[List[float]]:
    """标准晶胞重建：v1=(a,0,0)、v2=(b cosγ, b sinγ, 0)、v3 由 α/β/γ 确定。"""

    a, b, c = abc
    alpha, beta, gamma = angles
    cos_a = math.cos(math.radians(alpha))
    cos_b = math.cos(math.radians(beta))
    cos_g = math.cos(math.radians(gamma))
    sin_g = math.sin(math.radians(gamma))
    if sin_g <= _MIN_SIN_GAMMA:
        raise DerivedParameterUnresolved(
            "degenerate lattice: sin(gamma) is not safely positive",
            details={"gamma": gamma, "sin_gamma": sin_g},
        )
    shear = (cos_a - cos_b * cos_g) / sin_g
    radicand = 1.0 - cos_b * cos_b - shear * shear
    if radicand < -1e-12:
        raise DerivedParameterUnresolved(
            "inconsistent lattice angles: no real c-axis component",
            details={"angles": list(angles), "radicand": radicand},
        )
    return [
        [a, 0.0, 0.0],
        [b * cos_g, b * sin_g, 0.0],
        [c * cos_b, c * shear, c * math.sqrt(max(radicand, 0.0))],
    ]


def _lattice_geometry(
    lattice: Dict[str, Any],
) -> Tuple[List[List[float]], List[float], List[float]]:
    """解析晶格输入为单一几何真值 ``(matrix, abc, angles)``。

    - matrix 存在且合法时：matrix 为唯一几何真值，倒格矢长度、abc、angles
      全部由 matrix 派生；并行提供的 abc/angles 仅做容差内交叉校验，
      明显不一致直接 fail closed。
    - matrix 不存在时：要求完整 abc（三个正有限实数）+ 完整 angles
      （均在 (0, 180) 开区间），重建 matrix；**abc 无 angles 不再默认 90 度**。
    """

    raw_matrix = lattice.get("matrix")
    if raw_matrix:
        matrix = _validated_matrix(raw_matrix)
        volume = _cell_volume(matrix)
        if abs(volume) < _MIN_CELL_VOLUME:
            raise DerivedParameterUnresolved(
                "lattice.matrix is singular (|V| below threshold)",
                details={"volume": volume, "min_volume": _MIN_CELL_VOLUME},
            )
        abc = [_norm(matrix[i]) for i in range(3)]
        angles = _angles_from_matrix(matrix)
        _cross_check_provided(lattice, abc, angles)
        return matrix, abc, angles

    raw_abc = lattice.get("abc")
    raw_angles = lattice.get("angles")
    if not raw_abc or not raw_angles:
        raise DerivedParameterUnresolved(
            "lattice requires either a 3x3 matrix or complete abc + angles",
            details={"has_matrix": bool(raw_matrix), "has_abc": bool(raw_abc),
                     "has_angles": bool(raw_angles)},
        )
    if not isinstance(raw_abc, (list, tuple)) or len(raw_abc) != 3:
        raise DerivedParameterUnresolved(
            "lattice.abc must contain exactly 3 numbers",
            details={"abc": repr(raw_abc)},
        )
    if not isinstance(raw_angles, (list, tuple)) or len(raw_angles) != 3:
        raise DerivedParameterUnresolved(
            "lattice.angles must contain exactly 3 numbers",
            details={"angles": repr(raw_angles)},
        )
    abc = [_finite_number(value, field=f"lattice.abc[{index}]")
           for index, value in enumerate(raw_abc)]
    angles = [_finite_number(value, field=f"lattice.angles[{index}]")
              for index, value in enumerate(raw_angles)]
    if any(length <= 0.0 for length in abc):
        raise DerivedParameterUnresolved(
            "lattice.abc lengths must be positive", details={"abc": list(abc)}
        )
    for name, value in zip(("alpha", "beta", "gamma"), angles):
        if not 0.0 < value < 180.0:
            raise DerivedParameterUnresolved(
                f"lattice angle {name} must lie in the open interval (0, 180)",
                details={"angle": name, "value": value},
            )
    matrix = _matrix_from_abc_angles(abc, angles)
    volume = _cell_volume(matrix)
    if abs(volume) < _MIN_CELL_VOLUME:
        raise DerivedParameterUnresolved(
            "rebuilt lattice is singular (|V| below threshold)",
            details={"volume": volume, "min_volume": _MIN_CELL_VOLUME},
        )
    return matrix, abc, angles


def _reciprocal_lengths(matrix: Sequence[Sequence[float]]) -> List[float]:
    """倒格矢长度 |b_i| = 2π |a_j × a_k| / |V|（i, j, k 循环）。"""

    volume = abs(_cell_volume(matrix))
    if volume < _MIN_CELL_VOLUME:
        raise DerivedParameterUnresolved(
            "singular lattice: cannot compute reciprocal vectors",
            details={"volume": volume, "min_volume": _MIN_CELL_VOLUME},
        )
    lengths: List[float] = []
    for index in range(3):
        j, k = (index + 1) % 3, (index + 2) % 3
        lengths.append(2.0 * math.pi * _norm(_cross(matrix[j], matrix[k])) / volume)
    return lengths


def _grid_from_reciprocal(b_lengths: Sequence[float], total_kpoints: float) -> List[int]:
    """n_i = max(1, floor(s · |b_i| / g + 0.5))，s = total^(1/3)，g = (|b1||b2||b3|)^(1/3)。

    n_i ∝ |b_i|：实空间某方向越长 → 其倒格矢越短 → 该方向所需网格数越少。
    取整为确定性的 round-half-up（floor(x + 0.5)），不受 Python banker's rounding 影响。
    """

    geometric_mean = (b_lengths[0] * b_lengths[1] * b_lengths[2]) ** (1.0 / 3.0)
    if not math.isfinite(geometric_mean) or geometric_mean <= 0.0:
        raise DerivedParameterUnresolved(
            "invalid reciprocal lengths for kpoint grid",
            details={"b_lengths": list(b_lengths)},
        )
    scale = total_kpoints ** (1.0 / 3.0)
    return [
        max(1, int(math.floor(scale * length / geometric_mean + 0.5)))
        for length in b_lengths
    ]


def generate_kpoint_grid(inputs: Dict[str, Any]) -> Dict[str, Any]:
    """结构 + KPPA + 原子数 → 均匀网格与 centering（10.6 节）。

    inputs: ``{"kppa", "atom_count", "lattice"}``。语义：
    ``N_total ≈ kppa / atom_count``（与 pymatgen
    ``Kpoints.automatic_density(structure, kppa)`` 的 kppa/num_sites 语义一致，
    本项目不调用 pymatgen，采用基于完整倒格矢长度的自有确定性实现），
    各方向 ``n_i ∝ |b_i|``；matrix 存在时为唯一几何真值。
    返回 ``{"grid", "centering", "kppa"}``；任何非法输入抛 DerivedParameterUnresolved。
    """

    if "kppa" not in inputs:
        raise DerivedParameterUnresolved(
            "kppa is required for kpoint grid derivation", details={"missing": "kppa"}
        )
    kppa = _finite_number(inputs["kppa"], field="kppa")
    if kppa <= 0.0:
        raise DerivedParameterUnresolved(
            "kppa must be a positive finite number", details={"kppa": kppa}
        )
    if "atom_count" not in inputs:
        raise DerivedParameterUnresolved(
            "atom_count is required for kpoint grid derivation",
            details={"missing": "atom_count"},
        )
    atom_count = inputs["atom_count"]
    if isinstance(atom_count, bool) or not isinstance(atom_count, int) or atom_count <= 0:
        raise DerivedParameterUnresolved(
            "atom_count must be a positive integer",
            details={"atom_count": repr(atom_count)},
        )
    lattice = inputs.get("lattice") or {}
    if not isinstance(lattice, dict):
        raise DerivedParameterUnresolved(
            "lattice must be a mapping", details={"lattice": repr(lattice)}
        )
    matrix, _abc, angles = _lattice_geometry(lattice)
    grid = _grid_from_reciprocal(_reciprocal_lengths(matrix), kppa / atom_count)
    # Gamma/Monkhorst 选择规则不变：六方（任一实空间角度≈120°±1）或全奇网格 → Gamma。
    # 角度来自单一几何真值（matrix 存在时由 matrix 派生）。
    hexagonal = any(abs(angle - 120.0) < 1.0 for angle in angles)
    all_odd = all(n % 2 == 1 for n in grid)
    centering = "Gamma" if (hexagonal or all_odd) else "Monkhorst"
    return {"grid": grid, "centering": centering, "kppa": kppa}


def derive_system_label(inputs: Dict[str, Any]) -> str:
    formula = inputs["formula"]
    task = inputs["task"]
    return f"{formula}_{task}"


def derive_encut_from_precision(inputs: Dict[str, Any]) -> float:
    precision = inputs["precision"]
    if precision not in ENCUT_BY_PRECISION:
        raise DerivedParameterUnresolved(
            f"unknown precision {precision!r}", details={"precision": precision}
        )
    return ENCUT_BY_PRECISION[precision]


DERIVED_FUNCTIONS: Dict[str, Callable[[Dict[str, Any]], Any]] = {
    "generate_magmom_from_structure": generate_magmom_from_structure,
    "generate_ldau_arrays": generate_ldau_arrays,
    "generate_kpoint_grid": generate_kpoint_grid,
    "derive_system_label": derive_system_label,
    "derive_encut_from_precision": derive_encut_from_precision,
}


class DerivedParameterResolver:
    """白名单派生函数解析器；未注册函数名直接 fail closed。"""

    def __init__(self, registry: Dict[str, Callable[[Dict[str, Any]], Any]] | None = None):
        self._registry = dict(registry) if registry is not None else dict(DERIVED_FUNCTIONS)

    @property
    def registered_functions(self) -> List[str]:
        return sorted(self._registry)

    def is_registered(self, name: str) -> bool:
        return name in self._registry

    def resolve(self, ref: DerivedParameterRef, inputs: Dict[str, Any]) -> Any:
        function = self._registry.get(ref.function)
        if function is None:
            raise DerivedParameterUnresolved(
                f"derived function not in whitelist: {ref.function}",
                details={"function": ref.function, "whitelist": self.registered_functions},
            )
        try:
            return function(dict(inputs))
        except DerivedParameterUnresolved:
            raise
        except Exception as exc:  # noqa: BLE001
            raise DerivedParameterUnresolved(
                f"derived function failed: {ref.function}: {exc}",
                details={"function": ref.function},
            ) from exc
