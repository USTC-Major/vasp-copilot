"""库级 contract：门面对象/响应体对齐 6.4/6.5、workflow_plan.json 对齐 7.2 节。"""

import hashlib
import json

import pytest

from backend.app.recipes.errors import BeAError
from backend.app.schemas.generation import (
    DftuEntry, DftuSettings, MaterialAssumptions, SchedulerSettings,
    StructureContext, WorkflowGenerateRequest,
)
from backend.app.schemas.recipe import ElectronicType, PrecisionLevel, TaskType
from backend.app.workflow.pipeline import WorkflowGenerationPipeline


SI2_POSCAR = """Si diamond primitive cell; demonstration starting geometry, not optimized
5.43
0.0000000000 0.5000000000 0.5000000000
0.5000000000 0.0000000000 0.5000000000
0.5000000000 0.5000000000 0.0000000000
Si
2
Direct
0.0000000000 0.0000000000 0.0000000000
0.2500000000 0.2500000000 0.2500000000
"""


def test_si2_static_default_generates_gamma_8_with_unchanged_other_inputs():
    """Replay the accepted Si2 static options through the real generation pipeline."""
    source_sha = hashlib.sha256(SI2_POSCAR.encode("utf-8")).hexdigest()
    assert source_sha == "45db62da59abe72824c5628262451a4aaa6540a7af2356dcb0de955c53dcf8e7"
    request = WorkflowGenerateRequest(
        workflow_id="wf_e39f0382",
        structure=StructureContext(formula="Si2", elements=["Si"], counts=[2],
                                   poscar_text=SI2_POSCAR, source_sha256=source_sha),
        requested_tasks=[TaskType.STATIC], goal_text="static",
        material_assumptions=MaterialAssumptions(
            electronic_type=ElectronicType.SEMICONDUCTOR, magnetic=False),
        precision=PrecisionLevel.STANDARD,
        scheduler=SchedulerSettings(type="slurm", nodes=1, tasks_per_node=8,
                                    walltime="00:10:00"),
    )
    result = WorkflowGenerationPipeline().generate(request)
    files = result.bundle.files
    assert files["02_static/KPOINTS"].decode("utf-8").splitlines()[1:] == [
        "0", "Gamma", "8 8 8", "0 0 0",
    ]
    expected_hashes = {
        "02_static/POSCAR": source_sha,
        "02_static/INCAR": "dc5a2f04cb6cc114d8810d802498a31e76c5d293ea4211d8b2c5163c7bef9c47",
        "02_static/submit.sh": "fecb66fc0671638aef33bb95c33d28a1675de946b8348d07515fcf1bfa960cb8",
    }
    for path, expected in expected_hashes.items():
        assert hashlib.sha256(files[path]).hexdigest() == expected, path
    manifest = json.loads(files["workflow_manifest.json"].decode("utf-8"))
    kpoints_entry = next(item for item in manifest["files"] if item["path"] == "02_static/KPOINTS")
    assert kpoints_entry["sha256"] == hashlib.sha256(files["02_static/KPOINTS"]).hexdigest()


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
        # VASP Line-mode 官方四行头：comment / divisions / Line-mode / Reciprocal。
        # 旧断言 splitlines()[2] == "Reciprocal" 固化了缺失 Line-mode 行的非法格式。
        lines = kpoints.splitlines()
        assert lines[2] == "Line-mode"
        assert lines[3] == "Reciprocal"

    def test_band_kpoints_line_mode_end_to_end(self, nacl_request):
        """端到端：pipeline 生成的 04_band/KPOINTS 必须是合法 Line-mode 端点对格式。"""

        from backend.app.parsers.kpoints import parse_kpoints

        nacl_request.requested_tasks.append(TaskType.BAND)
        nacl_request.enable_band_workflow = True
        result = WorkflowGenerationPipeline().generate(nacl_request)
        text = result.bundle.files["04_band/KPOINTS"].decode("utf-8")
        lines = text.splitlines()
        assert len(lines) >= 6
        assert lines[2] == "Line-mode"
        assert lines[3] == "Reciprocal"
        divisions = int(lines[1])
        assert divisions >= 1
        assert text.endswith("\n") and not text.endswith("\n\n")
        body = text.split("Reciprocal\n", 1)[1]
        pairs = body.rstrip("\n").split("\n\n")
        assert pairs, "04_band/KPOINTS 必须列出高对称路径端点对"
        endpoint_count = 0
        for pair in pairs:
            endpoints = pair.split("\n")
            assert len(endpoints) == 2, f"每段必须恰为起点/终点两行，实际 {endpoints}"
            for line in endpoints:
                assert "!" in line
                assert len(line.split("!")[0].split()) == 3
                assert line.split("!")[1].strip()
            endpoint_count += 2
        assert endpoint_count == 2 * len(pairs)
        parsed = parse_kpoints(text)
        assert parsed.line_mode is True
        assert parsed.mode == "Line-mode"
        assert parsed.nkpts == divisions
