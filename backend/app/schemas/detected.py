from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict


class DetectedFile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    kind: str = "other"  # incar|poscar|kpoints|oszicar|outcar|concar|cif|vasprun|job_log|other
    size: int = 0
    path: str = ""
    # 设计 6.8 统一字段（设计 5.3 允许新增字段）：与 name/size/path 并存，向后兼容。
    relative_path: str = ""
    size_bytes: int = 0
    sha256: Optional[str] = None


class DetectedRun(BaseModel):
    """POST /api/diagnosis/upload 的结果（MVP 6.8）。"""

    model_config = ConfigDict(extra="ignore")

    root: str = ""
    run_type: Optional[str] = None
    files: list[DetectedFile] = []
    missing_recommended: list[str] = []
    candidate_job_logs: list[str] = []