"""Task-specific standard precision through the real preview/generation path."""

import json

import pytest

from backend.app.parsers.incar import parse_incar
from backend.app.recipes.derived import generate_kpoint_grid
from backend.app.schemas.generation import ParameterPatch
from backend.app.schemas.recipe import PrecisionLevel, TaskType
from backend.app.workflow.pipeline import WorkflowGenerationPipeline


@pytest.mark.parametrize("precision,ediffs,encut,nelm,kppas", [
    (PrecisionLevel.QUICK, [1e-4, 1e-4, 1e-4], 400, 60, [500, 500, 800]),
    (PrecisionLevel.STANDARD, [1e-5, 1e-6, 1e-6], 520, 100, [1000, 1500, 2000]),
    (PrecisionLevel.HIGH, [1e-6, 1e-6, 1e-6], 600, 200, [1500, 1500, 2000]),
])
def test_all_precision_modes_preview_match_generated(
    fe2o3_request, precision, ediffs, encut, nelm, kppas,
):
    fe2o3_request.precision = precision
    pipeline = WorkflowGenerationPipeline()
    preview = pipeline.preview_plan(fe2o3_request)
    result = pipeline.generate(fe2o3_request)
    for index, step in enumerate(result.steps):
        params = step.parameters
        assert preview["steps"][index]["parameters"] == params
        assert params["EDIFF"] == ediffs[index]
        assert params["ENCUT"] == encut
        assert params["NELM"] == nelm
        assert (params.get("ADDGRID") is True) == (precision == PrecisionLevel.HIGH)
        parsed = parse_incar(result.bundle.files[f"{step.directory}/INCAR"].decode())
        for key in ("EDIFF", "ENCUT", "NELM"):
            assert parsed.effective[key] == params[key]
        kpoints = result.bundle.files[f"{step.directory}/KPOINTS"].decode().splitlines()
        expected = generate_kpoint_grid({
            "kppa": kppas[index], "atom_count": 5,
            "lattice": fe2o3_request.structure.lattice.model_dump(),
        })
        assert kpoints[3] == " ".join(map(str, expected["grid"]))
        assert f"kppa={kppas[index]:g}" in kpoints[0].lower()
        selected = result.compositions[step.step_id].selected[-1]
        expected_recipe = ("precision.standard.scf" if precision == PrecisionLevel.STANDARD
                           and step.task in ("static", "dos") else f"precision.{precision.value}")
        assert selected.recipe_id == expected_recipe
        if precision == PrecisionLevel.STANDARD and step.task in ("static", "dos"):
            provenance = next(p for p in result.compositions[step.step_id].provenance
                              if p["parameter"] == "EDIFF")
            assert provenance["source_id"] == "precision.standard.scf@1.0.0"


@pytest.mark.parametrize("step_id,ediff", [
    (step_id, ediff) for step_id in ("02_static", "03_dos")
    for ediff in (2e-5, 1e-6, 1e-7)
])
def test_ediff_patch_wins_and_advice_uses_final_value(fe2o3_request, step_id, ediff):
    fe2o3_request.patches = [ParameterPatch(
        patch_id="user_ediff", step_id=step_id, parameter="EDIFF", operation="replace", value=ediff,
    )]
    pipeline = WorkflowGenerationPipeline()
    preview = pipeline.preview_plan(fe2o3_request)
    result = pipeline.generate(fe2o3_request)
    composition = result.compositions[step_id]
    assert composition.resolved_parameters["EDIFF"] == ediff
    assert next(p for p in composition.provenance if p["parameter"] == "EDIFF")["source_type"] == "user_patch"
    assert parse_incar(result.bundle.files[f"{step_id}/INCAR"].decode()).effective["EDIFF"] == ediff
    code = "STATIC_TIGHTER_EDIFF_HINT" if step_id == "02_static" else "DOS_TIGHTER_EDIFF_HINT"
    warnings = [w for w in composition.warnings if w["code"] == code]
    assert bool(warnings) == (ediff > 1e-6)
    if warnings:
        assert f"EDIFF={ediff:.6g}" in warnings[0]["message"]
        assert "1E-6" in warnings[0]["message"]
        assert "请选择 high" not in warnings[0]["message"]
    preview_composition = next(c for c in preview["recipe_compositions"] if c["step_id"] == step_id)
    assert preview_composition["resolved_parameters"] == composition.resolved_parameters
    assert preview_composition["warnings"] == composition.warnings
    assert result.compositions["01_relax"].resolved_parameters["EDIFF"] == 1e-5


def test_encut_override_does_not_change_precision_mode(fe2o3_request):
    fe2o3_request.patches = [ParameterPatch(
        patch_id="user_encut", parameter="ENCUT", operation="replace", value=575,
    )]
    result = WorkflowGenerationPipeline().generate(fe2o3_request)
    assert {s.parameters["ENCUT"] for s in result.steps} == {575}
    assert [s.parameters["EDIFF"] for s in result.steps] == [1e-5, 1e-6, 1e-6]
    assert all("ADDGRID" not in s.parameters for s in result.steps)


@pytest.mark.parametrize("precision,ediff", [
    (PrecisionLevel.QUICK, 1e-4), (PrecisionLevel.STANDARD, 1e-5), (PrecisionLevel.HIGH, 1e-6),
])
def test_band_keeps_existing_precision_recipe(nacl_request, precision, ediff):
    nacl_request.precision = precision
    nacl_request.requested_tasks = [TaskType.STATIC, TaskType.BAND]
    nacl_request.enable_band_workflow = True
    pipeline = WorkflowGenerationPipeline()
    preview = pipeline.preview_plan(nacl_request)
    result = pipeline.generate(nacl_request)
    band = result.compositions["04_band"]
    assert band.selected[-1].recipe_id == f"precision.{precision.value}"
    assert band.resolved_parameters["EDIFF"] == ediff
    assert preview["steps"][-1]["parameters"] == result.steps[-1].parameters
    assert parse_incar(result.bundle.files["04_band/INCAR"].decode()).effective["EDIFF"] == ediff


def test_dftu_dos_inheritance_stays_intact(fe2o3_request):
    result = WorkflowGenerationPipeline().generate(fe2o3_request)
    for step in result.steps:
        assert step.parameters["LDAU"] is True
        assert step.parameters["LDAUL"] == [2, -1]
        assert step.parameters["LDAUU"] == [4.0, 0.0]
        assert step.parameters["LDAUJ"] == [0.0, 0.0]
        assert step.parameters["LMAXMIX"] >= 4
    dos = result.compositions["03_dos"].resolved_parameters
    assert dos["ICHARG"] == 11
    assert dos["ISMEAR"] == -5
    assert dos["NEDOS"] == 2000
    assert any(d.from_step_id == "02_static" and d.to_step_id == "03_dos"
               and d.source_file == "CHGCAR" for d in result.file_inheritance_plan.dependencies)


def test_pack_version_and_repeat_generation_identity(fe2o3_request):
    pipeline = WorkflowGenerationPipeline()
    first = pipeline.generate(fe2o3_request)
    second = pipeline.generate(fe2o3_request)
    assert first.pack.version == "1.1.0"
    assert first.bundle.files == second.bundle.files
    assert first.bundle.zip_bytes == second.bundle.zip_bytes
    manifest = json.loads(first.bundle.files["workflow_manifest.json"])
    assert manifest["recipe_pack_version"] == "1.1.0"
