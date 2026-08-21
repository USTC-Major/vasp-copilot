from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


class ErrorBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    code: str
    message: str
    retryable: bool = False
    field_errors: list[dict[str, str]] = []


class ApiEnvelope(BaseModel):
    """带 request_id 的统一 API 响应（MVP 6）。"""

    model_config = ConfigDict(extra="ignore")

    request_id: str
    data: Any = None
    error: Optional[ErrorBody] = None