"""OverrideValidator（设计文档 4.1 节第 9 步、8.2/10.8 节）。

typed patch 白名单校验：
- 参数必须在 selected Recipe 的 allowed_overrides 或全局修复白名单内；
- 类型/范围/operation 合法性检查；
- remove 只允许显式可删除参数；
- 语义性参数（DFT+U/MAGMOM/ISPIN/EDIFFG 等）必须 confirmed_by_user；
- expected_revision 不匹配返回 COMPOSITION_REVISION_CONFLICT。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend.app.recipes.errors import CompositionRevisionConflict, OverrideNotAllowed
from backend.app.schemas.generation import (
    ParameterPatch,
    PatchOperation,
    PatchValidationResult,
)
from backend.app.schemas.recipe import AllowedOverrideRule, RecipeManifest

# 全局修复白名单（10.8 节：selected allowed_overrides 之外的最小集合）
GLOBAL_OVERRIDE_WHITELIST: Dict[str, AllowedOverrideRule] = {
    "ENCUT": AllowedOverrideRule(type="number", minimum=1),
    "ISPIN": AllowedOverrideRule(type="integer", enum=[1, 2]),
    "EDIFF": AllowedOverrideRule(type="number", exclusive_minimum=0),
    "EDIFFG": AllowedOverrideRule(type="number"),
    "ALGO": AllowedOverrideRule(type="string", enum=["Normal", "Fast", "VeryFast", "All", "Damped"]),
    "ISMEAR": AllowedOverrideRule(type="integer", minimum=-5, maximum=2),
    "SIGMA": AllowedOverrideRule(type="number", exclusive_minimum=0, maximum=2.0),
    "NELM": AllowedOverrideRule(type="integer", minimum=1),
    "NSW": AllowedOverrideRule(type="integer", minimum=0),
    "NEDOS": AllowedOverrideRule(type="integer", minimum=1),
    "EMIN": AllowedOverrideRule(type="number"),
    "EMAX": AllowedOverrideRule(type="number"),
}

# 语义性参数：patch 必须 confirmed_by_user=true（10.8 节）
SEMANTIC_PARAMETERS = {
    "ISPIN", "MAGMOM", "LDAU", "LDAUTYPE", "LDAUL", "LDAUU", "LDAUJ",
    "EDIFFG", "LCHARG", "LWAVE", "ICHARG",
}


class OverrideValidator:
    def __init__(self, global_whitelist: Optional[Dict[str, AllowedOverrideRule]] = None):
        self._global = dict(global_whitelist or GLOBAL_OVERRIDE_WHITELIST)

    def _lookup_rule(
        self, parameter: str, selected: List[RecipeManifest]
    ) -> Optional[AllowedOverrideRule]:
        for manifest in reversed(selected):  # 高层优先
            rule = manifest.allowed_overrides.get(parameter)
            if rule is not None:
                return rule
        return self._global.get(parameter)

    def validate_patch(
        self,
        patch: ParameterPatch,
        selected: List[RecipeManifest],
        resolved: Dict[str, Any],
        current_revision: int,
    ) -> PatchValidationResult:
        if patch.expected_revision != current_revision:
            raise CompositionRevisionConflict(
                f"patch expected revision {patch.expected_revision} "
                f"but composition is at {current_revision}",
                details={
                    "expected_revision": patch.expected_revision,
                    "current_revision": current_revision,
                },
            )
        parameter = patch.parameter
        if parameter in SEMANTIC_PARAMETERS and not patch.confirmed_by_user:
            raise OverrideNotAllowed(
                f"semantic parameter {parameter} requires confirmed_by_user=true",
                details={"parameter": parameter},
            )
        if patch.operation == PatchOperation.REMOVE:
            rule = self._lookup_rule(parameter, selected)
            if rule is None or not rule.removable:
                raise OverrideNotAllowed(
                    f"parameter {parameter} is not removable",
                    details={"parameter": parameter},
                )
            return PatchValidationResult(
                allowed=True, rule_ids=["OVERRIDE_WHITELIST", "REMOVE_ALLOWED"]
            )
        rule = self._lookup_rule(parameter, selected)
        if rule is None:
            raise OverrideNotAllowed(
                f"override not allowed for parameter {parameter}",
                details={"parameter": parameter},
            )
        self._check_value_type(parameter, patch.value, rule)
        rule_ids = ["OVERRIDE_WHITELIST"]
        if rule.minimum is not None or rule.exclusive_minimum is not None:
            rule_ids.append(f"{parameter}_RANGE")
        return PatchValidationResult(allowed=True, rule_ids=rule_ids)

    def _check_value_type(self, parameter: str, value: Any, rule: AllowedOverrideRule) -> None:
        def reject(reason: str) -> None:
            raise OverrideNotAllowed(
                f"invalid override value for {parameter}: {reason}",
                details={"parameter": parameter, "reason": reason},
            )

        if value is None:
            reject("value is required")
        if rule.type in ("number", "integer"):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                reject(f"expected {rule.type}")
            if rule.type == "integer" and isinstance(value, float) and not value.is_integer():
                reject("expected integer")
            numeric = float(value)
            if rule.minimum is not None and numeric < rule.minimum:
                reject(f"value below minimum {rule.minimum}")
            if rule.maximum is not None and numeric > rule.maximum:
                reject(f"value above maximum {rule.maximum}")
            if rule.exclusive_minimum is not None and numeric <= rule.exclusive_minimum:
                reject(f"value must be > {rule.exclusive_minimum}")
            if rule.exclusive_maximum is not None and numeric >= rule.exclusive_maximum:
                reject(f"value must be < {rule.exclusive_maximum}")
        elif rule.type == "boolean":
            if not isinstance(value, bool):
                reject("expected boolean")
        elif rule.type == "string":
            if not isinstance(value, str):
                reject("expected string")
        else:
            reject(f"unknown override type {rule.type}")
        if rule.enum is not None and value not in rule.enum:
            reject(f"value must be one of {rule.enum}")

    def validate_all(
        self,
        patches: List[ParameterPatch],
        selected: List[RecipeManifest],
        resolved: Dict[str, Any],
        current_revision: int,
    ) -> List[PatchValidationResult]:
        results: List[PatchValidationResult] = []
        working = dict(resolved)
        for patch in patches:
            result = self.validate_patch(patch, selected, working, current_revision)
            if patch.operation == PatchOperation.REMOVE:
                working.pop(patch.parameter, None)
            else:
                working[patch.parameter] = patch.value
            results.append(result)
        return results
