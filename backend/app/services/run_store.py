from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..core.errors import NotFoundError
from ..schemas.detected import DetectedRun
from ..schemas.report import ReportMetadata
from ..schemas.result import DiagnosisResult


@dataclass
class RunRecord:
    """保存单个诊断 run 在 5 个端点间共享的状态。"""

    diagnosis_id: str
    detected: DetectedRun
    base_dir: Path
    diagnosis_status: str = "uploaded"
    result: Optional[DiagnosisResult] = None
    report_text: Optional[str] = None
    report_metadata: Optional[ReportMetadata] = None
    fix_files: dict[str, dict[str, str]] = field(default_factory=dict)
    session_id: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    touched_at: float = field(default_factory=time.time)

    def touch(self) -> None:
        self.touched_at = time.time()


class RunStore:
    """MVP 的内存存储（单进程）。基于 TTL 清理。"""

    def __init__(self, ttl_seconds: int = 24 * 3600) -> None:
        self._ttl = ttl_seconds
        self._records: dict[str, RunRecord] = {}

    def create(self, diagnosis_id: str, detected: DetectedRun,
               base_dir: Path, session_id: Optional[str] = None) -> RunRecord:
        self.cleanup_expired()
        record = RunRecord(diagnosis_id=diagnosis_id, detected=detected,
                           base_dir=base_dir, session_id=session_id)
        self._records[diagnosis_id] = record
        return record

    def get(self, diagnosis_id: str) -> RunRecord:
        record = self._records.get(diagnosis_id)
        if record is None:
            raise NotFoundError("DIAGNOSIS_NOT_FOUND", "unknown diagnosis id")
        if time.time() - record.touched_at > self._ttl:
            self._records.pop(diagnosis_id, None)
            self._remove_dir(record)
            raise NotFoundError("DIAGNOSIS_NOT_FOUND", "diagnosis expired")
        record.touch()
        return record

    def put(self, record: RunRecord) -> None:
        record.touch()
        self._records[record.diagnosis_id] = record

    def cleanup_expired(self, now: Optional[float] = None) -> int:
        now = now or time.time()
        stale = [k for k, r in self._records.items()
                 if now - r.touched_at > self._ttl]
        for k in stale:
            record = self._records.pop(k, None)
            self._remove_dir(record)
        return len(stale)

    def sweep_orphaned_runs(self, runs_root: Path) -> int:
        """清理重启后遗留、超过 TTL 的孤儿 run 目录（内存记录未追踪）。

        仅处理 ``runs`` 目录下的 ``diag_`` 前缀子目录；受追踪记录永不删除。
        用解析路径并校验父目录收敛在 ``runs_root`` 内，避免符号链接越界。"""
        if not runs_root.is_dir():
            return 0
        runs_root = runs_root.resolve()
        tracked = {r.base_dir.resolve() for r in self._records.values()
                   if r.base_dir is not None}
        removed = 0
        now = time.time()
        for child in runs_root.iterdir():
            if not child.name.startswith("diag_"):
                continue
            try:
                target = child.resolve()
            except OSError:
                continue
            if target.parent != runs_root or not target.is_dir():
                continue
            if target in tracked:
                continue
            try:
                if now - target.stat().st_mtime <= self._ttl:
                    continue
            except OSError:
                continue
            try:
                shutil.rmtree(target)
                removed += 1
            except OSError:
                continue
        return removed

    def _remove_dir(self, record: Optional[RunRecord]) -> None:
        if record is None or record.base_dir is None:
            return
        try:
            target = record.base_dir.resolve()
            if target.exists():
                shutil.rmtree(target)
        except OSError:
            return
