"""RecipeComposer（设计文档 4.1 节第 6/9 步、8.2/10.3/7.17 节）。

固定 layer/order 合并：base(10) → task(20) → electronic(30) → modifier(40–49)
→ precision(50) → user_patch(60)。同层不同值形成 RecipeConflict，绝不依赖
文件加载顺序“最后一个获胜”。任何覆盖都记录 provenance。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from backend.app.recipes.derived import DerivedParameterResolver
from backend.app.recipes.errors import (
    RecipeConflictError,
    RecipeScopeMismatch,
)
from backend.app.recipes.overrides import OverrideValidator
from backend.app.recipes.provenance import ProvenanceBuilder
from backend.app.recipes.registry import RecipeRegistry
from backend.app.recipes.selector import SelectionEntry
from backend.app.schemas.generation import ParameterPatch, PatchOperation
from backend.app.schemas.recipe import RecipeManifest, RecipePackManifest, SelectionContext
from backend.app.schemas.workflow import (
    PendingConfirmation,
    RecipeComposition,
    RecipeCompositionStatus,
    RecipeConflict,
    SelectedRecipeEntry,
)


def _same_value(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return float(a) == float(b)
    return a == b


@dataclass
class ComposeRequest:
    step_id: str
    context: SelectionContext
    entries: List[SelectionEntry]
    patches: List[ParameterPatch] = field(default_factory=list)
    confirmed_keys: Set[str] = field(default_factory=set)
    derived_inputs: Dict[str, Any] = field(default_factory=dict)
    revision: int = 1


class RecipeComposer:
    def __init__(
        self,
        registry: RecipeRegistry,
        pack: Optional[RecipePackManifest] = None,
        resolver: Optional[DerivedParameterResolver] = None,
        validator: Optional[OverrideValidator] = None,
    ) -> None:
        self._registry = registry
        self._pack = pack
        self._resolver = resolver or DerivedParameterResolver()
        self._validator = validator or OverrideValidator()

    def compose(self, request: ComposeRequest) -> RecipeComposition:
        manifests: List[RecipeManifest] = []
        for entry in request.entries:
            manifest = self._registry.get(entry.ref)
            self._check_scope(manifest, request.context)
            self._check_requires(manifest, request.context)
            manifests.append(manifest)
        ordered = sorted(manifests, key=lambda m: (m.layer.value, m.recipe_id))
        self._check_conflicts_declared(ordered)

        composition_id = f"composition_{request.step_id}"
        builder = ProvenanceBuilder(composition_id, request.revision)
        merged: Dict[str, Any] = {}
        conflicts: List[RecipeConflict] = []
        parameter_source: Dict[str, str] = {}  # recipe_id@version
        parameter_layer: Dict[str, int] = {}

        for manifest in ordered:
            recipe_key = f"{manifest.recipe_id}@{manifest.version}"
            for parameter, value in sorted(manifest.parameters.items()):
                if parameter in merged and not _same_value(merged[parameter], value):
                    if manifest.layer.value == parameter_layer.get(parameter):
                        conflicts.append(
                            RecipeConflict(
                                parameter=parameter,
                                layer=manifest.layer.value,
                                values={},
                            )
                        )
                        continue
                    builder.remember(
                        parameter, merged[parameter], "recipe", parameter_source[parameter]
                    )
                merged[parameter] = value
                parameter_source[parameter] = recipe_key
                parameter_layer[parameter] = manifest.layer.value
                builder.set_recipe(parameter, value, recipe_key)

        if conflicts:
            for conflict in conflicts:
                conflict.values = {
                    m.recipe_id: m.parameters.get(conflict.parameter)
                    for m in ordered
                    if conflict.parameter in m.parameters
                }
            raise RecipeConflictError(
                "same-layer parameter conflicts detected",
                details={
                    "step_id": request.step_id,
                    "conflicts": [c.model_dump() for c in conflicts],
                },
            )

        warnings: List[Dict[str, Any]] = []
        pending: List[PendingConfirmation] = []
        for manifest in ordered:
            for warning in manifest.warnings:
                warnings.append(
                    {
                        "code": warning.code,
                        "message": warning.message or warning.code,
                        "severity": warning.severity,
                        "recipe_id": manifest.recipe_id,
                    }
                )
            for confirmation in manifest.confirmations:
                if confirmation.key not in request.confirmed_keys:
                    pending.append(
                        PendingConfirmation(
                            key=confirmation.key,
                            recipe_id=manifest.recipe_id,
                            prompt=confirmation.prompt,
                            required=confirmation.required,
                        )
                    )

        # derived parameters（白名单函数）。先于 user patch：patch(层 60)可覆盖派生值。
        derived_outputs: Dict[str, Any] = {}
        for manifest in ordered:
            for ref in manifest.derived_parameters:
                value = self._resolver.resolve(ref, request.derived_inputs)
                inputs_ref = ",".join(ref.inputs) or "structure"
                if ref.target == "KPOINTS":
                    derived_outputs[ref.parameter] = value
                    continue
                if isinstance(value, dict):
                    for key, sub_value in sorted(value.items()):
                        if key in merged:
                            builder.remember(
                                key, merged[key], "recipe", parameter_source.get(key, "unknown")
                            )
                        merged[key] = sub_value
                        parameter_source[key] = f"derived:{ref.function}"
                        parameter_layer[key] = manifest.layer.value
                        builder.set_derived(
                            key,
                            sub_value,
                            ref.function,
                            inputs_ref,
                            requires_confirmation=ref.function == "generate_ldau_arrays",
                            confirmed=ref.function == "generate_ldau_arrays",
                        )
                else:
                    if ref.parameter in merged:
                        builder.remember(
                            ref.parameter,
                            merged[ref.parameter],
                            "recipe",
                            parameter_source.get(ref.parameter, "unknown"),
                        )
                    merged[ref.parameter] = value
                    parameter_source[ref.parameter] = f"derived:{ref.function}"
                    parameter_layer[ref.parameter] = manifest.layer.value
                    builder.set_derived(
                        ref.parameter,
                        value,
                        ref.function,
                        inputs_ref,
                        requires_confirmation=ref.function == "generate_magmom_from_structure",
                        confirmed=request.context.magnetic
                        if ref.function == "generate_magmom_from_structure"
                        else True,
                    )

        # user patches（layer 60，最后应用，可覆盖任何下层值）
        applied_patches: List[Dict[str, Any]] = []
        if request.patches:
            self._validator.validate_all(
                request.patches, ordered, merged, request.revision
            )
            for patch in request.patches:
                if patch.operation == PatchOperation.REMOVE:
                    if patch.parameter in merged:
                        builder.remember(
                            patch.parameter,
                            merged[patch.parameter],
                            "recipe",
                            parameter_source.get(patch.parameter, "unknown"),
                        )
                        merged.pop(patch.parameter)
                        parameter_source.pop(patch.parameter, None)
                else:
                    if patch.parameter in merged:
                        builder.remember(
                            patch.parameter,
                            merged[patch.parameter],
                            "recipe",
                            parameter_source.get(patch.parameter, "unknown"),
                        )
                    merged[patch.parameter] = patch.value
                    builder.set_patch(
                        patch.parameter, patch.value, patch.patch_id, patch.confirmed_by_user
                    )
                applied_patches.append(patch.model_dump(mode="json"))

        selected_entries: List[SelectedRecipeEntry] = []
        order_counters: Dict[int, int] = {}
        for manifest in ordered:
            layer_value = manifest.layer.value
            index = order_counters.get(layer_value, 0)
            order_counters[layer_value] = index + 1
            reason = next(
                (e.reason for e in request.entries if e.ref.key == f"{manifest.recipe_id}@{manifest.version}"),
                "",
            )
            matched = next(
                (e.matched for e in request.entries if e.ref.key == f"{manifest.recipe_id}@{manifest.version}"),
                {},
            )
            selected_entries.append(
                SelectedRecipeEntry(
                    recipe_id=manifest.recipe_id,
                    version=manifest.version,
                    layer=manifest.kind.value,
                    order=layer_value + index,
                    sha256=manifest.sha256,
                    selection_reason=reason,
                    matched_context=matched,
                )
            )

        status = (
            RecipeCompositionStatus.CONFIRMED
            if not pending and not conflicts
            else RecipeCompositionStatus.NEEDS_CONFIRMATION
        )
        composition = RecipeComposition(
            composition_id=composition_id,
            step_id=request.step_id,
            revision=request.revision,
            composition_status=status,
            recipe_pack=(self._pack.model_dump(mode="json") if self._pack else {}),
            selected=selected_entries,
            resolved_parameters={k: merged[k] for k in sorted(merged)},
            derived_outputs=derived_outputs,
            patches=applied_patches,
            confirmations=pending,
            conflicts=conflicts,
            warnings=warnings,
        )
        composition.provenance = [p.model_dump(mode="json") for p in builder.build()]
        composition.composition_sha256 = self._composition_hash(composition)
        return composition

    # --- 校验 ---

    def _check_scope(self, manifest: RecipeManifest, context: SelectionContext) -> None:
        scope = manifest.scope
        if scope.tasks and context.task not in scope.tasks:
            raise RecipeScopeMismatch(
                f"recipe {manifest.recipe_id} scope does not cover task {context.task.value}",
                details={
                    "recipe_id": manifest.recipe_id,
                    "task": context.task.value,
                    "scope_tasks": [t.value for t in scope.tasks],
                },
            )
        if scope.electronic_types and context.electronic_type not in scope.electronic_types:
            raise RecipeScopeMismatch(
                f"recipe {manifest.recipe_id} scope does not cover electronic type "
                f"{context.electronic_type.value}",
                details={"recipe_id": manifest.recipe_id},
            )

    def _check_requires(self, manifest: RecipeManifest, context: SelectionContext) -> None:
        facts = {
            "material_assumptions.magnetic": context.magnetic,
            "material_assumptions.dftu": context.dftu,
            "material_assumptions.soc": False,
        }
        for requirement in manifest.requires:
            if facts.get(requirement) is False:
                raise RecipeScopeMismatch(
                    f"recipe {manifest.recipe_id} requires {requirement} which is not satisfied",
                    details={"recipe_id": manifest.recipe_id, "requires": requirement},
                )

    def _check_conflicts_declared(self, ordered: List[RecipeManifest]) -> None:
        selected_ids = {m.recipe_id for m in ordered}
        for manifest in ordered:
            for forbidden in manifest.conflicts:
                if forbidden in selected_ids:
                    raise RecipeConflictError(
                        f"recipe {manifest.recipe_id} declares conflict with {forbidden}",
                        details={"recipe_id": manifest.recipe_id, "conflict": forbidden},
                    )

    @staticmethod
    def _composition_hash(composition: RecipeComposition) -> str:
        payload = {
            "selected": [
                {"recipe_ref": f"{s.recipe_id}@{s.version}", "order": s.order}
                for s in composition.selected
            ],
            "resolved_parameters": composition.resolved_parameters,
            "derived_outputs": composition.derived_outputs,
            "patches": composition.patches,
            "confirmations_pending": [c.key for c in composition.confirmations],
            "revision": composition.revision,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return "composition-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]
