"""库级 contract：门面对象/响应体对齐 6.4/6.5、workflow_plan.json 对齐 7.2 节。"""

import json

import pytest

from backend.app.recipes.errors import BeAError
from backend.app.schemas.generation import DftuEntry, DftuSettings
from backend.app.schemas.recipe import TaskType
from backend.app.workflow.pipeline import WorkflowGenerationPipeline


class TestFacadeContract:
    def test_response_body_shape(self, fe2o3_request):
        result = WorkflowGenerationPipeline().generate(fe2o3_request)
        body = result.to_response_body()
        assert set(body) == {
            "workflow_id",
            "workflow_status",
            "revision",
            "file_tree",
            "validation",
            "manifest",
        }
        assert body["workflow_id"] == "wf_fe2o3"
        assert body["workflow_status"] == "generated"
        assert body["revision"] == 1
        assert body["validation"]["valid"] is True
        assert body["validation"]["provenance_complete"] is True
        assert body["manifest"]["bundle_sha256"] == result.bundle.manifest.bundle_sha256

    def test_every_step_has_four_generated_files(self, fe2o3_request):
        result = WorkflowGenerationPipeline().generate(fe2o3_request)
        for step in result.steps:
            for name in ("POSCAR", "INCAR", "KPOINTS", "submit.sh"):
                assert f"{step.directory}/{name}" in result.bundle.files

    def test_response_body_is_json_serializable(self, fe2o3_request):
        result = WorkflowGenerationPipeline().generate(fe2o3_request)
        text = json.dumps(result.to_response_body(), ensure_ascii=False)
        assert json.loads(text)["workflow_id"] == "wf_fe2o3"


class TestWorkflowPlanFileContract:
    def test_plan_file_required_blocks(self, fe2o3_request):
        result = WorkflowGenerationPipeline().generate(fe2o3_request)
        body = json.loads(result.bundle.files["workflow_plan.json"].decode("utf-8"))
        for key in (
            "workflow_id",
            "revision",
            "created_at",
            "structure",
            "goal",
            "assumptions",
            "dftu",
            "scheduler",
            "steps",
            "file_inheritance_plan",
            "recipe_compositions",
            "confirmations",
            "warnings",
            "template_versions",
        ):
            assert key in body, f"workflow_plan.json 缺少 {key}"
        assert body["structure"]["formula"] == "Fe2O3"
        assert body["assumptions"]["electronic_type"] == "metal"
        assert body["scheduler"]["scheduler_type"] == "slurm"

    def test_steps_embedded_with_gating(self, fe2o3_request):
        result = WorkflowGenerationPipeline().generate(fe2o3_request)
        body = json.loads(result.bundle.files["workflow_plan.json"].decode("utf-8"))
        steps = body["steps"]
        assert [step["step_id"] for step in steps] == ["01_relax", "02_static", "03_dos"]
        for step in steps:
            assert step["runnable"] is False
            assert "POTCAR_NOT_PREPARED" in step["blocked_by"]
            assert step["parameters"], "每个 step 必须携带解析后的参数"

    def test_recipe_compositions_reference_pack(self, fe2o3_request):
        result = WorkflowGenerationPipeline().generate(fe2o3_request)
        body = json.loads(result.bundle.files["workflow_plan.json"].decode("utf-8"))
        assert len(body["recipe_compositions"]) == len(result.steps)
        for entry in body["recipe_compositions"]:
            assert entry["composition_sha256"].startswith("composition-")
            assert entry["recipe_pack"]["pack_id"] == result.pack.pack_id
            assert entry["selected"]

    def test_confirm_false_fails_closed_with_pending_confirmations(self, nacl_request):
        """fail closed：存在待确认项且 confirm=false 时拒绝生成，并返回待确认清单。"""

        from backend.app.recipes.errors import RecipeConfirmationRequired

        nacl_request.confirm = False
        with pytest.raises(RecipeConfirmationRequired) as excinfo:
            WorkflowGenerationPipeline().generate(nacl_request)
        assert excinfo.value.code == "RECIPE_CONFIRMATION_REQUIRED"
        assert excinfo.value.details.get("confirmations"), "错误应携带待确认项清单"

    def test_confirm_true_clears_confirmations(self, nacl_request):
        result = WorkflowGenerationPipeline().generate(nacl_request)
        body = json.loads(result.bundle.files["workflow_plan.json"].decode("utf-8"))
        assert body["confirmations"] == []


class TestRequestValidation:
    def test_unconfirmed_dftu_rejected(self, fe2o3_request):
        fe2o3_request.dftu = DftuSettings(
            enabled=True,
            entries=[DftuEntry(element="Fe", l=2, u_ev=4.0, confirmed_by_user=False)],
        )
        with pytest.raises(BeAError) as excinfo:
            WorkflowGenerationPipeline().generate(fe2o3_request)
        assert excinfo.value.code == "DFTU_CONFIRMATION_REQUIRED"

    def test_missing_poscar_text_rejected(self, fe2o3_request):
        fe2o3_request.structure.poscar_text = ""
        with pytest.raises(BeAError):
            WorkflowGenerationPipeline().generate(fe2o3_request)

    def test_band_task_requires_flag(self, fe2o3_request):
        fe2o3_request.requested_tasks.append(TaskType.BAND)
        with pytest.raises(BeAError) as excinfo:
            WorkflowGenerationPipeline().generate(fe2o3_request)
        assert excinfo.value.code == "BAND_WORKFLOW_DISABLED"

    def test_band_task_allowed_with_flag(self, nacl_request):
        nacl_request.requested_tasks.append(TaskType.BAND)
        nacl_request.enable_band_workflow = True
        result = WorkflowGenerationPipeline().generate(nacl_request)
        band = result.steps[-1]
        assert band.step_id == "04_band"
        kpoints = result.bundle.files["04_band/KPOINTS"].decode("utf-8")
        assert kpoints.splitlines()[2] == "Reciprocal"
