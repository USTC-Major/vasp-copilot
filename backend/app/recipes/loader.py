"""RecipePackLoader（设计文档 8.2/10.4 节）。

safe YAML + Pydantic strict；拒绝未知字段/表达式/路径引用。
Recipe 文件只能是数据：禁止 Python/Jinja 表达式、eval、import、include、
URL、shell、环境变量与任意函数路径（派生函数只允许注册表白名单名）。
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml

from backend.app.recipes.errors import RecipeSchemaInvalid
from backend.app.schemas.recipe import RecipeManifest, RecipePackManifest

# 表达式/注入特征（fail closed）
_FORBIDDEN_PATTERNS: Tuple[Tuple[str, str], ...] = (
    (r"\$\{", "template expression ${...}"),
    (r"\{\{", "template expression {{...}}"),
    (r"\{%\s*", "jinja statement"),
    (r"(?i)\bimport\s+\w", "python import"),
    (r"(?i)\beval\s*\(", "eval()"),
    (r"(?i)\bexec\s*\(", "exec()"),
    (r"__(?:init|import|class|globals|builtins|subclasses)__", "dunder reference"),
    (r"(?i)(?:https?|ftp|file)://", "url reference"),
    (r"(?i)\bcurl\b|\bwget\b", "network command"),
    (r"(?i)\binclude\s*:", "file include"),
    (r"(?i)\bsource\s+[^\s]", "shell source"),
    (r"`[^`]+`", "shell backtick"),
    (r"\$\(\s*[^\s)]", "shell substitution"),
    (r"(?i)\bssh\b|\bscp\b|\bsftp\b", "ssh reference"),
)

# allowed_overrides 等合法 schema 键不受 include 误报影响：仅在字符串值上扫描。
_SCANNED_VALUE_TYPES = (str,)


def _scan_forbidden_text(value: str, path: str) -> None:
    for pattern, label in _FORBIDDEN_PATTERNS:
        if re.search(pattern, value):
            raise RecipeSchemaInvalid(
                f"forbidden content ({label}) at {path}",
                details={"path": path, "reason": label},
            )


def _scan_node(node: Any, path: str) -> None:
    if isinstance(node, str):
        _scan_forbidden_text(node, path)
    elif isinstance(node, dict):
        for key, child in node.items():
            if not isinstance(key, str):
                raise RecipeSchemaInvalid(f"non-string key at {path}")
            _scan_node(child, f"{path}.{key}" if path else key)
    elif isinstance(node, list):
        for idx, child in enumerate(node):
            _scan_node(child, f"{path}[{idx}]")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class RecipePackLoader:
    """加载仓库内固定 pack；published ID/version 不可变由 registry 保证。"""

    def load_pack(self, pack_dir: Path) -> Tuple[RecipePackManifest, List[RecipeManifest]]:
        pack_dir = Path(pack_dir)
        pack_file = pack_dir / "pack.yaml"
        if not pack_file.is_file():
            raise RecipeSchemaInvalid(
                f"pack.yaml not found in {pack_dir}", details={"pack_dir": str(pack_dir)}
            )
        pack_manifest = self._load_pack_manifest(pack_file)
        recipes = [self.load_recipe(path) for path in self._iter_recipe_files(pack_dir)]
        if not recipes:
            raise RecipeSchemaInvalid(f"no recipe files found in {pack_dir}")
        pack_manifest.sha256 = self._pack_hash(pack_file, recipes)
        return pack_manifest, recipes

    def _load_pack_manifest(self, pack_file: Path) -> RecipePackManifest:
        raw = self._safe_read(pack_file)
        try:
            return RecipePackManifest.model_validate(raw)
        except Exception as exc:  # noqa: BLE001 - 统一转为 fail-closed 错误
            raise RecipeSchemaInvalid(
                f"invalid pack manifest: {exc}", details={"file": str(pack_file)}
            ) from exc

    def _iter_recipe_files(self, pack_dir: Path) -> List[Path]:
        files = sorted(
            p for p in pack_dir.rglob("*.yaml") if p.name != "pack.yaml" and p.is_file()
        )
        return files

    def load_recipe(self, path: Path) -> RecipeManifest:
        path = Path(path)
        raw = self._safe_read(path)
        _scan_node(raw, path.name)
        try:
            manifest = RecipeManifest.model_validate(raw)
        except Exception as exc:  # noqa: BLE001
            raise RecipeSchemaInvalid(
                f"recipe schema invalid: {path.name}: {exc}",
                details={"file": str(path), "reason": str(exc)},
            ) from exc
        manifest.sha256 = sha256_hex(path.read_bytes())
        return manifest

    def _safe_read(self, path: Path) -> Dict[str, Any]:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise RecipeSchemaInvalid(
                f"yaml parse failed: {path.name}: {exc}", details={"file": str(path)}
            ) from exc
        if not isinstance(data, dict):
            raise RecipeSchemaInvalid(
                f"recipe root must be a mapping: {path.name}", details={"file": str(path)}
            )
        return data

    @staticmethod
    def _pack_hash(pack_file: Path, recipes: List[RecipeManifest]) -> str:
        hasher = hashlib.sha256()
        hasher.update(pack_file.read_bytes())
        for recipe in sorted(recipes, key=lambda r: (r.recipe_id, r.version)):
            hasher.update(f"{recipe.recipe_id}@{recipe.version}:{recipe.sha256}".encode())
        return hasher.hexdigest()
