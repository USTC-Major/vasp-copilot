"""P0 files API（设计 6.1 / 6.2）：上传单文件 + 统一预览。"""
from __future__ import annotations

import base64
import hashlib
import io
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, UploadFile

from ...core.errors import (
    SecurityError,
    UnsupportedMediaTypeError,
    ValidationError,
)
from ...schemas.api import ApiEnvelope
from .deps import file_store, get_request_id, settings

router = APIRouter()

_BINARY_EXTS = (
    ".zip", ".gz", ".bz2", ".tar", ".xz", ".png", ".jpg",
    ".jpeg", ".gif", ".pdf", ".pickle", ".npy",
)

_PREVIEW_KIND = {
    "INCAR": "incar", "KPOINTS": "kpoints", "POSCAR": "poscar",
    "CONTCAR": "poscar", "OSZICAR": "oszicar", "OUTCAR": "outcar",
    "WAVECAR": "wavecar", "CHGCAR": "chgcar", "POSCAR.tmp": "poscar",
}


def _file_kind(name: str) -> str:
    base = Path(name).name.upper()
    if base in ("POSCAR", "CONTCAR"):
        return "poscar"
    if base.endswith(".CIF"):
        return "cif"
    if "POTCAR" in base:
        return "pseudopotential"
    if base.endswith(".ZIP"):
        return "archive"
    return _PREVIEW_KIND.get(base, "file")


def _delta(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode("utf-8")).decode("ascii")


def _epsilon(cursor: str) -> int:
    try:
        return int(base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - opaque cursor must fail gracefully
        raise ValidationError("INVALID_CURSOR", "invalid opaque preview cursor") from exc


@router.post("/files/upload", response_model=ApiEnvelope)
async def upload(
    file: UploadFile = File(...),
    purpose: str = "structure",
    session_id: Optional[str] = None,
    license_confirmed: Optional[str] = None,
    x_request_id: str = Depends(get_request_id),
) -> ApiEnvelope:
    """设计 6.1：上传单个 POSCAR/CIF 或赝势目录 zip。"""
    data = await file.read()
    if len(data) > settings.max_upload_bytes:
        raise ValidationError(
            "FILE_TOO_LARGE",
            f"file exceeds {settings.max_upload_bytes} bytes",
        )
    name = file.filename or "upload"
    if purpose == "pseudopotential":
        if license_confirmed != "true":
            raise ValidationError(
                "PSEUDOPOTENTIAL_LICENSE_REQUIRED",
                "pseudopotential upload requires license_confirmed=true",
            )
        kind = "pseudopotential"
    else:
        kind = _file_kind(name)
    record = file_store.store_file(name, kind, data)
    return ApiEnvelope(request_id=x_request_id, data={
        "file": {
            "file_id": record.file_id,
            "name": record.name,
            "kind": record.kind,
            "size_bytes": record.size_bytes,
            "sha256": record.sha256,
            "file_status": record.file_status,
            "expires_at": record.expires_at,
        },
    })


@router.get("/files/{file_id}/preview", response_model=ApiEnvelope)
async def preview(
    file_id: str,
    mode: str = "head",
    start_line: int = 1,
    max_lines: Optional[int] = None,
    cursor: Optional[str] = None,
    x_request_id: str = Depends(get_request_id),
) -> ApiEnvelope:
    """设计 6.2：统一文本预览（对齐诊断侧 preview 策略）。"""
    record = file_store.get_file(file_id)
    data = await _build_preview(
        file_id=record.file_id,
        path=record.path,
        name=record.name,
        mode=mode,
        start_line=start_line,
        max_lines=max_lines,
        cursor=cursor,
    )
    return ApiEnvelope(request_id=x_request_id, data=data)


async def _build_preview(
    *,
    file_id: str,
    path: Path,
    name: str,
    mode: str,
    start_line: int,
    max_lines: Optional[int],
    cursor: Optional[str],
) -> dict:
    upper = name.upper()
    if upper == "POTCAR":
        raise SecurityError("FILE_PREVIEW_POLICY_DENIED",
                            "POTCAR is not previewable", http_status=403)
    kind = _file_kind(name)
    if kind in ("wavecar", "chgcar") or name.lower().endswith(_BINARY_EXTS):
        raise UnsupportedMediaTypeError(
            "FILE_PREVIEW_UNSUPPORTED_BINARY", "binary file not previewable")

    data_bytes = path.read_bytes()
    if b"\x00" in data_bytes[:8192]:
        raise UnsupportedMediaTypeError(
            "FILE_PREVIEW_UNSUPPORTED_BINARY", "binary file not previewable")
    try:
        text = data_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise UnsupportedMediaTypeError(
            "FILE_PREVIEW_UNSUPPORTED_BINARY", "undecodable file not previewable")

    lines = text.splitlines()
    total = len(lines)
    is_outcar = upper == "OUTCAR"

    if is_outcar:
        limit = max(1, settings.outcar_preview_lines)
        if max_lines is not None:
            limit = max(1, min(max_lines, limit))
        if mode == "tail":
            seg = lines[max(0, total - limit):]
            start = max(1, total - len(seg) + 1)
        else:
            start = max(1, start_line)
            seg = lines[start - 1:start - 1 + limit]
        end = max(start - 1, start + len(seg) - 1)
        truncated = False
        next_cursor = None
    else:
        effective_lines = max(1, settings.max_preview_lines)
        byte_cap = max(1, settings.max_preview_bytes)
        if max_lines is not None:
            effective_lines = max(1, min(max_lines, effective_lines))
        if mode == "tail":
            seg = lines[max(0, total - effective_lines):]
            start = max(1, total - len(seg) + 1)
            end = total
            truncated = False
            next_cursor = None
        else:
            idx = _epsilon(cursor) if cursor is not None else max(0, start_line - 1)
            idx = max(0, min(idx, total))
            seg = []
            used = 0
            for i in range(idx, total):
                line = lines[i]
                if seg and used + len(line.encode("utf-8")) + 1 > byte_cap:
                    break
                if len(seg) >= effective_lines:
                    break
                seg.append(line)
                used += len(line.encode("utf-8")) + 1
            start = idx + 1
            end = idx + len(seg)
            next_idx = idx + len(seg)
            truncated = next_idx < total
            next_cursor = _delta(next_idx) if truncated else None

    content = "\n".join(seg)
    return {
        "file_id": file_id,
        "name": name,
        "kind": kind,
        "mime_type": "text/plain",
        "encoding": "utf-8",
        "sha256": hashlib.sha256(data_bytes).hexdigest(),
        "preview": {
            "content": content,
            "start_line": start,
            "end_line": end,
            "total_lines": total,
            "returned_bytes": len(content.encode("utf-8")),
            "truncated": truncated,
            "next_cursor": next_cursor,
        },
        "policy": {
            "max_preview_bytes": settings.max_preview_bytes,
            "max_preview_lines": (settings.outcar_preview_lines
                                  if is_outcar else settings.max_preview_lines),
            "binary_rejected": True,
            "sensitive_content_redacted": False,
        },
    }
