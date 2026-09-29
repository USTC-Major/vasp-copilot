"""U-2: explicit DFT+U forms, legacy compatibility and final INCAR consistency."""

import math

import pytest
from pydantic import ValidationError

from backend.app.recipes.errors import BeAError, DerivedParameterUnresolved
from backend.app.recipes.derived import generate_ldau_arrays
from backend.app.schemas.generation import DftuSettings, StructureContext, WorkflowGenerateRequest
from backend.app.schemas.recipe import TaskType
from backend.app.workflow.pipeline import WorkflowGenerationPipeline

CE_POSCAR = "Ce\n1\n5 0 0\n0 5 0\n0 0 5\nCe\n1\nDirect\n0 0 0\n"


def _request(dftu: dict, tasks=None) -> WorkflowGenerateRequest:
    return WorkflowGenerateRequest(
        workflow_id="wf_u2_ce", structure=StructureContext(
            formula="Ce", elements=["Ce"], counts=[1], poscar_text=CE_POSCAR),
        requested_tasks=tasks or [TaskType.STATIC, TaskType.BAND],
        enable_band_workflow=True, dftu=DftuSettings.model_validate(dftu),
    )


@pytest.mark.parametrize("dftu,kind,u,j", [
    ({"enabled": True, "entries": [{"element": "Ce", "l": 3, "u_ev": 5.0,
                                   "j_ev": 1.0, "source_note": "old record", "confirmed_by_user": True}]},
     2, 5.0, 1.0),
    ({"enabled": True, "form": "dudarev", "input_mode": "u_eff",
      "entries": [{"element": "Ce", "l": 3, "u_eff_ev": 4.0, "confirmed_by_user": True}]},
     2, 4.0, 0.0),
    ({"enabled": True, "form": "liechtenstein", "input_mode": "u_j",
      "entries": [{"element": "Ce", "l": 3, "u_ev": 5.0, "j_ev": 1.0,
                   "confirmed_by_user": True}]},
     1, 5.0, 1.0),
])
def test_form_plan_and_band_zip(dftu, kind, u, j):
    request = _request(dftu)
    pipeline = WorkflowGenerationPipeline()
    preview = pipeline.preview_plan(request)
    assert [step["parameters"]["LDAUTYPE"] for step in preview["steps"]] == [kind, kind]
    result = pipeline.generate(request)
    for step in ("02_static", "04_band"):
        incar = result.bundle.files[f"{step}/INCAR"].decode()
        assert f"LDAUTYPE = {kind}" in incar
        assert f"LDAUU = {u:g}" in incar
        assert f"LDAUJ = {j:g}" in incar
        assert "LMAXMIX = 6" in incar
    assert "ICHARG = 11" in result.bundle.files["04_band/INCAR"].decode()
    assert any(dep.source_file == "CHGCAR" and dep.to_step_id == "04_band"
               for dep in result.file_inheritance_plan.dependencies)
    assert result.plan_file.dftu.form == request.dftu.form
    assert result.plan_file.dftu.input_mode == request.dftu.input_mode
    assert result.plan_file.dftu.entries[0]["source_note"] == dftu["entries"][0].get("source_note")


@pytest.mark.parametrize("payload", [
    {"form": "liechtenstein", "input_mode": "u_j", "entries": [{"element": "Ce", "l": 3, "u_ev": 5}]},
    {"form": "dudarev", "input_mode": "u_eff", "entries": [{"element": "Ce", "l": 3}]},
    {"form": "dudarev", "input_mode": "u_eff", "entries": [{"element": "Ce", "l": 3, "u_eff_ev": 4, "j_ev": 0}]},
    {"form": "liechtenstein", "input_mode": "u_eff", "entries": [{"element": "Ce", "l": 3, "u_eff_ev": 4}]},
    {"form": "unknown", "input_mode": "u_j", "entries": [{"element": "Ce", "l": 3, "u_ev": 5, "j_ev": 1}]},
    {"form": "liechtenstein", "entries": [{"element": "Ce", "l": 3, "u_ev": 5, "j_ev": 1}]},
    {"form": "liechtenstein", "input_mode": "u_j", "entries": [
        {"element": "Ce", "l": 3, "u_ev": 5, "j_ev": 1},
        {"element": "Ce", "l": 3, "u_ev": 6, "j_ev": 1}]},
    {"form": "liechtenstein", "input_mode": "u_j", "entries": [{"element": "Ce", "l": 3, "u_ev": math.nan, "j_ev": 1}]},
    {"form": "liechtenstein", "input_mode": "u_j", "entries": [{"element": "Ce", "l": 3, "u_ev": 5, "j_ev": math.inf}]},
    {"form": "liechtenstein", "input_mode": "u_j", "entries": [{"element": "Ce", "l": 2.5, "u_ev": 5, "j_ev": 1}]},
])
def test_malformed_form_or_entry_rejected(payload):
    with pytest.raises(ValidationError):
        DftuSettings.model_validate({"enabled": True, **payload})


