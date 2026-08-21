"""RecipeRegistry（设计文档 8.2 节）。

只读内存 registry；published ID/version 不可变（重复注册同 ID/version 报错）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple

from backend.app.recipes.errors import RecipeError, RecipeSchemaInvalid
from backend.app.recipes.loader import RecipePackLoader
from backend.app.schemas.recipe import (
    RecipeManifest,
    RecipePackManifest,
    RecipeRef,
    RecipeStatus,
)

DEFAULT_PACK_DIR = Path(__file__).resolve().parent / "packs" / "vasp_mvp_core"


class RecipeRegistry:
    def __init__(self) -> None:
        self._packs: Dict[str, RecipePackManifest] = {}
        self._recipes: Dict[str, RecipeManifest] = {}  # key: recipe_id@version

    # --- 注册 ---

    def register_pack(
        self, pack: RecipePackManifest, recipes: List[RecipeManifest]
    ) -> None:
        for recipe in recipes:
            self.register_recipe(recipe)
        self._packs[pack.pack_id] = pack

    def register_recipe(self, manifest: RecipeManifest) -> None:
        key = f"{manifest.recipe_id}@{manifest.version}"
        existing = self._recipes.get(key)
        if existing is not None:
            if existing.sha256 != manifest.sha256:
                raise RecipeSchemaInvalid(
                    f"immutable violation: {key} already registered with different hash",
                    details={"recipe": key},
                )
            return  # 幂等：同一内容重复注册
        self._recipes[key] = manifest

    # --- 查询 ---

    def get(self, ref: RecipeRef) -> RecipeManifest:
        manifest = self._recipes.get(ref.key)
        if manifest is None:
            raise RecipeError(
                f"recipe not found: {ref.key}", details={"recipe_ref": ref.key}
            )
        return manifest

    def get_by_id(self, recipe_id: str, version: Optional[str] = None) -> RecipeManifest:
        if version is not None:
            return self.get(RecipeRef(recipe_id=recipe_id, version=version))
        candidates = sorted(
            (m for m in self._recipes.values() if m.recipe_id == recipe_id),
            key=lambda m: m.version,
            reverse=True,
        )
        if not candidates:
            raise RecipeError(f"recipe not found: {recipe_id}", details={"recipe_id": recipe_id})
        return candidates[0]

    def list_published(self) -> List[RecipeManifest]:
        return sorted(
            (m for m in self._recipes.values() if m.recipe_status == RecipeStatus.PUBLISHED),
            key=lambda m: (m.layer.value, m.recipe_id),
        )

    def packs(self) -> List[RecipePackManifest]:
        return sorted(self._packs.values(), key=lambda p: p.pack_id)

    def pack_for(self, pack_id: str) -> RecipePackManifest:
        if pack_id not in self._packs:
            raise RecipeError(f"pack not found: {pack_id}", details={"pack_id": pack_id})
        return self._packs[pack_id]


def default_registry(loader: Optional[RecipePackLoader] = None) -> Tuple[RecipeRegistry, RecipePackManifest]:
    """加载内置 vasp_mvp_core pack 并返回 registry。"""

    loader = loader or RecipePackLoader()
    registry = RecipeRegistry()
    pack, recipes = loader.load_pack(DEFAULT_PACK_DIR)
    registry.register_pack(pack, recipes)
    return registry, pack
