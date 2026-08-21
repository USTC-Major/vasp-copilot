"""验收 8：每个最终参数恰有一条 provenance；composition hash 对输入敏感。"""

import pytest

from backend.app.recipes.composer import ComposeRequest, RecipeComposer
from backend.app.recipes.selector import RecipeSelector
from backend.app.schemas.generation import ParameterPatch, PatchOperation
from backend.app.schemas.recipe import (
    ElectronicType,
    PrecisionLevel,
    SelectionContext,
    TaskType,
)


def _compose(registry, pack, *, patch=None, precision=PrecisionLevel.STANDARD):
    context = SelectionContext(
        task=TaskType.RELAX,
        electronic_type=ElectronicType.SEMICONDUCTOR,
        precision=precision,
        magnetic=True,
        elements=["Fe", "O"],
    )
    entries = RecipeSelector().select(context)
    return RecipeComposer(registry, pack).compose(
        ComposeRequest(
            step_id="01_relax",
            context=context,
            entries=entries,
            patches=[patch] if patch else [],
            confirmed_keys={"EDIFFG"},
            derived_inputs={
                "elements": ["Fe", "O"],
                "counts": [2, 3],
                "formula": "Fe2O3",
                "task": "relax",
                "precision": precision.value,
            },
        )
    )


class TestProvenanceUniqueness:
    def test_each_parameter_has_exactly_one_provenance(self, registry, pack):
        composition = _compose(registry, pack)
        parameters = list(composition.resolved_parameters)
        provenance_parameters = [entry["parameter"] for entry in composition.provenance]
        assert sorted(provenance_parameters) == sorted(parameters)
        assert len(provenance_parameters) == len(set(provenance_parameters))

    def test_provenance_values_match_resolved(self, registry, pack):
        composition = _compose(registry, pack)
        for entry in composition.provenance:
            assert entry["value"] == composition.resolved_parameters[entry["parameter"]]

    def test_patch_creates_user_patch_provenance(self, registry, pack):
        patch = ParameterPatch(
            patch_id="patch_01",
            parameter="ENCUT",
            operation=PatchOperation.REPLACE,
            value=600.0,
            expected_revision=1,
            confirmed_by_user=True,
        )
        composition = _compose(registry, pack, patch=patch)
        provenance = {
            entry["parameter"]: entry for entry in composition.provenance
        }
        assert provenance["ENCUT"]["source_type"] == "user_patch"
        assert provenance["ENCUT"]["source_id"] == "patch_01"
        assert provenance["ENCUT"]["overrode"] is not None
        assert len([e for e in composition.provenance if e["parameter"] == "ENCUT"]) == 1

    def test_derived_provenance_records_function(self, registry, pack):
        composition = _compose(registry, pack)
        provenance = {
            entry["parameter"]: entry for entry in composition.provenance
        }
        assert provenance["MAGMOM"]["source_type"] == "derived_function"
        assert "generate_magmom_from_structure" in provenance["MAGMOM"]["derived_by"]


class TestCompositionHashSensitivity:
    def test_same_input_same_hash(self, registry, pack):
        first = _compose(registry, pack)
        second = _compose(registry, pack)
        assert first.composition_sha256 == second.composition_sha256

    def test_precision_change_changes_hash(self, registry, pack):
        standard = _compose(registry, pack)
        high = _compose(registry, pack, precision=PrecisionLevel.HIGH)
        assert standard.composition_sha256 != high.composition_sha256

    def test_patch_changes_hash(self, registry, pack):
        base = _compose(registry, pack)
        patched = _compose(
            registry,
            pack,
            patch=ParameterPatch(
                patch_id="patch_02",
                parameter="ENCUT",
                operation=PatchOperation.REPLACE,
                value=600.0,
                expected_revision=1,
                confirmed_by_user=True,
            ),
        )
        assert base.composition_sha256 != patched.composition_sha256
