"""BE-A 统一错误类型（设计文档 7.9 节 Recipe 稳定错误码）。"""

from __future__ import annotations

from typing import Any, Dict, Optional

# Recipe / 组合 / 覆盖相关稳定错误码（7.9 节）
RECIPE_NOT_FOUND = "RECIPE_NOT_FOUND"
RECIPE_SCHEMA_INVALID = "RECIPE_SCHEMA_INVALID"
RECIPE_SCOPE_MISMATCH = "RECIPE_SCOPE_MISMATCH"
RECIPE_CONFLICT = "RECIPE_CONFLICT"
RECIPE_CONFIRMATION_REQUIRED = "RECIPE_CONFIRMATION_REQUIRED"
OVERRIDE_NOT_ALLOWED = "OVERRIDE_NOT_ALLOWED"
DERIVED_PARAMETER_UNRESOLVED = "DERIVED_PARAMETER_UNRESOLVED"
COMPOSITION_REVISION_CONFLICT = "COMPOSITION_REVISION_CONFLICT"
UNKNOWN_PARAMETER_CONFLICT = "UNKNOWN_PARAMETER_CONFLICT"

# BE-A 扩展错误码（工作流/生成门控）
DFTU_CONFIRMATION_REQUIRED = "DFTU_CONFIRMATION_REQUIRED"
MAGMOM_LENGTH_MISMATCH = "MAGMOM_LENGTH_MISMATCH"
LDAU_LENGTH_MISMATCH = "LDAU_LENGTH_MISMATCH"
POTCAR_NOT_PREPARED = "POTCAR_NOT_PREPARED"
UPSTREAM_OUTPUT_MISSING = "UPSTREAM_OUTPUT_MISSING"
UPSTREAM_DIAGNOSIS_NOT_PASSED = "UPSTREAM_DIAGNOSIS_NOT_PASSED"
BAND_WORKFLOW_DISABLED = "BAND_WORKFLOW_DISABLED"
INCAR_ROUNDTRIP_MISMATCH = "INCAR_ROUNDTRIP_MISMATCH"
KPOINTS_GENERATION_FAILED = "KPOINTS_GENERATION_FAILED"


class BeAError(Exception):
    """BE-A 模块统一异常。集成时映射为 error_response（7.9 节）。"""

    code: str = "INTERNAL_ERROR"

    def __init__(
        self,
        message: str,
        *,
        code: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        self.details = details or {}
        self.retryable = retryable

    def to_error_body(self) -> Dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
                "retryable": self.retryable,
            }
        }


class RecipeError(BeAError):
    code = RECIPE_NOT_FOUND


class RecipeSchemaInvalid(RecipeError):
    code = RECIPE_SCHEMA_INVALID


class RecipeScopeMismatch(RecipeError):
    code = RECIPE_SCOPE_MISMATCH


class RecipeConflictError(RecipeError):
    code = RECIPE_CONFLICT


class RecipeConfirmationRequired(RecipeError):
    code = RECIPE_CONFIRMATION_REQUIRED


class OverrideNotAllowed(BeAError):
    code = OVERRIDE_NOT_ALLOWED


class DerivedParameterUnresolved(BeAError):
    code = DERIVED_PARAMETER_UNRESOLVED


class CompositionRevisionConflict(BeAError):
    code = COMPOSITION_REVISION_CONFLICT


class DftuConfirmationRequired(BeAError):
    code = DFTU_CONFIRMATION_REQUIRED


class IncarRoundtripMismatch(BeAError):
    code = INCAR_ROUNDTRIP_MISMATCH


class KpointsGenerationFailed(BeAError):
    code = KPOINTS_GENERATION_FAILED
