"""DerivedParameterResolver（设计文档 4.1 节第 7 步、8.2/10.4 节）。

白名单注册表：派生参数只能调用注册函数；无 eval/import。
每个函数返回 typed value，不返回 INCAR 文本。

注册函数：
- generate_magmom_from_structure：按 POSCAR 元素顺序展开 MAGMOM，长度==原子数
- generate_ldau_arrays：按 POSCAR 元素顺序生成 LDAUL/LDAUU/LDAUJ，长度==元素种类数
- generate_kpoint_grid：结构 + KPPA → 均匀网格与 centering
- derive_system_label：化学式 + task → SYSTEM 标签
- derive_encut_from_precision：精度档位 → ENCUT 初始推荐
"""

from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Sequence

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
KPPA_TABLE: Dict[str, Dict[str, float]] = {
    "relax": {"quick": 500.0, "standard": 1000.0, "high": 1500.0},
    "static": {"quick": 500.0, "standard": 1000.0, "high": 1500.0},
    "dos": {"quick": 800.0, "standard": 1500.0, "high": 2000.0},
    "band": {"quick": 40.0, "standard": 60.0, "high": 80.0},  # line-mode 密度
}


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


def _reciprocal_grid(lattice_lengths: Sequence[float], kppa: float) -> List[int]:
    """确定性网格公式：n_i = max(1, round(kppa^(1/3) * L_i / (L1*L2*L3)^(1/3)))。"""

    if len(lattice_lengths) != 3 or any(length <= 0 for length in lattice_lengths):
        raise DerivedParameterUnresolved(
            "invalid lattice lengths for kpoint grid",
            details={"lattice_lengths": list(lattice_lengths)},
        )
    geom = (lattice_lengths[0] * lattice_lengths[1] * lattice_lengths[2]) ** (1.0 / 3.0)
    factor = kppa ** (1.0 / 3.0) / geom
    return [max(1, int(round(factor * length))) for length in lattice_lengths]


def generate_kpoint_grid(inputs: Dict[str, Any]) -> Dict[str, Any]:
    """结构 + KPPA → 均匀网格与 centering（10.6 节）。"""

    kppa = float(inputs["kppa"])
    lattice = inputs.get("lattice") or {}
    lengths = lattice.get("abc") or []
    angles = lattice.get("angles") or [90.0, 90.0, 90.0]
    if not lengths:
        matrix = lattice.get("matrix") or []
        lengths = [math.sqrt(sum(x * x for x in row)) for row in matrix]
    grid = _reciprocal_grid(lengths, kppa)
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
