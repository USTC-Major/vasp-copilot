
"""FileStore — 单进程临时文件/结构缓存，带磁盘索引持久化。

设计 6.1/6.3：file_id/structure_id 记录在 data/files.index.json，
重启后可恢复（结构 ID 不再因进程重启而“过期/丢失”）。
字节本身以 root/<file_id> 落盘；仅索引持久化，不重写原始文件。
"""
from __future__ import annotations

import datetime
import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from ..core.errors import NotFoundError
from ..schemas.structure import StructureSummary


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class FileRecord:
    """设计 6.1：上传单文件的 TTL 临时记录。"""

    file_id: str
    name: str
    kind: str
    size_bytes: int
    sha256: str
    path: Path
    file_status: str = "ready"
    expires_at: str = ""
    created_at: float = field(default_factory=time.time)
    touched_at: float = field(default_factory=time.time)


@dataclass
class StructureRecord:
    """设计 6.3：structure/analyze 产物缓存 (structure_id -> summary + normalized)。"""

    structure_id: str
    file_id: str
    summary: StructureSummary
    normalized_notes: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    touched_at: float = field(default_factory=time.time)


class FileStore:
    """内存 + TTL 的临时存储，索引持久化到 root.parent/structs.index.json。

    所有写操作（register/store）会同步更新索引文件；__init__ 启动时若磁盘
    索引与 root 字节都还在，则恢复 record，保证重启后 structure_id 仍可用。
    """

    def __init__(self, root: Path, ttl_seconds: int = 24 * 3600) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)
        self._ttl = ttl_seconds
        self._files: Dict[str, FileRecord] = {}
        self._structures: Dict[str, StructureRecord] = {}
        self._index_path = (root.parent / "structs.index.json")
        self._load_index()
        self._persist()

    # ------------------------------------------------------------- helpers
    def _new_id(self, prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex[:10]}"

    def _path_for(self, file_id: str) -> Path:
        return self._root / file_id

    def _persist(self) -> None:
        """把当前索引（files + structures 元数据）写回磁盘。"""

        data = {
            "version": 1,
            "files": {
                fid: {
                    "file_id": rec.file_id,
                    "name": rec.name,
                    "kind": rec.kind,
                    "size_bytes": rec.size_bytes,
                    "sha256": rec.sha256,
                    "file_status": rec.file_status,
                    "expires_at": rec.expires_at,
                    "created_at": rec.created_at,
                    "touched_at": rec.touched_at,
                }
                for fid, rec in self._files.items()
                if self._path_for(rec.file_id).exists()
            },
            "structures": {
                sid: {
                    "structure_id": rec.structure_id,
                    "file_id": rec.file_id,
                    "summary": rec.summary.model_dump(mode="json"),
                    "normalized_notes": rec.normalized_notes,
                    "created_at": rec.created_at,
                    "touched_at": rec.touched_at,
                }
                for sid, rec in self._structures.items()
            },
        }
        try:
            tmp = self._index_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                           encoding="utf-8")
            tmp.replace(self._index_path)
        except OSError:
            # 磁盘不可写（只读环境）时保持内存模式，不抛错影响功能。
            pass

    def _load_index(self) -> None:
        try:
            raw = self._index_path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, ValueError):
            return
        for fid, d in (data.get("files") or {}).items():
            rec_path = self._path_for(fid)
            if not rec_path.exists():
                continue
            rec = FileRecord(
                file_id=d.get("file_id", fid),
                name=d.get("name", ""),
                kind=d.get("kind", ""),
                size_bytes=int(d.get("size_bytes", 0)),
                sha256=d.get("sha256", ""),
                path=rec_path,
                file_status=d.get("file_status", "ready"),
                expires_at=d.get("expires_at", ""),
                created_at=float(d.get("created_at", 0.0)),
                touched_at=float(d.get("touched_at", 0.0)),
            )
            self._files[fid] = rec
        for sid, d in (data.get("structures") or {}).items():
            summary = StructureSummary.model_validate(d.get("summary") or {})
            rec = StructureRecord(
                structure_id=d.get("structure_id", sid),
                file_id=d.get("file_id", ""),
                summary=summary,
                normalized_notes=d.get("normalized_notes"),
                created_at=float(d.get("created_at", 0.0)),
                touched_at=float(d.get("touched_at", 0.0)),
            )
            self._structures[sid] = rec

    # ------------------------------------------------------------- file API
    def store_file(self, name: str, kind: str, data: bytes) -> FileRecord:
        return self.register_file(self._new_id("file"), name, kind, data)

    def register_file(self, file_id: str, name: str, kind: str, data: bytes) -> FileRecord:
        """按指定 file_id 注册文件（生成产物以文件树中的 file_id 供预览）。"""
        dest = self._path_for(file_id)
        dest.write_bytes(data)
        record = FileRecord(
            file_id=file_id,
            name=name,
            kind=kind,
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            path=dest,
            expires_at=_now_iso(),
        )
        self._files[file_id] = record
        self._persist()
        return record

    def get_file(self, file_id: str) -> FileRecord:
        record = self._files.get(file_id)
        if record is None or time.time() - record.touched_at > self._ttl:
            raise NotFoundError("FILE_NOT_FOUND", "unknown or expired file id")
        record.touched_at = time.time()
        self._persist()
        return record

    # ------------------------------------------------------- structure API
    def store_structure(
        self,
        file_id: str,
        summary: StructureSummary,
        normalized_poscar_file_id: Optional[str] = None,
    ) -> StructureRecord:
        sid = self._new_id("str")
        record = StructureRecord(
            structure_id=sid,
            file_id=file_id,
            summary=summary,
            normalized_notes=normalized_poscar_file_id,
        )
        self._structures[sid] = record
        self._persist()
        return record

    def get_structure(self, structure_id: str) -> StructureRecord:
        record = self._structures.get(structure_id)
        if record is None or time.time() - record.touched_at > self._ttl:
            raise NotFoundError("STRUCTURE_NOT_FOUND",
                                "unknown or expired structure id")
        record.touched_at = time.time()
        self._persist()
        return record

    def cleanup_expired(self) -> None:
        now = time.time()
        for fid, rec in list(self._files.items()):
            if now - rec.touched_at > self._ttl:
                self._files.pop(fid, None)
                try:
                    rec.path.unlink(missing_ok=True)
                except OSError:
                    pass
        for sid, rec in list(self._structures.items()):
            if now - rec.touched_at > self._ttl:
                self._structures.pop(sid, None)
        self._persist()
