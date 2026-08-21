"""BundleBuilder：manifest + sha256 + 确定性 zip（任务书 17、7.23 节）。

确定性要求：
- 文件按相对路径排序写入；
- zip 内所有条目固定 mtime（无时间戳漂移）；
- manifest 的 created_at 使用固定常量；
- 同一输入两次构建必须产出字节级一致的 zip 与相同 bundle_sha256。
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Union

from backend.app.schemas.generation import WorkflowBundleManifest
from backend.app.schemas.recipe import RecipePackManifest

# 固定时间戳：保证可复现（生成时间不进入产物）。
FIXED_TIMESTAMP = "2026-08-10T00:00:00Z"
FIXED_ZIP_DATE_TIME = (2026, 8, 10, 0, 0, 0)


@dataclass
class BundleResult:
    manifest: WorkflowBundleManifest
    zip_bytes: bytes
    files: Dict[str, bytes] = field(default_factory=dict)


def _to_bytes(content: Union[str, bytes]) -> bytes:
    if isinstance(content, bytes):
        return content
    return content.encode("utf-8")


class BundleBuilder:
    """把 {相对路径: 内容} 组装为确定性 zip + manifest。"""

    def build(
        self,
        workflow_id: str,
        files: Dict[str, Union[str, bytes]],
        revision: int = 1,
        pack: Optional[RecipePackManifest] = None,
    ) -> BundleResult:
        normalized: Dict[str, bytes] = {}
        for relative_path, content in files.items():
            self._validate_path(relative_path)
            normalized[relative_path.replace("\\", "/")] = _to_bytes(content)

        file_entries: List[Dict[str, object]] = []
        for relative_path in sorted(normalized):
            data = normalized[relative_path]
            file_entries.append(
                {
                    "path": relative_path,
                    "size_bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )

        zip_bytes = self._build_zip(normalized)
        manifest = WorkflowBundleManifest(
            workflow_id=workflow_id,
            revision=revision,
            bundle_sha256=hashlib.sha256(zip_bytes).hexdigest(),
            recipe_pack_version=pack.version if pack else None,
            recipe_pack_sha256=pack.sha256 if pack else None,
            files=file_entries,
            created_at=FIXED_TIMESTAMP,
        )
        return BundleResult(manifest=manifest, zip_bytes=zip_bytes, files=normalized)

    @staticmethod
    def _validate_path(relative_path: str) -> None:
        parts = relative_path.replace("\\", "/").split("/")
        if not relative_path or any(part in ("", ".", "..") for part in parts):
            raise ValueError(f"unsafe relative path in bundle: {relative_path!r}")
        if relative_path.startswith(("/", "\\")) or ":" in relative_path:
            raise ValueError(f"absolute path not allowed in bundle: {relative_path!r}")

    @staticmethod
    def _build_zip(files: Dict[str, bytes]) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
            for relative_path in sorted(files):
                info = zipfile.ZipInfo(filename=relative_path, date_time=FIXED_ZIP_DATE_TIME)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                bundle.writestr(info, files[relative_path])
        return buffer.getvalue()
