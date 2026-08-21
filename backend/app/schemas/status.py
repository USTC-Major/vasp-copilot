from __future__ import annotations

from enum import Enum


class DiagnosisStatus(str, Enum):
    UPLOADED = "uploaded"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class FixStatus(str, Enum):
    PROPOSED = "proposed"
    GENERATED = "generated"
    UNAVAILABLE = "unavailable"


class CheckStatus(str, Enum):
    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"
    NOT_RUN = "not_run"


class ModeKind(str, Enum):
    RULE_BASED = "rule_based"
    RULE_PLUS_LLM = "rule_plus_llm"


class Severity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"