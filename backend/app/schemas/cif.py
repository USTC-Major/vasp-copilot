from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict


class CifData(BaseModel):
    """从 CIF 文件解析出的结构（设计 13.4：结构格式 = POSCAR、CIF）。"""

    model_config = ConfigDict(extra="ignore")

    formula: str = ""
    elements: list[str] = []
    counts: list[int] = []
    lattice_a: Optional[float] = None
    lattice_b: Optional[float] = None
    lattice_c: Optional[float] = None
    angle_alpha: Optional[float] = None
    angle_beta: Optional[float] = None
    angle_gamma: Optional[float] = None
    space_group: str = ""
    source_file: str = ""
