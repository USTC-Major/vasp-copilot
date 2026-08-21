from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict


class VasprunInfo(BaseModel):
    """vasprun.xml 的最小可选摘要（MVP 3.3：可选）。

    仅用作存在性证据 / roadmap 钩子（设计 roadmap 项「更多解析证据」）；
    从不必需、从不喂给诊断规则，因此诊断结果保持不变。"""

    model_config = ConfigDict(extra="ignore")

    source_file: str = "vasprun.xml"
    present: bool = True
    size_bytes: int = 0
    truncated: bool = False  # 超过受限大小仅标记存在，不解析内容
    final_energy: Optional[float] = None
    converged: Optional[bool] = None
    n_ionic_steps: int = 0
    warnings: list[str] = []
