"""typed patch / OverrideValidator：白名单、语义参数确认、乐观并发、remove 限制。"""

import pytest

from backend.app.recipes.errors import (
    CompositionRevisionConflict,
    OverrideNotAllowed,
)
from backend.app.recipes.overrides import OverrideValidator
from backend.app.schemas.generation import ParameterPatch, PatchOperation
from backend.app.schemas.recipe import (
    AllowedOverrideRule,
    RecipeKind,
    RecipeManifest,
    RecipeScope,
    TaskType,
)
from backend.app.workflow.pipeline import WorkflowGenerationPipeline


def _manifest(allowed_overrides, parameters):
    return RecipeManifest(
        recipe_id="custom",
        version="1.0.0",
        kind=RecipeKind.TASK,
        scope=RecipeScope(tasks=[TaskType.RELAX]),
        parameters=parameters,
        allowed_overrides=allowed_overrides,
    )


def _patch(parameter, value=None, operation="replace", confirmed=False, expected_revision=1):
    return ParameterPatch(
        patch_id=f"patch_{parameter}",
        parameter=parameter,
        operation=PatchOperation(operation),
        value=value,
        expected_revision=expected_revision,
        confirmed_by_user=confirmed,
    )


class TestWhitelist:
    def test_whitelisted_numeric_allowed(self):
        result = OverrideValidator().validate_patch(
            _patch("ENCUT", 600.0), [], {"ENCUT": 520.0}, current_revision=1
        )
        assert result.allowed is True
        assert "OVERRIDE_WHITELIST" in result.rule_ids

    def test_unknown_parameter_rejected(self):
        with pytest.raises(OverrideNotAllowed) as excinfo:
            OverrideValidator().validate_patch(
                _patch("LSORBIT", True, confirmed=True), [], {}, current_revision=1
            )
        assert excinfo.value.code == "OVERRIDE_NOT_ALLOWED"

    def test_type_violation_rejected(self):
        validator = OverrideValidator()
        with pytest.raises(OverrideNotAllowed):
            validator.validate_patch(_patch("ISMEAR", 1.5, confirmed=True), [], {}, 1)
        with pytest.raises(OverrideNotAllowed):
            validator.validate_patch(_patch("ALGO", "Turbo", confirmed=True), [], {}, 1)
        with pytest.raises(OverrideNotAllowed):
            validator.validate_patch(_patch("ENCUT", -10.0), [], {}, 1)

    def test_recipe_rule_overrides_global(self):
        manifest = _manifest(
            {"NSW": AllowedOverrideRule(type="integer", minimum=10)}, {"NSW": 100}
        )
        validator = OverrideValidator()
        result = validator.validate_patch(_patch("NSW", 50), [manifest], {}, 1)
        assert result.allowed is True
        with pytest.raises(OverrideNotAllowed):
            validator.validate_patch(_patch("NSW", 5), [manifest], {}, 1)


class TestSemanticConfirmation:
    @pytest.mark.parametrize("parameter", ["MAGMOM", "ISPIN", "EDIFFG", "LDAUU", "ICHARG"])
    def test_semantic_requires_user_confirmation(self, parameter):
        value = 2 if parameter in ("ISPIN", "EDIFFG") else [1.0]
        with pytest.raises(OverrideNotAllowed):
            OverrideValidator().validate_patch(
                _patch(parameter, value, confirmed=False), [], {}, 1
            )

    def test_semantic_allowed_when_confirmed(self):
        result = OverrideValidator().validate_patch(
            _patch("ISPIN", 2, confirmed=True), [], {}, 1
        )
        assert result.allowed is True


class TestRevisionAndRemove:
    def test_expected_revision_conflict(self):
        with pytest.raises(CompositionRevisionConflict) as excinfo:
            OverrideValidator().validate_patch(
                _patch("ENCUT", 600.0, expected_revision=2), [], {}, current_revision=1
            )
        assert excinfo.value.code == "COMPOSITION_REVISION_CONFLICT"

    def test_remove_requires_removable_rule(self):
        with pytest.raises(OverrideNotAllowed):
            OverrideValidator().validate_patch(
                _patch("ENCUT", operation="remove"), [], {"ENCUT": 520.0}, 1
            )

    def test_remove_allowed_when_rule_says_so(self):
        manifest = _manifest(
            {"LWAVE": AllowedOverrideRule(type="boolean", removable=True)},
            {"LWAVE": True},
        )
        result = OverrideValidator().validate_patch(
            _patch("LWAVE", operation="remove", confirmed=True),
            [manifest],
            {"LWAVE": True},
            1,
        )
        assert result.allowed is True
        assert "REMOVE_ALLOWED" in result.rule_ids


class TestPipelinePatchIntegration:
    def test_encut_patch_applied_and_provenance_user_patch(self, fe2o3_request):
        fe2o3_request.patches = [
            _patch("ENCUT", 600.0, operation="replace")
        ]
        result = WorkflowGenerationPipeline().generate(fe2o3_request)
        for composition in result.compositions.values():
            assert composition.resolved_parameters["ENCUT"] == 600.0
            entry = next(
                p for p in composition.provenance if p["parameter"] == "ENCUT"
            )
            assert entry["source_type"] == "user_patch"

    def test_step_scoped_patch_only_affects_target_step(self, fe2o3_request):
        patch = _patch("ENCUT", 640.0)
        patch.step_id = "02_static"
        fe2o3_request.patches = [patch]
        result = WorkflowGenerationPipeline().generate(fe2o3_request)
        assert result.compositions["02_static"].resolved_parameters["ENCUT"] == 640.0
        assert result.compositions["01_relax"].resolved_parameters["ENCUT"] != 640.0

    def test_disallowed_patch_blocks_generation(self, fe2o3_request):
        fe2o3_request.patches = [_patch("LSORBIT", True, confirmed=True)]
        with pytest.raises(OverrideNotAllowed):
            WorkflowGenerationPipeline().generate(fe2o3_request)

    def test_semantic_patch_without_confirmation_blocks(self, fe2o3_request):
        fe2o3_request.patches = [_patch("EDIFFG", -0.02)]
        with pytest.raises(OverrideNotAllowed):
            WorkflowGenerationPipeline().generate(fe2o3_request)

    def test_stale_revision_blocks_generation(self, fe2o3_request):
        fe2o3_request.patches = [_patch("ENCUT", 600.0, expected_revision=5)]
        with pytest.raises(CompositionRevisionConflict):
            WorkflowGenerationPipeline().generate(fe2o3_request)
