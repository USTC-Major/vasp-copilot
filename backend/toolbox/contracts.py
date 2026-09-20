"""Neutral tool request and structured execution failures."""
from dataclasses import dataclass, field
from typing import Any

@dataclass
class ToolRequest:
    name: str
    args: dict[str, Any] = field(default_factory=dict)
    rationale: str = ''

class ToolboxError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 400, retryable: bool = False):
        super().__init__(message)
        self.code, self.status, self.retryable = code, status, retryable

    def payload(self):
        return {'code': self.code, 'message': str(self), 'retryable': self.retryable}

class ToolFailure(str):
    """Legacy textual receipt with explicit machine-readable failure metadata."""
    def __new__(cls, code, message):
        value = super().__new__(cls, message)
        value.code = code
        return value
