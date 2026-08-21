from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict


class MagnetizationAnalysisMode(str, Enum):
    COLLINEAR = "collinear"
    UNSUPPORTED_NONCOLLINEAR_OR_SOC = "unsupported_noncollinear_or_soc"
    UNAVAILABLE = "unavailable"


class CalculationMode(BaseModel):
    """MVP 7.21：spin / DFT+U / SOC / 非共线(noncollinear) 摘要。"""

    model_config = ConfigDict(extra="allow")

    is_spin_polarized: bool = False
    is_dftu: bool = False
    is_soc: bool = False
    is_noncollinear: bool = False
    magnetization_analysis_mode: MagnetizationAnalysisMode = (
        MagnetizationAnalysisMode.UNAVAILABLE
    )