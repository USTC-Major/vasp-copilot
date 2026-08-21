"""PoscarGenerator（设计文档 7.7 节）。

BE-A 不解析结构（parsers/** 属于他人目录）：
- 默认 verbatim 复制用户上传的 POSCAR 文本（只规范化行尾）；
- normalize=True 时用 pymatgen 重排输出为固定格式（Direct 坐标、固定精度）。

不调用 ``backend/app/parsers/**``，不修改晶胞/原子顺序。
"""

from __future__ import annotations

from backend.app.recipes.errors import BeAError
from backend.app.schemas.generation import StructureContext


class PoscarGenerator:
    def generate(self, structure: StructureContext, normalize: bool = False) -> str:
        text = structure.poscar_text
        if not text:
            raise BeAError(
                "structure has no POSCAR text; BE-A does not synthesize structures",
                code="UPSTREAM_OUTPUT_MISSING",
                details={"structure_id": structure.structure_id},
            )
        if not normalize:
            return self._verbatim(text)
        return self._normalize(text)

    @staticmethod
    def _verbatim(text: str) -> str:
        # 统一为 LF 行尾并确保以换行结尾；不改动内容。
        return text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n") + "\n"

    @staticmethod
    def _normalize(text: str) -> str:
        try:
            from pymatgen.io.vasp import Poscar
        except ImportError as exc:  # pragma: no cover
            raise BeAError(
                f"pymatgen unavailable for POSCAR normalization: {exc}",
                code="UPSTREAM_OUTPUT_MISSING",
                details={"reason": str(exc)},
            ) from exc
        try:
            poscar = Poscar.from_string(text)
        except Exception as exc:  # noqa: BLE001
            raise BeAError(
                f"POSCAR normalization failed: {exc}",
                code="UPSTREAM_OUTPUT_MISSING",
                details={"reason": str(exc)},
            ) from exc
        rendered = Poscar(poscar.structure, direct=True, significant_digits=8).get_text()
        return rendered.rstrip("\n") + "\n"