def test_element_must_be_in_poscar():
    request = _request({"enabled": True, "form": "liechtenstein", "input_mode": "u_j",
                        "entries": [{"element": "Fe", "l": 2, "u_ev": 5, "j_ev": 1}]})
    with pytest.raises(BeAError) as excinfo:
        WorkflowGenerationPipeline().preview_plan(request)
    assert excinfo.value.code == "DFTU_ELEMENT_NOT_IN_STRUCTURE"


def test_enabled_without_entries_rejected_before_plan():
    request = _request({"enabled": True, "form": "dudarev", "input_mode": "u_eff", "entries": []})
    with pytest.raises(BeAError) as excinfo:
        WorkflowGenerationPipeline().preview_plan(request)
    assert excinfo.value.code == "DFTU_CONFIRMATION_REQUIRED"


def test_legacy_missing_j_and_null_form_roundtrip():
    legacy = DftuSettings.model_validate({"enabled": True, "entries": [
        {"element": "Ce", "l": 3, "u_ev": 5, "confirmed_by_user": True}]})
    assert legacy.entries[0].j_ev == 0.0
    restored = DftuSettings.model_validate(legacy.model_dump(mode="json"))
    assert restored.form is None and restored.input_mode is None
    assert restored.entries[0].u_ev == 5.0 and restored.entries[0].j_ev == 0.0
    request = _request(restored.model_dump(mode="json"), tasks=[TaskType.STATIC])
    incar = WorkflowGenerationPipeline().generate(request).bundle.files["02_static/INCAR"].decode()
    assert "LDAUTYPE = 2" in incar and "LDAUU = 5" in incar and "LDAUJ = 0" in incar


def test_null_and_absent_legacy_forms_are_equivalent():
    entry = {"element": "Ce", "l": 3, "u_ev": 5, "j_ev": 1}
    absent = DftuSettings.model_validate({"enabled": True, "entries": [entry]})
    null = DftuSettings.model_validate({"enabled": True, "form": None,
                                        "input_mode": None, "entries": [entry]})
    assert absent.model_dump(mode="json") == null.model_dump(mode="json")


def test_disabled_dftu_still_rejects_mismatched_form():
    with pytest.raises(ValidationError, match="Liechtenstein"):
        DftuSettings.model_validate({"enabled": False, "form": "liechtenstein",
                                     "input_mode": "u_eff", "entries": []})


def test_new_form_arrays_follow_poscar_species_order():
    arrays = generate_ldau_arrays({"elements": ["O", "Fe", "Ce"],
        "dftu_input_mode": "u_j", "dftu_entries": [
            {"element": "Ce", "l": 3, "u_ev": 6, "j_ev": 0.5},
            {"element": "Fe", "l": 2, "u_ev": 5, "j_ev": 1}]})
    assert arrays == {"LDAUL": [-1.0, 2.0, 3.0],
                      "LDAUU": [0.0, 5.0, 6.0], "LDAUJ": [0.0, 1.0, 0.5]}


def test_derived_arrays_require_explicit_j_but_preserve_legacy_default():
    inputs = {"elements": ["Ce"], "dftu_entries": [
        {"element": "Ce", "l": 3, "u_ev": 5.0}]}
    with pytest.raises(DerivedParameterUnresolved, match="j_ev"):
        generate_ldau_arrays({**inputs, "dftu_input_mode": "u_j"})
    assert generate_ldau_arrays(inputs) == {
        "LDAUL": [3.0], "LDAUU": [5.0], "LDAUJ": [0.0]}


def test_non_band_final_type_must_match_confirmed_form(monkeypatch):
    request = _request({"enabled": True, "form": "liechtenstein", "input_mode": "u_j",
                        "entries": [{"element": "Ce", "l": 3, "u_ev": 5, "j_ev": 1,
                                     "confirmed_by_user": True}]}, tasks=[TaskType.STATIC])
    pipeline = WorkflowGenerationPipeline()
    original = pipeline._composer.compose

    def drifted(*args, **kwargs):
        composition = original(*args, **kwargs)
        composition.resolved_parameters["LDAUTYPE"] = 2
        return composition

    monkeypatch.setattr(pipeline._composer, "compose", drifted)
    with pytest.raises(BeAError) as excinfo:
        pipeline.preview_plan(request)
    assert excinfo.value.code == "DFTU_FINAL_PARAMETERS_MISMATCH"
    assert excinfo.value.details["parameter"] == "LDAUTYPE"
    assert excinfo.value.details["expected"] == 1
