"""IncarGenerator（设计文档 4.1 节第 10 步、7.5 节）。

输入是 RecipeComposer 产出的 resolved parameters（typed），绝不接受 LLM 文本。
生成前执行结构一致性检查：
- MAGMOM 长度 == 原子数（按 POSCAR 顺序）→ 否则 MAGMOM_LENGTH_MISMATCH
- LDAUL/LDAUU/LDAUJ 长度 == 元素种类数 → 否则 LDAU_LENGTH_MISMATCH
- LDAU=True 必须带齐三个数组
- DFT+U 数组只允许在用户确认全部 U/J 后出现 → 否则 DFTU_CONFIRMATION_REQUIRED
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from backend.app.recipes.errors import BeAError, DftuConfirmationRequired
from backend.app.recipes.errors import (
    LDAU_LENGTH_MISMATCH,
    MAGMOM_LENGTH_MISMATCH,
)
from backend.app.generators.serializer import IncarSerializer
from backend.app.schemas.generation import DftuSettings, StructureContext


class IncarGenerator:
    def __init__(self, serializer: Optional[IncarSerializer] = None) -> None:
        self._serializer = serializer or IncarSerializer()

    def generate(
        self,
        parameters: Dict[str, Any],
        structure: StructureContext,
        dftu: Optional[DftuSettings] = None,
    ) -> str:
        self._validate(parameters, structure, dftu)
        return self._serializer.serialize(dict(parameters))

    # --- 一致性检查 ---

    def _validate(
        self,
        parameters: Dict[str, Any],
        structure: StructureContext,
        dftu: Optional[DftuSettings],
    ) -> None:
        magmom = parameters.get("MAGMOM")
        if magmom is not None:
            if not isinstance(magmom, list):
                raise BeAError(
                    "MAGMOM must be a per-atom list",
                    code=MAGMOM_LENGTH_MISMATCH,
                    details={"magmom": repr(magmom)},
                )
            if len(magmom) != structure.atom_count:
                raise BeAError(
                    f"MAGMOM length {len(magmom)} != atom count {structure.atom_count}",
                    code=MAGMOM_LENGTH_MISMATCH,
                    details={
                        "magmom_length": len(magmom),
                        "atom_count": structure.atom_count,
                    },
                )

        ldau_enabled = parameters.get("LDAU") is True
        arrays = {
            name: parameters.get(name) for name in ("LDAUL", "LDAUU", "LDAUJ")
        }
        present = [name for name, value in arrays.items() if value is not None]
        if ldau_enabled and len(present) != 3:
            raise BeAError(
                f"LDAU=True requires LDAUL/LDAUU/LDAUJ, missing: "
                f"{sorted(set(arrays) - set(present))}",
                code=LDAU_LENGTH_MISMATCH,
                details={"present": present},
            )
        if present and not ldau_enabled:
            raise BeAError(
                "LDAU arrays present but LDAU is not True",
                code=LDAU_LENGTH_MISMATCH,
                details={"present": present},
            )
        element_count = len(structure.elements)
        for name, value in arrays.items():
            if value is None:
                continue
            if not isinstance(value, list) or len(value) != element_count:
                raise BeAError(
                    f"{name} length must equal element count {element_count}",
                    code=LDAU_LENGTH_MISMATCH,
                    details={
                        "parameter": name,
                        "length": len(value) if isinstance(value, list) else None,
                        "element_count": element_count,
                    },
                )
        if present and dftu is not None and dftu.entries and not dftu.all_confirmed:
            unconfirmed = [e.element for e in dftu.entries if not e.confirmed_by_user]
            raise DftuConfirmationRequired(
                "DFT+U U/J values must be confirmed by user before LDAU generation",
                details={"unconfirmed_elements": unconfirmed},
            )

        ispin = parameters.get("ISPIN")
        if magmom is not None and ispin not in (2, None):
            raise BeAError(
                "MAGMOM present but ISPIN is not 2",
                code="UNKNOWN_PARAMETER_CONFLICT",
                details={"ISPIN": ispin},
            )
