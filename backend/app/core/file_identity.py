"""Deterministic, content-bound identities for workflow preview artifacts.

These opaque IDs are response metadata, never part of generated scientific files
or ZIP members. The versioned namespace distinguishes safe identities from the
legacy global file_01 counters whose workflow ownership cannot be recovered.
"""
from __future__ import annotations

import hashlib
import json
import re

_GENERATED_ID = re.compile(r"file_g1_[0-9a-f]{64}\Z")


def generated_file_id(workflow_id: str, relative_path: str, data: bytes) -> str:
    identity = json.dumps(
        ["workflow-file-v1", workflow_id, relative_path, hashlib.sha256(data).hexdigest()],
        ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")
    return "file_g1_" + hashlib.sha256(identity).hexdigest()


def is_bound_generated_file_id(file_id: str) -> bool:
    return _GENERATED_ID.fullmatch(file_id) is not None
