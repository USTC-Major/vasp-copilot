from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ScfMetrics:
    energy_series: list[float] = field(default_factory=list)
    d_e: list[float] = field(default_factory=list)
    sign_flip_rate: float = 0.0
    amplitude_slope: float = 0.0
    final_amplitude: float = 0.0
    converged: bool = False
    steps: int = 0
    reached_nelm: bool = False


def _sign_flip_rate(d_e: list[float]) -> float:
    flips = 0
    for i in range(1, len(d_e)):
        if (d_e[i] > 0) != (d_e[i - 1] > 0) and d_e[i] != 0 and d_e[i - 1] != 0:
            flips += 1
    return flips / len(d_e) if len(d_e) > 1 else 0.0


def _amplitude_slope(amplitudes: list[float]) -> float:
    """窗口内 |dE| 的斜率（负值表示衰减）。"""
    if len(amplitudes) < 2:
        return 0.0
    n = len(amplitudes)
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(amplitudes) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, amplitudes))
    den = sum((x - mean_x) ** 2 for x in xs)
    return num / den if den else 0.0


def analyze_scf(oszicar_energy: list[float], nelm: Optional[int] = None,
                oszicar_last_step: int = 0) -> ScfMetrics:
    """从 OSZICAR 能量序列计算确定性 SCF 指标。"""
    m = ScfMetrics()
    m.energy_series = list(oszicar_energy)
    m.steps = len(oszicar_energy)
    if len(oszicar_energy) >= 2:
        m.d_e = [oszicar_energy[i] - oszicar_energy[i - 1]
                 for i in range(1, len(oszicar_energy))]
    amps = [abs(de) for de in m.d_e]
    m.sign_flip_rate = _sign_flip_rate(m.d_e)
    m.amplitude_slope = _amplitude_slope(amps)
    m.final_amplitude = amps[-1] if amps else 0.0
    # convergence heuristic: electron steps stop when dE below a small epsilon
    m.converged = bool(amps) and amps[-1] < 1e-4
    # reached NELM: if the reported electronic steps hit the configured NELM,
    # and the run did NOT converge (it stopped because of the iteration cap)
    if nelm is not None and oszicar_last_step:
        m.reached_nelm = (oszicar_last_step >= nelm) and (not m.converged)
    return m