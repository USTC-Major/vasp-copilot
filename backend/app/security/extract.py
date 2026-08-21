from __future__ import annotations

import io
import shutil
import stat
import zipfile
from pathlib import Path

from ..core.errors import (
    PayloadTooLargeError,
    SecurityError,
    UnsupportedMediaTypeError,
    ValidationError,
)

_ZIP_MAGIC = b"PK\x03\x04"
_DIR_END_MARK = "/"


class ExtractResult:
    """安全归档解压的结果。"""

    def __init__(self, files: list[Path]) -> None:
        self.files = files


class SafeArchiveExtractor:
    """校验并解压 VASP 运行目录 zip，避免安全漏洞。

    防护：上传大小、条目数、逐条目/成员的绝对路径或 ".."、符号链接条目、
    zip 炸弹（解压后大小 / 压缩比）、写入时的路径穿越；内容缺少 zip
    魔数头时拒绝伪造扩展名。"""

    def __init__(
        self,
        *,
        max_upload_bytes: int,
        max_uncompressed_bytes: int,
        max_file_count: int,
        max_uncompression_ratio: float,
    ) -> None:
        self.max_upload_bytes = max_upload_bytes
        self.max_uncompressed_bytes = max_uncompressed_bytes
        self.max_file_count = max_file_count
        self.max_uncompression_ratio = max_uncompression_ratio

    def extract(self, data: bytes, dest_root: Path) -> ExtractResult:
        if len(data) > self.max_upload_bytes:
            raise PayloadTooLargeError("UPLOAD_TOO_LARGE", "upload exceeds size limit")
        if not data.startswith(_ZIP_MAGIC):
            raise UnsupportedMediaTypeError("NOT_A_ZIP", "file is not a zip archive")
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            raise UnsupportedMediaTypeError("NOT_A_ZIP", "file is not a valid zip archive")

        names = archive.namelist()
        if len(names) > self.max_file_count:
            raise PayloadTooLargeError("TOO_MANY_FILES", "archive contains too many files")

        total_uncompressed = 0
        for info in archive.infolist():
            self._validate_member_name(info.filename)
            self._validate_member_type(info)
            total_uncompressed += info.file_size
            if total_uncompressed > self.max_uncompressed_bytes:
                raise PayloadTooLargeError(
                    "ARCHIVE_TOO_LARGE", "uncompressed size exceeds limit"
                )
            if info.compress_size > 0 and (
                info.file_size / info.compress_size > self.max_uncompression_ratio
            ):
                raise SecurityError("ZIP_BOMB", "archive looks like a decompression bomb")

        dest_root.mkdir(parents=True, exist_ok=True)
        extracted: list[Path] = []
        for info in archive.infolist():
            if info.is_dir() or info.filename.endswith(_DIR_END_MARK):
                continue
            target = (dest_root / info.filename).resolve()
            if not str(target).startswith(str(dest_root.resolve())):
                raise SecurityError("PATH_TRAVERSAL", "archive member escapes root")
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)
            extracted.append(target)
        return ExtractResult(files=extracted)

    @staticmethod
    def _validate_member_name(name: str) -> None:
        if not name:
            return
        if name.startswith("/") or name.startswith("\\"):
            raise SecurityError("PATH_TRAVERSAL", "absolute path not allowed")
        if ":" in name.split("/")[0]:
            raise SecurityError("PATH_TRAVERSAL", "drive letter not allowed")
        parts = name.replace("\\", "/").split("/")
        if ".." in parts:
            raise SecurityError("PATH_TRAVERSAL", "parent traversal not allowed")

    @staticmethod
    def _validate_member_type(info: zipfile.ZipInfo) -> None:
        mode = (info.external_attr >> 16) & 0xFFFF
        if stat.S_ISLNK(mode):
            raise SecurityError("SYMLINK_NOT_ALLOWED", "symlink entries are not allowed")
        if info.file_size < 0:
            raise ValidationError("INVALID_ARCHIVE", "negative member size")