from __future__ import annotations

from typing import Any, Optional


class AppError(Exception):
    """携带稳定错误码与 HTTP 状态的基础异常。"""

    def __init__(
        self,
        code: str,
        message: str,
        http_status: int,
        *,
        retryable: bool = False,
        details: Optional[list[dict[str, Any]]] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.retryable = retryable
        self.details = details or []


class ValidationError(AppError):
    def __init__(self, code: str, message: str, details=None) -> None:
        super().__init__(code, message, 422, details=details)


class NotFoundError(AppError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(code, message, 404)


class ConflictError(AppError):
    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(code, message, 409, retryable=retryable)


class UnsupportedMediaTypeError(AppError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(code, message, 415)


class PayloadTooLargeError(AppError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(code, message, 413)


class SecurityError(AppError):
    def __init__(self, code: str, message: str, http_status: int = 403) -> None:
        super().__init__(code, message, http_status)


# Stable error codes (MVP 7.9 semantics + doctor-specific)
def err(code: str, message: str, http_status: int = 422, retryable: bool = False) -> AppError:
    return AppError(code, message, http_status, retryable=retryable)