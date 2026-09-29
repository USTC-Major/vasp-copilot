"""Strict, shared Materials Project search criteria for AI and Toolbox."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator
from pymatgen.core import Element


_FORMULA_TOKEN = re.compile(r"([A-Z][a-z]?)([1-9][0-9]*)?")


def formula_elements(value: str) -> set[str] | None:
    """Accept only an entire, unambiguous elemental formula."""
    if not isinstance(value, str) or not value or len(value) > 100:
        return None
    elements: set[str] = set()
    end = 0
    for match in _FORMULA_TOKEN.finditer(value):
        if match.start() != end or not Element.is_valid_symbol(match.group(1)):
            return None
        elements.add(match.group(1))
        end = match.end()
    return elements if end == len(value) and elements else None


def _symbol(value: str) -> str:
    if not isinstance(value, str) or not Element.is_valid_symbol(value):
        raise ValueError("元素符号无效，请使用标准大小写（例如 Fe、O）")
    return value


class BandGapRange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    max: float | None = Field(default=None, ge=0, allow_inf_nan=False)

    @field_validator("min", "max", mode="before")
    @classmethod
    def strict_number(cls, value: Any) -> Any:
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
            raise ValueError("带隙边界必须是数字")
        return value

    @model_validator(mode="after")
    def valid_range(self) -> "BandGapRange":
        if self.min is None and self.max is None:
            raise ValueError("带隙范围至少需要 min 或 max")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("带隙范围 min 不能大于 max")
        return self


class MaterialCriteria(BaseModel):
    model_config = ConfigDict(extra="forbid")

    formula: str | None = None
    elements: list[str] | None = None
    chemsys: str | None = None
    band_gap: BandGapRange | None = None
    is_stable: StrictBool | None = None
    is_metal: StrictBool | None = None

    @field_validator("formula")
    @classmethod
    def valid_formula(cls, value: str | None) -> str | None:
        if value is not None and formula_elements(value) is None:
            raise ValueError("只接受完整的元素符号与正整数计数化学式（不含括号、分数或描述文本）")
        return value

    @field_validator("elements")
    @classmethod
    def valid_elements(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        if not value or len(set(value)) != len(value):
            raise ValueError("元素列表必须非空且无重复")
        return [_symbol(item) for item in value]

    @field_validator("chemsys")
    @classmethod
    def valid_chemsys(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parts = value.split("-")
        if not parts or len(set(parts)) != len(parts):
            raise ValueError("化学体系必须由不重复的元素符号组成")
        for part in parts:
            _symbol(part)
        return "-".join(sorted(parts))

    @model_validator(mode="after")
    def compatible(self) -> "MaterialCriteria":
        if not self.model_fields_set or all(value is None for value in self.model_dump().values()):
            raise ValueError("至少需要一个查询条件")
        formula = formula_elements(self.formula) if self.formula else None
        required = set(self.elements or [])
        system = set(self.chemsys.split("-")) if self.chemsys else None
        if formula is not None and not required.issubset(formula):
            raise ValueError("化学式不包含全部要求的元素")
        if system is not None and not required.issubset(system):
            raise ValueError("化学体系不包含全部要求的元素")
        if formula is not None and system is not None and formula != system:
            raise ValueError("化学式与化学体系冲突")
        if self.is_metal is True and self.band_gap and self.band_gap.min is not None and self.band_gap.min > 0:
            raise ValueError("金属条件与正带隙下限冲突")
        return self

    def to_mp(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)
