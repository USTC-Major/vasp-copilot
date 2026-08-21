from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

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


class ElectronicStep(BaseModel):
    """单条电子自洽迭代行（DAV/RMM/CG/DMP/SDA）。"""

    model_config = ConfigDict(extra="ignore")

    ionic_step: int  # 所属离子步（由 F= 汇总行实际编号确定；尾部块为推断值）
    electronic_step: int  # 行内迭代编号（每个电子块从 1 重新计数）
    algorithm: str  # 白名单算法标签
    energy: Optional[float] = None  # E 列
    delta_energy: Optional[float] = None  # dE 列
    delta_epsilon: Optional[float] = None  # d eps 列
    ncg: Optional[int] = None  # 仅当对应位置为合法整数
    rms: Optional[float] = None
    rms_c: Optional[float] = None
    source_line: Optional[int] = None  # 1-based 源行号


class OszicarData(BaseModel):
    model_config = ConfigDict(extra="allow")

    ionic_steps: list[dict[str, Any]] = Field(default_factory=list)  # 只存 F=/E0= 离子步汇总
    electronic_steps: list[ElectronicStep] = Field(default_factory=list)
    last_ionic_step: int = 0
    last_electronic_step: int = 0  # 最后一个电子块的末尾电子步编号
    total_electronic_lines: int = 0  # 只统计真实电子迭代行
    # SCF 分析序列：仅最后一个电子块的有效 energy，不跨离子步拼接。
    electronic_energy_series: list[float] = Field(default_factory=list)
    parser_warnings: list[str] = Field(default_factory=list)
    # deprecated：离子步汇总 F/E0 能量序列；SCF 规则/绘图禁止使用。
    energy_series: list[float] = Field(default_factory=list)
    converged: bool = False
    last_step: int = 0  # deprecated：last_ionic_step 别名，禁止再表示电子步


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
    # 仅表示"检测到 OUTCAR 结构优化收敛停止文本"，不代表电子 SCF 收敛；
    # True=检测到；None=证据不足；绝不设为 False 声称确定未收敛。
    ionic_convergence_reached: Optional[bool] = None
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
