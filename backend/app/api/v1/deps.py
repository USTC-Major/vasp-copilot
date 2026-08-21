from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import Request
from fastapi import Header

from ...core.config import Settings
from ...security.extract import SafeArchiveExtractor
from ...services.run_store import RunStore
from ...services.file_store import FileStore

settings = Settings.from_env()
file_store = FileStore(root=Path(settings.data_dir) / "files", ttl_seconds=settings.ttl_seconds)
store = RunStore(ttl_seconds=settings.ttl_seconds)
extractor = SafeArchiveExtractor(
    max_upload_bytes=settings.max_upload_bytes,
    max_uncompressed_bytes=settings.max_uncompressed_bytes,
    max_file_count=settings.max_file_count,
    max_uncompression_ratio=settings.max_uncompression_ratio,
)


def get_request_id(
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> str:
    if x_request_id:
        return x_request_id
    return "req_" + uuid.uuid4().hex[:8]

