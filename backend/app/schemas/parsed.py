from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict

from .cif import CifData
from .mode import CalculationMode
from .vasprun import VasprunInfo


from typing import Any


class IncarAssignment(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    value: Any = None
    raw_value: str = ""
    value_type: str = ""  # bool|int|float|string|bool_array|int_array|float_array|array|empty
    source_line: int = 0
    is_unknown: bool = False


class DuplicateParam(BaseModel):
    name: str
    lines: list[int] = []
    original_values: list[str] = []


class IncarData(BaseModel):
    model_config = ConfigDict(extra="ignore")

    effective: dict[str, Any] = {}
    duplicate: list[DuplicateParam] = []
    unknown: list[str] = []
    warnings: list[str] = []
    raw_lines: list[str] = []
    assignments: list[IncarAssignment] = []


class PoscarData(BaseModel):
    model_config = ConfigDict(extra="ignore")

    elements: list[str] = []
    counts: list[int] = []
    source_file: str = ""  # 结构来源：POSCAR 或 CONTCAR（设计 4.2：每字段记录来源文件）


class OszicarData(BaseModel):
    model_config = ConfigDict(extra="allow")

    ionic_steps: list[dict[str, Any]] = []
    energy_series: list[float] = []
    converged: bool = False
    last_step: int = 0
    total_electronic_lines: int = 0


class KpointsData(BaseModel):
    model_config = ConfigDict(extra="ignore")

    comment: str = ""
    nkpts: Optional[int] = None
    mode: str = ""
    line_mode: bool = False


class OutcarData(BaseModel):
    model_config = ConfigDict(extra="ignore")

    normal_termination: Optional[bool] = None
    truncated: Optional[bool] = None
    vasp_version: Optional[str] = None
    vasp_binary_hint: Optional[str] = None
    calculation_mode: CalculationMode = CalculationMode()
    final_energy: Optional[float] = None
    final_magnetization: Optional[list[dict[str, Any]]] = None
    magnetization_total: Optional[dict[str, Any]] = None
    error_lines: list[dict[str, Any]] = []
    warnings: list[str] = []


class JobLogData(BaseModel):
    model_config = ConfigDict(extra="ignore")

    path: str = ""
    keywords: list[dict[str, Any]] = []
    tail_lines: list[str] = []


class ParsedRunData(BaseModel):
    """统一解析器输出（MVP 7.21）。"""

    model_config = ConfigDict(extra="ignore")

    vasp_version: Optional[str] = None
    vasp_binary_hint: Optional[str] = None
    calculation_mode: CalculationMode = CalculationMode()
    incar: IncarData = IncarData()
    poscar: PoscarData = PoscarData()
    oszicar: OszicarData = OszicarData()
    outcar: OutcarData = OutcarData()
    cif: Optional[CifData] = None
    vasprun: Optional[VasprunInfo] = None
    kpoints: KpointsData = KpointsData()
    job_logs: list[JobLogData] = []
    source_files: list[str] = []