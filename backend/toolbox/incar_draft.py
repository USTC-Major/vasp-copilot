"""Typed, deterministic INCAR proposal and atomic commit helpers."""
from __future__ import annotations

import difflib
import hashlib
import math
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from backend.app.generators.serializer import IncarParser, IncarSerializer
from backend.app.parsers.incar import KNOWN_TAGS

_TAG_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_MAX_ENTRIES = 256
_MAX_VALUE_ITEMS = 4096
_MAX_TEXT_BYTES = 1_000_000
_MISSING_HASH = hashlib.sha256(b"<missing>").hexdigest()


class IncarUnknownTagError(ValueError):
    """A proposal used a tag outside the curated scientific allowlist."""


def _normalize_value(tag: str, value: object) -> bool | int | float | str:
    if not isinstance(value, (bool, int, float, str)):
        raise ValueError(f"{tag} 包含不支持的值类型")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{tag} 包含非有限数值")
    if isinstance(value, str):
        if len(value.encode("utf-8")) > _MAX_TEXT_BYTES:
            raise ValueError(f"{tag} 的文本值过长")
        if any(ch in value for ch in "\r\n;#!"):
            raise ValueError(f"{tag} 的文本值包含换行、分号或注释分隔符")
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
            raise ValueError(f"{tag} 的文本值包含控制字符")
    return value


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalize_entries(raw: object) -> list[dict[str, Any]]:
    """Validate an ordered list of typed ``{tag, value}`` entries."""
    if not isinstance(raw, list) or not raw:
        raise ValueError("entries 必须是非空有序数组")
    if len(raw) > _MAX_ENTRIES:
        raise ValueError(f"entries 超过上限 {_MAX_ENTRIES}")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict) or set(item) != {"tag", "value"}:
            raise ValueError(f"entries[{index}] 只允许 tag/value")
        tag = str(item.get("tag") or "").strip().upper()
        if not _TAG_RE.fullmatch(tag):
            raise ValueError(f"非法 INCAR 参数名: {tag or '<empty>'}")
        if tag in seen:
            raise ValueError(f"重复 INCAR 参数: {tag}")
        if tag not in KNOWN_TAGS:
            raise IncarUnknownTagError(f"未知 INCAR 参数: {tag}")
        seen.add(tag)
        value = item.get("value")
        if isinstance(value, list):
            if not value or len(value) > _MAX_VALUE_ITEMS:
                raise ValueError(f"{tag} 的数组值为空或过长")
            value = [_normalize_value(tag, v) for v in value]
        else:
            value = _normalize_value(tag, value)
        result.append({"tag": tag, "value": value})
    return result


def serialize_entries(entries: list[dict[str, Any]]) -> str:
    """Serialize in caller-specified order and verify a deterministic round trip."""
    serializer = IncarSerializer()
    lines = [
        f"{item['tag']} = {serializer._render_value(item['value'], item['tag'])}"
        for item in entries
    ]
    text = "\n".join(lines) + "\n"
    reparsed = IncarParser().parse(text)
    if list(reparsed) != [item["tag"] for item in entries]:
        raise ValueError("INCAR 序列化顺序校验失败")
    # Reusing the established serializer verifies value round-trip semantics.
    serializer._verify_roundtrip(
        text, {item["tag"]: item["value"] for item in entries}
    )
    return text


def build_incar_action(*, root: Path, relative_path: str,
                       entries: object, project_id: str,
                       task_id: str, job_key: str) -> tuple[dict, str]:
    normalized = normalize_entries(entries)
    rel = relative_path.replace("\\", "/").strip("/")
    if not rel or rel.split("/")[-1].upper() != "INCAR":
        raise ValueError("INCAR 草稿目标必须是作业目录内的 INCAR")
    if any(part in {"", ".", ".."} or part.startswith(".")
           for part in rel.split("/")):
        raise ValueError("非法 INCAR 目标路径")
    root = root.resolve()
    target = (root / Path(*rel.split("/"))).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("INCAR 目标越出计算工作区") from exc
    old = target.read_bytes() if target.is_file() else b""
    if len(old) > _MAX_TEXT_BYTES:
        raise ValueError("现有 INCAR 超过安全预览上限")
    try:
        old_text = old.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError("现有 INCAR 不是有效 UTF-8 文本") from exc
    if any(ord(ch) < 32 and ch not in "\n\r\t" for ch in old_text):
        raise ValueError("现有 INCAR 包含二进制控制字符")
    base_hash = sha256_bytes(old) if target.is_file() else _MISSING_HASH
    text = serialize_entries(normalized)
    encoded = text.encode("utf-8")
    if len(encoded) > _MAX_TEXT_BYTES:
        raise ValueError("INCAR 草稿超过安全大小上限")
    binding = {
        "operation": "incar_write",
        "project_id": project_id,
        "task_id": task_id,
        "job_key": job_key,
        "execution_kind": "atomic_local_replace",
        "workspace_root": str(root),
        "relative_path": rel,
        "base_sha256": base_hash,
        "proposal_sha256": sha256_bytes(encoded),
        "proposal_size": len(encoded),
        "entries": normalized,
        "content": text,
    }
    before = old_text.splitlines()
    after = text.splitlines()
    diff = "\n".join(difflib.unified_diff(
        before, after, fromfile=f"{rel} (current)",
        tofile=f"{rel} (proposal)", lineterm="",
    ))
    return binding, diff[:20_000]


def commit_incar_action(action: dict, *, root: Path) -> str:
    """Revalidate hashes and atomically replace the one confirmed INCAR file."""
    binding = action.get("binding") or {}
    if binding.get("operation") != "incar_write":
        raise ValueError("action is not an INCAR write")
    rel = str(binding.get("relative_path") or "")
    root = root.resolve()
    if str(root) != binding.get("workspace_root"):
        raise ValueError("INCAR workspace changed after confirmation")
    target = (root / Path(*rel.split("/"))).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("INCAR target escaped workspace") from exc
    current = target.read_bytes() if target.is_file() else b""
    current_hash = sha256_bytes(current) if target.is_file() else _MISSING_HASH
    if current_hash != binding.get("base_sha256"):
        raise ValueError("INCAR changed after preview; confirmation is stale")
    data = str(binding.get("content") or "").encode("utf-8")
    if (len(data) != binding.get("proposal_size")
            or sha256_bytes(data) != binding.get("proposal_sha256")):
        raise ValueError("INCAR proposal hash mismatch")
    # Reparse immediately before commit so persisted payload corruption cannot
    # turn into an invalid file even if another layer regresses.
    IncarParser().parse(data.decode("utf-8"))
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_name = ""
    try:
        with tempfile.NamedTemporaryFile(
                mode="wb", dir=target.parent, prefix=".INCAR.",
                suffix=".tmp", delete=False) as handle:
            temp_name = handle.name
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)
    return (f"已原子写入 `{rel}`（{len(data)} B，"
            f"SHA-256 {binding['proposal_sha256'][:12]}…）")
